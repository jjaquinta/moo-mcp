"""One-shot smoke test against a live MOO.

Reads creds from MOO_HOST/MOO_PORT/MOO_USER/MOO_PASS env vars.
Optionally TEST_OBJECT (default $wiz) for the introspection probes - pick
something on your MOO with a few verbs and properties for the most signal.

Exercises every tool module against safe read-only probes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys

from moo_mcp.connection import MOOConfig, MOOConnection
from moo_mcp.tools import eval as t_eval
from moo_mcp.tools import inspect as t_inspect
from moo_mcp.tools import props as t_props
from moo_mcp.tools import topology as t_topo
from moo_mcp.tools import verbs as t_verbs

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")


def show(label: str, payload) -> None:
    s = json.dumps(payload, indent=2, default=str)
    if len(s) > 600:
        s = s[:600] + "\n  ..."
    print(f"\n=== {label} ===\n{s}")


async def main() -> int:
    cfg = MOOConfig.from_env()
    conn = MOOConnection(cfg)
    await conn.connect()
    target = os.environ.get("TEST_OBJECT", "$wiz")
    try:
        show("eval 1+1", await t_eval.eval_expression(conn, "1+1"))
        show("eval string_utils:title_case", await t_eval.eval_expression(conn, '$string_utils:title_case("hello world")'))

        show(f"parent({target})", await t_topo.parent_of(conn, target))
        show(f"verbs({target})", await t_topo.list_local_verbs(conn, target))
        show(f"properties({target})", await t_topo.list_local_properties(conn, target))

        # Pick a known-good builtin verb that any wizard-class object should have.
        show(f"has_verb {target}:tell", await t_topo.has_verb(conn, target, "tell"))
        show(f"has_property {target}.name", await t_topo.has_property(conn, target, "name"))
        show(f"has_verb {target}:bogus", await t_topo.has_verb(conn, target, "bogus_verb_xyz"))

        show(f"get_property {target}.name", await t_props.get_property(conn, target, "name"))

        # Inspect the .name property's inheritance chain to exercise the new tool.
        show(f"inspect_property {target}.name", await t_inspect.inspect_property(conn, target, "name", max_depth=10))

        show("eval missing prop (error)", await t_eval.eval_expression(conn, f"{target}.nonexistent_xyz"))
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
