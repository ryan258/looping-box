# 50 Looping Box examples

Pick a number, see what Looping Box puts in the draft or review list, then try the follow-up. Every example starts with synthetic files in its own temporary workspace and uses the shipped 0.2.0 policy. The offline draft keeps short source excerpts and their paths. A review decision can make a source eligible for local drafting. Actions such as sending, publishing, and deploying belong to your own tools.

## Run an example

From the checkout, use a supported Python. The commands below use Python 3.14.

```sh
python3.14 demos/examples.py            # List all 50 examples
python3.14 demos/examples.py 12         # Run one example
python3.14 demos/examples.py 45 --shell # Run it and open its offline follow-up shell
```

The launcher prints the workspace path, the CLI result, and what to look for. It leaves the temporary workspace available afterward. Choose `--shell` for examples that ask you to change a fixture or record a review decision. In that shell, `looping-box` uses the checkout, the example workspace, and disabled model roles. Type `exit` to leave it. The [fixture catalog](../demos/offline-examples.json) contains the exact sample files.

**Draft** means the first pass has eligible excerpts. **Held for review** means a configured term holds a source. **Blocked-class review** requires an explicit `--allow-blocked` override before approval. A pass with a hold can still show clean excerpts. Open the review entry to see its matched terms. The same wording can have different results under a different workspace policy.

### Try a free OpenRouter draft

