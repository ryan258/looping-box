# Looping Box Demos

These demos are designed to be run from the repository root. They exercise the
Phase 1 local ingestion loop through the file system only.

Runtime outputs are ignored by git:

- `cache/state/phase1_state.json`
- `cache/deltas/*.json`
- `cache/workers/**/*.json`
- `cache/verifiers/*.json`
- `staging/pending_review.json`
- `staging/reviews/*.json`

Use this helper after any run to inspect the newest delta:

```sh
latest="$(ls -t cache/deltas/*.json | head -1)"
python3 -m json.tool "$latest"
```

To reset all demo runtime state:

```sh
find inbox -maxdepth 1 -type f -name 'demo-*' -delete
find cache/deltas -maxdepth 1 -type f -name '*.json' -delete
find cache/workers -type f \( -name '*.json' -o -name '*.md' \) -delete
find cache/verifiers -maxdepth 1 -type f -name '*.json' -delete
find staging/reviews -maxdepth 1 -type f -name '*.json' -delete
rm -f cache/state/phase1_state.json staging/pending_review.json .world_state.json
```

## Demo 1: Run an Empty Inbox Pass

Goal: Confirm the loop can run with no inputs and still writes a structured
delta.

Steps:

1. Reset runtime state:

   ```sh
   find inbox -maxdepth 1 -type f -name 'demo-*' -delete
   find cache/deltas -maxdepth 1 -type f -name '*.json' -delete
   find cache/workers -type f \( -name '*.json' -o -name '*.md' \) -delete
   find cache/verifiers -maxdepth 1 -type f -name '*.json' -delete
   find staging/reviews -maxdepth 1 -type f -name '*.json' -delete
   rm -f cache/state/phase1_state.json staging/pending_review.json .world_state.json
   ```

2. Run the loop:

   ```sh
   ./startday.sh
   ```

3. Inspect the newest delta:

   ```sh
   latest="$(ls -t cache/deltas/*.json | head -1)"
   python3 -m json.tool "$latest"
   ```

Expected result: the terminal prints `0 changed, 0 skipped, review=clear`, and
the delta summary shows `"changed": 0`.

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
   latest="$(ls -t cache/deltas/*.json | head -1)"
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
   latest="$(ls -t cache/deltas/*.json | head -1)"
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
   latest="$(ls -t cache/deltas/*.json | head -1)"
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
   latest="$(ls -t cache/deltas/*.json | head -1)"
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
   latest="$(ls -t cache/deltas/*.json | head -1)"
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
   find cache/deltas -maxdepth 1 -type f -name '*.json' -delete
   find cache/workers -type f \( -name '*.json' -o -name '*.md' \) -delete
   find cache/verifiers -maxdepth 1 -type f -name '*.json' -delete
   find staging/reviews -maxdepth 1 -type f -name '*.json' -delete
   rm -f cache/state/phase1_state.json staging/pending_review.json .world_state.json
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
   latest="$(ls -t cache/deltas/*.json | head -1)"
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
   latest="$(ls -t cache/deltas/*.json | head -1)"
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

3. Inspect the pending review index and latest payload:

   ```sh
   python3 -m json.tool staging/pending_review.json
   latest_review="$(python3 -c 'import json; print(json.load(open("staging/pending_review.json"))["latest"])')"
   python3 -m json.tool "$latest_review"
   ```

4. Inspect the newest delta:

   ```sh
   latest="$(ls -t cache/deltas/*.json | head -1)"
   python3 -m json.tool "$latest"
   ```

Expected result: the terminal prints `review=pending_review`, the delta
boundary gate status is `pending_review`, `staging/pending_review.json` points
to a review payload under `staging/reviews/`, and that payload lists reasons
such as `deploy`, `production`, `send`, and `email`.
