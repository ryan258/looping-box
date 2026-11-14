# Looping Box

<img src="docs/brand/assets/app-icon.svg" width="32" height="32" alt=""> **Traceable drafts from local files**

*Make a draft. Keep the trail.*

Looping Box is a local drafting CLI. Put Markdown, text, or JSON files in a
workspace inbox and run one pass. Open the saved Markdown draft to see the
source paths, with provenance in the adjacent JSON. The default workflow runs
offline and assembles short excerpts from your files.

Files that match configured outward-action terms such as `deploy` or `send`
are held for review. **Approved for drafting** means a signed decision makes
that source eligible for a later local draft. Sending, publishing, and deploying
happen separately in the operator's own tools.

**0.2.0 is a private local release candidate under acceptance.** The target is
Python 3.9–3.14 on macOS or Linux, with one operator and local disk. See
[review status](REVIEW.md) for remaining acceptance work and
[the threat model](docs/THREAT-MODEL.md) for limits.

[Try a runnable example](docs/50-offline-examples.md) · [Walk through the demos](docs/sales-demos.md) · [Open the brand and marketing kit](docs/brand/README.md)

## Start offline

From this checkout, create a virtual environment with a supported Python
version. This example uses Python 3.14 and starts a fresh workspace.

```sh
python3.14 -m venv .venv
. .venv/bin/activate
python -m pip install .

(
  demo_root="$(mktemp -d)" || exit 1
  export OPENROUTER_API_KEY= MODEL_CONTEXT_BUILDER= MODEL_EXECUTION_ENGINE= MODEL_VERIFIER=
  export XDG_CONFIG_HOME="$demo_root/private-config"
  unset OPENROUTER_BASE_URL LOOPING_BOX_REVIEW_KEY LOOPING_BOX_ALLOW_NONINTERACTIVE
  looping-box init "$demo_root" || exit 1
  printf '%s\n' 'Project docs: summarize the backlog.' > "$demo_root/inbox/notes.txt"
  looping-box run --root "$demo_root" || exit 1
  cat "$demo_root/cache/workers/execution_engine/draft.md" || exit 1
  looping-box status --root "$demo_root"
  looping-box doctor --root "$demo_root"
  looping-box report --root "$demo_root"
  printf '\nDemo workspace: %s\n' "$demo_root"
)
```

