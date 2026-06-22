"""Property lifecycle: add, remove, set, read."""

from __future__ import annotations

from typing import Any

from moo_mcp.connection import MOOConnection
from moo_mcp.parser import parse_response
from moo_mcp.tools._base import assert_identifier, assert_object_ref, eval_value


def _serialize_value(value: Any) -> str:
    """Turn a Python value into a MOO literal suitable for splicing into eval."""
    if value is None:
        return "0"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        if value.startswith("#") and value[1:].lstrip("-").isdigit():
            return value
        if value.startswith("$") and value[1:].replace("_", "").isalnum():
            return value
        # SECURITY: also escape CR/LF/NUL - a raw newline in the string would
        # break out of the MOO source line we splice it into. MOO has no
        # embedded-newline literal for strings; use chr(10) at the MOO layer
        # if you actually need one.
        escaped = (
            value.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\r", " ")
            .replace("\n", " ")
            .replace("\x00", "")
        )
        return f'"{escaped}"'
    if isinstance(value, list):
        items = ", ".join(_serialize_value(v) for v in value)
        return "{" + items + "}"
    raise ValueError(f"can't serialize {type(value).__name__} to MOO literal: {value!r}")


async def get_property(conn: MOOConnection, obj: str, prop: str) -> dict[str, Any]:
    """Read a property's current value."""
    ref = assert_object_ref(obj)
    name = assert_identifier(prop, what="property")
    return await eval_value(conn, f"{ref}.{name}")


async def set_property(conn: MOOConnection, obj: str, prop: str, value: Any) -> dict[str, Any]:
    """Assign a value to an existing property. Returns {success, value, error?}."""
    ref = assert_object_ref(obj)
    name = assert_identifier(prop, what="property")
    literal = _serialize_value(value)
    result = await eval_value(conn, f"{ref}.{name} = {literal}")
    if "error" in result:
        return {"success": False, "error": result["error"]}
    return {"success": True, "value": result["value"]}


async def add_property(
    conn: MOOConnection,
    obj: str,
    prop: str,
    value: Any = None,
    perms: str = "rwc",
    owner: str | None = None,
) -> dict[str, Any]:
    """Add a new property to obj. Owner defaults to obj.owner.

    Perms is a MOO property perm string like "rwc" (read/write/chown). Note:
    `rxd` is NOT valid for properties - that's verb perms. Pass at most rwc.
    """
    ref = assert_object_ref(obj)
    name = assert_identifier(prop, what="property")
    literal = _serialize_value(value)
    owner_ref = assert_object_ref(owner) if owner else f"{ref}.owner"
    perms_clean = "".join(c for c in perms if c in "rwc")
    if not perms_clean:
        raise ValueError(f"perms must be subset of 'rwc', got: {perms!r}")
    expr = f'add_property({ref}, "{name}", {literal}, {{{owner_ref}, "{perms_clean}"}})'
    raw = await conn.send(f";{expr}; return 1")
    value_parsed, error = parse_response(raw)
    if error is not None:
        return {"success": False, "error": dict(error)}
    return {"success": True}


async def remove_property(conn: MOOConnection, obj: str, prop: str) -> dict[str, Any]:
    """Delete a property from obj. Inherited copies elsewhere are untouched."""
    ref = assert_object_ref(obj)
    name = assert_identifier(prop, what="property")
    raw = await conn.send(f';delete_property({ref}, "{name}"); return 1')
    _, error = parse_response(raw)
    if error is not None:
        return {"success": False, "error": dict(error)}
    return {"success": True}
