"""Shared helpers for tool modules."""

from __future__ import annotations

from typing import Any

from moo_mcp.connection import MOOConnection
from moo_mcp.parser import parse_response


async def eval_value(conn: MOOConnection, expression: str) -> dict[str, Any]:
    """Run `;return <expression>` and return {value, error?}.

    Returns a dict suitable for direct JSON serialization back to the MCP
    client. On MOO traceback, error is populated and value is None.
    """
    raw = await conn.send(f";return {expression}")
    value, error = parse_response(raw)
    out: dict[str, Any] = {"value": value}
    if error is not None:
        out["error"] = dict(error)
        out["value"] = None
    return out


def assert_object_ref(obj: str) -> str:
    """Light validation: object refs must start with # or $ (corified)."""
    s = obj.strip()
    if not s.startswith(("#", "$")):
        raise ValueError(f"object must be a MOO ref like #123 or $name, got: {obj!r}")
    return s


def assert_identifier(name: str, *, what: str) -> str:
    """Verb/property names must be plain identifiers (no spaces, no semicolons)."""
    s = name.strip()
    if not s or any(c in s for c in " \t\r\n;\"'"):
        raise ValueError(f"{what} must be a bare identifier, got: {name!r}")
    return s
