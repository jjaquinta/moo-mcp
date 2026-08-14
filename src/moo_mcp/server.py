"""MCP server entry point.

Exposes the moo_mcp.tools.* implementations as MCP tools over stdio.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from moo_mcp.connection import MOOConfig, MOOConnection
from moo_mcp.tools import eval as t_eval
from moo_mcp.tools import inspect as t_inspect
from moo_mcp.tools import props as t_props
from moo_mcp.tools import search as t_search
from moo_mcp.tools import topology as t_topo
from moo_mcp.tools import verbs as t_verbs

logger = logging.getLogger("moo_mcp")


@asynccontextmanager
async def _lifespan(_app):
    cfg = MOOConfig.from_env()
    conn = MOOConnection(cfg)
    await conn.connect()
    logger.info("moo-mcp ready (connected to %s:%s as %s)", cfg.host, cfg.port, cfg.user)
    try:
        yield {"conn": conn}
    finally:
        await conn.close()


app = FastMCP("moo-mcp", lifespan=_lifespan)


def _conn(ctx: Context) -> MOOConnection:
    return ctx.request_context.lifespan_context["conn"]


@app.tool()
async def eval_moo(expression: str, ctx: Context) -> dict[str, Any]:
    """Evaluate a MOO expression (without leading `;`).

    Multi-statement/block input (e.g. `a = 5; return a * 2;`) runs correctly
    as a whole program - it's routed through the eval() builtin rather than
    sent as a single raw admin-port line, since the raw line form silently
    truncates at the first statement on this server. Value is 0 if the code
    never hits a `return`.

    Returns {value, raw, error?}. On a MOO traceback, error is populated and
    value is null. Use for ad-hoc probes when no higher-level tool fits.
    """
    return await t_eval.eval_expression(_conn(ctx), expression)


@app.tool()
async def list_verb(object: str, verb: str, ctx: Context) -> dict[str, Any]:
    """Read a verb body. Returns {header, args, lines, raw}."""
    return await t_verbs.list_verb(_conn(ctx), object, verb)


@app.tool()
async def program_verb(
    object: str,
    verb: str,
    body: str,
    ctx: Context,
    verify: bool = True,
) -> dict[str, Any]:
    """Replace a verb body. Automatically reads back the stored body and
    diffs it against `body` - any mismatch flips success to false.

    `body` must NOT include a trailing `.` line (the tool adds the terminator).
    """
    return await t_verbs.program_verb(_conn(ctx), object, verb, body, verify=verify)


@app.tool()
async def add_verb(
    object: str,
    verb: str,
    ctx: Context,
    dobj: str = "this",
    prep: str = "none",
    iobj: str = "this",
    perms: str = "rxd",
    owner: str | None = None,
) -> dict[str, Any]:
    """Create a new (empty) verb on `object` with the given arg spec + perms."""
    return await t_verbs.add_verb(
        _conn(ctx), object, verb, dobj=dobj, prep=prep, iobj=iobj, perms=perms, owner=owner
    )


@app.tool()
async def remove_verb(object: str, verb: str, ctx: Context) -> dict[str, Any]:
    """Delete a verb from `object` (inherited copies elsewhere are untouched)."""
    return await t_verbs.remove_verb(_conn(ctx), object, verb)


@app.tool()
async def chmod_verb(object: str, verb: str, perms: str, ctx: Context) -> dict[str, Any]:
    """Set verb permission flags. Perms must be a subset of "rxd"."""
    return await t_verbs.chmod_verb(_conn(ctx), object, verb, perms)


@app.tool()
async def get_property(object: str, property: str, ctx: Context) -> dict[str, Any]:
    """Read a property's current value."""
    return await t_props.get_property(_conn(ctx), object, property)


@app.tool()
async def set_property(object: str, property: str, value: Any, ctx: Context) -> dict[str, Any]:
    """Assign a value to an existing property. Supports int/float/str/bool/list/None."""
    return await t_props.set_property(_conn(ctx), object, property, value)


@app.tool()
async def add_property(
    object: str,
    property: str,
    ctx: Context,
    value: Any = None,
    perms: str = "rwc",
    owner: str | None = None,
) -> dict[str, Any]:
    """Add a new property. Perms must be a subset of "rwc" (NOT "rxd" - that's verbs)."""
    return await t_props.add_property(
        _conn(ctx), object, property, value=value, perms=perms, owner=owner
    )


@app.tool()
async def remove_property(object: str, property: str, ctx: Context) -> dict[str, Any]:
    """Delete a property from `object`."""
    return await t_props.remove_property(_conn(ctx), object, property)


@app.tool()
async def verbs(object: str, ctx: Context) -> dict[str, Any]:
    """List verbs defined LOCALLY on `object` (not inherited)."""
    return await t_topo.list_local_verbs(_conn(ctx), object)


@app.tool()
async def properties(object: str, ctx: Context) -> dict[str, Any]:
    """List properties defined LOCALLY on `object` (not inherited)."""
    return await t_topo.list_local_properties(_conn(ctx), object)


@app.tool()
async def parent(object: str, ctx: Context) -> dict[str, Any]:
    """Return the immediate parent of `object` (`#-1` if none)."""
    return await t_topo.parent_of(_conn(ctx), object)


@app.tool()
async def chparent(object: str, new_parent: str, ctx: Context) -> dict[str, Any]:
    """Change `object`'s parent. Destructive - verify you've migrated needed verbs/props first."""
    return await t_topo.chparent(_conn(ctx), object, new_parent)


@app.tool()
async def has_verb(object: str, verb: str, ctx: Context) -> dict[str, Any]:
    """Is `verb` defined anywhere in `object`'s inheritance chain?

    Returns {defined_on: [objs]} - empty list means not defined anywhere.
    """
    return await t_topo.has_verb(_conn(ctx), object, verb)


@app.tool()
async def has_property(object: str, property: str, ctx: Context) -> dict[str, Any]:
    """Is `property` defined anywhere in `object`'s inheritance chain? Returns {exists: bool}."""
    return await t_topo.has_property(_conn(ctx), object, property)


@app.tool()
async def inspect_property(
    object: str, property: str, ctx: Context, max_depth: int = 20
) -> dict[str, Any]:
    """Walk the inheritance chain of `object` reporting where `property` lives at each level.

    Returns {chain, defined_on, effective_value}. Each chain entry has:
    locally_defined (true where add_property was called), has_value, value,
    and equals_parent (false on a non-locally-defined level means there is a
    VALUE OVERRIDE on that ancestor - the silent gotcha to look for when a
    descendant disagrees with the root default).
    """
    return await t_inspect.inspect_property(_conn(ctx), object, property, max_depth=max_depth)


@app.tool()
async def search_verbs(
    object: str,
    pattern: str,
    ctx: Context,
    include_descendants: bool = False,
    case_insensitive: bool = True,
    max_results: int = 100,
) -> dict[str, Any]:
    """Find verbs on `object` (optionally descendants too) whose body matches `pattern` (regex).

    Returns {matches: [{obj, verb, line, snippet}], total_verbs_searched, truncated}.
    """
    return await t_search.search_verbs(
        _conn(ctx),
        object,
        pattern,
        include_descendants=include_descendants,
        case_insensitive=case_insensitive,
        max_results=max_results,
    )


def main() -> None:
    log_level = os.environ.get("MOO_MCP_LOG", "WARNING").upper()
    logging.basicConfig(level=log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    app.run()


if __name__ == "__main__":
    main()
