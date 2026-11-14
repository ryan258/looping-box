"""Contained filesystem preparation for preexisting path traps (T02).

Workspace roots are selected by the operator and canonicalized by entry points.
Artifact references are strictly relative to that root. Never resolve an artifact
before checking its components: resolving would erase evidence of a symlink.
These checks do not defend against an attacker replacing paths during an operation.
"""
from __future__ import annotations

import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any

from .validation import CONFIG_SCHEMAS, SCHEMAS, ValidationError, validate_data


class UnsafePathError(ValueError):
    """A path must not be used; callers must not treat this as missing evidence."""

    def __init__(self, path: Path | str, reason: str) -> None:
        super().__init__(f"unsafe_path: {path}: {reason}")


def checked_path(path: Path, *, kind: str | None = None) -> Path:
    """Check a constructed absolute path, including every existing ancestor.

    Low-level callers supply canonical workspace-root paths. Missing components
    are permitted for creation, but dangling links and special files are not.
    Only lstat is used for application components. On macOS, the standard
    /tmp and /var host aliases are accepted only at their exact system targets.
    """
    path = Path(path)
    if ".." in path.parts:
        raise UnsafePathError(path, "parent traversal is forbidden")
    if not path.is_absolute():
        path = Path.cwd() / path
    if sys.platform == "darwin" and len(path.parts) > 1 and path.parts[1] in {"tmp", "var"}:
        alias = Path("/") / path.parts[1]
        host_target = Path("/private") / path.parts[1]
        if alias.is_symlink() and alias.resolve() == host_target:
            path = host_target.joinpath(*path.parts[2:])
    current = Path(path.anchor)
    for index, part in enumerate(path.parts[1:]):
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            break
        if stat.S_ISLNK(mode):
            raise UnsafePathError(current, "symbolic links are forbidden")
        leaf = index == len(path.parts) - 2
        if not leaf or kind == "directory":
            if not stat.S_ISDIR(mode):
                raise UnsafePathError(current, "expected a directory")
        elif kind == "file":
            if not stat.S_ISREG(mode):
                raise UnsafePathError(current, "expected a regular file")
        elif not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            raise UnsafePathError(current, "special files are forbidden")
    return path


def file_path(path: Path) -> Path:
    return checked_path(path, kind="file")


def directory_path(path: Path) -> Path:
    return checked_path(path, kind="directory")


def contained_path(root: Path, value: Path | str) -> Path:
    """Validate a relative artifact reference without following any descendant."""
    text = _relative_reference(value)
    root = Path(root).resolve()
    return checked_path(root / text)


def _relative_reference(value: Path | str) -> str:
    text = str(value)
    if (not text or text.startswith("/") or re.match(r"^[A-Za-z]:", text)
            or "\\" in text or any(ord(char) < 32 for char in text)
            or any(part in {"", ".", ".."} for part in text.split("/"))):
        raise UnsafePathError(text, "expected a normalized workspace-relative path")
    return text


def _check_references(value: Any, rule: dict[str, Any], schema: dict[str, Any], field: str, artifact: Path) -> None:
    """Classify unsafe reference syntax before the general T01 shape validator.

    Walk only declared Path fields, without accessing their targets: references
    in historical artifacts are identifiers and need not exist on disk.
    """
    reference = rule.get("$ref")
    if reference == "#/$defs/Path" and isinstance(value, str):
        try:
            _relative_reference(value)
        except UnsafePathError as exc:
            raise UnsafePathError(artifact, f"{field}: invalid workspace-relative reference") from exc
    elif reference:
        _check_references(value, schema["$defs"][reference.split("/")[-1]], schema, field, artifact)
    if isinstance(value, dict):
        properties = rule.get("properties", {})
        for key, item in value.items():
            if isinstance(rule.get("propertyNames"), dict):
                _check_references(key, rule["propertyNames"], schema, f"{field}[{key!r}]", artifact)
            child = properties.get(key, rule.get("additionalProperties", {}))
            if isinstance(child, dict):
                _check_references(item, child, schema, f"{field}.{key}", artifact)
    if isinstance(value, list) and isinstance(rule.get("items"), dict):
        for index, item in enumerate(value):
            _check_references(item, rule["items"], schema, f"{field}[{index}]", artifact)
    for keyword in ("allOf", "anyOf", "oneOf"):
        branches = rule.get(keyword, [])
        if keyword != "allOf" and any("const" in branch and value == branch["const"] for branch in branches):
            continue  # e.g. the explicitly allowed empty pending-index latest
        for branch in branches:
            _check_references(value, branch, schema, field, artifact)
    for keyword in ("then", "else"):
        if keyword in rule:
            _check_references(value, rule[keyword], schema, field, artifact)


def read_validated(path: Path, kind: str, **options: Any) -> dict[str, Any]:
    return read_validated_snapshot(path, kind, **options)[0]


def read_validated_snapshot(path: Path, kind: str, **options: Any) -> tuple[dict[str, Any], bytes]:
    """Validate and return the exact bytes that supplied the parsed artifact."""
    path = file_path(path)
    code = "invalid_config" if kind in CONFIG_SCHEMAS else "invalid_artifact"
    try:
        raw = path.read_bytes()
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError) as exc:
        raise ValidationError(str(path), "$: invalid JSON", code=code) from exc
    try:
        _check_references(data, SCHEMAS[kind], SCHEMAS[kind], "$", path)
    except RecursionError as exc:
        raise ValidationError(str(path), "$: nesting too deep", code=code) from exc
    return validate_data(data, kind, str(path), **options), raw


def matching_files(directory: Path, pattern: str) -> list[Path]:
    """Nonrecursive glob whose directory and all returned entries are checked."""
    directory = directory_path(directory)
    return [file_path(path) for path in sorted(directory.glob(pattern))]


def tree_entries(directory: Path) -> list[Path]:
    """Validate a whole internal tree before snapshot, archive, or rollback work."""
    directory = directory_path(directory)
    if not directory.exists():
        return []
    entries: list[Path] = []
    for child in sorted(directory.iterdir()):
        child = checked_path(child)
        entries.append(child)
        if child.is_dir():
            entries.extend(tree_entries(child))
    return entries


def source_files(directory: Path, extensions: set[str]) -> list[Path]:
    """Skip source symlinks (including directory links), hidden and special files."""
    directory = directory_path(directory)
    if not directory.exists():
        return []

    def fail_scan(error: OSError) -> None:
        raise OSError(f"input_scan_failed: {error}; restore access and re-ingest") from error

    files: list[Path] = []
    for current, folders, names in os.walk(directory, onerror=fail_scan, followlinks=False):
        parent = directory_path(Path(current))
        folders[:] = sorted(name for name in folders
                            if not name.startswith(".") and not (parent / name).is_symlink())
        for name in sorted(names):
            candidate = parent / name
            if name.startswith(".") or candidate.is_symlink() or candidate.suffix.lower() not in extensions:
                continue
            if stat.S_ISREG(candidate.lstat().st_mode):
                relative = candidate.relative_to(directory).as_posix()
                try:
                    _relative_reference(relative)
                    relative.encode("utf-8")
                except (UnsafePathError, UnicodeError) as exc:
                    raise UnsafePathError(repr(str(candidate)), "rename this input to a supported path") from exc
                files.append(file_path(candidate))
    return sorted(files)
