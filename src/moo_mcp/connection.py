"""Persistent asyncio TCP connection to a LambdaMOO admin port.

One socket, opened once, kept warm. Concurrent callers are serialized through
an asyncio.Lock and demultiplexed via per-request sentinel markers so we never
mistake one request's output for another. Auto-reconnects on drop.

Login: `connect <user> <pass>\\r\\n`, then verified with a round-trip eval
before the connection is considered ready.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import ssl
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class MOOError(Exception):
    """Base class for moo-mcp connection errors."""


class MOONotConnected(MOOError):
    """Raised when a command is issued and reconnection is disabled."""


class MOOTimeout(MOOError):
    """Raised when a command's end-marker is not seen within the timeout."""


class MOOLoginFailed(MOOError):
    """Raised when the post-login verification round-trip fails."""


@dataclass
class MOOConfig:
    host: str
    port: int
    user: str
    password: str
    timeout: float = 15.0
    reconnect: bool = True
    banner_wait: float = 0.8
    tls: bool = False
    tls_insecure: bool = False

    @classmethod
    def from_env(cls) -> MOOConfig:
        missing = [k for k in ("MOO_HOST", "MOO_PORT", "MOO_USER", "MOO_PASS") if not os.environ.get(k)]
        if missing:
            raise MOOError(f"missing required env vars: {', '.join(missing)}")
        user = os.environ["MOO_USER"]
        password = os.environ["MOO_PASS"]
        # SECURITY: user/password get spliced into a `connect <user> <pass>\r\n`
        # TCP frame. CR/LF in either would let a malicious env-var value inject
        # additional commands. Reject at config load.
        for label, val in (("MOO_USER", user), ("MOO_PASS", password)):
            if "\r" in val or "\n" in val or "\x00" in val:
                raise MOOError(f"{label} must not contain CR, LF, or NUL bytes")
        return cls(
            host=os.environ["MOO_HOST"],
            port=int(os.environ["MOO_PORT"]),
            user=user,
            password=password,
            timeout=float(os.environ.get("MOO_TIMEOUT", "15")),
            reconnect=os.environ.get("MOO_RECONNECT", "true").lower() != "false",
            tls=os.environ.get("MOO_TLS", "false").lower() == "true",
            tls_insecure=os.environ.get("MOO_TLS_INSECURE", "false").lower() == "true",
        )

    def build_ssl_context(self) -> ssl.SSLContext | None:
        """Build an SSL context for the connection, or None if TLS is disabled.

        With tls_insecure=True, hostname verification and certificate validation
        are BOTH disabled - only do this for self-signed test MOOs you control.
        """
        if not self.tls:
            return None
        ctx = ssl.create_default_context()
        if self.tls_insecure:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx


class MOOConnection:
    def __init__(self, config: MOOConfig) -> None:
        self.config = config
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lines: asyncio.Queue[str] = asyncio.Queue()
        self._lock = asyncio.Lock()
        self._reader_task: asyncio.Task | None = None
        self._closed = False
        self._connected = False

    async def connect(self) -> None:
        if self._connected:
            return
        scheme = "tls" if self.config.tls else "tcp"
        logger.info("connecting to %s://%s:%s", scheme, self.config.host, self.config.port)
        ssl_ctx = self.config.build_ssl_context()
        # 50MB readline buffer - MOO eval responses for assignments to large
        # lists include the full new list value, which can be many megabytes.
        self._reader, self._writer = await asyncio.open_connection(
            self.config.host,
            self.config.port,
            limit=50_000_000,
            ssl=ssl_ctx,
            server_hostname=self.config.host if ssl_ctx and not self.config.tls_insecure else None,
        )
        self._closed = False
        self._reader_task = asyncio.create_task(self._read_loop(), name="moo-mcp-reader")
        await asyncio.sleep(self.config.banner_wait)
        self._drain_queue()
        login_cmd = f"connect {self.config.user} {self.config.password}\r\n".encode()
        self._writer.write(login_cmd)
        await self._writer.drain()
        await asyncio.sleep(self.config.banner_wait)
        self._drain_queue()
        self._connected = True
        token = secrets.token_hex(4)
        result = await self._send_locked(f';return "moo-mcp-login-{token}"')
        if f"moo-mcp-login-{token}" not in result:
            self._connected = False
            raise MOOLoginFailed(f"login verification did not echo back; got: {result!r}")
        logger.info("connected as %s", self.config.user)

    async def close(self) -> None:
        self._closed = True
        self._connected = False
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        self._writer = None
        self._reader = None

    async def _read_loop(self) -> None:
        assert self._reader is not None
        while not self._closed:
            try:
                raw = await self._reader.readline()
            except (asyncio.CancelledError, ConnectionError):
                break
            except Exception as exc:
                logger.warning("reader error: %s", exc)
                break
            if not raw:
                logger.warning("remote closed connection")
                break
            text = raw.rstrip(b"\r\n").decode("utf-8", errors="replace")
            while text.startswith("﻿"):
                text = text[1:]
            await self._lines.put(text)
        self._connected = False

    def _drain_queue(self) -> None:
        while not self._lines.empty():
            try:
                self._lines.get_nowait()
            except asyncio.QueueEmpty:
                return

    async def send(self, command: str, *, timeout: float | None = None) -> str:
        """Send a command (or multi-line block) and return the response between markers.

        Concurrent calls are serialized. If the connection drops, reconnects once
        before retrying (when MOO_RECONNECT is true).
        """
        async with self._lock:
            if not self._connected:
                if self.config.reconnect:
                    await self._reconnect()
                else:
                    raise MOONotConnected("not connected and reconnect disabled")
            try:
                return await self._send_locked(command, timeout=timeout)
            except (ConnectionError, MOOTimeout) as first_err:
                if not self.config.reconnect:
                    raise
                logger.warning("send failed (%s), reconnecting and retrying once", first_err)
                self._connected = False
                await self._reconnect()
                return await self._send_locked(command, timeout=timeout)

    async def _reconnect(self) -> None:
        await self.close()
        self._closed = False
        await self.connect()

    async def _send_locked(self, command: str, *, timeout: float | None = None) -> str:
        if not self._writer:
            raise MOONotConnected("writer is None")
        start = f"moomcp-{secrets.token_hex(6)}-S"
        end = f"moomcp-{secrets.token_hex(6)}-E"
        block = f';"{start}"\r\n{command}\r\n;"{end}"\r\n'.encode("utf-8")
        self._writer.write(block)
        await self._writer.drain()

        wait = timeout if timeout is not None else self.config.timeout
        captured: list[str] = []
        saw_start = False
        start_token = f'"{start}"'
        end_token = f'"{end}"'
        try:
            async with asyncio.timeout(wait):
                while True:
                    line = await self._lines.get()
                    if not saw_start:
                        if start_token in line and line.lstrip().startswith("=>"):
                            saw_start = True
                        continue
                    if end_token in line and line.lstrip().startswith("=>"):
                        break
                    captured.append(line)
        except TimeoutError as exc:
            raise MOOTimeout(f"no response to end-marker within {wait}s") from exc
        return "\n".join(captured)
