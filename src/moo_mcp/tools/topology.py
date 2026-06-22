"""Inheritance and structure tools: verbs/properties on an object, parent, chparent."""

from __future__ import annotations

from typing import Any

from moo_mcp.connection import MOOConnection
from moo_mcp.tools._base import assert_identifier, assert_object_ref, eval_value


async def list_local_verbs(conn: MOOConnection, obj: str) -> dict[str, Any]:
    """Return the list of verbs defined locally on `obj` (not inherited)."""
    ref = assert_object_ref(obj)
    return await eval_value(conn, f"verbs({ref})")


async def list_local_properties(conn: MOOConnection, obj: str) -> dict[str, Any]:
    """Return the list of properties defined locally on `obj` (not inherited)."""
    ref = assert_object_ref(obj)
    return await eval_value(conn, f"properties({ref})")


async def parent_of(conn: MOOConnection, obj: str) -> dict[str, Any]:
    """Return the immediate parent of `obj` (`#-1` if none)."""
    ref = assert_object_ref(obj)
    return await eval_value(conn, f"parent({ref})")


async def chparent(conn: MOOConnection, obj: str, new_parent: str) -> dict[str, Any]:
    """Set obj's parent to new_parent. Returns {success, error?}."""
    ref = assert_object_ref(obj)
    new_ref = assert_object_ref(new_parent)
    result = await eval_value(conn, f"chparent({ref}, {new_ref})")
    if "error" in result:
        return {"success": False, "error": result["error"]}
    return {"success": True, "obj": ref, "new_parent": new_ref}


async def has_verb(conn: MOOConnection, obj: str, verb: str) -> dict[str, Any]:
    """Where (if anywhere) is `verb` defined in `obj`'s inheritance chain?

    Returns {defined_on: [obj_refs]} - empty list means not defined.
    """
    ref = assert_object_ref(obj)
    name = assert_identifier(verb, what="verb")
    result = await eval_value(conn, f'$object_utils:has_verb({ref}, "{name}")')
    if "error" in result:
        return {"defined_on": [], "error": result["error"]}
    val = result["value"]
    if val == 0 or val is False:
        return {"defined_on": []}
    if isinstance(val, list):
        return {"defined_on": val}
    return {"defined_on": [val] if val else []}


async def has_property(conn: MOOConnection, obj: str, prop: str) -> dict[str, Any]:
    """Is `prop` defined anywhere in `obj`'s inheritance chain? Returns {exists: bool}."""
    ref = assert_object_ref(obj)
    name = assert_identifier(prop, what="property")
    result = await eval_value(conn, f'$object_utils:has_property({ref}, "{name}")')
    if "error" in result:
        return {"exists": False, "error": result["error"]}
    val = result["value"]
    return {"exists": bool(val)}
