# Looping Box

Local phases of the Loop of Loops framework: deterministic ingestion, worker
routing, supervision, and human review with the file system as the boundary.

## Layout

- `config/sops/` contains human and machine-readable SOPs.
- `inbox/` is the local ingestion endpoint for `.md`, `.txt`, and `.json` inputs.
- `cache/state/` stores processed-content state.
- `cache/deltas/` stores structured delta JSON for each run.
- `cache/workers/` stores worker state and generated local artifacts.
- `cache/verifiers/` stores deterministic review-verifier results.
- `logs/transactions/` stores append-only audit events.
- `staging/` stores `pending_review.json` plus review decision records when the
  boundary gate is tripped.
  See [docs/RECOVERY.md](docs/RECOVERY.md) for how to clear it and resume.

## Run

Place source files in `inbox/`, then run:

```sh
./startday.sh
```

The command prints the generated delta path and whether the boundary gate is
clear or pending review. Runtime state and deltas are ignored by git.

Run the worker/supervisor loop:

```sh
PYTHONPATH=src python3 -m looping_box.supervisor --once
PYTHONPATH=src python3 -m looping_box.supervisor --status
```

Inspect or record review decisions:

```sh
PYTHONPATH=src python3 -m looping_box.review list
PYTHONPATH=src python3 -m looping_box.review show <review_id>
PYTHONPATH=src python3 -m looping_box.review approve <review_id> --note "handled manually"
PYTHONPATH=src python3 -m looping_box.review reject <review_id> --note "not allowed"
```

## Verify

```sh
python3 -m unittest discover -s tests
```

## Phase 1 Guarantees

- Identical content is not reprocessed across sequential runs.
- Worker context is limited to the SOP and the files being scanned.
- Outward-action language is staged for human review instead of executed.
- All generated outputs are materialized as local JSON files.
- Workers communicate through file artifacts only.
- Approvals and rejections are explicit local records, not inferred from content.
