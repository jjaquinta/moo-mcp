"""Resolve an optional `as_player` argument to a live MOOConnection.

The primary connection (MOO_USER/MOO_PASS) is always available. A tool call
may additionally target a pre-configured secondary player identity by name
(see MOOConfig.for_identity) - this module owns finding or opening that
connection and caching it for reuse across calls, without depending on
FastMCP's Context so it's unit-testable on its own.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from moo_mcp.connection import MOOConfig, MOOConnection


async def resolve_identity_conn(
    *,
    identities: dict[str, MOOConnection],
    lock: asyncio.Lock,
    primary_config: MOOConfig,
    primary_conn: MOOConnection,
    as_player: str | None,
    connection_factory: Callable[[MOOConfig], MOOConnection] = MOOConnection,
) -> MOOConnection:
    """Return the connection to use for `as_player` (or the primary if None).

    `identities` is keyed by resolved *username* (lowercased), not the raw
    `as_player` label, and the caller is expected to pre-seed it with the
    primary connection under its own username. That means an `as_player`
    that happens to resolve to the already-connected primary user reuses the
    primary connection automatically, instead of opening a second concurrent
    login as the same player (which many MOO cores boot or warn on).

    `lock` must be held across the whole check-then-create sequence so two
    concurrent first-time requests for the same new identity can't both miss
    the cache and open duplicate logins.
    """
    if as_player is None:
        return primary_conn
    cfg = MOOConfig.for_identity(primary_config, as_player)
    key = cfg.user.lower()
    async with lock:
        if key in identities:
            return identities[key]
        conn = connection_factory(cfg)
        await conn.connect()
        identities[key] = conn
        return conn
