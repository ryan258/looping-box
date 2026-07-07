# Looping Box

Local phases of the Loop of Loops framework: deterministic ingestion, worker
routing, supervision, and human review with the file system as the boundary.

## Layout

- `config/sops/` contains human and machine-readable SOPs.
- `config/super_loop.json` drives supervisor worker routing and cycle limits
  (`max_files_per_cycle`, `max_payload_bytes`, `max_worker_runtime_seconds`).
- `config/action_classes.json` maps review reasons to an action class
  (`safe_local_transform`, `review_required`, `blocked`, `forbidden`).
- `inbox/` is the local ingestion endpoint for `.md`, `.txt`, and `.json` inputs.
- `cache/state/` stores processed-content state.
- `cache/deltas/` stores active delta JSON; observed deltas move to
  `cache/deltas/archive/`.
- `cache/workers/` stores worker state and generated local artifacts.
- `cache/verifiers/` stores deterministic review-verifier results.
- `cache/supervisor/` stores the blocked-cycle payload when a supervisor
  resource limit trips.
- `logs/transactions/` stores append-only audit events.
- `staging/` stores `pending_review.json` plus review decision records when the
  boundary gate is tripped.
- `.world_state.json` (repo root, git-ignored) tracks supervisor run history,
  worker states, and recovery status; read via `looping-box-supervisor --status`.

  See [docs/RECOVERY.md](docs/RECOVERY.md) for how to clear a pending review
  or a resource-limit block and resume.

## Run

Install the local console scripts once:

```sh
python3 -m pip install -e .
```

Place source files in `inbox/`, then run:

```sh
./startday.sh
```

The command prints the generated delta path and whether the boundary gate is
clear or pending review. Runtime state and deltas are ignored by git.

Run the worker/supervisor loop:

```sh
looping-box-supervisor --once
looping-box-supervisor --status
```

Inspect or record review decisions:

```sh
looping-box-review list
looping-box-review show <review_id>
looping-box-review approve <review_id> --note "handled manually"
looping-box-review reject <review_id> --note "not allowed"
```

## Verify

```sh
python3 -m unittest discover -s tests
```

## Model Access (optional)

Every role (`context_builder`, `execution_engine`, `verifier`) runs
deterministically offline by default. To turn on AI assistance for a role,
copy `.env.example` to `.env` and set `OPENROUTER_API_KEY` plus a
`MODEL_<ROLE>` (e.g. `MODEL_EXECUTION_ENGINE`). A role with no key or no model
configured keeps its offline path — the test suite and demos never hit the
network. `.env` is git-ignored.

## Guarantees

- Identical content is not reprocessed across sequential runs.
- Worker context is limited to the SOP and the files being scanned.
- Outward-action language is staged for human review instead of executed.
- All generated outputs are materialized as local JSON files.
- Workers communicate through file artifacts only.
- Approvals and rejections are explicit local records, not inferred from content.
- Deterministic supervisor resource-limit blocks (file count and payload size)
  persist across reruns until the operator raises the limit or shrinks the
  batch. Worker-runtime blocks roll back local worker output and retry the same
  work; a genuinely transient timeout can clear on a later rerun.
