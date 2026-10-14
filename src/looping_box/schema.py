"""Standard-library validator for the JSON Schema vocabulary used by this package.

This is deliberately not a general JSON Schema implementation. References are
local-only, formats are checked explicitly, and no schema can trigger I/O.
"""
from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any

_TYPES = {
    "object": dict, "array": list, "string": str, "integer": int,
    "number": (int, float), "boolean": bool, "null": type(None),
}


def validate(instance: Any, schema: dict[str, Any], path: str = "$") -> None:
    """Raise ValueError with a field path; never coerce input or fetch references."""
    _validate_json_values(instance, path)
    _validate(instance, schema, path, schema)


def _validate_json_values(value: Any, path: str) -> None:
    """JSON extension metadata is allowed, but NaN/Infinity are never JSON data."""
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{path}: number must be finite")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path}: object keys must be strings")
            _validate_json_values(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_values(item, f"{path}[{index}]")


def _validate(value: Any, rule: dict[str, Any], path: str, root: dict[str, Any]) -> None:
    if "$ref" in rule:
        reference = rule["$ref"]
        if not reference.startswith("#/$defs/"):
            raise ValueError(f"{path}: unsupported nonlocal schema reference")
        _validate(value, root["$defs"][reference[len('#/$defs/'):]], path, root)
    expected = rule.get("type")
    if expected is not None:
        names = expected if isinstance(expected, list) else [expected]
        valid = any(isinstance(value, _TYPES[name]) for name in names)
        if isinstance(value, bool) and "boolean" not in names:
            valid = False
        if not valid:
            raise ValueError(f"{path}: expected {expected}, got {type(value).__name__}")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{path}: number must be finite")
    if "const" in rule and (value != rule["const"] or isinstance(value, bool) != isinstance(rule['const'], bool)):
        raise ValueError(f"{path}: unexpected constant")
    if "enum" in rule and not any(
        value == item and isinstance(value, bool) == isinstance(item, bool) for item in rule["enum"]
    ):
        raise ValueError(f"{path}: expected one of {rule['enum']}")
    # Report missing envelope fields before conditional branches can require
    # fields for an event whose discriminator is itself absent.
    if isinstance(value, dict):
        for key in rule.get("required", []):
            if key not in value:
                raise ValueError(f"{path}.{key}: required field missing")
    for branch in rule.get("allOf", []):
        _validate(value, branch, path, root)
    for keyword in ("anyOf", "oneOf"):
        if keyword in rule:
            successes = 0
            for branch in rule[keyword]:
                try:
                    _validate(value, branch, path, root)
                except ValueError:
                    continue
                successes += 1
            if successes == 0 or (keyword == "oneOf" and successes != 1):
                raise ValueError(f"{path}: does not match {keyword} contract")
    if "if" in rule:
        try:
            _validate(value, rule["if"], path, root)
        except ValueError:
            branch = rule.get("else", {})
        else:
            branch = rule.get("then", {})
        _validate(value, branch, path, root)
    if isinstance(value, str):
        if len(value) < rule.get("minLength", 0):
            raise ValueError(f"{path}: string is too short")
        if "pattern" in rule and re.search(rule["pattern"], value) is None:
            raise ValueError(f"{path}: invalid string format")
        if rule.get("format") == "date-time":
            try:
                datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
            except ValueError as exc:
                raise ValueError(f"{path}: invalid UTC timestamp") from exc
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in rule and value < rule["minimum"]:
            raise ValueError(f"{path}: must be >= {rule['minimum']}")
        if "maximum" in rule and value > rule["maximum"]:
            raise ValueError(f"{path}: must be <= {rule['maximum']}")
        if "exclusiveMinimum" in rule and value <= rule["exclusiveMinimum"]:
            raise ValueError(f"{path}: must be > {rule['exclusiveMinimum']}")
    if isinstance(value, dict):
        properties = rule.get("properties", {})
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path}: object keys must be strings")
            if "propertyNames" in rule:
                _validate(key, rule["propertyNames"], f"{path}[{key!r}]", root)
            if key in properties:
                _validate(item, properties[key], f"{path}.{key}", root)
            elif rule.get("additionalProperties") is False:
                raise ValueError(f"{path}.{key}: unknown configuration key")
            elif isinstance(rule.get("additionalProperties"), dict):
                _validate(item, rule["additionalProperties"], f"{path}.{key}", root)
        for key, dependencies in rule.get("dependentRequired", {}).items():
            if key in value:
                for dependency in dependencies:
                    if dependency not in value:
                        raise ValueError(f"{path}.{dependency}: required with {key}")
    if isinstance(value, list):
        if len(value) < rule.get("minItems", 0) or len(value) > rule.get("maxItems", math.inf):
            raise ValueError(f"{path}: invalid array length")
        if rule.get("uniqueItems") and any(item in value[:index] for index, item in enumerate(value)):
            raise ValueError(f"{path}: duplicate array entries")
        for index, item in enumerate(value):
            if "items" in rule:
                _validate(item, rule["items"], f"{path}[{index}]", root)
