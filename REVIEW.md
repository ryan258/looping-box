# Code Review — Looping Box

Reviewed: 2026-06-24 (phase1 only), extended 2026-07-03 (full repo: phase1,
worker, supervisor, review, model, action_policy, schema, config, docs).

Overall: small, deterministic, well-factored. Atomic writes, content-hash
dedup, the file-system-as-boundary model, and the review/verifier flow are
all sound. Fixed items are recorded in `CHANGELOG.md` (Unreleased → Fixed).
What remains below is open or left by design.

## Medium — substring keyword matching

`_match_keywords` uses `in`, not word boundaries. "send" matches
"sender"/"resend", "remove" matches "removed". Kept as-is by design: for a
safety gate, substring keeps recall high ("deploy" catches "deployment",
"credential" catches "credentials"), and word boundaries would *reduce*
recall. This is the intended ceiling; revisit only if false-positive review
fatigue becomes a problem.

## Low — unbounded state growth

`runs` is capped at 50, but `processed_files` and `processed_hashes` grow
forever. Fine for a local Phase 1 inbox; flag it before this scales.

## Low — a single blocked item holds up the whole worker batch

`context_builder` sets the entire context package `status: "blocked"` when
*any* item in the batch has review reasons, and `execution_engine` refuses to
draft anything at all while that status is `blocked` — even the items with no
review reasons. Tested and intentional (fail-closed), but worth knowing:
one flagged file in an inbox of ten stalls drafting for the other nine until
the flagged one is resolved.

## Nits

- `_read_json` raises on a malformed SOP. Failing loud is intended; left as-is.
