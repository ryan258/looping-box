# Looping Box

Phase 1 of the Loop of Loops framework: a deterministic local ingestion loop
that treats the file system as the system boundary.

## Layout

- `config/sops/` contains human and machine-readable SOPs.
- `inbox/` is the local ingestion endpoint for `.md`, `.txt`, and `.json` inputs.
- `cache/state/` stores processed-content state.
- `cache/deltas/` stores structured delta JSON for each run.
- `staging/` stores `pending_review.json` when the boundary gate is tripped.

## Run

Place source files in `inbox/`, then run:

```sh
./startday.sh
```

The command prints the generated delta path and whether the boundary gate is
clear or pending review. Runtime state and deltas are ignored by git.

## Verify

```sh
python3 -m unittest tests/test_phase1.py
```

## Phase 1 Guarantees

- Identical content is not reprocessed across sequential runs.
- Worker context is limited to the SOP and the files being scanned.
- Outward-action language is staged for human review instead of executed.
- All generated outputs are materialized as local JSON files.

