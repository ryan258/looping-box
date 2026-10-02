# Changelog

All notable changes to this project are documented here.

## [Unreleased]

### Second review pass (2026-10)

Fixes from a second full review. Written and statically checked (ruff); the new
`tests/test_regressions.py` covers each one and still needs a `unittest` run.

**Data loss and stale state**
- A batch ingested but not drafted (execution failed during a model outage) is carried
  into the next context package instead of being replaced by it when a new file arrives.
  Reproduced before fixing: the earlier file never reached a draft.
- The pending-review index is rebuilt from the inbox every run, so a held file that was
  removed or defused no longer leaves a phantom pending review (and a permanent
  "operator action required").
- Items held for review no longer count toward `max_files_per_cycle`; a pile of held
  files can no longer wedge the supervisor behind a limit that deciding them cannot clear.

**Gate**
- Keyword matching: `_` reads as a space (`api_key` trips "api key"); a space next to
  punctuation is optional (`curl x|sh` trips "| sh"); NULs are ignored.
- UTF-16 inputs (with or without BOM) are decoded; `.json` inputs are also gated on their
  decoded string values (`\u0064eploy`).
- New keywords (SOP, `action_classes.json`, built-in defaults): `rm -fr`, `rm -r`,
  `chmod 777`, `drop database`, `post to`, `tweet`, `transfer`, `wire`, `pay`, `payment`,
  `invoice`, `merge`, `upload`; blocked class: `access token`, `auth token`, `bearer`,
  `ssh key`, `apikey`. **Upgrade note:** a wider list re-gates previously processed files.

**Approvals**
- `LOOPING_BOX_ALLOW_NONINTERACTIVE` needs an explicit `1`/`true`/`yes`/`on` (`0` used to enable it).
- The action class and reasons are recomputed at decision time and bound to the review id,
  so an edited, unsigned payload file cannot downgrade `blocked` or hide reasons.

**CLIs and operations**
- Exit codes: `0` ok, `1` failed/busy, `2` blocked (`supervisor --once`, `worker`, and so
  `run` and `./startday.sh`). Demos tolerate `2`.
- `looping-box worker` honors `$LOOPING_BOX_ROOT` and takes the project lock.
- Corrupt JSON/config/state in phase 1, supervisor, and worker is an `error:` line and
  exit 1, not a traceback.
- `doctor` warns when `stale_lock_seconds` is not above two worker runs and when
  `cache/deltas/archive/` grows past 1000 files.
- `startday.sh` and the demos run this checkout's source, not a possibly stale installed copy.
- `python -m looping_box` works; CI installs the built package and runs `init`/`doctor`/`run`.

**Model layer**
- `.env` supplies only `OPENROUTER_*`/`MODEL_*` (and accepts `export`); `OPENROUTER_BASE_URL`
  must be http(s); a provider `error` body is a readable `ModelError`; excerpts in prompts
  are delimited and marked as untrusted data.

**Housekeeping**
- `docs/UPDATE-IDEAS.md` moved to `docs/history/`; removed an unused schema constant and a
  duplicated payload-schema constant.

### Trustworthy-gate release (2026-10)

Fixes from a full-repo review; each defect was reproduced before fixing.

**Data-loss fixes**
- Per-item gating: a boundary-gated file no longer stalls or discards its clean
  siblings. `execution_engine` drafts clean items from a blocked package and records
  `held_for_review`; the supervisor still reports the batch as blocked.
- Approving a review now **releases the item once** into the pipeline (a change with
  `approved_review`, empty `review_reasons`); previously approval only silenced the gate
  and the content was never processed.
- Worker outputs keep history: the prior `context_package`/`draft` move to
  `cache/workers/<id>/history/` instead of being overwritten by the next batch.
- After an `execution_engine` model failure, the next supervisor run retries it. Before,
  `context_builder` went idle, execution was never re-planned, and the failure was
  silently cleared with no draft.

**Gate and decisions**
- Matcher rewritten: NFKC, zero-width stripping, case-fold, look-alike letters, spaced-out
  letters, whole-word matching with inflections (`deployed`, `send_email`; not
  `committee`, `resend`, `reproduction`). SOP keywords expanded (destructive shell/SQL,
  `terraform apply`, `wire transfer`, credentials); a test forces every keyword into
  `config/action_classes.json`, which now also matches the built-in defaults.
