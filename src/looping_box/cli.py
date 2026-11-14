"""One `looping-box` entry point over the existing tools (the old console scripts keep working)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable

from . import doctor, phase1, report, review, scaffold, supervisor, worker
from ._util import default_root

USAGE = """usage: looping-box <command> [args]

  init [DIR]            create a workspace with default config (never overwrites)
  run [--root R]        ingest the inbox, then one supervisor pass (what ./startday.sh does)
  status                supervisor status, including live pending reviews
  review [--root R] <sub>  list | show | approve | reject | key | audit
                           Approval makes the item eligible for local drafting only.
  worker <id>           run one worker pass (context_builder | execution_engine)
  doctor                read-only health check with remedies
  report [--since D]    audit logs and decisions as one markdown summary
  phase1, supervisor    low-level entry points (same flags as looping-box-phase1 / -supervisor)

Root: --root > LOOPING_BOX_ROOT > cwd. init [DIR] uses DIR (default cwd).
run accepts only --root; custom paths belong to phase1.
"""

_TOOLS: dict[str, Callable[[], int]] = {
    "phase1": phase1.main,
    "supervisor": supervisor.main,
    "review": review.main,
    "worker": worker.main,
    "doctor": doctor.main,
    "report": report.main,
    "init": scaffold.main,
}


def _call(command: str, entry: Callable[[], int], args: list[str]) -> int:
    saved = sys.argv
    sys.argv = [f"looping-box {command}", *args]
    try:
        return entry()
    finally:
        sys.argv = saved


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help", "help"}:
        print(USAGE)
        return 0
    command, rest = args[0], args[1:]

    if command == "run":
        parser = argparse.ArgumentParser(prog="looping-box run")
        parser.add_argument("--root", default=default_root())
        known = parser.parse_args(rest)
        try:
            root = str(Path(known.root).expanduser().resolve())
        except (ValueError, OSError, RuntimeError) as exc:
            print(f"invalid_root: {known.root}: {exc}", file=sys.stderr)
            return 1
        code = _call("phase1", phase1.main, ["--root", root])
        if code:
            return code
        return _call("supervisor", supervisor.main, ["--root", root, "--once"])
    if command == "status":
        return _call("status", supervisor.main, ["--status", *rest])
    if command in _TOOLS:
        return _call(command, _TOOLS[command], rest)

    print(f"looping-box: unknown command {command!r}\n\n{USAGE}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
