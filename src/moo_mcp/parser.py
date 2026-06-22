"""Parse MOO eval output into Python values + structured errors.

The output of a single eval is one or more lines. The "result" line starts
with `=> ` followed by a MOO literal. Errors produce a multi-line traceback:

    eval input(3): Property not found
    Via BF eval()
    Via $prog:eval_cmd_string(19) [T=#2]
    Via $prog:eval(13) [T=#2]
    (EOT)

We classify by looking for `Via ` lines + `(EOT)`. Otherwise we extract the
`=> X` line and parse X into a Python value.
"""

from __future__ import annotations

import re
from typing import Any

_OBJ_RE = re.compile(r"#-?\d+")
_CORIFIED_RE = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*")
_ERR_CONST_RE = re.compile(r"^E_[A-Z]+$")


class ParsedError(dict):
    """A MOO traceback parsed into {message, traceback: [str]}."""


def parse_response(raw: str) -> tuple[Any, ParsedError | None]:
    """Parse a captured MOO response.

    Returns (value, error). If the response was a traceback, value is None
    and error is a ParsedError. If it was a normal result, error is None and
    value is the parsed literal. If no `=>` line is found, returns the raw
    string as the value.
    """
    if not raw or not raw.strip():
        return None, None
    lines = [line.rstrip() for line in raw.splitlines() if line.strip()]
    if _looks_like_traceback(lines):
        return None, _parse_traceback(lines)
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("=>"):
            payload = stripped[2:].strip()
            return _parse_value(payload), None
    return raw, None


def _looks_like_traceback(lines: list[str]) -> bool:
    if not lines:
        return False
    has_via = any(line.lstrip().startswith("Via ") for line in lines)
    has_eot = any(line.strip() == "(EOT)" for line in lines)
    return has_via and has_eot


def _parse_traceback(lines: list[str]) -> ParsedError:
    message_idx = 0
    callstack: list[str] = []
    for idx, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("Via "):
            message_idx = idx
            break
    message = " ".join(lines[:message_idx]).strip() or lines[0].strip()
    message = re.sub(r"^eval input\(\d+\):\s*", "", message)
    for line in lines[message_idx:]:
        stripped = line.strip()
        if stripped == "(EOT)" or not stripped:
            continue
        callstack.append(stripped)
    return ParsedError(message=message, traceback=callstack)


def _parse_value(s: str) -> Any:
    s = s.strip()
    if not s:
        return None
    if s.startswith('"'):
        return _parse_string(s)
    if s.startswith("{"):
        return _parse_list(s)
    if s.startswith("["):
        return _parse_map(s)
    if s.startswith("#"):
        return _parse_objref(s)
    if s.startswith("$"):
        return _parse_corified(s)
    if _ERR_CONST_RE.match(s):
        return s
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def _parse_string(s: str) -> str:
    if not s.endswith('"'):
        return s
    body = s[1:-1]
    out: list[str] = []
    i = 0
    while i < len(body):
        c = body[i]
        if c == "\\" and i + 1 < len(body):
            nxt = body[i + 1]
            if nxt in ('"', "\\"):
                out.append(nxt)
                i += 2
                continue
        out.append(c)
        i += 1
    return "".join(out)


def _parse_list(s: str) -> list[Any]:
    if not s.endswith("}"):
        return [s]
    return _split_top_level(s[1:-1], parse=_parse_value)


def _parse_map(s: str) -> dict[Any, Any]:
    if not s.endswith("]"):
        return {"_raw": s}
    pairs = _split_top_level(s[1:-1], parse=lambda x: x)
    out: dict[Any, Any] = {}
    for pair in pairs:
        if "->" in pair:
            k, v = pair.split("->", 1)
            out[_parse_value(k.strip())] = _parse_value(v.strip())
    return out


def _parse_objref(s: str) -> str:
    m = _OBJ_RE.match(s)
    return m.group(0) if m else s


def _parse_corified(s: str) -> str:
    m = _CORIFIED_RE.match(s)
    return m.group(0) if m else s


def _split_top_level(body: str, *, parse) -> list[Any]:
    items: list[Any] = []
    depth_curly = 0
    depth_square = 0
    in_string = False
    current: list[str] = []
    i = 0
    while i < len(body):
        c = body[i]
        if in_string:
            current.append(c)
            if c == "\\" and i + 1 < len(body):
                current.append(body[i + 1])
                i += 2
                continue
            if c == '"':
                in_string = False
            i += 1
            continue
        if c == '"':
            in_string = True
            current.append(c)
        elif c == "{":
            depth_curly += 1
            current.append(c)
        elif c == "}":
            depth_curly -= 1
            current.append(c)
        elif c == "[":
            depth_square += 1
            current.append(c)
        elif c == "]":
            depth_square -= 1
            current.append(c)
        elif c == "," and depth_curly == 0 and depth_square == 0:
            piece = "".join(current).strip()
            if piece:
                items.append(parse(piece))
            current = []
        else:
            current.append(c)
        i += 1
    piece = "".join(current).strip()
    if piece:
        items.append(parse(piece))
    return items
