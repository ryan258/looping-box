# Looping Box

Looping Box turns local inbox files into traceable drafts in one pass. Recognized
outward-action language is held for review. The two workers build context and
write drafts; neither executes instructions in source files or model responses.
It runs offline by default, using the Python standard library.

**0.2.0 is a local release candidate under acceptance, not a published or fully
verified release.** Supported target: Python 3.9–3.14 on macOS or Linux, one
operator, local disk. Windows and network filesystems are unsupported. See
[review status](REVIEW.md) for remaining acceptance work and
[the threat model](docs/THREAT-MODEL.md) for limits.

## Start offline

From this checkout, install locally, then create a separate workspace:

```sh
python3 -m pip install .
looping-box init ~/looping-workspace
cd ~/looping-workspace
printf '%s' 'Project docs: summarize the backlog.' > inbox/notes.txt
looping-box run
looping-box status
looping-box doctor
looping-box report
```

No key or model is needed for drafting offline. The resulting draft is
`cache/workers/execution_engine/draft.md`, with provenance in the adjacent JSON.
Offline drafts copy compacted source excerpts; they are not generated summaries.
Read [the walkthrough](docs/DEMOS.md) for a clean, held, and removal example.

For source development, `python3 -m pip install -e .` is also supported. Without
installation, the checkout's `./startday.sh` uses its `src/` package. A local wheel
can be built with `python3 -m pip wheel --no-deps . --wheel-dir dist`, then installed
by its filename. Default configuration and runtime schemas are package data;
a checkout is not intended to be required after installation. Installed-wheel
acceptance and the platform matrix remain separate verification gates.

## Commands and roots

`looping-box --help` lists commands. `python -m looping_box` uses the same CLI.
The legacy `looping-box-phase1`, `looping-box-supervisor`, `looping-box-worker`,
and `looping-box-review` entry points remain supported.

| Command | Purpose |
|---|---|
| `init [DIR]` | Create missing scaffold; keep existing files |
| `run [--root R]` | Default ingestion followed by one supervisor pass |
| `status [--root R]` | Inspect saved recovery and live review evidence |
| `phase1 [--root R]` | Ingest only; accepts contained custom paths |
| `supervisor --once [--root R]` | Run the workers once |
| `supervisor --status [--root R]` | Inspect only; wins if both flags occur |
| `worker context_builder --dry-run [--root R]` | Plan context work without writes or model calls |
| `worker execution_engine [--root R]` | Write drafts from eligible current context |
| `review [--root R] list` | List current reviews |
| `review [--root R] show ID` | Inspect one current review |
| `review [--root R] approve ID --note TEXT` | Confirm a signed release into drafting |
| `review [--root R] reject ID --note TEXT` | Record a signed rejection |
| `review [--root R] key` / `audit` | Inspect key usability / audit chains |
| `doctor [--root R]` | Read-only local checks and remedies |
| `report [--root R] [--since PREFIX]` | Markdown activity and integrity report |

Root precedence is **explicit `--root` → `LOOPING_BOX_ROOT` → cwd**. The selected
root expands `~` and resolves aliases; a user-selected root symlink is allowed.
Symlink descendants used for internal artifacts are rejected, even when they
point inside the root. Input symlinks are skipped.

Two deliberate defaults differ: `init [DIR]` uses its positional directory or
cwd, ignoring `LOOPING_BOX_ROOT`; `startday.sh` uses explicit `--root`, then
`LOOPING_BOX_ROOT`, then the script's directory. The wrapper prefers checkout
source and performs `run`. For review, put the root **before** the subcommand:
`looping-box review --root ~/looping-workspace list`.

`run` and `startday.sh` reject `--input-dir`, `--sop`, `--state`, `--delta-dir`,
and `--staging-dir` with usage exit 2 before work. Those flags remain available
on `phase1` only, with paths contained under the selected root.

| Operation | 0 | 1 | 2 |
|---|---|---|---|
| Run, supervisor once, worker, wrapper | Complete or idle | Failed or busy | Blocked or usage error |
| Phase 1 | Ingested, including held items | Failed or busy | Usage error |
| Status, review list/show/key | Inspection succeeded, including blocked status | Invalid/unavailable evidence or unknown ID | Usage error |
| Approve/reject | Decision recorded | Declined, verification failed, invalid evidence, or busy | Usage error |
| Doctor | No failures; warnings allowed | Failure findings | Usage error |
| Report/audit | Available chains valid | Broken chain or invalid/unreadable evidence | Usage error |
| Init | Scaffold ready | Invalid path/config or I/O failure | Usage error |

An unknown command and a supervisor invocation without `--once` or `--status`
are usage errors. Report date prefixes accept `YYYY`, `YYYY-MM`, `YYYY-MM-DD`,
or a full UTC timestamp such as `2026-10-03T12:00:00Z`. Filtering applies to event
rows, not current pending counts or whole-log integrity. Counts describe events
and change entries, not unique files.

## Configuration and processing

`init` creates `inbox/`, configuration, cache, staging, and log directories from
bundled templates. It never overwrites existing files; malformed existing config
or a file/directory conflict fails. Missing ingestion SOP is
`workspace_uninitialized`. Missing optional action-class and supervisor config
use shipped defaults; malformed present files fail `invalid_config`. Unknown
configuration keys are errors. See [schema documents](docs/schemas/) and the
[ingestion SOP](config/sops/phase1_ingestion.md).

