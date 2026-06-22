"""Verb lifecycle: list, program (with auto-verify), add, remove, chmod.

`program_verb` does the heavy lifting: sends `@program`, captures the response,
then immediately `@list`s the verb and diffs the stored body against the
requested body. Returns a structured result that makes "0 errors but verb
wasn't actually stored" impossible to miss.
"""

from __future__ import annotations

import re
from typing import Any

from moo_mcp.connection import MOOConnection
from moo_mcp.parser import parse_response
from moo_mcp.tools._base import assert_identifier, assert_object_ref

_LIST_HEADER_RE = re.compile(r"^(#-?\d+|\$\w+):(\S+)\s+(.+)$")
_LIST_LINE_RE = re.compile(r"^\s*(\d+):\s?(.*)$")

# Allowed MOO verb arg-spec values. dobj and iobj have a small fixed set;
# prep accepts a wider set including multi-word forms like "in front of".
# We validate each input against safe characters to prevent breaking out of
# the MOO double-quoted literal we splice them into.
_DOBJ_IOBJ_VALUES = frozenset({"this", "any", "none"})
_PREP_SAFE_CHARS_RE = re.compile(r"^[a-z0-9 /-]+$")


async def list_verb(conn: MOOConnection, obj: str, verb: str) -> dict[str, Any]:
    """Read a verb body. Returns {header, args, lines: [str], raw}.

    `args` is a parsed `{dobj, prep, iobj}` triple from the header line.
    """
    ref = assert_object_ref(obj)
    name = assert_identifier(verb, what="verb")
    raw = await conn.send(f"@list {ref}:{name}")
    return _parse_list_output(raw)


def _parse_list_output(raw: str) -> dict[str, Any]:
    """Parse `@list` output into structured form."""
    if not raw or not raw.strip():
        return {"header": None, "args": None, "lines": [], "raw": raw, "error": "empty response"}
    lines = raw.splitlines()
    header: str | None = None
    args: list[str] | None = None
    body: list[str] = []
    for line in lines:
        m = _LIST_HEADER_RE.match(line)
        if m and header is None:
            header = line
            tail = m.group(3).strip()
            args = tail.split()
            continue
        bm = _LIST_LINE_RE.match(line)
        if bm:
            body.append(bm.group(2))
    if header is None:
        return {"header": None, "args": None, "lines": [], "raw": raw, "error": "no header found"}
    return {"header": header, "args": args, "lines": body, "raw": raw}


async def program_verb(
    conn: MOOConnection,
    obj: str,
    verb: str,
    body: str,
    *,
    verify: bool = True,
) -> dict[str, Any]:
    """Replace a verb body. Returns {success, errors, stored_body, mismatches}.

    By default (verify=True) the stored body is re-read after the @program and
    diffed against the supplied body. Whitespace-only differences (trailing
    spaces, blank-line normalization) are tolerated. Any other difference
    flips success to False.
    """
    ref = assert_object_ref(obj)
    name = assert_identifier(verb, what="verb")
    body_stripped = body.rstrip()
    if body_stripped.endswith("."):
        raise ValueError("body must not end with a trailing '.' on its own line - the tool adds it")
    # SECURITY: reject any body line that is exactly "." - that's the MOO @program
    # terminator. A malicious or careless body with a bare-dot line would end
    # the @program block early and the rest would land in the command parser.
    for idx, line in enumerate(body_stripped.splitlines(), start=1):
        if line.strip() == ".":
            raise ValueError(
                f"body line {idx} is a bare '.' - that's the @program terminator and "
                "cannot appear inside a verb body. Indent or escape the line."
            )
    program_block = f"@program {ref}:{name}\r\n{body_stripped}\r\n."
    raw = await conn.send(program_block, timeout=30.0)
    errors: list[str] = []
    success = True
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.lower().startswith("0 errors"):
            continue
        if stripped.lower() == "verb programmed.":
            continue
        if "error" in stripped.lower() or "fail" in stripped.lower():
            errors.append(stripped)
            success = False
    out: dict[str, Any] = {"success": success, "errors": errors, "raw_program_output": raw}
    if not verify:
        return out
    listed = await list_verb(conn, ref, name)
    stored_lines = listed.get("lines") or []
    # MOO auto-appends a "Last modified ... by X" comment line on store; drop it before diffing.
    if stored_lines and re.match(r'^\s*"Last modified .*\.";?\s*$', stored_lines[-1]):
        stored_lines = stored_lines[:-1]
    requested_lines = body_stripped.splitlines()
    mismatches: list[dict[str, Any]] = []
    if len(stored_lines) != len(requested_lines):
        mismatches.append({"kind": "length", "stored": len(stored_lines), "requested": len(requested_lines)})
    # MOO normalizes leading whitespace when storing; compare with both sides stripped.
    for idx, (req, got) in enumerate(zip(requested_lines, stored_lines, strict=False), start=1):
        if req.strip() != got.strip():
            mismatches.append({"line": idx, "requested": req, "stored": got})
    out["stored_body"] = stored_lines
    out["mismatches"] = mismatches
    if mismatches:
        out["success"] = False
    return out