The offline run needs no model or API key. The draft shows `inbox/notes.txt`
and its compacted excerpt. The adjacent `draft.json` carries provenance. The
walkthrough covers a clean pass, a held item, and a source removal.
[Offline walkthrough](docs/DEMOS.md#a-clean-offline-pass)

Installation may fetch build tooling. The runtime uses the standard library.
For source development, `python -m pip install -e .` in the virtual environment
is also supported. The checkout's `./startday.sh` uses its `src/` package. A local
wheel can be built with `python -m pip wheel --no-deps . --wheel-dir dist`, then installed
by its filename. Default configuration and runtime schemas are package data;
a checkout is not intended to be required after installation. Installed-wheel
acceptance and the platform matrix remain separate verification gates.

## Try an example from the checkout

The 50 runnable examples each start with synthetic files in a fresh workspace.
List the examples, run the zero-value configuration fixture, or open the review
fixture in an offline follow-up shell. [Example catalog](docs/50-offline-examples.md)

```sh
python3.14 demos/examples.py
python3.14 demos/examples.py 17
python3.14 demos/examples.py 45 --shell
```

The example launcher runs offline by default. Add `--free` to use the existing
OpenRouter key in the checkout's `.env`. For the first draft, the launcher
sends eligible sample excerpts to `openrouter/free`. Review stays local, and
the example shell starts offline. [Free model walkthrough](docs/50-offline-examples.md#try-a-free-openrouter-draft)

```sh
python3.14 demos/examples.py 22 --free
```

Each `--free` run records its outcome in the Git-ignored
`logs/openrouter-examples/` folder. The record holds a snapshot of the draft and
its provenance when they were saved, along with any returned model, token counts,
and cost OpenRouter reports. The two `--runs` commands read local files, either
listing saved runs or opening the latest run for the given example.
[Saved runs guide](docs/50-offline-examples.md#review-saved-free-runs)

```sh
python3.14 demos/examples.py --runs
python3.14 demos/examples.py --runs 22
```

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
| `review [--root R] approve ID --note TEXT` | Record signed approval for drafting |
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
Use `supervisor --once` after ingesting with a custom SOP. Review and workers
check the current selected SOP before using its excerpts. The workspace's
gate keywords and smaller input byte limit still apply.

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
If a newer scan supersedes saved context, rebuild it before running execution
directly. A scan that only confirms unchanged work keeps the current draft in
place.

## Reviews and optional models

**Approved for drafting.** Inspect the pending item and choose one decision.
Replace `REVIEW_ID` with the ID from the list.

```sh
looping-box review list
looping-box review show REVIEW_ID
looping-box review approve REVIEW_ID --note 'Reviewed for local drafting'
```

Read the prompt, then type `approve` to confirm. This example uses a sample
review ID and workspace path.

```text
Approve review-3a3aada822f63534 [review_required: deploy]?
Approval makes the item eligible for local drafting only.
Type 'approve': approve
approved: review-3a3aada822f63534
Item approved for local drafting. With default ingestion settings, run this next.
looping-box run --root /private/tmp/looping-box-demo
For custom Phase 1 ingestion, repeat your input, SOP, and state settings and omit --delta-dir.
Then run
looping-box supervisor --once --root /private/tmp/looping-box-demo
```

With default ingestion settings, copy the `looping-box run --root ...` command
from your own terminal. It includes the workspace you approved in. The
[interactive offline walkthrough](docs/DEMOS.md#a-held-item-with-a-real-human-decision)
shows how to check the resulting draft.

To reject the item, use `looping-box review reject REVIEW_ID --note 'Do not include this source'`.
Approval normally requires an interactive terminal. Notes must be nonblank.
`blocked` requires `--allow-blocked`; `forbidden` cannot be approved.
Approval rechecks current source and policy. New approvals bind the signed
record to both the review payload and the verifier result. A fresh approval can
resume a previously released item that had yet to draft. Previously drafted bytes
stay deduplicated, and edited bytes have a new identity.

The signing key is `LOOPING_BOX_REVIEW_KEY`, or
`${XDG_CONFIG_HOME:-~/.config}/looping-box/review.key`; a missing file is created
with mode `0600` on the first decision. Empty/unreadable or group/other-accessible
key files are refused. `review key` does not print key bytes. Attribution uses
`LOOPING_BOX_APPROVER` or the login name; it is not authenticated identity.
`LOOPING_BOX_ALLOW_NONINTERACTIVE` accepts only trimmed, case-insensitive
`1`, `true`, `yes`, or `on`; use deliberately, because it bypasses terminal friction.

### Configure models in your own workspace

To enable a model role in a workspace, copy its `.env.example` to `.env` and
set private permissions with `chmod 600 .env`. The example key and all three
model IDs are blank. A role needs both `OPENROUTER_API_KEY` and its ID (`MODEL_CONTEXT_BUILDER`,
`MODEL_EXECUTION_ENGINE`, or `MODEL_VERIFIER`). The default endpoint is
`https://openrouter.ai/api/v1`. A configured base URL must use HTTP(S) with
a host and optional path.
Process values override `.env`, including an explicitly empty value. Only
`OPENROUTER_*` and `MODEL_*` load from that file, once per resolved root per
process. Workspace values stay scoped to that root. Changes to the configured execution-model ID invalidate execution work;
context-builder/verifier model changes and key changes alone do not.

Enabled roles send excerpts/context or review content to the configured endpoint.
Provider redirects and responses containing the configured API key are rejected.
Prompts may contain unredacted source excerpts. Provider requests may incur charges.
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

Use these commands to check local offline behavior. The review status records
the acceptance evidence and remaining platform and release work. Publication
and the license decision are owner actions. [Review status](REVIEW.md) · [Roadmap](ROADMAP.md)