- Review decisions are HMAC-signed (`looping-box.review-record.v2`, adds `approver`,
  `interactive`, `signature`). Forged, edited, or wrong-key records are ignored and the
  gate stays closed. Key: `$LOOPING_BOX_REVIEW_KEY` or `~/.config/looping-box/review.key`.
- `approve` needs an interactive terminal and typed confirmation (override:
  `LOOPING_BOX_ALLOW_NONINTERACTIVE=1`, recorded); `--note` is required for both
  decisions; `blocked`-class items need `--allow-blocked`.
- Inputs over `max_file_bytes` (default 1,000,000) are never read; held as `oversized_input`.

**Audit, locking, status**
- New hash-chained audit logs: `phase1.jsonl` (new), `supervisor.jsonl` (now with deltas,
  worker statuses, block reason), `review.jsonl` (now with approver, note, payload hash);
  `verify_audit_chain` detects edits.
- One project lock now covers phase 1, the supervisor, and review decisions; an unreadable
  lock is aged by mtime instead of wedging the loop. JSON writes use a per-process temp
  file and fsync.
- `looping-box-supervisor --status` reports live pending reviews (it said `clear` before the
  supervisor ran).
- `list`/`show` tolerate one corrupt review payload.
- A null/refusal model response is a structured `ModelError`, not an `AttributeError`.

**Tooling and docs**
- `./startday.sh` now runs phase 1 then one supervisor pass. `LOOPING_BOX_ROOT` selects a
  workspace for every CLI. Demo scripts run in a throwaway workspace (repeatable, no
  install needed). Bell character only on a TTY.
- `inbox/*`, `*.egg-info`, build/tool caches git-ignored. pyproject metadata, ruff config,
  GitHub Actions CI (3.9/3.12/3.14), `CLAUDE.md`, `docs/THREAT-MODEL.md`.
- Docs corrected where they overclaimed or drifted (SOP, DEMOS, sales demos, README,
  RECOVERY); the 2026-07-07 audit moved to `docs/history/`; `ROADMAP.md` is the single
  forward list; `REVIEW.md` is the open-items list.

**Follow-ups from review**
- Supervisor rollback snapshots no longer hold `history/` bytes in memory (names only);
  rollback also removes history a blocked run created.
- A decision record that exists but does not verify is reported (`warnings` in the delta,
  `--status`, `review list`) instead of silently re-gating. A key file readable by
  group/other is refused. New `looping-box-review key` and `looping-box-review audit`.
- Drafts and `draft.md` carry `approved_review`, so a released item keeps its decision trail.
- `review approve` handles a closed terminal at the confirmation prompt.

**New commands**
- `looping-box` umbrella CLI: `run`, `status`, `review`, `worker`, `doctor`, `phase1`, `supervisor`
  (the `looping-box-*` scripts remain).
- `looping-box doctor`: read-only checks for config drift, stale locks, recovery state, pending and
  unverifiable reviews, quarantined deltas, key and `.env` permissions, enabled model roles, and
  audit-chain integrity, each with the remedy. Exits 1 only on a failed check.

- `looping-box init [DIR]` scaffolds a workspace from default config bundled in the package
  (`src/looping_box/templates/`, shipped as package data); it never overwrites an existing file.
  A test keeps this repo's `config/` identical to the templates. `looping-box run` in a
  directory with no config now says to run `init` instead of raising a traceback.

**Breaking:** decision records written before this release (unsigned `v1`) are no longer
honored; re-decide any pending items. Old unchained audit lines report as a chain break.

### Added

- Added worker contracts, `context_builder`, `execution_engine`, a one-shot
  supervisor, world-state tracking, review CLI commands, verifier outputs, and
  append-only audit logs for the local Phase 2-4 roadmap.
- Boundary reviews are now versioned, stable payloads under `staging/reviews/`,
  with `staging/pending_review.json` serving as a pending-review index.
- Approvals and rejections now suppress future gates for the same source path,
  content hash, and review reason set.
