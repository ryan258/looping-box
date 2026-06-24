"""Tiny JSON-Schema-subset validator.

ponytail: supports only the keywords our local schema fixtures use
(type/properties/required/items/const/enum). It is NOT a full JSON Schema
implementation. If we ever need $ref, oneOf, formats, etc., drop this and add
the `jsonschema` package instead.
"""

from __future__ import annotations

from typing import Any

_TYPES: dict[str, type | tuple[type, ...]] = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "null": type(None),
}


def validate(instance: Any, schema: dict[str, Any], path: str = "$") -> None:
    """Raise ValueError on the first violation; return None when valid."""
    if "const" in schema and instance != schema["const"]:
        raise ValueError(f"{path}: expected const {schema['const']!r}, got {instance!r}")

    if "enum" in schema and instance not in schema["enum"]:
        raise ValueError(f"{path}: {instance!r} not in enum {schema['enum']!r}")

    expected = schema.get("type")
    if expected is not None:
        allowed = expected if isinstance(expected, list) else [expected]
        # bool is an int subclass in Python; exclude it from integer/number.
        py_types = tuple(_TYPES[name] for name in allowed)
        ok = isinstance(instance, py_types)
        if isinstance(instance, bool) and "boolean" not in allowed:
            ok = False
        if not ok:
            raise ValueError(f"{path}: expected type {expected!r}, got {type(instance).__name__}")

    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                raise ValueError(f"{path}: missing required key {key!r}")
        for key, subschema in schema.get("properties", {}).items():
            if key in instance:
                validate(instance[key], subschema, f"{path}.{key}")

    if isinstance(instance, list) and "items" in schema:
        for index, item in enumerate(instance):
            validate(item, schema["items"], f"{path}[{index}]")
