"""eval - evaluate a raw MOO expression.

The tool normally pushes a value-returning command down to the MOO admin
port, but it also accepts raw statement code blocks and preserves them as
MOO evaluation bodies instead of wrapping them as `;return` probes.
"""

from __future__ import annotations

import re
from typing import Any

from moo_mcp.connection import MOOConnection
from moo_mcp.parser import parse_response

_CONTROL_FLOW_STARTS = (
    "if",
    "else",
    "elseif",
    "while",
    "for",
    "do",
    "fork",
    "try",
    "catch",
    "finally",
    "break",
    "continue",
)


def _looks_like_block(expression: str) -> bool:
    """Return true when the payload should be executed as raw MOO code.

    The MOO admin `;eval` protocol has two useful forms:
    - `;return <expr>` for a single value probe, and
    - `;<program>` for arbitrary statement bodies (possibly multi-line) that
      do not need a forced value wrapper.

    The legacy wrapper here previously sent every non-`return` payload through
    `;return <expr>` which is wrong for loops and multi-statement bodies. This
    heuristic keeps the old single-expression behaviour while preserving
    multi-line and control-flow bodies as executable blocks.
    """
    stripped = expression.strip()
    if not stripped:
        return False
    if "\n" in stripped or "\r" in stripped:
        return True
    if "{" in stripped or "}" in stripped:
        return True
    if ";" in stripped:
        return True

    first_word = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)", stripped)
    if first_word:
        first = first_word.group(1).lower()
        if first in _CONTROL_FLOW_STARTS:
            return True
    return False


async def eval_expression(conn: MOOConnection, expression: str) -> dict[str, Any]:
    """Evaluate a MOO expression (no leading `;` needed).

    If `expression` is a statement that doesn't return (e.g., a multi-statement
    block), the result will be the value of the last expression evaluated, per
    MOO `eval()` semantics. For a single value, prefer `return <expr>`.
    """
    stripped = expression.strip()
    if stripped.startswith(";"):
        raw = await conn.send(stripped)
    elif _looks_like_block(stripped):
        raw = await conn.send(f";{stripped}")
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
