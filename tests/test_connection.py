"""Unit tests for MOOConnection internals that don't need a real socket:
the heuristic login-rejection check, and send_raw's stray-flush + multi-
command pacing + idle-drain wiring (writer stubbed, self._lines is a real
asyncio.Queue fed from a background task)."""

import asyncio

import pytest

from moo_mcp.connection import MOOConfig, MOOConnection, MOOLoginFailed

CFG = MOOConfig(host="example.test", port=7777, user="wizard", password="pw", timeout=5.0)


def test_check_banner_for_login_failure_raises_on_known_rejection():
    conn = MOOConnection(CFG)
    conn._connected = True
    with pytest.raises(MOOLoginFailed):
        conn._check_banner_for_login_failure(
            ["*** Either that player does not exist, or has a different password. ***"]
        )
    assert conn._connected is False


def test_check_banner_for_login_failure_passes_on_unrecognized_text():
    conn = MOOConnection(CFG)
    conn._connected = True
    conn._check_banner_for_login_failure(["Welcome to the MOO!", "*** Connected ***"])
    assert conn._connected is True


class _StubWriter:
    def __init__(self) -> None:
        self.writes: list[str] = []

    def write(self, data: bytes) -> None:
        self.writes.append(data.decode("utf-8"))

    async def drain(self) -> None:
        return None


@pytest.mark.asyncio
async def test_send_raw_flushes_stray_output_before_writing():
    conn = MOOConnection(CFG)
    conn._connected = True
    conn._writer = _StubWriter()
    conn._lines.put_nowait("*** stray broadcast from another player ***")

    async def producer():
        await asyncio.sleep(0.02)
        await conn._lines.put("response")

    task = asyncio.create_task(producer())
    raw = await conn.send_raw(["look"], idle=0.1, max_wait=1.0)
    await task

    assert raw == "response"  # stray line was NOT included


@pytest.mark.asyncio
async def test_send_raw_writes_multiple_commands_in_order():
    conn = MOOConnection(CFG)
    conn._connected = True
    conn._writer = _StubWriter()

    raw = await conn.send_raw(
        ["south", "north", "look"], idle=0.05, max_wait=0.3, inter_command_delay=0.01
    )

    assert conn._writer.writes == ["south\r\n", "north\r\n", "look\r\n"]
    assert raw == ""


@pytest.mark.asyncio
async def test_send_raw_writes_embedded_newlines_as_separate_physical_lines():
    conn = MOOConnection(CFG)
    conn._connected = True
    conn._writer = _StubWriter()

    await conn.send_raw(["line one\nline two"], idle=0.05, max_wait=0.3)

    assert conn._writer.writes == ["line one\r\n", "line two\r\n"]


@pytest.mark.asyncio
async def test_send_raw_collects_output_via_idle_drain():
    conn = MOOConnection(CFG)
    conn._connected = True
    conn._writer = _StubWriter()

    async def producer():
        await asyncio.sleep(0.01)
        await conn._lines.put("line1")
        await asyncio.sleep(0.01)
        await conn._lines.put("line2")

    task = asyncio.create_task(producer())
    raw = await conn.send_raw(["look"], idle=0.1, max_wait=1.0)
    await task

    assert raw == "line1\nline2"
