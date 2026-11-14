# Looping Box sales demos

**Traceable drafts from local files**

*Make a draft. Keep the trail.*

Looping Box is a local drafting CLI for one operator. Show the result first. A
pass on a folder of text files produces a working draft with source paths when
there is eligible material. The default offline workflow assembles short
excerpts. A source that matches a configured term such as `deploy` or `send`
waits for a decision.

The private 0.2.0 candidate has three ways to show the workflow. Use the offline
sequence below to show a draft, a hold, and signed approval. Pick one of the
50 runnable examples for a particular audience. Use a saved OpenRouter run
to show model wording and its local evidence. [Runnable examples](50-offline-examples.md)

## Before you start

Run these from the project checkout. Each script creates and prints its own
temporary workspace, disables model roles, and uses a demo-only review key.
The Demo 3 script records a fixture approval and shows the CLI success output.
The manual Demo 3 below shows the interactive prompt.

```sh
./demo-1.sh   # Save a draft and show its source
./demo-2.sh   # Inspect a held source
./demo-3.sh   # Record a fixture approval for drafting and show the result
```

For a live, interactive demonstration, install the CLI using the
[README](../README.md#start-offline) and follow the
[offline workspace setup](DEMOS.md#set-up-the-offline-workspace). Use that Bash
session and the same workspace for all three manual demos.

## Demo 1: Draft saved

1. Put a short project note in the inbox.

   ```sh
   printf '%s\n' 'Project docs: summarize the readme and the backlog.' > "$demo_root/inbox/notes.txt"
   ```

2. Run one pass.

   ```sh
   looping-box run --root "$demo_root"
   ```

3. Open the draft. Point to the heading `inbox/notes.txt` and the excerpt
   underneath it. The adjacent `draft.json` holds provenance.

   ```sh
   cat "$demo_root/cache/workers/execution_engine/draft.md"
   ```

**Say:** “The draft keeps the source path beside the excerpt. I can see where the
material came from when I come back to it.”

## Demo 2: Held for review

1. Add an example with configured outward-action wording.

   ```sh
   printf '%s\n' 'Please deploy the release and send the announcement email.' > "$demo_root/inbox/release.txt"
   ```

2. Run another pass. This fixture exits 2 and reports `review=pending_review`.

   ```sh
   looping-box run --root "$demo_root"
   ```

3. List the pending items. Point to the ID marked `review_required`. The next
   demo uses that ID to open the payload and show the source and matched terms.

   ```sh
   looping-box review --root "$demo_root" list
   ```

**Say:** “This file matched configured terms such as ‘deploy’ and ‘send,’ so it
waits for review. I can inspect the source before deciding whether it can
enter drafting.”

If you open the saved Markdown at this point, look for the held source path.
Its excerpt stays out of the draft text until the source is eligible.

## Demo 3: Approved for drafting

1. Open the pending item from Demo 2. Replace `REVIEW_ID` with its actual ID.
   Point to `inbox/release.txt` and the matching terms in the review payload.

   ```sh
   looping-box review --root "$demo_root" show REVIEW_ID
   ```

2. Record approval for drafting with a note. In a terminal, read the prompt and
   type `approve` to record the fixture decision.

   ```sh
   looping-box review --root "$demo_root" approve REVIEW_ID --note 'Reviewed for local drafting'
   ```

   Show the prompt and success output. This transcript uses a sample review ID
   and workspace path.

   ```text
   Approve review-bec25b3db48c36ba [review_required: deploy, send, email]?
   Approval makes the item eligible for local drafting only.
   Type 'approve': approve
   approved: review-bec25b3db48c36ba
   Item approved for local drafting. With default ingestion settings, run this next.
   looping-box run --root /private/tmp/looping-box-demo
   For custom Phase 1 ingestion, repeat your input, SOP, and state settings and omit --delta-dir.
   Then run
   looping-box supervisor --once --root /private/tmp/looping-box-demo
   ```

3. Copy the `looping-box run --root ...` command from the terminal and run it
   for this demo. Open the saved Markdown and point to `inbox/release.txt`, the
   excerpt, and the approval reference.

   ```sh
   cat "$demo_root/cache/workers/execution_engine/draft.md"
   ```

**Say:** “I recorded a signed decision for local drafting. On the next pass,
the source appeared in the Markdown draft with its review ID. Sending the
announcement or deploying the release happens separately in my own tools.”

The decision file is in `staging/approvals/` and the review log is in
`logs/transactions/review.jsonl`. If you use `./demo-3.sh` in a recording,
describe the decision as a scripted fixture approval.

## Show a saved OpenRouter draft and its record

From the checkout, list the local records and open the latest saved run for
example 22. These commands replay the saved files locally. If the checkout has
no saved records, use the offline demos above or run the opt-in fixture below.

```sh
python3.14 demos/examples.py --runs
python3.14 demos/examples.py --runs 22
```

In the November 12, 2026 demo batch, OpenRouter returned a draft for the
configuration bug (17), the interview summary (22), and the voiceover pickup
sheet (36). Each returned record reported `$0.00` for that run. In example 22,
`recursive/flint-v1.0:free` drafted the eligible interview summary. Looping Box
added `HELD for review: inbox/quote-permission.txt` from the local review queue.
Point to the source heading, the held path, and the reported model and cost.

**Say:** “I can read the saved draft and see which model returned it. The
quotation request is still waiting in the local review queue.”

To make a fresh model-assisted draft, use a checkout `.env` with an OpenRouter
key and opt in with `--free`. This sends eligible synthetic sample excerpts to
`openrouter/free` for the first draft. OpenRouter availability, the returned
model, and draft wording can vary. The launcher saves the draft snapshot and
call metadata in `logs/openrouter-examples/`, a Git-ignored local folder.
[Free model and run history guide](50-offline-examples.md#try-a-free-openrouter-draft)

```sh
python3.14 demos/examples.py 22 --free
```

## Likely questions and honest answers

**What does `review=clear` tell me?**

It reports the run's review state. Review terms are configurable, and wording
outside those rules can pass. An incidental match can also hold a source.
Show the draft and its source path to demonstrate the result.

**What happens if I leave an item pending?**

Inspect it when you are ready. You can approve eligible material for drafting
or reject it. If you edit or remove the source, run ingestion again to
reconcile pending entries with the current input.

**Where does my data go?**

The default workflow runs offline. In the example launcher, `--free` enables
only the drafting role for the first pass. It sends eligible sample excerpts
and leaves held items in local review. In a separately configured workspace,
enabled model roles send included data to the configured endpoint. An enabled
verifier can receive a held excerpt during an approval attempt, after
deterministic checks and before the decision is recorded. Oversized sources
have empty excerpts.

**What does the review record show?**

The signed decision and review log record the review ID, approver label, and
note. The name is local attribution. Hash-linked log entries can reveal some
edits and broken links. The chain has no external anchor.

**Where can I check the exact limits?**

See the [claims and evidence guide](brand/claims-and-evidence.md), the
[threat model](THREAT-MODEL.md), and [candidate review status](../REVIEW.md).

## Repeat a demo

Use a new [temporary workspace](DEMOS.md#set-up-the-offline-workspace) for a manual
repeat. Each numbered script creates a new one automatically. The prior
workspace remains at its printed path so you can inspect the draft and review
record afterward. The [checked offline transcript](brand/collateral/offline-demo-transcript.md)
shows representative output from the two-source workflow.

Pick a numbered scenario from the [50 runnable examples](50-offline-examples.md)
when you want to show a workflow from writing, software maintenance, personal
admin, or recovery.
