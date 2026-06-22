"""Search verb bodies for a substring or regex pattern.

When you need to find every verb that uses a particular builtin, calls a
specific helper, references a property, etc. Reading every verb body via
list_verb is N round trips; this tool does the search server-side and returns
just the matches.
"""

from __future__ import annotations

import re
from typing import Any

from moo_mcp.connection import MOOConnection
from moo_mcp.parser import parse_response
from moo_mcp.tools._base import assert_object_ref


async def search_verbs(
    conn: MOOConnection,
    obj: str,
    pattern: str,
    *,
    include_descendants: bool = False,
    case_insensitive: bool = True,
    max_results: int = 100,
) -> dict[str, Any]:
    """Find verbs on `obj` (and optionally its descendants) whose body matches `pattern`.

    `pattern` is treated as a Python regex. Set `case_insensitive=False` for
    case-sensitive matching. Set `include_descendants=True` to also search
    every object inheriting from `obj` (uses `$object_utils:descendants`).

    Returns {matches: [{obj, verb, line, snippet}], total_searched, truncated}.
    """
    ref = assert_object_ref(obj)
    flags = re.IGNORECASE if case_insensitive else 0
    try:
        regex = re.compile(pattern, flags)
    except re.error as exc:
        return {"error": f"bad regex: {exc}", "matches": []}

    targets = await _get_targets(conn, ref, include_descendants)
    matches: list[dict[str, Any]] = []
    searched = 0
    truncated = False
    for target in targets:
        verb_list_result = await _get_verbs(conn, target)
        verb_names = verb_list_result or []
        for verb_name in verb_names:
            searched += 1
            body = await _get_verb_code(conn, target, verb_name)
            if not body:
                continue
            for line_no, line in enumerate(body, start=1):
                if regex.search(line):
                    matches.append(
                        {
                            "obj": target,
                            "verb": verb_name,
                            "line": line_no,
                            "snippet": line,
                        }
                    )
                    if len(matches) >= max_results:
                        truncated = True
                        break
            if truncated:
                break
        if truncated:
            break
    return {
        "matches": matches,
        "total_verbs_searched": searched,
        "truncated": truncated,
    }


async def _get_targets(conn: MOOConnection, root: str, include_descendants: bool) -> list[str]:
    if not include_descendants:
        return [root]
    raw = await conn.send(f";return $object_utils:descendants({root})")
    value, error = parse_response(raw)
    if error is not None or not isinstance(value, list):
        return [root]
    return [root] + [v for v in value if isinstance(v, str)]


async def _get_verbs(conn: MOOConnection, target: str) -> list[str]:
    raw = await conn.send(f";return verbs({target})")
    value, error = parse_response(raw)
    if error is not None or not isinstance(value, list):
        return []
    return [v for v in value if isinstance(v, str)]


async def _get_verb_code(conn: MOOConnection, target: str, verb: str) -> list[str]:
    verb_q = verb.replace('"', '\\"')
    raw = await conn.send(f';return verb_code({target}, "{verb_q}")')
    value, error = parse_response(raw)
    if error is not None or not isinstance(value, list):
        return []
    return [v if isinstance(v, str) else str(v) for v in value]