async def add_verb(
    conn: MOOConnection,
    obj: str,
    verb: str,
    dobj: str = "this",
    prep: str = "none",
    iobj: str = "this",
    owner: str | None = None,
    perms: str = "rxd",
) -> dict[str, Any]:
    """Create a new (empty) verb on obj.

    args: dobj/prep/iobj are the MOO verb-arg spec (e.g., "this none this").
    perms: verb perm string subset of "rxd" (read/execute/debug).
    """
    ref = assert_object_ref(obj)
    name = assert_identifier(verb, what="verb")
    owner_ref = assert_object_ref(owner) if owner else f"{ref}.owner"
    perms_clean = "".join(c for c in perms if c in "rxd")
    if not perms_clean:
        raise ValueError(f"verb perms must be subset of 'rxd', got: {perms!r}")
    # SECURITY: validate the arg-spec values before splicing them into a MOO
    # double-quoted literal. Without this, a value with an embedded `"` could
    # break out and append arbitrary wizard-eval.
    dobj_l = dobj.strip().lower()
    iobj_l = iobj.strip().lower()
    prep_l = prep.strip().lower()
    if dobj_l not in _DOBJ_IOBJ_VALUES:
        raise ValueError(f"dobj must be one of {sorted(_DOBJ_IOBJ_VALUES)}, got: {dobj!r}")
    if iobj_l not in _DOBJ_IOBJ_VALUES:
        raise ValueError(f"iobj must be one of {sorted(_DOBJ_IOBJ_VALUES)}, got: {iobj!r}")
    if not _PREP_SAFE_CHARS_RE.fullmatch(prep_l):
        raise ValueError(
            f"prep must contain only lowercase letters, digits, spaces, slashes, hyphens; got: {prep!r}"
        )
    expr = (
        f'add_verb({ref}, {{{owner_ref}, "{perms_clean}", "{name}"}}, '
        f'{{"{dobj_l}", "{prep_l}", "{iobj_l}"}})'
    )
    raw = await conn.send(f";{expr}; return 1")
    _, error = parse_response(raw)
    if error is not None:
        return {"success": False, "error": dict(error)}
    return {"success": True}


async def remove_verb(conn: MOOConnection, obj: str, verb: str) -> dict[str, Any]:
    """Delete a verb from obj."""
    ref = assert_object_ref(obj)
    name = assert_identifier(verb, what="verb")
    raw = await conn.send(f';delete_verb({ref}, "{name}"); return 1')
    _, error = parse_response(raw)
    if error is not None:
        return {"success": False, "error": dict(error)}
    return {"success": True}


async def chmod_verb(conn: MOOConnection, obj: str, verb: str, perms: str) -> dict[str, Any]:
    """Set verb permission flags (e.g., "rxd")."""
    ref = assert_object_ref(obj)
    name = assert_identifier(verb, what="verb")
    perms_clean = "".join(c for c in perms if c in "rxd")
    if not perms_clean:
        raise ValueError(f"verb perms must be subset of 'rxd', got: {perms!r}")
    expr = (
        f'info = verb_info({ref}, "{name}"); '
        f'set_verb_info({ref}, "{name}", {{info[1], "{perms_clean}", info[3]}})'
    )
    raw = await conn.send(f";{expr}; return 1")
    _, error = parse_response(raw)
    if error is not None:
        return {"success": False, "error": dict(error)}
    return {"success": True}
