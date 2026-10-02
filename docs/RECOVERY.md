# Operator Recovery

`looping-box-supervisor --status` reports `operator action required` (and `--once` exits
`2`) for two unrelated
reasons: content that tripped the boundary gate (below), or the supervisor's
own resource limits (see [Resource-Limit Blocks](#resource-limit-blocks)).
Check `state["recovery"]["blocked_reason"]` (or just read the `Next:` line —
it now says which kind you're looking at) before picking a fix.

Commands below assume the local scripts were installed with
`python3 -m pip install -e .`.

## Clearing the Boundary Gate

When an inbox file contains outward-action language (`deploy`, `commit`, `send`, ...)
or exceeds `max_file_bytes`, phase 1 trips the boundary gate:

- It writes a payload under `staging/reviews/` and updates `staging/pending_review.json`.
- The run reports `review=pending_review`; `looping-box-supervisor --status` shows
  `pending reviews: N` immediately (it reads live review state).
- The file is not marked processed, so it re-surfaces every run until decided.
- Clean files in the same batch are drafted normally; only the held item waits.

Deleting `staging/pending_review.json` does not resume anything: the next run
rebuilds the index from the same inbox file.

### Decide

```sh
looping-box-review list
looping-box-review show <review_id>
looping-box-review approve <review_id> --note "why"    # add --allow-blocked for blocked-class items
looping-box-review reject  <review_id> --note "why"
```

Approving needs an interactive terminal (you confirm by typing `approve`;
`LOOPING_BOX_ALLOW_NONINTERACTIVE` must be an explicit `1`/`true`/`yes`/`on`).
Approving **releases the item once** into the pipeline as ordinary work; it does
not perform the requested action. Rejecting only records the decision. Decisions
are HMAC-signed (see README); a record that was hand-written, edited, or signed
with a different key is ignored and the item is gated again.

Approvals run the verifier checks (`cache/verifiers/<id>.json`): payload integrity
always, plus an optional model check when `MODEL_VERIFIER` is set.

### Resume

```sh
./startday.sh
```

An approved item shows up once as a change; a rejected one is skipped as
`review_decision_recorded`. You can also remove the source file from `inbox/`, or
edit it to remove the triggering language and re-ingest: the next run drops its
pending review.

### Confirm clear state

```sh
looping-box-supervisor --status
```

If it reports pending review again, another inbox file contains gate language, the
reviewed file changed since the decision (a changed file is a new review), or the
signing key differs from the one that signed the decision.

### Audit log integrity

`logs/transactions/*.jsonl` are hash-chained. Check them with:

```sh
looping-box review audit
# or: looping-box report (includes activity summary + audit integrity)
```

It prints `ok` (or `intact`) or `BROKEN at line N` per log (exit 1 if any is broken). Lines
written before chaining existed report as a break at line 1: archive the old file
and start fresh.

### "I approved it but it keeps coming back"

If `review list` or `--status` says a decision record "did not verify", the record
is present but not trusted: it was edited, or it was signed with a different key
(another shell, `XDG_CONFIG_HOME`, cron, or a changed `LOOPING_BOX_REVIEW_KEY`). Run
`looping-box-review key` to see which key is in use. Fix the environment so the same
key is used everywhere, or simply approve the item again.

## Resource-Limit Blocks

The supervisor also stops and requires operator action when a cycle exceeds a
bound in `config/super_loop.json`, independent of the boundary gate. It writes
`cache/supervisor/blocked.json` with a `reason` field:

- `file_count_limit` — the pending deltas contain more changed files than
  `max_files_per_cycle`. No worker ran; nothing to roll back.
- `payload_size_limit` — a worker produced an artifact larger than
  `max_payload_bytes`.
- `worker_timeout` — a worker ran longer than `max_worker_runtime_seconds`.

For `payload_size_limit`, the worker already ran once. The supervisor rolls
back that worker's local state and artifacts (`cache/workers/<id>/`) so the
*identical* work is retried on the next `--once` instead of quietly being
treated as already done.

For `worker_timeout`, the same rollback happens before retry. If the timeout
was a transient slow run, a later rerun can clear it; repeated timeouts mean
you should raise `max_worker_runtime_seconds` or reduce the work in the cycle.

There is no source file to edit for any of these, and removing files from `inbox/`
does not help: the files were already ingested and their delta is kept, so the work
runs once you raise the matching limit in `config/super_loop.json`. (Fewer files per
run only prevents the next block.) Items held for review do not count toward
`max_files_per_cycle`. Then rerun:

```sh
looping-box-supervisor --once
looping-box-supervisor --status
```

## Malformed Delta Quarantine

If `context_builder` finds unreadable JSON in `cache/deltas/`, it renames the
bad file to `*.bad`, reports `malformed_delta_quarantined` in its worker output,
and continues with any valid deltas. Inspect the `.bad` file if you need the
corrupt payload for audit; otherwise it is safe to archive or delete after the
good deltas have been processed.

## Stuck Lock

phase 1, the supervisor, review decisions, and bare worker passes share
`.looping_box.lock`. A second caller gets `busy: active supervisor lock`. A lock
older than `stale_lock_seconds` (default 300, in `config/super_loop.json`) is
recovered automatically, including an unreadable lock (aged by file mtime). If
you are certain nothing is running, delete `.looping_box.lock`.

## Model Outage

A model error during `execution_engine` or `context_builder` shows as
`status: failed` (exit code 1) with the error. Just rerun `./startday.sh`: the failed
step is retried. If new files arrive before the retry, the earlier undrafted batch is
carried into the new context package, so nothing is dropped.
