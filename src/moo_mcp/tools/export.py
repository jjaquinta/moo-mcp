"""Full-fidelity object export: properties, verbs, perms in one call.

Addresses the gap where @dump gives complete object state but via raw text,
while individual tools (properties, verbs, list_verb, etc.) require ~19 calls
for an object with 10 verbs and 8 properties.

The tool is named `export_object` and returns the @dump output directly,
plus parsed structured components (properties, verbs) so callers don't have
to parse MOO output.
"""

from __future__ import annotations

import re
from typing import Any

from moo_mcp.connection import MOOConnection
from moo_mcp.tools._base import assert_object_ref


async def export_object(conn: MOOConnection, obj: str) -> dict[str, Any]:
    """Export full-fidelity object state: properties + verb code + perms in one call.

    This is the equivalent of `@dump #<id>` over the admin port, returned as:
    - `raw`: the literal @dump output as a string (for archival, diffs)
    - `object_id`: parsed object reference (#123 or $name)
    - `properties`: structured dict of {name: {value, owner, perms}} for locally-defined props
    - `verbs`: structured list of [{name, dobj, prep, iobj, owner, perms, lines}] for locally-defined verbs

    `owner`/`perms` are populated from @chmod/@chown lines in the dump, which
    only appear when a property/verb's owner or perms differ from its
    creation-time default - they're null (not wrong, just unknown) for
    anything still at the default, since @dump doesn't encode that. `lines`
    is always the real verb body regardless (@chmod/@chown lines between
    @args and @program are recognized as directives, not body content).

    Returns early with {error} if the object doesn't exist or isn't accessible.
    """
    ref = assert_object_ref(obj)
    raw = await conn.send(f"@dump {ref}")

    # Early exit on error messages (but allow responses that might contain "is not an object"
    # as part of normal output - we check this after parsing)
    if raw.startswith("E_"):
        return {"error": f"MOO error response: {raw}", "raw": raw}

    # Parse the dump output
    parsed = _parse_dump_output(raw)
    
    # If we got no object_id and no properties/verbs, it's likely an error
    if not parsed.get("object_id") and not parsed.get("properties") and not parsed.get("verbs"):
        return {"error": f"Object {ref} not found or not accessible", "raw": raw}
    
    parsed["raw"] = raw
    return parsed


_PROP_LINE_RE = re.compile(r';;([#\$][\w-]+)\.\("([^"]+)"\)\s*=\s*(.+)')
_PROP_CHMOD_RE = re.compile(r'@chmod\s+[#\$][\w-]+\."([^"]+)"\s+(\S+)')
_PROP_CHOWN_RE = re.compile(r'@chown\s+[#\$][\w-]+\."([^"]+)"\s+([#\$][\w-]+)')
_VERB_ARGS_RE = re.compile(r'@args\s+[#\$][\w-]+:"([^"]+)"\s+(.+)')
_VERB_CHMOD_RE = re.compile(r"@chmod\s+[#\$][\w-]+:\S+\s+(\S+)")
_VERB_CHOWN_RE = re.compile(r"@chown\s+[#\$][\w-]+:\S+\s+([#\$][\w-]+)")


