"""send_command - inject literal top-level game command(s), not eval.

Every other tool in this package goes through `;eval`, which is unfaithful
for reproducing real command-dispatch behavior: `caller_perms()` returns
`#-1` only when the executing verb is the first verb called in a command or
server task - a framing eval-invoked code can never produce - and
`dobjstr`/`prepstr`/`iobjstr` (populated by real command parsing) are
similarly unavailable from eval. This tool writes literal command text to
the connection instead and captures whatever comes back before a reasonable
quiet point, via MOOConnection.send_raw's idle-timeout framing.

Output is deliberately unstructured beyond splitting into lines - no guessed
dobj/iobj echo parsing, no success/failure inference. Real command output
has no fixed grammar, so pretending otherwise would reintroduce exactly the
eval-shaped assumptions this tool exists to avoid.
"""

from __future__ import annotations

from typing import Any

from moo_mcp.connection import MOOConnection


async def send_command(
    conn: MOOConnection,
    commands: list[str],
    *,
    idle: float = 1.0,
    max_wait: float = 8.0,
) -> dict[str, Any]:
    raw = await conn.send_raw(commands, idle=idle, max_wait=max_wait)
    return {"raw": raw, "lines": raw.splitlines() if raw else []}
