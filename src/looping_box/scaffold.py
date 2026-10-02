"""`looping-box init`: create a workspace from the defaults bundled in the package."""

from __future__ import annotations

import argparse
from importlib import resources
from pathlib import Path

from ._util import default_root

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


def init_workspace(target: Path | str) -> list[tuple[str, str]]:
    """Create the workspace tree and default config. Never overwrites an existing file.

    Returns (status, relative_path) pairs where status is "created" or "kept".
    """
    root = Path(target).resolve()
    results: list[tuple[str, str]] = []
    for directory in DIRECTORIES:
        (root / directory).mkdir(parents=True, exist_ok=True)
    for template, destination in TEMPLATES.items():
        path = root / destination
        if path.exists():
            results.append(("kept", destination))
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(template_text(template), encoding="utf-8")
        results.append(("created", destination))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a Looping Box workspace (never overwrites).")
    parser.add_argument(
        "directory",
        nargs="?",
        default=default_root(),
        help="Workspace directory. Defaults to $LOOPING_BOX_ROOT, else the current directory.",
    )
    args = parser.parse_args()
    for status, path in init_workspace(args.directory):
        print(f"{status:7} {path}")
    print(
        f"\nWorkspace ready: {Path(args.directory).resolve()}\n"
        "Next: cd there, drop .md/.txt/.json files in inbox/, then:\n"
        "  looping-box doctor\n"
        "  looping-box run"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