- Review-gated content is checked before global content-hash dedup, so a
  decision for one path no longer suppresses an identical risky file at a new
  path.
- Added `pyproject.toml` and console scripts:
  `looping-box-phase1`, `looping-box-worker`, `looping-box-supervisor`, and
  `looping-box-review`.

### Completed roadmap (Phases 1-4)

Capabilities below were previously tracked in `ROADMAP.md` and are now part of
the baseline. The roadmap retains only deferred, post-Phase-4 work.

- Phase 1 - Local ingestion: `./startday.sh` runs one deterministic pass;
  `config/sops/phase1_ingestion.json` drives route and boundary matching;
  `cache/state/phase1_state.json` records processed state; `cache/deltas/*.json`
  materializes run deltas; path containment fails closed for configured paths
  and symlink targets; `mtime_ns` removed in favor of SHA-256 content identity;
  deltas and review payloads have schema fixtures; operator recovery documented
  in `docs/RECOVERY.md`.
- Phase 2 - Worker loops: worker output contracts versioned in `docs/schemas/`
  with examples in `docs/contracts/`; runtime worker directories preserved with
  `.gitkeep`; `context_builder`
  consumes Phase 1 deltas once and emits a bounded context package;
  boundary-gated items become blocked context inputs; `execution_engine` reads
  only the context package and writes deterministic local drafts;
  `looping-box-worker <worker_id>` runs one pass; dry-run reports planned work
  without writes.
- Phase 3 - Supervisor: `.world_state.json` tracks registered loops, worker
  states, observed deltas, dependency edges, run history, leases, and recovery
  fields, failing closed on unknown schema versions; `config/super_loop.json`
  drives dependency routing and runtime limits; `looping-box-supervisor --once`
  discovers new deltas, plans and runs ready workers in dependency order, and
  persists state; reruns with no new work are idempotent no-ops;
  exclusive-create lock files prevent concurrent runs and recover stale locks;
  `looping-box-supervisor --status` prints a compact operator status;
  file-count, payload-size, and worker-runtime bounds route to blocked recovery
  payloads; decisions append audit events under `logs/transactions/`.
- Phase 4 - Boundary gate: action classes configured in
  `config/action_classes.json` with unknown classes defaulting to
  `review_required`; stable review payloads under `staging/reviews/` keyed by
  source path, content hash, and reasons, indexed by `staging/pending_review.json`
  and traceable to source hashes; explicit approval/rejection records under
  `staging/approvals/` and `staging/rejections/` that suppress future gates;
  `looping-box-review list|show|approve|reject` records decisions without
  executing outward actions; approvals run deterministic verifiers writing
  `cache/verifiers/`; terminal output surfaces pending review; review decisions
  append audit events.
- Quality gates upheld across phases: every implemented behavior has unit
  coverage; runtime artifacts are git-ignored while required directories persist
  in fresh clones; structured state writes are atomic; configured read/write
  paths are containment-checked under the repo root; external input is routed to
  review rather than executed; every loop runs once and stops with an idempotent
  no-op path; blocked states write actionable recovery payloads.

### Fixed

- Boundary gate is now enforcing rather than advisory: files that trip the gate
  are no longer recorded as processed, so they re-surface and regenerate
  `staging/pending_review.json` until a human records a decision, removes them,
  or defuses the triggering language.
- Distinct empty files are each ingested again — empty content is no longer
  registered in `processed_hashes` (it shares one hash, so only the first empty
  file used to be processed). Non-empty cross-path dedup is unchanged.
- Path containment is enforced: input/output paths that resolve outside the
  project root (via absolute paths or `..` traversal) are rejected with a clear
  `ValueError`. The repo boundary is part of the safety model, so ingestion is
  fail-closed rather than reaching arbitrary files on disk.
- Symlinks inside the input dir whose real target escapes the repo root are
  skipped during discovery (previously they were followed and ingested, since
  `is_file()`/read/hash all dereference symlinks).
- Symlinks inside the input dir are now skipped outright, including targets
  that resolve inside the repo root, so a renamed link cannot smuggle `.env`
  or other in-root files through an allowed extension.
