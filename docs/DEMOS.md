# Local walkthroughs — 0.2.0

These walkthroughs use synthetic fixtures and fresh local workspaces. Choose a
numbered example for a quick run, use the interactive walkthrough to follow a
signed decision, or review a saved model draft.

## Run a numbered example from the checkout

Use supported Python to launch any of the 50 examples from the checkout. Run a
configuration fixture, or open an offline shell to try the follow-up steps for
a held item. [Example catalog](50-offline-examples.md)

```sh
python3.14 demos/examples.py 17
python3.14 demos/examples.py 45 --shell
```

The launcher prints the workspace path, CLI result, and follow-up. The numbered
scripts `./demo-1.sh` through `./demo-3.sh` and `./demos/run-all.sh` also create
disposable workspaces with provider settings disabled. The sales walkthrough
gives you a talk track for the three scripts. [Sales demos](sales-demos.md)

## A clean offline pass

The interactive walkthrough uses an installed `looping-box` CLI.
[Install from the checkout](../README.md#start-offline)

### Set up the offline workspace

Start an interactive Bash session first. Keep it open for all the steps below;
`exit` at the end restores your previous environment.

```sh
bash --noprofile --norc
```

In that session, create the disposable workspace. This disables model roles and
isolates the review key from your real key directory:

```sh
demo_root="$(mktemp -d)"
export LOOPING_BOX_ROOT="$demo_root"
export XDG_CONFIG_HOME="$demo_root/private-config"
export LOOPING_BOX_REVIEW_KEY=demo-key-not-a-secret
export LOOPING_BOX_APPROVER=demo-operator
export OPENROUTER_API_KEY= MODEL_CONTEXT_BUILDER= MODEL_EXECUTION_ENGINE= MODEL_VERIFIER=
export OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
unset LOOPING_BOX_ALLOW_NONINTERACTIVE
looping-box init "$demo_root"
```

### Run the clean pass

```sh
printf '%s' 'Project docs: summarize the backlog.' > "$demo_root/inbox/notes.txt"
looping-box run
cat "$demo_root/cache/workers/execution_engine/draft.md"
looping-box status
looping-box report
printf '\nPreserved demo workspace: %s\n' "$demo_root"
```

Expected: run exits 0, the offline draft contains the source excerpt and path,
and status is clear. A repeated unchanged pass emits skipped ingestion evidence
without repeating draft content. Each scan with supported input creates a delta;
the supervisor can archive validated consumed deltas. A never-populated empty
inbox writes ingestion state but no delta. No model output quality is implied.

The demo key is intentionally public fixture text; never use it for real reviews.
The temporary workspace is preserved for inspection. There is no broad reset
command and no lock-file deletion.

## A held item, with a real human decision

Continue in the same interactive demo session, where `demo_root` and the
fixture environment are still set:

```sh
printf '%s' 'Deploy the site.' > "$demo_root/inbox/action.txt"
looping-box phase1                 # exits 0 even though an item is held
looping-box run                    # exits 2: blocked by review
looping-box review list            # exits 0: successful inspection
```

Replace `REVIEW_ID` with the returned ID and inspect the source before approving.

```sh
looping-box review show REVIEW_ID
looping-box review approve REVIEW_ID --note 'Reviewed for local drafting'
```

Read the prompt and type `approve` to confirm the fixture decision. The
transcript uses a sample review ID and workspace path.

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

Copy the `looping-box run --root ...` command from your terminal. After it
finishes, open the draft and find `inbox/action.txt` and its approval reference.

```sh
cat "$demo_root/cache/workers/execution_engine/draft.md"
```

The approval records a signed decision for local drafting. Deployment happens
separately in the operator's tools. To try a rejection in a fresh fixture, use
`looping-box review reject REVIEW_ID --note 'Exclude from drafting'`.

A source containing `Save the api key.` is blocked-class: approval additionally
requires `--allow-blocked`. An edited source needs ingestion and fresh review;
editing a staged payload is not a way to lower severity. When scripting, handle
expected exit 2 explicitly instead of treating every nonzero exit as a crash.

## Run or replay an OpenRouter example

From the checkout, add `--free` to use its `.env` key with `openrouter/free` for
the first draft. Example 22 sends the eligible interview summary and holds the
source with the proposed named quotation. You can inspect the pending review
locally. Any follow-up shell starts offline.

```sh
python3.14 demos/examples.py 22 --free
```

To replay the result or see which earlier runs are saved locally, use `--runs`.
On a fresh checkout, the list can be empty. The second command opens the latest
run for example 22 and prints its draft if one was saved.

```sh
python3.14 demos/examples.py --runs
python3.14 demos/examples.py --runs 22
```

These commands read the Git-ignored `logs/openrouter-examples/` records locally.
The log distinguishes a reported zero cost, unavailable cost, and a run that
made no request. A draft snapshot preserves the first pass before follow-up
edits. [Free model and run history guide](50-offline-examples.md#review-saved-free-runs)

## Remove or defuse a source deliberately

In the disposable workspace only, edit the held source to harmless content or
remove that input file, then run `phase1`. Pending references reconcile with the
current inputs. A tracked removal produces a removal-only delta even when the
inbox is now empty; the next scan with no inputs/removals produces none. Older
queued/carry-over versions of that path are suppressed. Retained review payloads
and old drafts remain historical evidence, not current pending work.

Do not remove the pending index or `.looping_box.lock` to simulate this. Missing
index with retained payloads is unavailable evidence, and unlinking a live lock
can break coordination. See [RECOVERY](RECOVERY.md).

## Inspect without changing work

```sh
looping-box worker context_builder --dry-run
looping-box doctor
looping-box review audit
looping-box report --since 2026-10
```

Dry run plans artifacts/quarantines but creates no file, lock, history, or model
request. Doctor inspects local health and suggests remedies; it does not repair
or probe provider reachability. Reports filter event rows while retaining current
pending count and full-chain integrity checks. The interactive session keeps the same
workspace selection and isolated credentials throughout. When finished:

```sh
exit
```

This leaves the disposable workspace available at the printed path and restores
your original shell environment. To repeat, start a new session and fresh root.

The broader idea catalog and nine use-case scripts remain exploratory material:
[101 ways](101-ways-to-use-this-project-for-fun-and-profit.md). Their presence is
not a test result, release claim, or requirement to commercialize this tool.
