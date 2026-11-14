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
inspect the current review. Verification checks the staged payload before an
optional model check and rechecks source, policy, and payload afterward. If
evidence changes during verification, the review stays pending and the edit is
preserved. If the staged payload changes while the terminal prompt is open,
inspect it and confirm again. When current policy changes an existing review,
ingestion stages the current evidence. A change to the gate reasons can give
unchanged input a new review ID. Ingestion retains superseded same-ID payloads
and records in `staging/archive/`. Inspect the current review before deciding.
Ingestion saves the queue delta before publishing its review payload or pending
index. Alternate queue directories used with shared `staging/` are recorded in
the default Phase 1 state, even when ingestion uses a separate `--state` file.
If an older pending payload lacks a matching ingestion record, restore the
corresponding Phase 1 delta from a consistent backup and retry. Keep registered
queue directories and their archives with the workspace backup. A successful
scan reconciles only the input directory it inspected. Restore
access to an unreadable directory and retry the scan before acting on its holds.
Deliberately removing or defusing a source clears its pending reference on the
next ingestion and preserves its history. Removal also supersedes queued work.
A fresh approval can resume a previously released item that had yet to draft,
even if the earlier release was queued in a different registered directory.
To deliver an undrafted release to the supervisor after an alternate-directory
scan, run `looping-box phase1 --sop PATH` with the same SOP, input and state
settings and omit `--delta-dir`. Then run `looping-box supervisor --once`.
Previously drafted bytes stay deduplicated.
If a newer signed rejection supersedes an older approval for the same source,
ingestion reopens the review before it can release those bytes. Inspect the
current review and make a fresh decision. When an older workspace has a signed
rejection whose original queue cannot be found, workers withhold the affected
source while its release order is unproven.

`review_retirement_incomplete` means the current review and its history were
saved. Resolve the reported filesystem error and retry ingestion to retire the
superseded record.

Do not delete `staging/pending_review.json` as a reset. Missing index plus retained
payloads is `review_state_unavailable`. Restore the index from consistent evidence
or run an ingestion that covers its unresolved reviews. A scan that covers only
part of the queue preserves the unavailable status. Old unreferenced payloads
are historical when a valid index exists.

Use `looping-box review key` to inspect key location/usability without exposing
its contents. The default is `${XDG_CONFIG_HOME:-~/.config}/looping-box/review.key`;
`LOOPING_BOX_REVIEW_KEY` overrides it. Preserve the signing key when restoring
records. Lost/replaced keys can make decisions unverifiable. Do not assume a
record file's presence establishes a valid decision.

`review_decision_recorded_incomplete` means the signed decision was saved and a
later audit or pending-index write failed. Preserve the record, inspect
`looping-box review audit`, and resolve the reported write error. The next
ingestion reconciles the pending index from the valid decision. Repeating the
same decision does not repair a missing audit event. New audit entries include
a digest of the signed decision so a prior approval of the same payload cannot
satisfy the current decision's audit check. `review audit`, `report`, and
`doctor` flag a missing entry while its decision record is retained, including
after the record moves to the review archive. A fresh decision for the same ID
archives the prior evidence before replacing it. Keep the archived records and
their audit log together when restoring a backup.

## Busy and interrupted runs

`.looping_box.lock` is a persistent kernel-lock file. Its existence, old timestamp,
or recorded PID does not establish active ownership; doctor probes the lock.
`stale_lock_seconds` is accepted for config compatibility and cannot steal it.
The lock and audit logs must each be regular files with a single filesystem
link. Copy them when making an offline backup.
**Do not unlink or replace the lock file to clear a busy run.** Wait for the owner,
or deliberately stop that known process; process exit releases its descriptor.
Retry after ownership is released. Never infer ownership only from a stale PID.

On its first pass, the supervisor saves a recovery marker before starting
workers. If a write interrupts that pass, resolve the reported write error and
retry supervision. Work that reached its saved consumption marker can be
reconciled without rerunning the worker. A normal `looping-box run` retry keeps
the saved draft when ingestion confirms the same work, including repeated
unchanged holds. If a draft write was interrupted and the committed draft is
still current, a retry can restore the exact JSON from history and regenerate
its Markdown without another model request. `state_inconsistent` means the
matching committed draft could not be recovered from local evidence. Preserve
the existing artifacts and restore them from a consistent backup.

Phase 1 retains the recorded order of scans from different queues that share a
timestamp. If older state has lost that order, a fresh scan that confirms the
same inputs can retire a context tied to an already committed draft. The draft
and its source context remain on disk, and that context is retired from execution.

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
Files named `*-draft.incomplete.json` retain partial draft evidence for
inspection. Reapproval deduplicates against completed drafts.

Ingestion, supervision, and review check their audit destination and existing
chain before changing workspace evidence. If the audit append fails after
ingestion or supervision has saved local work, the command reports
`audit_incomplete`. Preserve the saved artifacts and inspect the named audit
log before retrying. A retry can continue from recorded progress. The missing
audit event may still need investigation.
`looping-box run` runs ingestion first. If supervision stops on its audit
check, the saved ingestion delta remains queued for the next supervisor pass.
Report/audit inspect all available chain bytes even with `--since`; a text note
containing `BROKEN` is ordinary log content.
Missing logs mean no activity evidence, not proof of no past activity. Unanchored
chains cannot prove that valid suffixes or whole logs were never removed.

Retention is operator-managed. Before deliberate archival/removal, wait for all
mutators, preserve a consistent backup, and account for references among state,
reviews, decisions, and artifacts. Avoid blanket deletion of cache/staging/logs
in an active workspace. Use a fresh temporary workspace for experiments rather
than reset production evidence. No automatic migration/compaction is provided.