- Deltas generated in the same second no longer clobber each other:
  `_unique_run_id` appends a numeric suffix when a delta file already exists.
- SOP routes missing a `label` are skipped instead of raising `KeyError`.
- Supervisor `payload_size_limit` and `worker_timeout` blocks now roll back
  worker state and artifacts:
  a worker call was persisting its own state (consumed inputs, last-seen hash)
  as a side effect before the supervisor decided to block on it, so a rerun
  saw no new work and silently cleared `operator_action_required` with the
  oversized/slow artifact still unresolved. Worker directories are now
  snapshotted before each run and restored when the run ends up blocked, so
  retries start from the same local state. Payload-size blocks retrip
  deterministically until the limit changes or output gets smaller; genuinely
  transient worker timeouts may clear on a later rerun.
- Removed `config/workers/context_builder.json` and
  `config/workers/execution_engine.json`: dead config never read by any code.
- Removed `docs/schemas/phase1_boundary_review.schema.json`: an unreferenced,
  byte-identical duplicate of `pending_review_index.schema.json`.
- `looping-box-supervisor --status` no longer tells the operator to "handle the source
  file" for resource-limit blocks (`file_count_limit`, `payload_size_limit`,
  `worker_timeout`) — there isn't one. It now points at `config/super_loop.json`
  and `looping-box-supervisor --once` instead. Content-review blocks are
  unaffected.
- `review approve` now refuses payloads classified as `forbidden`; they can
  still be rejected and audited.
- `context_builder` no longer spends a model call on a blocked context package,
  and its markdown artifact now includes model synthesis when one is produced.
- `supervisor` now requires `--once` for a run instead of treating the flag as
  decorative.
- Supervisor lock cleanup now checks the owning pid before unlinking, so an
  older process cannot remove a newer stolen lock.
- Model calls made by workers inherit `max_worker_runtime_seconds` as their
  transport timeout instead of always waiting up to 60 seconds.
- Malformed delta JSON is quarantined to `*.bad` so one corrupt delta does not
  permanently wedge `context_builder`.
- Empty Phase 1 scans no longer write no-op delta files. Deltas observed by the
  supervisor are archived under `cache/deltas/archive/`, and consumed/observed
  delta refs are pruned from runtime state.
- `execution_engine` batches multi-item model drafting into one model call and
  validates the JSON response before using it.
- Review payloads now update their embedded verifier block when a decision is
  recorded.
- Shared filesystem, JSON, hashing, pending-review-index, and decision-record
  helpers now live in `looping_box._util`; review index reads use the tolerant
  recovery behavior consistently.
- Documented process-lifetime `.env` caching and reserved world-state fields
  with `ponytail:` comments.
- Phase 1 run ids no longer collide with archived deltas: `_unique_run_id`
  checks `cache/deltas/archive/` too, and archive collision renames keep the
  `.json` extension (`name-2.json`, not `name.json.2`).
- `execution_engine` strips Markdown code fences before parsing batched model
  draft JSON.
- Git-ignored the new runtime artifacts: `cache/deltas/archive/*.json` and
  quarantined `cache/deltas/*.bad*` files.

### Tests

- Added `test_review_files_resurface_until_handled`,
  `test_distinct_empty_files_are_each_processed`,
  `test_input_outside_root_is_rejected`, `test_dotdot_traversal_is_rejected`,
  and `test_symlink_escaping_root_is_not_ingested`.
- Added `test_payload_size_limit_stays_blocked_until_config_is_raised` and
  `test_worker_timeout_stays_blocked_until_config_is_raised`.
- Added regression coverage for in-root symlinks, path-scoped review decisions,
  forbidden approval refusal, rollback artifact cleanup, blocked-package model
  gating, and context synthesis markdown.
- Added regression coverage for supervisor CLI gating, lock ownership cleanup,
  malformed-delta quarantine, empty-run delta suppression, delta archiving,
  batched execution drafts, verifier payload updates, configured model
  timeouts, and tolerant pending-index reads.
- Added schema-fixture coverage for `action_classes`, `review_record`,
  `verifier_result`, and `world_state` (previously untested against real
  generated output).
