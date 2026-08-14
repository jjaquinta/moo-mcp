"""eval - evaluate a raw MOO expression.

The tool normally pushes a value-returning command down to the MOO admin
port, but it also accepts raw statement code blocks. Block-shaped payloads
are run through the `eval()` builtin (`;return eval("...")`) rather than
sent as a raw `;<code>` line: this server's interactive `;` command silently
truncates at the first statement of multi-statement input (only the first
statement runs, no error) - `eval()` compiles and runs the whole string as
a real program instead. See Issue #6.
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


def _moo_string_literal(code: str) -> str:
    """Escape `code` as a MOO double-quoted string literal for eval().

    This server's string-literal parser only recognizes `\\\\` and `\\"` as
    escapes - a literal `\\n` is NOT decoded back into a newline (it comes
    through as a stray `n` with the backslash swallowed). MOO's grammar
    doesn't need real newlines between statements/keywords, though (`;` and
    keywords already delimit them), so embedded newlines are collapsed to
    spaces instead of escaped - confirmed against a live server that this
    still compiles multi-statement/loop/try-except code correctly.
    """
    escaped = code.replace("\\", "\\\\").replace('"', '\\"')
    escaped = re.sub(r"[\r\n]+", " ", escaped)
    return f'"{escaped}"'


def _unpack_eval_builtin_response(raw: str) -> dict[str, Any]:
    """Unpack the response to a `;return eval("...")` call.

    On success, eval() returns `{1, result}` (result is 0 if the code never
    hit a `return`). On a compile error, it returns `{0, {message lines}}`.
    A runtime error that escapes uncaught doesn't produce either shape - it
    comes back as a raw traceback that parse_response doesn't currently
    classify (see Issue #6 investigation notes); that's treated as an error
    too rather than risking a traceback being reported back as a value.
    """
    value, error = parse_response(raw)
    if error is not None:
        return {"value": None, "raw": raw, "error": dict(error)}
    if isinstance(value, list) and len(value) == 2 and value[0] in (0, 1):
        success, payload = value
        if success == 1:
            return {"value": payload, "raw": raw}
        messages = payload if isinstance(payload, list) else [str(payload)]
        message = str(messages[0]) if messages else "eval() compile error"
        return {
            "value": None,
            "raw": raw,
            "error": {"message": message, "traceback": [str(m) for m in messages]},
        }
    return {
        "value": None,
        "raw": raw,
        "error": {
            "message": "eval() did not return the expected {success, result} shape",
            "traceback": [raw],
        },
    }


async def eval_expression(conn: MOOConnection, expression: str) -> dict[str, Any]:
    """Evaluate a MOO expression (no leading `;` needed).

    Multi-statement/block payloads run through the eval() builtin so every
    statement actually executes (see module docstring) - the result is
    whatever the code `return`s, or 0 if it never does. For a single value,
    prefer `return <expr>`.
    """
    stripped = expression.strip()
    code = stripped[1:].strip() if stripped.startswith(";") else stripped

    if _looks_like_block(code):
        raw = await conn.send(f";return eval({_moo_string_literal(code)})")
        return _unpack_eval_builtin_response(raw)

    if code.startswith("return "):
        raw = await conn.send(f";{code}")
    else:
        raw = await conn.send(f";return {code}")
    value, error = parse_response(raw)
    out: dict[str, Any] = {"value": value, "raw": raw}
    if error is not None:
        out["error"] = dict(error)
        out["value"] = None
    return out
