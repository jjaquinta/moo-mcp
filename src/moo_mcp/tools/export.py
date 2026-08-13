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


def _parse_dump_output(raw: str) -> dict[str, Any]:
    """Parse @dump output into structured format.

    @dump output format (LambdaMOO):
    ```
    @Dump $object_name
    ;;#52.("prop_name") = value [perms info]
    ;;#52.("prop_name") = value [perms info]
    ...
    @args #52:"verb_name" dobj prep iobj
    @program #52:verb_name
    verb body line 1
    verb body line 2
    ...
    .
    @args #52:"another_verb" dobj prep iobj
    @program #52:another_verb
    verb body line 1
    ...
    .
    ```
    
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

    # Properties section: lines starting with ;;#<id>.("propname") = value
    # Example: ;;#52.("name") = "Foo" (perms: [owner] "rwc")
    prop_pattern = re.compile(r';;([#\$][\w-]+)\.\("([^"]+)"\)\s*=\s*(.+)')
    verbs_start = None

    for idx in range(start_idx, len(lines)):
        line = lines[idx]
        if line.startswith("@args") or line.startswith("@program"):
            verbs_start = idx
            break
        
        # Extract object ID from property lines if not already found
        prop_match = prop_pattern.match(line)
        if prop_match:
            if not result["object_id"]:
                result["object_id"] = prop_match.group(1)
            
            prop_name = prop_match.group(2)
            rest = prop_match.group(3)
            # Extract perms info if present: "(perms: [owner] "rwc")"
            owner = None
            perms = None
            value_str = rest
            perms_match = re.search(r"\(perms:\s*\[([^\]]+)\]\s*\"([^\"]+)\"\)", rest)
            if perms_match:
                owner = perms_match.group(1)
                perms = perms_match.group(2)
                # Remove the perms info from value_str
                value_str = rest[: perms_match.start()].strip()

            result["properties"][prop_name] = {
                "value": value_str,
                "owner": owner,
                "perms": perms,
            }
        elif line.startswith("@chmod"):
            # @chmod can appear between properties
            # Format: @chmod #57."mail_identity" c
            # This is not a property definition, skip it
            pass

    # Verbs section: starts with @args and continues until next @args or end
    if verbs_start is not None:
        current_verb = None
        verb_lines = []
        verb_dobj = None
        verb_prep = None
        verb_iobj = None
        verb_owner = None
        verb_perms = None

        for idx in range(verbs_start, len(lines)):
            line = lines[idx]

            # Parse @args line: @args #52:"verb_name" dobj prep iobj
            args_match = re.match(r"@args\s+[#\$][\w-]+:\"([^\"]+)\"\s+(.+)", line)
            if args_match:
                # Save previous verb if any
                if current_verb is not None and verb_lines:
                    # Last line should be the standalone "."
                    if verb_lines[-1].strip() == ".":
                        verb_lines = verb_lines[:-1]
                    result["verbs"].append(
                        {
                            "name": current_verb,
                            "dobj": verb_dobj,
                            "prep": verb_prep,
                            "iobj": verb_iobj,
                            "owner": verb_owner,
                            "perms": verb_perms,
                            "lines": verb_lines,
                        }
                    )
                    verb_lines = []

                current_verb = args_match.group(1)
                arg_spec = args_match.group(2).strip()
                parts = arg_spec.split()
                if len(parts) >= 3:
                    verb_dobj = parts[0]
                    verb_prep = parts[1]
                    verb_iobj = parts[2]

                # The next line should be @program with perms and owner info
                if idx + 1 < len(lines):
                    program_line = lines[idx + 1]
                    program_match = re.match(
                        r"@program\s+[#\$][\w-]+:\"[^\"]+\"\s+(?:\[([^\]]+)\]\s+)?\"([^\"]+)\"(?:\s+[^\"]*)?",
                        program_line,
                    )
                    if program_match:
                        verb_owner = program_match.group(1)
                        verb_perms = program_match.group(2)

            elif line.startswith("@program"):
                # Parse @program line for owner/perms if not already parsed from @args
                # Format varies: @program #52:"verb_name" "rwx" or similar
                program_match = re.match(
                    r'@program\s+[#\$][\w-]+:"[^"]+"\s+(?:\[([^\]]+)\]\s+)?"([^"]+)"',
                    line,
                )
                if program_match:
                    verb_owner = program_match.group(1)
                    verb_perms = program_match.group(2)
            elif current_verb is not None:
                # Accumulate verb body lines
                verb_lines.append(line)

        # Save last verb
        if current_verb is not None and verb_lines:
            # Remove trailing "."
            if verb_lines and verb_lines[-1].strip() == ".":
                verb_lines = verb_lines[:-1]
            result["verbs"].append(
                {
                    "name": current_verb,
                    "dobj": verb_dobj,
                    "prep": verb_prep,
                    "iobj": verb_iobj,
                    "owner": verb_owner,
                    "perms": verb_perms,
                    "lines": verb_lines,
                }
            )

    return result
