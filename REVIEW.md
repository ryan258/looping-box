# Code Review — Looping Box (Phase 1)

Reviewed: 2026-06-24. Scope: `src/looping_box/phase1.py`, `tests/test_phase1.py`,
config, and shell entrypoint.

Overall: small, deterministic, well-factored. Atomic writes, content-hash dedup,
and the file-system-as-boundary model are all sound.

Fixed items are recorded in `CHANGELOG.md` (Unreleased → Fixed). What remains
below is open or left by design.

## Medium — substring keyword matching

`_match_keywords` uses `in`, not word boundaries. "send" matches
"sender"/"resend", "remove" matches "removed". Kept as-is by design: for a
safety gate, substring keeps recall high ("deploy" catches "deployment",
"credential" catches "credentials"), and word boundaries would *reduce* recall.
This is the intended ceiling; revisit only if false-positive review fatigue
becomes a problem.

## Low — unbounded state growth

`runs` is capped at 50, but `processed_files` and `processed_hashes` grow
forever. Fine for a local Phase 1 inbox; flag it before this scales.

## Nits

- `change["mtime_ns"]` is recorded but never consumed; dedup is content-only.
  Drop it or use it.
- `_read_json` raises on a malformed SOP. Failing loud is intended; left as-is.
