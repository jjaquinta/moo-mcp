"""Deep introspection tools that walk the inheritance chain.

`inspect_property` is the headline tool. When a property's effective value
seems to disagree with its declared default, the bug is usually a value
override on an intermediate class. Walking the parent chain manually via
eval is painful - this tool does it in one call.
"""

from __future__ import annotations

from typing import Any

from moo_mcp.connection import MOOConnection
from moo_mcp.parser import parse_response
from moo_mcp.tools._base import assert_identifier, assert_object_ref


async def inspect_property(
    conn: MOOConnection, obj: str, prop: str, *, max_depth: int = 20
) -> dict[str, Any]:
    """Walk the inheritance chain of `obj` reporting where `prop` lives at each level.

    Returns {chain: [{obj, name, locally_defined, has_value, value, equals_parent}],
    defined_on, effective_value}.

    `locally_defined` is true at the level where `add_property` was called
    (i.e., name appears in `properties(level)`). All deeper ancestors inherit
    or value-override.

    `equals_parent` is true when this level's value matches its parent's. If it's
    false on a level that's NOT locally_defined, that level has a VALUE OVERRIDE
    (set via `level.propname = ...` without `add_property`). Value overrides are
    invisible to `properties()` but DO change the inherited value chain. They are
    the most common silent-failure mode for "I changed the parent default but
    descendants still see the old value."

    `defined_on` is the level where `add_property` officially declared the prop.
    """
    ref = assert_object_ref(obj)
    name = assert_identifier(prop, what="property")
    eval_src = f"""
chain = {{}};
node = {ref};
defined_on = $nothing;
prev_val = 0;
prev_set = 0;
hops = 0;
while (valid(node) && hops < {int(max_depth)})
  hops = hops + 1;
  is_local = "{name}" in properties(node);
  if (defined_on == $nothing && is_local)
    defined_on = node;
  endif
  cur_has = $object_utils:has_property(node, "{name}");
  cur_val = cur_has ? `node.{name} ! ANY => "ERR"' | "(no property)";
  equals_parent = prev_set ? (cur_val == prev_val) | 0;
  chain = {{@chain, {{node, node.name, is_local, cur_has, cur_val, equals_parent}}}};
  prev_val = cur_val;
  prev_set = cur_has;
  node = parent(node);
endwhile
return {{chain, defined_on, cur_has ? `{ref}.{name} ! ANY => "ERR"' | "(no property)"}};
""".strip()
    raw = await conn.send(f';return eval("{_escape(eval_src)}")[2]')
    value, error = parse_response(raw)
    if error is not None:
        return {"error": dict(error), "chain": [], "defined_on": None}
    if not isinstance(value, list) or len(value) < 3:
        return {"error": "unexpected eval result", "raw": value}
    chain_raw, defined_on, effective = value[0], value[1], value[2]
    chain: list[dict[str, Any]] = []
    for entry in chain_raw or []:
        if not isinstance(entry, list) or len(entry) < 6:
            continue
        obj_ref, obj_name, locally, has_val, val, eq_parent = entry[:6]
        chain.append(
            {
                "obj": obj_ref,
                "name": obj_name,
                "locally_defined": bool(locally),
                "has_value": bool(has_val),
                "value": val,
                "equals_parent": bool(eq_parent),
            }
        )
    return {
        "chain": chain,
        "defined_on": defined_on if defined_on and defined_on != "$nothing" else None,
        "effective_value": effective,
    }


def _escape(src: str) -> str:
    """Escape a MOO source string for embedding inside a MOO string literal."""
    return src.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
