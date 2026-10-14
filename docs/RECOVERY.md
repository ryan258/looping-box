# Recovery — 0.2.0

Start with read-only evidence inspection in the intended workspace:

```sh
looping-box status
looping-box doctor
looping-box review list
looping-box report
```

Outside the workspace, add `--root DIR`; for review it must precede the
subcommand: `looping-box review --root DIR list`. Inspection failure means
unavailable/invalid evidence, not an empty queue. Preserve the original bytes
before deliberate repair or restoration. Diagnostics do not automatically repair.

## Choose the remedy from the reason

| Reason | Next action |
|---|---|
| Review pending | Inspect and decide, or deliberately edit/remove the source and ingest |
| `review_source_changed` / freshness block | Ingest the current source before retrying |
| Resource bound | Inspect retained work and adjust the matching limit deliberately |
| Model failure | Correct role/endpoint/key/provider availability, then retry |
| Routing disabled | Enable the required supported worker/edge, then retry |
| Invalid evidence or broken audit | Preserve and inspect; restore consistent known-good evidence |
| Busy | Wait for the current kernel-lock owner to finish |
| Unsupported schema | Use a compatible version or plan an explicit migration |
| `workspace_uninitialized` | Run `looping-box init DIR`; inspect any existing malformed config first |

## Review and source reconciliation

```sh
looping-box review show REVIEW_ID
looping-box review approve REVIEW_ID --note 'Reviewed for local drafting'
# Or:
looping-box review reject REVIEW_ID --note 'Exclude this source'
looping-box run
```

Approval asks for the exact word `approve` in an interactive terminal. A note
must contain non-whitespace text. Blocked classes additionally need
`--allow-blocked`; forbidden classes cannot be approved. Approval releases
eligible content into drafting only. It never executes the requested action.
Rejection does not require the typed confirmation. Noninteractive approval needs
the explicit environment override described in [README](../README.md).

A changed source or policy can invalidate an approval attempt; re-ingest and
inspect the current review. Deliberately removing or defusing a source clears its
pending reference on the next ingestion while retaining old evidence. Removal
also supersedes queued/carry-over content. Approval of already processed bytes
after policy re-gating does not force another draft; do not delete processed
state to manufacture replay without understanding the loss of history.

Do not delete `staging/pending_review.json` as a reset. Missing index plus retained
payloads is `review_state_unavailable`. Restore consistent evidence before retry;
a malformed referenced payload is not silently overwritten. Old unreferenced
payloads are historical when a valid index exists.

Use `looping-box review key` to inspect key location/usability without exposing
its contents. The default is `${XDG_CONFIG_HOME:-~/.config}/looping-box/review.key`;
`LOOPING_BOX_REVIEW_KEY` overrides it. Preserve the signing key when restoring
records. Lost/replaced keys can make decisions unverifiable. Do not assume a
record file's presence establishes a valid decision.

## Busy and interrupted runs

`.looping_box.lock` is a persistent kernel-lock file. Its existence, old timestamp,
or recorded PID does not establish active ownership; doctor probes the lock.
`stale_lock_seconds` is accepted for config compatibility and cannot steal it.
**Do not unlink or replace the lock file to clear a busy run.** Wait for the owner,
or deliberately stop that known process; process exit releases its descriptor.
Retry after ownership is released. Never infer ownership only from a stale PID.

Individual JSON/Markdown replacements are atomic, but a pass changes several
files. A crash between them is not generally transactional. Keep any inconsistent
artifacts and logs for inspection; do not label the run complete solely because
one output exists. Restore a consistent backup or use a new workspace with
intentional inputs if reconstruction is uncertain. State rollback can permit
replay, so it is an explicit recovery choice.

## Resource and model failures

`config/super_loop.json` defaults to 20 processable change entries, 65,536 payload
bytes, and 30 worker seconds. Held entries do not consume the file-count bound;
processable entries are counted before latest-path collapse. Payload/runtime
checks occur after worker return. A runtime block does not undo provider traffic
or charges. Local worker rollback is bounded; it is not a global transaction.

Inspect `.world_state.json`, `cache/supervisor/blocked.json`, and retained deltas/
context. Adjust the relevant positive bound only when the work is understood,
then retry `looping-box run`. Retained historical blocked payloads are not proof
of a current block; status consults current recovery and live pending evidence.
Successful recovery clears the active recovery state, while evidence is retained.

If a provider fails, successful consumption is not advanced. Correct model
settings and start a new command; `.env` loads once per root per process. A role
needs a nonempty API key and model ID. Changing the configured execution-model ID
invalidates execution work; context-builder/verifier model changes do not; there is no automatic retry/backoff or cost budget.

## Invalid deltas, audits, and retention

Normal workers quarantine malformed/unsupported deltas to collision-safe `.bad*`
files, preserving bytes. Mixed valid/invalid input can proceed with visible
nonfatal errors; invalid-only input fails. Dry run only reports the planned
quarantine and writes nothing:

```sh
looping-box worker context_builder --dry-run
looping-box review audit
```

Preserve quarantine evidence and diagnose its producer instead of renaming it
blindly into the active queue. Consumed valid deltas are archived with original
bytes; invalid ones are not acknowledged as consumed. Prior nonempty worker
outputs are retained in `cache/workers/WORKER/history/`.

Audit append refuses a broken chain. Report/audit inspect all available chain
bytes even with `--since`; a text note containing `BROKEN` is not itself a failure.
Missing logs mean no activity evidence, not proof of no past activity. Unanchored
chains cannot prove that valid suffixes or whole logs were never removed.

Retention is operator-managed. Before deliberate archival/removal, wait for all
mutators, preserve a consistent backup, and account for references among state,
reviews, decisions, and artifacts. Avoid blanket deletion of cache/staging/logs
in an active workspace. Use a fresh temporary workspace for experiments rather
than reset production evidence. No automatic migration/compaction is provided.
