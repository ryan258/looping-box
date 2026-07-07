# Looping Box — Sales Demos

A plain-language guide for showing Looping Box to a prospect. No coding
background needed. Copy-paste the lines in the gray boxes, then talk to what
appears on screen.

## The one-sentence pitch

> Looping Box is an automation assistant that does the busywork on your files
> **but never does anything risky without a human saying yes first.**

## The problem it solves

Most automation tools have one scary flaw: once you turn them loose, they can
send an email, push a change, or delete something **on their own**. One bad
input and there's no undo.

Looping Box is built the opposite way. It happily reads and organizes your
files, but the moment it sees anything that looks like a real-world action —
*deploy, send, publish, email, delete, anything touching credentials* — it
**stops and waits for a person**. Nothing leaves the building without approval.

That's the whole sell: **automation you can trust because a human stays in
control of the dangerous part.**

## Before you start

Open a terminal in the project folder. You only ever type the lines in the gray
boxes. Everything else is just reading the screen out loud.

**Shortcut:** Demos 1 and 2 are also one-tap scripts. Instead of typing the
individual lines, you can just run:

```sh
./demo-1.sh   # safe work gets handled
./demo-2.sh   # risky work gets blocked
./demo-3.sh   # a human approves, and the gate clears  (run after demo-2)
```

Each script drops the example file, runs the loop, and prints what to say.
`demo-3.sh` approves the item `demo-2.sh` left waiting, so run them in order. The
step-by-step versions below are there if you'd rather narrate each command.

---

## Demo 1 — "It just works on safe stuff" (60 seconds)

**Story:** Drop in some everyday notes; the system files them automatically, no
fuss.

1. Put a harmless file in the inbox:

   ```sh
   echo "Project docs: summarize the readme and the backlog." > inbox/notes.txt
   ```

2. Run it:

   ```sh
   ./startday.sh
   ```

3. **Point at the screen.** It says `review=clear` and `1 changed`. Translation:
   *"I processed this. Nothing risky here, so I just did it."*

**Say:** "Safe, routine work gets handled instantly. No babysitting."

---

## Demo 2 — "It refuses to do the dangerous thing" (the money demo, 90 seconds)

**Story:** Now we give it something that *sounds* like a real action. Watch it
slam the brakes.

1. Drop in a file with action language:

   ```sh
   echo "Please deploy the release and send the announcement email." > inbox/release.txt
   ```

2. Run it again:

   ```sh
   ./startday.sh
   ```

3. **Point at the screen.** Now it says `BOUNDARY GATE: review required` and
   `review=pending_review`. Translation: *"This wants me to deploy and send
   things. I will NOT do that on my own. A human needs to look."*

4. Show the waiting items:

   ```sh
   looping-box-review list
   ```

   It lists the held item, marked `review_required`.

**Say:** "This is the part competitors get wrong. It saw 'deploy' and 'send' and
stopped cold. It will keep flagging this every single time until a real person
decides. There is no way for it to quietly go rogue."

---

## Demo 3 — "The human gives the green light" (60 seconds)

**Story:** A person reviews the held item and approves it. Only *then* is it
considered handled.

1. List the pending item again and copy its ID (the `review-...` value):

   ```sh
   looping-box-review list
   ```

2. Read the full details of what's being asked:

   ```sh
   looping-box-review show <paste-the-id>
   ```

3. Approve it, with a note for the record:

   ```sh
   looping-box-review approve <paste-the-id> --note "Checked with the team, good to go"
   ```

4. Run the loop one more time:

   ```sh
   ./startday.sh
   ```

   It now says `review=clear`. The item was approved, so it stops nagging.

**Say:** "Every approval is signed, dated, and saved. You get a full paper trail
of who allowed what and when — auditors love this."

---

## The three things to repeat

1. **Safe work happens automatically.** (Demo 1)
2. **Risky work is blocked until a human approves it.** (Demo 2 — the wow moment)
3. **Every decision is recorded for audit.** (Demo 3)

## Likely questions, and honest answers

- **"Can it send an email or deploy by accident?"**
  No. Outward actions are blocked by design and require an explicit human
  approval that's saved to a file. It can't approve itself.

- **"What if it sees something it doesn't recognize?"**
  It defaults to *caution* — unknown actions are treated as "needs review,"
  never as "safe."

- **"Where does my data go?"**
  Offline by default — with no AI models configured, it runs entirely on the
  local machine with no internet and no outside servers. If you *choose* to turn
  on AI assistance for a role (via the OpenRouter setting), that role sends its
  prompt — which can include excerpts of the files it's working on — to
  OpenRouter to generate a response. Nothing else leaves the machine, and the
  human-approval gate is unchanged either way.

- **"What if I ignore a flagged item?"**
  It keeps re-flagging it on every run until a person approves, rejects, or
  removes it. Nothing falls through the cracks.

- **"Is there a record if something goes wrong?"**
  Yes. Every run, approval, and rejection is written to an append-only log you
  can hand to an auditor.

## What NOT to promise

Keep it honest — these are **not** in the product today:

- It does not actually send emails, deploy, or post anything itself — it
  prepares and stages work for a human.
- No mobile app or multi-user dashboard yet. (The only outside service it can
  use is OpenRouter, and only when you enable AI assistance for a role.)
- No live integrations with third-party tools yet.

If a prospect needs those, log it as a request — don't promise it on the call.

## Reset between demos

To start fresh for the next prospect:

```sh
rm -f inbox/notes.txt inbox/release.txt
find staging/reviews staging/approvals staging/rejections cache/deltas cache/state -type f ! -name .gitkeep -delete
```
