#!/usr/bin/env python3
"""Run one Looping Box example in a temporary workspace."""

import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

import example_runs


CATALOG = Path(__file__).with_name("offline-examples.json")
SOURCE = CATALOG.resolve().parents[1] / "src"
DRAFT = Path("cache/workers/execution_engine/draft.md")


def checkout_api_key():
    sys.path.insert(0, str(SOURCE))
    from looping_box.model import _read_workspace_settings
    from looping_box.paths import file_path

    return _read_workspace_settings(file_path(SOURCE.parent / ".env")).get("OPENROUTER_API_KEY", "")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("number", nargs="?", help="Example number. Omit it to list the examples.")
    parser.add_argument("--shell", action="store_true", help="Open a Bash session for the follow-up steps.")
    parser.add_argument("--free", action="store_true", help="Use OpenRouter's free router for the first draft.")
    parser.add_argument("--runs", action="store_true", help="Review saved runs. Add a number to open its latest run.")
    args = parser.parse_args()
    if args.runs:
        if args.free or args.shell:
            parser.error("--runs is for reading saved runs. Use it without --free or --shell")
        return example_runs.show_runs(args.number.zfill(2) if args.number else None)

    examples = json.loads(CATALOG.read_text(encoding="utf-8"))
    if args.number is None:
        if args.shell:
            parser.error("--shell requires an example number")
        if args.free:
            parser.error("--free requires an example number")
        for example in examples:
            print(f"{example['id']}  {example['title']}")
        return 0

    selected = next((example for example in examples if example["id"] == args.number.zfill(2)), None)
    if selected is None:
        parser.error(f"unknown example {args.number!r}. Run without a number to see the list")

    record_dir = None
    if args.free:
        record_dir, record = example_runs.start_run(selected)
        root = record_dir / "workspace"
        root.mkdir()
        print(f"Saved run record: {record_dir}", flush=True)
    else:
        root = Path(tempfile.mkdtemp(prefix="looping-box-example-")).resolve()
    env = dict(
        os.environ,
        PYTHONPATH=str(SOURCE),
        LOOPING_BOX_ROOT=str(root),
        XDG_CONFIG_HOME=str(root / "private-config"),
        LOOPING_BOX_REVIEW_KEY="offline-example-fixture-key",
        LOOPING_BOX_APPROVER="example-operator",
        OPENROUTER_API_KEY="",
        MODEL_CONTEXT_BUILDER="",
        MODEL_EXECUTION_ENGINE="",
        MODEL_VERIFIER="",
    )
    env.pop("LOOPING_BOX_ALLOW_NONINTERACTIVE", None)
    env.pop("OPENROUTER_BASE_URL", None)

    code = 1
    try:
        api_key = ""
        if args.free:
            try:
                api_key = checkout_api_key()
            except (OSError, ValueError):
                code = 2
                parser.error("Could not read OpenRouter settings from the checkout .env")
            if not api_key:
                code = 2
                parser.error("Set OPENROUTER_API_KEY in the checkout .env to use --free")
        code = run_example(selected, root, env, record_dir, api_key)
    except KeyboardInterrupt:
        code = 130
        raise
    finally:
        if record_dir is not None:
            example_runs.finish_run(record_dir, record, code, root / DRAFT)

    if code == 0 and args.shell:
        return open_example_shell(root, env)
    return code


def run_example(selected, root, env, record_dir, api_key):
    cli = [sys.executable, "-m", "looping_box"]

    print(f"{selected['id']}  {selected['title']}", flush=True)
    print(f"Example workspace: {root}", flush=True)
    initialized = subprocess.run([*cli, "init", str(root)], cwd=root, env=env)
    if initialized.returncode:
        return initialized.returncode

    for name, content in selected["files"].items():
        if Path(name).name != name or Path(name).suffix.lower() not in {".md", ".txt", ".json"}:
            raise ValueError(f"Invalid example filename: {name!r}")
        (root / "inbox" / name).write_text(content + "\n", encoding="utf-8")

    run_env = env
    run_command = [*cli, "run"]
    if record_dir is not None:
        run_env = dict(
            env,
            OPENROUTER_API_KEY=api_key,
            OPENROUTER_BASE_URL="https://openrouter.ai/api/v1",
            MODEL_EXECUTION_ENGINE=example_runs.FREE_MODEL,
        )
        run_command = [sys.executable, str(Path(example_runs.__file__).resolve()), str(record_dir), selected["id"]]
        print(f"Draft model: {example_runs.FREE_MODEL}. Eligible sample excerpts go to OpenRouter.", flush=True)
    result = subprocess.run(run_command, cwd=root, env=run_env)
    expected = 0 if selected["expect"] == "draft" else 2
    if result.returncode != expected:
        print(f"Example expected run exit {expected}, got {result.returncode}.", file=sys.stderr)
        if record_dir is not None:
            print("Run without --free to try the offline example.", file=sys.stderr)
        return 1

    if expected == 2:
        print("\nPending reviews", flush=True)
        inspection = subprocess.run([*cli, "review", "list"], cwd=root, env=env)
        if inspection.returncode:
            return inspection.returncode

    draft = root / DRAFT
    if draft.is_file():
        print(f"\nSaved draft at {draft}\n")
        print(draft.read_text(encoding="utf-8").rstrip())
        if record_dir is not None:
            items = json.loads(draft.with_suffix(".json").read_text(encoding="utf-8"))["items"]
            models = sorted({item["model"] for item in items if "model" in item})
            if models:
                print(f"\nOpenRouter model used: {', '.join(models)}")
                print("The checks below describe the offline excerpts. Compare model wording with the inbox files.")
            else:
                print("\nThis pass had no eligible excerpts for the draft model.")
    print(f"\nWhat to check\n{selected['check']}")
    print(f"\nTry next\n{selected['next']}", flush=True)

    return 0


def open_example_shell(root, env):
    bin_dir = root / "private-config" / "bin"
    bin_dir.mkdir(parents=True)
    launcher = bin_dir / "looping-box"
    launcher.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} -m looping_box "$@"\n', encoding="utf-8")
    launcher.chmod(0o700)
    env["PATH"] = os.pathsep.join((str(bin_dir), env.get("PATH", os.defpath)))
    print("\nOffline example shell. Use looping-box for the follow-up. Type exit when done.", flush=True)
    return subprocess.run(["/bin/bash", "--noprofile", "--norc"], cwd=root, env=env).returncode


if __name__ == "__main__":
    raise SystemExit(main())