def _parse_dump_output(raw: str) -> dict[str, Any]:
    """Parse @dump output into structured format.

    Confirmed live-server @dump format (this core never inlines perms/owner
    the way earlier code assumed - see Issue #7):
    ```
    @Dump $object_name
    @chmod #52."prop_name" r          <- optional, only when perms/owner
    @chown #52."prop_name" #36        <- differ from creation defaults
    ;;#52.("prop_name") = value
    ;;#52.("prop_name") = value       <- no override lines = no perm info
    ...
    @args #52:"verb_name" dobj prep iobj
    @chown #52:verb_name #36          <- optional, same deal, sits BETWEEN
    @chmod #52:verb_name rxd          <- @args and @program (not appended
    @program #52:verb_name               to the verb body - see Issue #7)
    verb body line 1
    ...
    .
    ```
    Since @chmod/@chown are override-only, `owner`/`perms` stay null for any
    property/verb that's still at its creation-time default - that's not
    recoverable from @dump text at all, only from a live verb_info()/
    property_info() call per entry, which would reintroduce the N-round-trip
    problem this tool exists to avoid. `raw` always has the full text either way.

    Note: The @Dump header line may not be present in all MOO implementations.
    Object ID can be extracted from property lines (;;#52.(...)).
    """
    if not raw or not raw.strip():
        return {"error": "Empty dump output", "object_id": None, "properties": {}, "verbs": []}

    lines = raw.splitlines()
    result: dict[str, Any] = {"object_id": None, "properties": {}, "verbs": []}

    # Parse header line if present: @Dump $name or @Dump #123
    if lines and lines[0].startswith("@Dump"):
        header_match = re.match(r"@Dump\s+([#$][\w-]+)", lines[0])
        if header_match:
            result["object_id"] = header_match.group(1)
        start_idx = 1
    else:
        start_idx = 0

    # Properties section: lines starting with ;;#<id>.("propname") = value,
    # optionally preceded by @chmod/@chown override lines for that property.
    verbs_start = None
    pending_owner: str | None = None
    pending_perms: str | None = None

    for idx in range(start_idx, len(lines)):
        line = lines[idx]
        if line.startswith("@args") or line.startswith("@program"):
            verbs_start = idx
            break

        chmod_match = _PROP_CHMOD_RE.match(line)
        if chmod_match:
            pending_perms = chmod_match.group(2)
            continue
        chown_match = _PROP_CHOWN_RE.match(line)
        if chown_match:
            pending_owner = chown_match.group(2)
            continue

        prop_match = _PROP_LINE_RE.match(line)
        if prop_match:
            if not result["object_id"]:
                result["object_id"] = prop_match.group(1)
            prop_name = prop_match.group(2)
            value_str = prop_match.group(3)
            result["properties"][prop_name] = {
                "value": value_str,
                "owner": pending_owner,
                "perms": pending_perms,
            }
            pending_owner = None
            pending_perms = None

    # Verbs section: @args, optional @chown/@chmod, @program, body, "."
    if verbs_start is not None:
        current_verb = None
        verb_lines: list[str] = []
        verb_dobj = verb_prep = verb_iobj = None
        verb_owner: str | None = None
        verb_perms: str | None = None
        in_body = False

        def _flush_current_verb() -> None:
            if current_verb is None:
                return
            body = verb_lines[:-1] if verb_lines and verb_lines[-1].strip() == "." else verb_lines
            result["verbs"].append(
                {
                    "name": current_verb,
                    "dobj": verb_dobj,
                    "prep": verb_prep,
                    "iobj": verb_iobj,
                    "owner": verb_owner,
                    "perms": verb_perms,
                    "lines": body,
                }
            )

        for idx in range(verbs_start, len(lines)):
            line = lines[idx]

            args_match = _VERB_ARGS_RE.match(line)
            if args_match:
                _flush_current_verb()
                current_verb = args_match.group(1)
                parts = args_match.group(2).strip().split()
                verb_dobj, verb_prep, verb_iobj = (parts + [None, None, None])[:3]
                verb_owner = None
                verb_perms = None
                verb_lines = []
                in_body = False
                continue

            if not in_body:
                chown_match = _VERB_CHOWN_RE.match(line)
                if chown_match:
                    verb_owner = chown_match.group(1)
                    continue
                chmod_match = _VERB_CHMOD_RE.match(line)
                if chmod_match:
                    verb_perms = chmod_match.group(1)
                    continue
                if line.startswith("@program"):
                    in_body = True
                    continue
                # Unexpected line between @args and @program - ignore rather
                # than risk contaminating the next verb's body.
                continue

            verb_lines.append(line)

        _flush_current_verb()

    return result
