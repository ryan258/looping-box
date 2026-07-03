# Changelog

All notable changes to this project are documented here.

## [Unreleased]

### Added

- Added worker contracts, `context_builder`, `execution_engine`, a one-shot
  supervisor, world-state tracking, review CLI commands, verifier outputs, and
  append-only audit logs for the local Phase 2-4 roadmap.
- Boundary reviews are now versioned, stable payloads under `staging/reviews/`,
  with `staging/pending_review.json` serving as a pending-review index.
- Approvals and rejections now suppress future gates for the same source path,
  content hash, and review reason set.

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
  `python3 -m looping_box.worker <worker_id>` runs one pass; dry-run reports
  planned work without writes.
- Phase 3 - Supervisor: `.world_state.json` tracks registered loops, worker
  states, observed deltas, dependency edges, run history, leases, and recovery
  fields, failing closed on unknown schema versions; `config/super_loop.json`
  drives dependency routing and runtime limits; `supervisor --once` discovers
  new deltas, plans and runs ready workers in dependency order, and persists
  state; reruns with no new work are idempotent no-ops; exclusive-create lock
  files prevent concurrent runs and recover stale locks; `supervisor --status`
  prints a compact operator status; file-count, payload-size, and worker-runtime
  bounds route to blocked recovery payloads; decisions append audit events under
  `logs/transactions/`.
- Phase 4 - Boundary gate: action classes configured in
  `config/action_classes.json` with unknown classes defaulting to
  `review_required`; stable review payloads under `staging/reviews/` keyed by
  source path, content hash, and reasons, indexed by `staging/pending_review.json`
  and traceable to source hashes; explicit approval/rejection records under
  `staging/approvals/` and `staging/rejections/` that suppress future gates;
  `review list|show|approve|reject` records decisions without executing outward
  actions; approvals run deterministic verifiers writing `cache/verifiers/`;
  terminal output surfaces pending review; review decisions append audit events.
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
- Deltas generated in the same second no longer clobber each other:
  `_unique_run_id` appends a numeric suffix when a delta file already exists.
- SOP routes missing a `label` are skipped instead of raising `KeyError`.
- Supervisor `payload_size_limit` and `worker_timeout` blocks are now durable:
  a worker call was persisting its own state (consumed inputs, last-seen hash)
  as a side effect before the supervisor decided to block on it, so a rerun
  saw no new work and silently cleared `operator_action_required` with the
  oversized/slow artifact still unresolved. Worker state is now snapshotted
  before each run and restored when the run ends up blocked, so the exact
  same work is retried, and stays blocked, until the operator actually raises
  the limit (matching `file_count_limit`, which was never affected since it
  blocks before any worker runs).
- Removed `config/workers/context_builder.json` and
  `config/workers/execution_engine.json`: dead config never read by any code.
- Removed `docs/schemas/phase1_boundary_review.schema.json`: an unreferenced,
  byte-identical duplicate of `pending_review_index.schema.json`.
- `supervisor --status` no longer tells the operator to "handle the source
  file" for resource-limit blocks (`file_count_limit`, `payload_size_limit`,
  `worker_timeout`) — there isn't one. It now points at `config/super_loop.json`
  instead. Content-review blocks are unaffected.

### Tests

- Added `test_review_files_resurface_until_handled`,
  `test_distinct_empty_files_are_each_processed`,
  `test_input_outside_root_is_rejected`, `test_dotdot_traversal_is_rejected`,
  and `test_symlink_escaping_root_is_not_ingested`.
- Added `test_payload_size_limit_stays_blocked_until_config_is_raised` and
  `test_worker_timeout_stays_blocked_until_config_is_raised`.
- Added schema-fixture coverage for `action_classes`, `review_record`,
  `verifier_result`, and `world_state` (previously untested against real
  generated output).