| Setting | Shipped default |
|---|---|
| Supported extensions | `.md`, `.txt`, `.json` (case-insensitive) |
| `max_excerpt_chars` | 500 |
| `max_file_bytes` | 1,000,000 |
| `max_files_per_cycle` | 20 processable change entries before collapse |
| `max_payload_bytes` | 65,536 |
| `max_worker_runtime_seconds` | 30 |
| `stale_lock_seconds` | 300; deprecated compatibility field, never permits lock stealing |
| Workers / routing | `phase1_delta → context_builder → execution_engine` |

Routing matches documentation, task-backlog, and context keywords. The gate
matches configured content terms before deduplication. It normalizes selected
Unicode look-alikes, spacing, underscores, and simple inflections; unlisted
language can evade it. Every gate term must have one explicit action-class
mapping in `config/action_classes.json`. An empty gate-keyword list is valid but doctor
warns. Empty worker/edge lists disable routing; eligible work then remains blocked.

Within the file cap, hashing and decoding use one bounded byte snapshot.
Oversized inputs are **streamed for hashing**, held as `oversized_input`, and
receive empty routes/excerpts. Every scan with supported inputs writes a delta,
even if all entries are skipped. Removing a previously tracked source emits a
removal-only delta; a later empty scan with no removals writes state but no delta.
Latest held or removed entries suppress older queued content. Workers recheck
source/policy freshness and preserve undrafted eligible carry-over.

## Reviews and optional models

```sh
looping-box review list
looping-box review show REVIEW_ID
looping-box review approve REVIEW_ID --note 'Reviewed for local drafting'
# Or decline the item:
looping-box review reject REVIEW_ID --note 'Do not include this source'
looping-box run
```

Approval normally requires a terminal and typing `approve`. Notes must be
nonblank. `blocked` requires `--allow-blocked`; `forbidden` cannot be approved.
Approval rechecks current source and policy, and signs a record bound to the
review payload hash. It permits local drafting, not the action described by the
input. Already processed bytes are not forcibly redrafted by a later approval;
editing bytes creates a new identity. General exactly-once delivery is not promised.

The signing key is `LOOPING_BOX_REVIEW_KEY`, or
`${XDG_CONFIG_HOME:-~/.config}/looping-box/review.key`; a missing file is created
with mode `0600` on the first decision. Empty/unreadable or group/other-accessible
key files are refused. `review key` does not print key bytes. Attribution uses
`LOOPING_BOX_APPROVER` or the login name; it is not authenticated identity.
`LOOPING_BOX_ALLOW_NONINTERACTIVE` accepts only trimmed, case-insensitive
`1`, `true`, `yes`, or `on`; use deliberately, because it bypasses terminal friction.

Copy `.env.example` to `.env` only if using optional models, and make it private
with `chmod 600 .env`. The example key and all three model IDs are blank. A role
needs both `OPENROUTER_API_KEY` and its ID (`MODEL_CONTEXT_BUILDER`,
`MODEL_EXECUTION_ENGINE`, or `MODEL_VERIFIER`). The default endpoint is
`https://openrouter.ai/api/v1`; a configured base URL must use HTTP(S).
Process values override `.env`; only `OPENROUTER_*` and `MODEL_*` load from that
file, once per resolved root per process. Changes to the configured execution-model ID invalidate execution work;
context-builder/verifier model changes and key changes alone do not.

Enabled roles send excerpts/context or review content to the configured endpoint.
There is no automatic secret redaction. Provider requests may incur charges.
The optional verifier is advisory and can add a block; it cannot override failed
deterministic checks. Prompts are not a complete injection defense.

## Evidence and recovery

State, deltas, context/drafts, review payloads/decisions, and JSONL audit logs are
ordinary local files. Prior nonempty worker outputs are kept under each worker's
`history/`; consumed valid deltas move to `cache/deltas/archive/`. Invalid deltas
are retained under `.bad*` names. Dry run reports proposed actions without making
those changes. Doctor does not call a provider or repair evidence.

Mutators use a nonblocking POSIX kernel lock. `.looping_box.lock` stays on disk;
its presence/age does not imply ownership. Do not unlink it to clear a busy run.
Writes replace individual files atomically; there is no general multi-file
transaction. Preserve inconsistent evidence and follow
[recovery instructions](docs/RECOVERY.md). Audit chains have no external anchor
and cannot detect complete rewriting or deletion of valid suffixes/all logs.

Local verification commands for this candidate:

```sh
python3 -m unittest discover -s tests
ruff check src tests
# For installed wheel verification outside checkout:
# Set LOOPING_BOX_TEST_CLI to the absolute path of the installed looping-box executable
python3 -m unittest discover -s tests -p 'test_package_acceptance.py'
```

These commands confirm local offline acceptance. They do not establish full platform-matrix,
live-provider, draft-quality, or published release status. See [REVIEW.md](REVIEW.md)
and [ROADMAP.md](ROADMAP.md). Publication and a license decision are separate owner
actions, not requirements for this private local candidate.
