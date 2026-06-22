"""eval - evaluate a raw MOO expression.

The expression is appended to `;return ` and sent. The result is parsed.
Use this for ad-hoc probes when no higher-level tool fits.
"""

from __future__ import annotations

from typing import Any

from moo_mcp.connection import MOOConnection
from moo_mcp.parser import parse_response


async def eval_expression(conn: MOOConnection, expression: str) -> dict[str, Any]:
    """Evaluate a MOO expression (no leading `;` needed).

    If `expression` is a statement that doesn't return (e.g., a multi-statement
    block), the result will be the value of the last expression evaluated, per
    MOO `eval()` semantics. For a single value, prefer `return <expr>`.
    """
    stripped = expression.strip()
    if stripped.startswith(";"):
        raw = await conn.send(stripped)
    elif stripped.startswith("return "):
        raw = await conn.send(f";{stripped}")
    else:
        raw = await conn.send(f";return {stripped}")
    value, error = parse_response(raw)
    out: dict[str, Any] = {"value": value, "raw": raw}
    if error is not None:
        out["error"] = dict(error)
        out["value"] = None
    return out
