"""`looping-box init`: create a workspace from the defaults bundled in the package."""

from __future__ import annotations

import argparse
import json
import sys
from importlib import resources
from pathlib import Path

from ._util import project_lock, utc_now, write_text
from .paths import contained_path, directory_path, file_path, read_validated
from .validation import validate_data, validate_policy

# template (inside looping_box/templates) -> path in the workspace
TEMPLATES = {
    "action_classes.json": "config/action_classes.json",
    "super_loop.json": "config/super_loop.json",
    "sops/phase1_ingestion.json": "config/sops/phase1_ingestion.json",
    "sops/phase1_ingestion.md": "config/sops/phase1_ingestion.md",
    "env.example": ".env.example",
    "gitignore": ".gitignore",
}

DIRECTORIES = [
    "inbox",
    "cache/state",
    "cache/deltas",
    "cache/supervisor",
    "cache/verifiers",
    "cache/workers/context_builder",
    "cache/workers/execution_engine",
    "staging/approvals",
    "staging/rejections",
    "staging/reviews",
    "logs/transactions",
]


def template_text(name: str) -> str:
    node = resources.files("looping_box") / "templates"
    for part in name.split("/"):
        node = node / part
    return node.read_text(encoding="utf-8")


def _validate_scaffold_config(root: Path) -> None:
    configs = {}
    for template, kind in (("action_classes.json", "action_classes"), ("super_loop.json", "super_loop"),
                           ("sops/phase1_ingestion.json", "phase1_ingestion")):
        path = file_path(contained_path(root, TEMPLATES[template]))
        configs[kind] = (read_validated(path, kind) if path.exists()
                         else validate_data(json.loads(template_text(template)), kind, f"bundled:{template}"))
    validate_policy(configs["phase1_ingestion"], configs["action_classes"], "config/sops/phase1_ingestion.json")


def init_workspace(target: Path | str) -> list[tuple[str, str]]:
    """Create the workspace tree and default config. Never overwrites an existing file.

    Returns (status, relative_path) pairs where status is "created" or "kept".
    """
    root = directory_path(Path(target).expanduser().resolve())
    # Preflight every destination before creating even the first directory.
    for directory in DIRECTORIES:
        directory_path(contained_path(root, directory))
    for destination in TEMPLATES.values():
        file_path(contained_path(root, destination))
    _validate_scaffold_config(root)
    with project_lock(root, utc_now()):
        _validate_scaffold_config(root)
        results: list[tuple[str, str]] = []
        for directory in DIRECTORIES:
            directory_path(contained_path(root, directory)).mkdir(parents=True, exist_ok=True)
        for template, destination in TEMPLATES.items():
            path = file_path(contained_path(root, destination))
            if path.exists():
                results.append(("kept", destination))
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            write_text(path, template_text(template))
            results.append(("created", destination))
        return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a Looping Box workspace (never overwrites).")
    parser.add_argument(
        "directory",
        nargs="?",
        default=".",
        help="Workspace directory. Defaults to the current directory (ignores LOOPING_BOX_ROOT).",
    )
    args = parser.parse_args()
    try:
        results = init_workspace(args.directory)
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"error: init_error: {exc}", file=sys.stderr)
        return 1
    for status, path in results:
        print(f"{status:7} {path}")
    print(
        f"\nWorkspace ready: {Path(args.directory).expanduser().resolve()}\n"
        "Next: cd there, drop .md/.txt/.json files in inbox/, then:\n"
        "  looping-box doctor\n"
        "  looping-box run"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
