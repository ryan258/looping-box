# Review — open items

Living list of what is still open. Resolved history: `CHANGELOG.md` and
`docs/history/`. Feature ideas: `ROADMAP.md`. Trust model: `docs/THREAT-MODEL.md`.

Last full review: 2026-10-02 (all code, docs, and demos; reproduced defects fixed in
the Unreleased changelog entry).

## Open by design

- **Keyword gate is a tripwire.** Matching is normalized and whole-word, but it only
  knows its list. Next step if review fatigue or misses show up: a model classifier
  that can only *raise* severity (ROADMAP).
- **Same-user attacker** can read the signing key, set the non-interactive override,
  or rewrite logs (THREAT-MODEL).
- **Model verifier is advisory**; deterministic verifier checks prove payload integrity only.
- **Unbounded phase 1 state.** `processed_files` / `processed_hashes` grow forever (runs are capped).
- **Malformed SOP fails loudly** by design.
- **Drafts only see 500-char excerpts** (`max_excerpt_chars`); full-content processing is on the ROADMAP.

## Open, small

- `supervisor --once` exits 0 even when blocked; consider `2` for "operator action required".
- Phase 1 hashes then reads a file; a change in between is only caught on the next run.
- A wider SOP keyword list re-gates previously processed files (the gate runs before dedup).
  Fail-closed, but note it when upgrading the SOP.
- No tests yet for the interactive approve prompt, `startday.sh`, or the demo scripts.

- `worker_output.schema.json` lists statuses no worker produces (`ready`, `running`, `pending_review`).
- No schemas yet for `context_package`, `execution_draft`, audit events, or the config files.
- `config/workers/` is an empty reserved directory (kept for the pluggable-workers idea).
- Python 3.9 is declared and run in CI only; develop on a current Python.
