# Review — open items

Living list of what is still open. Resolved history: `CHANGELOG.md` and
`docs/history/`. Feature ideas: `ROADMAP.md`. Trust model: `docs/THREAT-MODEL.md`.

Last full review: 2026-10-02 (two passes; all fixes verified with regression tests
and bundled for 0.1.0).

## Open by design

- **Keyword gate is a tripwire.** Matching is normalized and whole-word, but it only
  knows its list (generic words like `token`/`release` are deliberately absent). Next
  step if review fatigue or misses show up: a model classifier that can only *raise*
  severity (ROADMAP).
- **Same-user attacker** can read the signing key, set the non-interactive override,
  or rewrite logs (THREAT-MODEL).
- **Model verifier is advisory**; deterministic verifier checks prove payload integrity only.
- **Unbounded phase 1 state.** `processed_files` / `processed_hashes` grow forever (runs are capped); `doctor` warns.
- **Per-run growth while a review is pending.** Each run writes a delta (archived) and audit
  lines while any item is held. Audit logs are append-only by design; `doctor` warns on a large archive.
- **Project lock is advisory and time-based** (not refreshed, no pid check; two processes that
  both judge it stale can both proceed). Runs are short; `doctor` checks the config. Fix is a
  kernel-backed lock (ROADMAP).
- **Worker timeout is measured after the fact**, not enforced. Each worker makes at most one
  model call and its transport timeout equals the limit, so the blocked-and-retried-with-spend
  case needs a response that lands just over the limit. A hard deadline needs threads or a subprocess.
- **Malformed SOP fails loudly** by design (now as an `error:` line, exit 1).
- **Drafts only see 500-char excerpts** (`max_excerpt_chars`); offline, a draft is that excerpt.
  Full-content processing is on the ROADMAP.
- **Carry-over assumes `execution_engine` runs after `context_builder`** (default routing). With it
  unrouted, undrafted items accumulate until `max_payload_bytes` blocks.

## Open, small

- Phase 1 hashes then reads a file; a change in between is only caught on the next run.
- A wider SOP keyword list re-gates previously processed files (the gate runs before dedup).
  Fail-closed, but note it when upgrading the SOP.
- No tests yet for the interactive approve prompt, `startday.sh`, or the demo scripts.
- `worker_output.schema.json` lists statuses no worker produces (`ready`, `running`, `pending_review`).
- No schemas yet for `context_package`, `execution_draft`, audit events, or the config files.
- `config/workers/` is an empty reserved directory (kept for the pluggable-workers idea).
- `schema.py` (test-only validator) and `classify_action` (test-only) live in the package.
- Modules import `_util` helpers under underscore aliases (`read_json as _read_json`); cosmetic.
- Python 3.9 is declared and run in CI only; develop on a current Python.
- `.env.example` model ids are examples; confirm them on openrouter.ai before relying on them.

## Owner decisions (not code)

- **LICENSE** and the `license` field in `pyproject.toml` (blocks PyPI).
- Whether to keep the GitNexus-generated `AGENTS.md` and `.claude/skills/gitnexus-*`
  (their counts drift from `CLAUDE.md`, and the Cyborg manual says GitNexus is not active).
- Whether `docs/sales-demos.md` earns its place before there is a prospect.
- A 0.1.0 tag and what it contains.
