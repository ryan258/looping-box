from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_ACTION_CLASSES = {
    "default_class": "review_required",
    "classes": {
        "safe_local_transform": ["docs", "format", "summarize"],
        "review_required": [
            "commit",
            "push",
            "deploy",
            "publish",
            "send",
            "email",
            "execute",
            "run script",
            "delete",
            "remove",
        ],
        "blocked": ["credential", "secret", "production"],
        "forbidden": [],
    },
}


def classify_action(root: Path | str, action: str) -> str:
    root_path = Path(root).resolve()
    config = _read_action_config(root_path)
    normalized = action.lower()
    for action_class, terms in config["classes"].items():
        if normalized in {str(term).lower() for term in terms}:
            return str(action_class)
    return str(config["default_class"])


def classify_reasons(root: Path | str, reasons: list[str]) -> str:
    classes = [classify_action(root, reason) for reason in reasons]
    for action_class in ("forbidden", "blocked", "review_required"):
        if action_class in classes:
            return action_class
    return "review_required"


def _read_action_config(root: Path) -> dict[str, Any]:
    config = {
        "default_class": DEFAULT_ACTION_CLASSES["default_class"],
        "classes": dict(DEFAULT_ACTION_CLASSES["classes"]),
    }
    config_path = _resolve_under_root(root, "config/action_classes.json")
    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        config["default_class"] = loaded.get("default_class", config["default_class"])
        config["classes"] = loaded.get("classes", config["classes"])
    return config


def _resolve_under_root(root: Path, value: Path | str) -> Path:
    path = Path(value)
    candidate = (path if path.is_absolute() else root / path).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"path escapes project root: {value!r} -> {candidate}")
    return candidate
