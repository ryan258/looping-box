"""One `looping-box` entry point over the existing tools (the old console scripts keep working)."""

from __future__ import annotations

import argparse
import sys
from typing import Callable

from . import doctor, phase1, report, review, scaffold, supervisor, worker
from ._util import default_root

USAGE = """usage: looping-box <command> [args]

  init [DIR]            create a workspace with default config (never overwrites)
  run [--root R]        ingest the inbox, then one supervisor pass (what ./startday.sh does)
  status                supervisor status, including live pending reviews
  review <sub>          list | show | approve | reject | key | audit
  worker <id>           run one worker pass (context_builder | execution_engine)
  doctor                read-only health check with remedies
  report [--since D]    audit logs and decisions as one markdown summary
  phase1, supervisor    low-level entry points (same flags as looping-box-phase1 / -supervisor)

Set LOOPING_BOX_ROOT (or pass --root) to choose the workspace.
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
        known, extra = parser.parse_known_args(rest)
        code = _call("phase1", phase1.main, ["--root", known.root, *extra])
        if code:
            return code
        return _call("supervisor", supervisor.main, ["--root", known.root, "--once"])
    if command == "status":
        return _call("status", supervisor.main, ["--status", *rest])
    if command in _TOOLS:
        return _call(command, _TOOLS[command], rest)

    print(f"looping-box: unknown command {command!r}\n\n{USAGE}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
