# Operator Recovery

`supervisor --status` reports `operator action required` for two unrelated
reasons: content that tripped the boundary gate (below), or the supervisor's
own resource limits (see [Resource-Limit Blocks](#resource-limit-blocks)).
Check `state["recovery"]["blocked_reason"]` (or just read the `Next:` line —
it now says which kind you're looking at) before picking a fix.

## Clearing the Boundary Gate

When an inbox file contains outward-action language such as `deploy`, `commit`,
or `send`, Phase 1 trips the boundary gate:

- It writes a unique payload under `staging/reviews/`.
- It updates `staging/pending_review.json`, a stable index of pending payloads.
- The run reports `review=pending_review`.
- The triggering file is not marked processed, so it re-surfaces until handled.

Deleting `staging/pending_review.json` alone does not resume the loop. The next
run rebuilds the index from the same inbox file.

### Inspect Pending Reviews

```sh
PYTHONPATH=src python3 -m looping_box.review list
PYTHONPATH=src python3 -m looping_box.review show <review_id>
```

Approving or rejecting records the operator decision for that exact source path,
content hash, and review reason set. It does not execute the requested action.

```sh
PYTHONPATH=src python3 -m looping_box.review approve <review_id> --note "handled manually"
PYTHONPATH=src python3 -m looping_box.review reject <review_id> --note "not allowed"
```

Approvals run deterministic verifier checks and write `cache/verifiers/<id>.json`.

### Resume Ingestion

After recording the decision, rerun ingestion. The unchanged reviewed source item
is recorded as handled and will not recreate a pending review:

```sh
./startday.sh
```

You may also handle the source file listed in the review payload:

1. Approve and remove: you handled the request manually. Move or delete the
   source file from `inbox/`.
2. Defuse and re-ingest: edit the source file to remove the triggering language.
3. Reject: remove the source file from `inbox/`.

Archive or remove stale staging index files if you do not need them for audit:

```sh
mkdir -p staging/archive
mv staging/pending_review.json staging/archive/pending_review-$(date +%Y%m%dT%H%M%SZ).json
```

### Confirm Clear State

```sh
./startday.sh
PYTHONPATH=src python3 -m looping_box.supervisor --once
PYTHONPATH=src python3 -m looping_box.supervisor --status
```

The status should be clear. If it reports pending review again, another inbox
file still contains boundary-gate language or the reviewed file changed since
the decision was recorded.

## Resource-Limit Blocks

The supervisor also stops and requires operator action when a cycle exceeds a
bound in `config/super_loop.json`, independent of the boundary gate. It writes
`cache/supervisor/blocked.json` with a `reason` field:

- `file_count_limit` — the pending deltas contain more changed files than
  `max_files_per_cycle`. No worker ran; nothing to roll back.
- `payload_size_limit` — a worker produced an artifact larger than
  `max_payload_bytes`.
- `worker_timeout` — a worker ran longer than `max_worker_runtime_seconds`.

For the latter two, the worker already ran once. The supervisor rolls back
that worker's local state (`cache/workers/<id>/state.json`) so the *identical*
work is retried on the next `--once` instead of quietly being treated as
already done — the block persists across reruns until you act, same as
`file_count_limit`.

There is no source file to edit for any of these. Resolve by either raising
the matching limit in `config/super_loop.json`, or shrinking the batch (fewer
files in `inbox/` per run), then rerun:

```sh
PYTHONPATH=src python3 -m looping_box.supervisor --once
PYTHONPATH=src python3 -m looping_box.supervisor --status
```