Add `--free` to send eligible sample excerpts to OpenRouter for the first draft. The launcher reads `OPENROUTER_API_KEY` from the checkout's `.env` and uses OpenRouter's free-only model router. [OpenRouter Free Models Router](https://openrouter.ai/docs/guides/routing/routers/free-router)

```sh
python3.14 demos/examples.py 01 --free
```

Example 01 has one eligible sample note. The launcher shows the model OpenRouter returned. Generated wording can vary, so compare it with the source file. Model assistance applies to drafting. Pending held excerpts stay out of the request, and `--shell` opens an offline follow-up session. Omit `--free` for a local excerpt draft. Free requests are subject to provider availability and rate limits. [OpenRouter rate limits](https://openrouter.ai/docs/api_reference/limits)

### Review saved free runs

Each `--free` run saves a local record under the Git-ignored `logs/openrouter-examples/` folder. The record includes the request outcome, returned model, token counts and cost when OpenRouter reports them, and a snapshot of the draft before any follow-up edits. The call log stores metadata only. The draft and its provenance are separate local files in the same run folder.

List the saved runs, newest first, or add an example number to open its latest run and read the draft if it was saved.

```sh
python3.14 demos/examples.py --runs
python3.14 demos/examples.py --runs 22
```

These review commands read the saved files locally. A reported zero, an unavailable cost, and a run with no provider request appear as distinct results.

Examples 17, 22, and 36 have recorded model output and provider-reported costs you can review. [Recorded OpenRouter examples](brand/collateral/openrouter-example-transcript.md)

### Try an approval or rejection

Open example 45 with `--shell`, then inspect the pending item.

```sh
looping-box review list
looping-box review show REVIEW_ID
```

Replace `REVIEW_ID` with the displayed ID. To approve the fixture for local drafting, use the following command and confirm at the prompt. Pause after the decision if you are trying example 45. When you are ready to continue, use the `looping-box run --root ...` command printed by the CLI.

```sh
looping-box review approve REVIEW_ID --note 'Reviewed for local drafting'
```

For example 46, use a rejection instead and inspect `looping-box report` after the next pass.

```sh
looping-box review reject REVIEW_ID --note 'Outdated rehearsal notes'
looping-box run
looping-box report
```

See the [interactive walkthrough](DEMOS.md#a-held-item-with-a-real-human-decision) for a sample prompt and decision output.

## Pick up work and hand it off

### 01. Resume a fix after an interruption

**Draft.** Find the source path and the exact restart point in draft.md.

**Try next.** Return later, open the draft, and try only the comparison named in the note.

### 02. Keep a stopping point beside a follow-up

**Draft.** The last confirmed observation and the next action have separate source headings.

**Try next.** Open sample five and capture the result in a third note. Keep the earlier checkpoint as evidence.

### 03. Save a failed attempt before switching approaches

**Draft.** The failed attempt and the proposed next step stay separately attributable.

**Try next.** Read the failed attempt before retrying the task. Add a new source for the first observation from the new approach.

### 04. Keep exact identifiers in a dictated note

**Draft.** Compare the ID, version, field name, and timestamp with the source.

**Try next.** Use the draft as a reference for the next session. Correct any dictation error in the source and run another pass.

### 05. Carry a fallback decision into the next session

**Draft.** The draft includes the JSON text as an excerpt. The decision and its fallback remain readable.

**Try next.** Use the fallback on a small screen and save the observed result as a separate note.

### 06. Preserve two accounts of the same session

**Draft.** Both observations appear under their own source paths. The different launch conditions remain visible.

**Try next.** Repeat both paths once and record the conditions you can reproduce.

### 07. Restart from a known folder after a move

**Draft.** The draft gives you a short restart instruction and its original path.

**Try next.** Run doctor in the example shell for a read-only workspace check. Use the draft as the checklist.

### 08. Review a handoff destination before sharing

**Held for review.** The review lists the source and the configured term send. The held text is outside the working draft.

**Try next.** Open the review and decide whether the destination is appropriate for a draft. Approval makes the source eligible for local drafting.

### 09. Turn an open question into a small experiment

**Draft.** The question and fixed conditions appear as separate source excerpts.

**Try next.** Try both versions, then add a note with the exact difference you observed.

### 10. Carry a deliberate deferral forward

**Draft.** Find the deferral and the specific condition for revisiting it.

**Try next.** When that condition is met, add a dated observation alongside the original decision.

## Develop and maintain software

### 11. Shrink a Unicode regression to one input

**Draft.** Find both source paths and their separate expected and observed behavior in the Markdown draft.

**Try next.** Turn the two representations into a paired regression test in your own project.

### 12. Compare two API responses at a compatibility boundary

**Draft.** Read the two JSON excerpts side by side. The client fixture contains a null cursor and the server fixture omits the field.

**Try next.** Make the missing-versus-null case explicit in the contract test before changing either fixture.

### 13. Separate a migration rehearsal from a live operation

**Blocked-class review.** The review payload for 02-live-plan.txt shows the blocked class and the matched term production. The local rehearsal note is a separate input.

**Try next.** Use the fixture counts to write the rehearsal checklist. Keep live operation decisions in your deployment tool.

### 14. Turn a held prohibition into a positive invariant

**Held for review.** The pending review shows send as the matched term, including when the sentence describes a prohibition.

**Try next.** In the disposable source, try 'Expected fake transport calls while the retry clock is frozen: 0.' Run another pass and inspect the draft and review list.

### 15. Keep packaging checks specific to each platform

**Draft.** The draft keeps the recorded macOS fixture observation and the planned Linux check under distinct source paths.

**Try next.** Use the excerpts to build a two-row verification matrix with separate observed and pending states.

### 16. Pair a performance baseline with a comparable trial

**Draft.** The draft preserves the two trial series and peak memory figures as excerpts.

**Try next.** Compare runtime and peak memory separately before deciding whether the trade-off fits your workload.

### 17. Reproduce a zero-value configuration bug

**Draft.** Find the two configs and the observation under separate source paths. The excerpts preserve the literal zero.

**Try next.** Add the smallest config normalization test that asserts an explicit zero survives parsing.

### 18. Compare a synthetic auth header with a field-only trace

**Blocked-class review.** The review payload for 01-header-example.txt shows bearer and the blocked class. The field-only trace still preserves the fields needed for a status-code repro.

**Try next.** For a real bug report, start with the field-only structure and substitute synthetic data for header values.

### 19. Keep a feature flag retirement proposal reviewable

**Held for review.** The retirement proposal has a pending review for delete. The inventory records the specific files and values a maintainer should check.

**Try next.** Write down the condition that must be true before editing the flag, then inspect the proposal and decide whether it belongs in the local draft.

### 20. Find a mismatch between help text and observed behavior

**Draft.** The draft shows the documented expectation and the actual fixture observation with both source paths.

**Try next.** Check the option parser and decide whether the next change belongs in the CLI, the docs, or both.

## Write, research, and teach

### 21. Check a disputed date before writing

**Draft.** Find both source paths and both dates in the draft. The excerpts preserve the difference between the vote and the effective date.

**Try next.** Write two separate timeline entries in your own working document. Cite the minutes page beside the effective date.

### 22. Pause a quotation with unresolved consent

**Held for review.** The draft contains the interview summary and lists quote-permission.txt as held. Open the review payload to see the matched term publish.

**Try next.** For the fixture, reject the named quote with a note recording that consent is pending. Use the summary to draft a version without the quotation.

### 23. Carry a chart's denominator into its caption

**Draft.** The caption sketch and the tally appear under separate source paths. The JSON appears as source text, with 18, 24, and the exclusion count visible.

**Try next.** Write a caption that says 18 of 24 eligible submissions met the target in April. Add the timestamp exclusion to the figure note.

### 24. Keep a translation decision beside its context

**Draft.** The draft preserves both source paths and the accented text. The context and the chosen wording remain available for comparison.

**Try next.** Read the label and help phrase aloud in the prototype. Record any wording change in the terminology decision file.

### 25. Separate an observation from its explanation

**Draft.** The observation and hypothesis appear under their own filenames. The draft preserves the measurements and the missing observation.

**Try next.** Carry both into the lab notebook under distinct headings, and make the cap check the first step of the repeat.

### 26. Prepare a student handout before its answer key

**Held for review.** The student handout has a draft excerpt. The answer key has a held path and a review payload with upload as the matched term.

**Try next.** Open the handout excerpt for today's preparation. Review the answer-key source and verify the timing before making a drafting decision.

### 27. Catch a private field in a methods note

**Blocked-class review.** Review lists the environment note as blocked because access token is a configured term. The clean methods excerpt still appears in the draft.

**Try next.** For the fixture, revise the environment note to say Use the placeholder for the protected configuration field, then rerun. Compare the new draft and review list.

### 28. Explain a hold on a quoted instruction

**Held for review.** The lesson context drafts and the quoted dialog is held. The review payload shows that the literal delete match triggered the hold.

**Try next.** Approve the fixture for local drafting with a note that the instruction is a quotation in a paper exercise. Run the printed next command and find the review reference in the draft.

### 29. Pair an accessibility requirement with a test task

**Draft.** The requirement and the test task appear under separate source paths. These excerpts give the reviewer a criterion and an observable result.

**Try next.** Try the task manually on a local prototype. Add a third note with the browser, the actual focus destination, and the outcome.

### 30. Calibrate a rubric with two sample responses

**Draft.** The rubric and both answers appear under separate filenames. Compare the different kinds of support each answer supplies.

**Try next.** Score both responses by hand, then save the reason for each score in a separate rubric decision note.

## Personal admin and creative production

### 31. Record a move-in meter handover

**Draft.** Look for both source paths in draft.md. The reading values and the form instruction appear as separate excerpts.

**Try next.** Check each value against its original photo before filling in the move-in form.

### 32. Prepare a warranty packet

**Held for review.** Inspect the review for portal-step.txt and look for upload in its matched reasons. The facts have their own source path.

**Try next.** Use the symptom and date in your local draft. Open the receipt and crop unrelated details before using the warranty portal yourself.

### 33. Reconcile emergency supply counts

**Draft.** Find the two dated counts under their original source paths. The draft preserves the possible overlap in the notes.

**Try next.** Check the sticker and count the batteries again before writing a single verified total.

### 34. Bring a symptom timeline to a repair visit

**Draft.** Find both time-stamped excerpts in the draft and use the headings to locate the original notes.

**Try next.** Bring the two paper samples and the draft to the repair visit. Ask the technician to reproduce the difference between trays.

### 35. Keep login details out of a device handover

**Blocked-class review.** Open the blocked review and check the source path tablet-login.txt. Its matched reason is password and the fixture uses placeholder text.

**Try next.** Reject the fixture request and keep the practical tablet notes. Decide privately how the recipient should sign in.

### 36. Make a voiceover pickup sheet

**Draft.** Find the recorded take note and the pickup instruction under their own paths. Both carry the 00:41 cue.

**Try next.** Use the pickup excerpt at the mic and keep the take note beside the edit timeline.

### 37. Catch a prop continuity conflict

**Draft.** Find both key locations under their scene paths. The conflict is visible in the two excerpts.

**Try next.** Decide whether a scene should show the key changing places, then update the relevant source scene.

### 38. Review the request to list a cover

**Held for review.** Inspect the upload review for listing-step.txt. Compare that path with the cover brief that supplies the open photo-credit question.

**Try next.** Check the photo credit and compare both crops before deciding what to put on a listing in your own tools.

### 39. Assemble a tactile prop build card

**Draft.** The draft includes the JSON dimensions as text and the materials as a separate excerpt. Both headings identify their sources.

**Try next.** Check the dimensions against the set and copy the verified measurements onto the build card.

### 40. Review a request to discard raw takes

**Held for review.** Find storage-step.txt in review with delete as a matched reason. Use take-index.md to see which audio the proposed cleanup would affect.

**Try next.** Reject the fixture cleanup with a note about the room tone. Keep the takes you still need in your editing workspace.

## Inspect evidence and recover

### 41. Run the same inbox twice

**Draft.** Record the draft path and its single source section.

**Try next.** In the example shell, run looping-box run again without changing the source. Compare the skipped count and the saved draft.

### 42. Turn a skipped duplicate into a new source

**Draft.** The two files have identical bytes. Look for one drafted excerpt and an ingestion skip.

**Try next.** Add a distinct observation to the skipped copy and run again. Look for the changed source in the new draft.

### 43. Clarify an accidental hold in the source

**Held for review.** The configured word remove holds the whole source, even though the fixture describes a harmless copy edit.

**Try next.** In the sample file, change the line to ‘Task: Shorten the sample caption to one line.’ Run again and confirm the hold clears.

### 44. Withdraw an obsolete request from the inbox

**Held for review.** The request appears in the review list with a source path.

**Try next.** Remove only inbox/obsolete.txt from this disposable workspace. Run again and compare the pending list with the retained review file in staging/reviews.

### 45. Pick up a recorded approval after an interruption

**Held for review.** Look for upload in the review payload and confirm the excerpt is held.

**Try next.** Approve the fixture for local drafting and pause before running the next pass. Check status, then run the printed command. Compare the review ID in the new draft with the signed decision.

### 46. Record why a request stayed out

**Held for review.** The request is waiting on a decision and its text has not entered the draft.

**Try next.** Reject the sample using the steps below and the note ‘Outdated rehearsal notes.’ Run again, then check report for the decision and the draft for the held excerpt.

### 47. Compare two review classes side by side

**Blocked-class review.** review list shows review_required for publish and blocked for production.

**Try next.** Open both payloads and compare the matched terms. Leave the blocked item pending while you explain the different classes.

### 48. Check what happens when a held source changes

**Held for review.** Save the initial review ID before touching the fixture.

**Try next.** Change revision A to revision B in the source and run again. Compare the new ID and current excerpt with the original review. Use the current ID for any decision.

### 49. Remove a source before making the next draft

**Draft.** The initial draft gives both layouts their own source sections.

**Try next.** Remove inbox/retired.md only in this workspace and add a new note about layout B. Run again and check which source headings appear in the new draft.

### 50. Inspect a context plan before writing anything else

**Draft.** The first run leaves an ordinary draft and a context package.

**Try next.** Add a second short note. Run `looping-box phase1`, then `looping-box worker context_builder --dry-run`. Read the planned context artifact paths and check that the draft is still the first version. Then run `looping-box supervisor --once` and find the second note.
