# Marketing and evaluation playbook

## Objective

Find out whether the local drafting pass is worth repeating and whether a person can explain the review boundary after seeing it once. Start with Ryan's real workflow. A small private evaluation with people who keep local notes can follow if Ryan chooses to invite them. Wider distribution is a separate decision.

## Campaign

**Campaign line.** Make a draft. Keep the trail.

**Story.** A maintainer comes back to a folder of project notes. Looping Box gives them a draft with a source path. Another source says “Deploy the site” and appears in the review list. A permitted approval makes that source eligible for local drafting. The CLI prints the next command for the workspace.

**Offer.** Start with a guided offline walkthrough in a temporary workspace. Then pick one of 50 runnable examples that resembles the person's work. For an optional model demo, show example 22's saved first draft and the returned model and reported cost. Live model assistance sends eligible synthetic excerpts to OpenRouter's free router. [50 runnable examples](../50-offline-examples.md) · [Saved run review](../50-offline-examples.md#review-saved-free-runs)

| Audience moment | Lead asset | Action | Evidence to collect |
| --- | --- | --- | --- |
| First look | [Landing-page prototype](preview/index.html), [one-sheet](collateral/looping-box-one-sheet.pdf), or [wide social card](assets/social-wide.png) | See the offline walkthrough | Can the reader describe the product in one sentence? |
| First try | [Offline walkthrough](../DEMOS.md) and [50 runnable examples](../50-offline-examples.md) | Pick a relevant example and produce a draft in a temporary workspace | Did the person find the draft and its source without help? |
| Review | Held fixture and [claims](claims-and-evidence.md) | Read a pending item, explain a decision, and run the printed command after approval | Did they understand what a permitted approval does and find the next command? |
| Optional model demo | [Example 22](launch-copy.md#optional-model-assisted-demo) and `--runs 22` | Compare the model-assisted draft with the source and inspect its saved run | Could they explain which excerpt went to OpenRouter and find the returned model and reported cost? |
| Return | Same local workspace, last draft, and `status` | Start a second session and pick up the draft | What did they repeat, and where did they stop or ask for help? |

## Rollout

| Step | Owner action | Done when |
| --- | --- | --- |
| 1 · Owner walkthrough | Start with a runnable example that resembles Ryan's workflow. Show the held fixture, the next command after approval, and the draft after returning in a later session. | Ryan can get back to the saved draft and explain any remaining step that costs effort. |
| 2 · Private evaluation, if invited | Send the [evaluation invite](launch-copy.md) to a small set of terminal-comfortable solo operators with a relevant use case. Suggested starting scope is three sessions. | Each session records completion, comprehension, and friction. Collect feedback with permission and give the participant a simple way to stop. |
| 3 · Review the evidence | Compare observed use with the [SWOT](market-and-swot.md), refine copy, and prioritize the biggest recurring obstacle. | The copy reflects what people understood and the demo reflects what they could actually complete. |
| 4 · Optional public introduction | Choose a distribution route, license posture, version and support language, contact path, and current evidence. Review any new claim before using it. | Ryan authorizes the specific share. Link to a destination with the same stated availability as the campaign. |

The default distribution path for evaluation is a direct, personal handoff. A short repo introduction and the README walkthrough can serve as the home base after a share is authorized. An owned-site note can tell the story behind the tool. A single community or social post can point to the walkthrough when there is an appropriate public destination. Match captions and alt text to the image and put any meaningful instructions in post text. The [launch copy](launch-copy.md) contains copy for these uses.

## Measurement

Use an owner-controlled note or table. Record counts with their denominators. Treat small samples as observations about the tested workflow.

| Measure | Definition | How to collect |
| --- | --- | --- |
| First-draft completion | Participant creates and opens the saved Markdown draft | Observe during the walkthrough or ask them to report whether they found it. |
| Source trace | Participant identifies the input behind a draft | Ask them to point to its path or adjacent evidence before offering a hint. |
| Review comprehension | Participant says approval makes the source eligible for local drafting, names the item, and finds the printed next command | Ask “What will this decision allow?” before the next pass. |
| Operating mode comprehension | Participant explains what offline drafting produces and which excerpts the optional OpenRouter example sends | Ask after the demo with the actual output visible. |
| Saved run review | Participant opens the saved draft and distinguishes a reported zero cost, unavailable cost, and a run with no provider request | Ask them to use `--runs` if they tried the optional model demo. |
| Return and reuse | Participant resumes in the same workspace and chooses to use the output again | Follow up after the next relevant work session. Record what they used and why. |
| Input effort | Participant pauses, retypes a command, copies a review ID more than once, or needs assistance | Capture the step and the specific friction. A local alias or text expansion can be noted if the participant already uses one. |

A proposed three-session decision rule is to fix any recurring misunderstanding about approval or data transfer before broader sharing. Look for independent return and use of the draft by at least two participants before treating repeat value as a signal. Ryan can use the same measures on his own workflow and decide whether the tool earns a place in it. Collect version, platform, task, observations, and optional quotes with consent. Use participant labels in the evaluation notes and omit files, paths, or credentials that disclose personal information.

### Session note template

```text
Date / candidate / platform
Participant label and job
Draft opened? Source identified?
What did they say approval allows?
What did they say the offline and optional model modes do?
If they tried the model demo, could they find the draft, model, and reported cost?
Where did they stop or ask for help?
Second-session outcome
Change to try
Permission to quote, if requested
```

## Reusable creative brief

Use this for a launch asset, case note, blog post, or demo refresh.

| Field | Default |
| --- | --- |
| Audience and occasion | A solo maintainer returning to a local documentation backlog |
| One outcome | Find a useful draft with a source trail |
| Proof to capture | Input filename, local draft, and one review state or recorded decision. For a model example, include the returned model and reported cost from the saved run. |
| Headline | Make a draft. Keep the trail. |
| Descriptor | Traceable drafts from local files |
| Primary action | See the offline walkthrough |
| Format | One-screen proof, wide card for link previews, square card for feeds, text transcript for moving media |
| Review | Confirm exact version and claim, validate links and file sizes, inspect legibility, include alt text, and confirm intended distribution |

### Five-slide briefing outline

| Slide | On screen | Presenter note |
| --- | --- | --- |
| 1 · Make a draft. Keep the trail. | Primary lockup, tagline, and descriptor | “Looping Box starts with the text files you already have.” |
| 2 · Start from a folder | Two synthetic filenames in a workspace inbox | “Run one pass when you are ready to work through the notes.” |
| 3 · See where the draft came from | A line of draft text and its source path | “In offline mode, the draft assembles source excerpts.” |
| 4 · Make an explicit review decision | Held wording, the pending item, and the next command after “Approved for drafting” | “A configured match can hold the source. A permitted approval makes it eligible for local drafting. Run the printed command to continue.” |
| 5 · Try one of 50 runnable examples | A relevant example number and the private candidate status | “Pick an example that fits your work and use its temporary workspace.” |

Use the [visual identity guide](visual-identity.md) for layouts and the [claims register](claims-and-evidence.md) for captions. Refresh dated market sources and version-specific copy before a new campaign. Use the [kit index](README.md) to resume after a break.
