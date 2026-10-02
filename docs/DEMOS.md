# Looping Box Demos

These demos are designed to be run from the repository root. They exercise the
Phase 1 ingestion loop through the file system only. `./startday.sh` also runs one
supervisor pass, which archives observed deltas, so the helper below looks in
`cache/deltas/archive/` too. For hands-off, repeatable runs use `./demo-1.sh` ..
`./demo-3.sh`, which work in a throwaway workspace.

## Use-case demos

One runnable script per use-case section of
[101-ways-to-use-this-project-for-fun-and-profit.md](101-ways-to-use-this-project-for-fun-and-profit.md#the-winners-runnable).
Each runs in its own throwaway workspace and is repeatable; `./demos/run-all.sh` runs
all nine.

| Demo | Script | Shows |
|---|---|---|
| A. What do I owe people? | [a-owed-list.sh](../demos/a-owed-list.sh) | Meeting notes sorted into drafted and held follow-ups |
| B. Agent pre-flight | [b-agent-preflight.sh](../demos/b-agent-preflight.sh) | Risky agent plans held; `blocked` class refuses a casual approve |
| C. Tamper evidence | [c-tamper-evidence.sh](../demos/c-tamper-evidence.sh) | Edited log or record is detected |
| D. Overclaim tripwire | [d-overclaim-tripwire.sh](../demos/d-overclaim-tripwire.sh) | Custom gate words; editing the text clears the hold |
| E. CI gate | [e-ci-gate.sh](../demos/e-ci-gate.sh) | Exit code `2` as a human sign-off step |
| F. Evasion lab | [f-evasion-lab.sh](../demos/f-evasion-lab.sh) | What the gate catches and what it misses |
| G. Gate tuning | [g-gate-tuning.sh](../demos/g-gate-tuning.sh) | Measured before/after on a client's incidents |
| H. Dungeon Master's table | [h-dm-table.sh](../demos/h-dm-table.sh) | Forbidden class, approvals with notes, log as record |
| I. Report | [i-report.sh](../demos/i-report.sh) | `looping-box report` summary |

The numbered demos below are the older step-by-step walkthroughs that run in this repo's
own workspace.

Runtime outputs (state, deltas, worker output, verifier results, staging records,
logs) and everything in `inbox/` except `.gitkeep` are ignored by git.

Use this helper after any run to inspect the newest delta:

```sh
latest="$(ls -t cache/deltas/*.json cache/deltas/archive/*.json 2>/dev/null | head -1)"
python3 -m json.tool "$latest"
```

To reset all demo runtime state:

```sh
find inbox -maxdepth 1 -type f -name 'demo-*' -delete
find cache logs staging -type f ! -name .gitkeep -delete
rm -f .world_state.json .looping_box.lock
```

## Demo 1: Run an Empty Inbox Pass

Goal: Confirm the loop can run with no inputs and stops cleanly.

Steps:

1. Reset runtime state:

   ```sh
   find inbox -maxdepth 1 -type f -name 'demo-*' -delete
   find cache logs staging -type f ! -name .gitkeep -delete
   rm -f .world_state.json .looping_box.lock
   ```

2. Run the loop:

   ```sh
   ./startday.sh
   ```

3. Inspect the newest delta:

   ```sh
   latest="$(ls -t cache/deltas/*.json cache/deltas/archive/*.json 2>/dev/null | head -1)"
   python3 -m json.tool "$latest"
   ```

Expected result: the terminal prints `delta: none` and
`0 changed, 0 skipped, review=clear`. An empty scan writes no delta file, so skip
step 3 for this demo.

## Demo 2: Ingest a Documentation Note

Goal: Show keyword routing into the `documentation` route.

Steps:

1. Create a documentation input:

   ```sh
   printf '# Demo Docs\n\nReadme update notes for the local ingestion loop.\n' > inbox/demo-docs.md
   ```

2. Run the loop:

   ```sh
   ./startday.sh
   ```

3. Inspect the newest delta:

   ```sh
   latest="$(ls -t cache/deltas/*.json cache/deltas/archive/*.json 2>/dev/null | head -1)"
   python3 -m json.tool "$latest"
   ```

Expected result: the delta contains `inbox/demo-docs.md` with
`"matched_routes": ["documentation"]`.

## Demo 3: Ingest a Task Backlog Item

Goal: Show backlog language being classified as work to process later.

Steps:

1. Create a backlog input:

   ```sh
   printf 'TODO: add a task for validating stale cache recovery.\n' > inbox/demo-backlog.txt
   ```

2. Run the loop:

   ```sh
   ./startday.sh
   ```

3. Inspect the newest delta:

   ```sh
   latest="$(ls -t cache/deltas/*.json cache/deltas/archive/*.json 2>/dev/null | head -1)"
   python3 -m json.tool "$latest"
   ```

Expected result: the delta contains `inbox/demo-backlog.txt` with
`"matched_routes": ["task_backlog"]`.

## Demo 4: Ingest a Context Package

Goal: Show context-oriented notes being routed without loading the whole repo
into the worker.

Steps:

1. Create a context input:

   ```sh
   printf 'Context brief: summarize the current requirements and notes.\n' > inbox/demo-context.md
   ```

2. Run the loop:

   ```sh
   ./startday.sh
   ```

3. Inspect the newest delta:

   ```sh
   latest="$(ls -t cache/deltas/*.json cache/deltas/archive/*.json 2>/dev/null | head -1)"
   python3 -m json.tool "$latest"
   ```

Expected result: the delta contains `inbox/demo-context.md` with
`"matched_routes": ["context_package"]`.

## Demo 5: Trigger Multiple Routes From One File

Goal: Show that one input can fan out to more than one downstream concern.

Steps:

1. Create a mixed input:

   ```sh
   printf 'Readme docs TODO: capture the next step in the project backlog.\n' > inbox/demo-multiroute.md
   ```

2. Run the loop:

   ```sh
   ./startday.sh
   ```

3. Inspect the newest delta:

   ```sh
   latest="$(ls -t cache/deltas/*.json cache/deltas/archive/*.json 2>/dev/null | head -1)"
   python3 -m json.tool "$latest"
   ```

Expected result: the delta contains `inbox/demo-multiroute.md` with both
`documentation` and `task_backlog` in `matched_routes`.

## Demo 6: Ingest a JSON Input

Goal: Confirm `.json` files are valid ingestion inputs under the SOP.

Steps:

1. Create a JSON input:

   ```sh
   printf '{"kind":"notes","body":"Context summary for requirements review."}\n' > inbox/demo-input.json
   ```

2. Run the loop:

   ```sh
   ./startday.sh
   ```

3. Inspect the newest delta:

   ```sh
   latest="$(ls -t cache/deltas/*.json cache/deltas/archive/*.json 2>/dev/null | head -1)"
   python3 -m json.tool "$latest"
   ```

Expected result: the delta contains `inbox/demo-input.json`, its SHA-256 hash,
and at least the `context_package` route.

## Demo 7: Prove Identical Content Is Skipped

Goal: Demonstrate local caching and sequential idempotence.

Steps:

1. Reset runtime state for a clean cache demonstration:

   ```sh
   find inbox -maxdepth 1 -type f -name 'demo-*' -delete
   find cache logs staging -type f ! -name .gitkeep -delete
   rm -f .world_state.json .looping_box.lock
   ```

2. Create one input:

   ```sh
   printf 'Backlog task: verify repeated runs skip identical data.\n' > inbox/demo-cache.txt
   ```

3. Run the loop twice:

   ```sh
   ./startday.sh
   ./startday.sh
   ```

4. Inspect the newest delta:

   ```sh
   latest="$(ls -t cache/deltas/*.json cache/deltas/archive/*.json 2>/dev/null | head -1)"
   python3 -m json.tool "$latest"
   ```

Expected result: the second delta summary shows `"changed": 0` and
`"skipped": 1`, with the skipped item reason set to `already_processed`.

## Demo 8: Reprocess a Changed File

Goal: Show that the cache is content-based, so changed content produces a new
delta.

Steps:

1. Start from the file created in Demo 7, then append new content:

   ```sh
   printf ' Documentation decision: record the cache behavior.\n' >> inbox/demo-cache.txt
   ```

2. Run the loop:

   ```sh
   ./startday.sh
   ```

3. Inspect the newest delta:

   ```sh
   latest="$(ls -t cache/deltas/*.json cache/deltas/archive/*.json 2>/dev/null | head -1)"
   python3 -m json.tool "$latest"
   ```

Expected result: the delta shows `inbox/demo-cache.txt` under `changes`, not
`skipped`, and includes the updated route matches.

## Demo 9: Trip the Boundary Gate

Goal: Show that outward-action language is staged for human review instead of
being executed.

Steps:

1. Create an input that asks for high-leverage action:

   ```sh
   printf 'Please deploy this production change and send the email announcement.\n' > inbox/demo-boundary.txt
   ```

2. Run the loop:

   ```sh
   ./startday.sh
   ```

   (Note: `./startday.sh` exits with code `2` to signal that operator action is required.)

3. Inspect the pending review index and latest payload:

   ```sh
   python3 -m json.tool staging/pending_review.json
   latest_review="$(python3 -c 'import json; print(json.load(open("staging/pending_review.json"))["latest"])')"
   python3 -m json.tool "$latest_review"
   ```

4. Inspect the newest delta:

   ```sh
   latest="$(ls -t cache/deltas/*.json cache/deltas/archive/*.json 2>/dev/null | head -1)"
   python3 -m json.tool "$latest"
   ```

Expected result: the terminal prints `review=pending_review` (and exits `2` for blocked), the delta
boundary gate status is `pending_review`, `staging/pending_review.json` points
to a review payload under `staging/reviews/`, and that payload lists reasons
such as `deploy`, `production`, `send`, and `email`.
