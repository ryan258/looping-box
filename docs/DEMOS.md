# Local walkthroughs — 0.2.0

Use a new temporary workspace for each experiment. These steps are examples,
not acceptance results. They use an installed `looping-box`; install the checkout
as described in [README](../README.md). Existing numbered and `demos/` shell
scripts are optional historical examples, not required 0.2.0 acceptance gates.
They have not been comprehensively revised by the bounded command/docs task.

## A clean offline pass

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
looping-box init "$demo_root"
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

Use the returned ID in `review show ID`. In a terminal, run
`looping-box review approve ID --note 'Reviewed for local drafting'`, inspect the
prompt, and type `approve` only if intended. Alternatively use
`looping-box review reject ID --note 'Exclude from drafting'`. Then rerun `run`.
Approval permits a local draft of the request; no deployment occurs.

A source containing `Save the api key.` is blocked-class: approval additionally
requires `--allow-blocked`. An edited source needs ingestion and fresh review;
editing a staged payload is not a way to lower severity. When scripting, handle
expected exit 2 explicitly instead of treating every nonzero exit as a crash.

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
