# Brand strategy

## The decision

Present Looping Box as a **local drafting CLI** for people who want to turn a folder of text files into usable working material and see where each draft came from. Lead with the draft. Show the source trail as proof. Introduce review as a clearly named step for inputs that match configured outward-action wording.

| Brand element | Direction |
| --- | --- |
| Product name | **Looping Box** in prose. Use `looping-box` for the command, package, and repository identifier. |
| First-contact descriptor | **Traceable drafts from local files** |
| Tagline | **Make a draft. Keep the trail.** |
| Category | Local drafting CLI. “Command-line tool for local drafting” on first mention with a general audience. |
| Promise | Bring a folder of notes into a repeatable drafting pass and keep the source and review evidence close enough to pick the work back up. |
| Operator story | Drop text files in an inbox and run a pass. Open the draft and its source paths. Inspect any held source, record a decision, and follow the printed next command after approval. |
| Brand character | Plainspoken, exact, calm, and considerate of the operator's effort. |
| Availability | Private 0.2.0 release candidate for one operator on local macOS or Linux storage. |

The tagline and visual identity are recommendations for this package. The project name remains the working name. Pair it with the descriptor in titles, demos, and search metadata so “looping” reads as a repeatable pass the operator runs. Refer to the [market assessment](market-and-swot.md) before using the name for broader distribution.

## Purpose and direction

**Purpose.** Make it easier to turn loose local notes into something you can work from and return to later.

**Mission.** Give one operator a short, repeatable path from a folder of notes to a working draft with inspectable local evidence.

**Vision.** Make it normal for personal automation to leave behind a result someone can understand, check, and resume after an interruption.

**Principles.** Preserve the source trail. Describe the exact scope of a decision. Make the next action easy to find. Respect the operator's choice about sending data to a model.

## Audiences and jobs

| Priority | Situation | Job | Proof they need |
| --- | --- | --- | --- |
| Primary · Ryan | Local project notes and backlog fragments accumulate between sessions. Typing and repeated setup cost effort. | “When I return to a project, give me a draft I can start from and show me what it used.” | A short invocation from a known workspace, readable output, and an obvious return path. |
| Secondary hypothesis · solo maintainers | Documentation and release-prep notes already live in plain files. | “Give me a starting draft for the next pass and show the source or review record.” | A repeatable demo using two fixtures, a visible source path, and precise approval language. |
| Secondary hypothesis · terminal-comfortable documentation practitioners and local-first workflow builders | They prefer inspectable text artifacts and are willing to try a CLI in a temporary folder. | “Help me see whether a small drafting routine is worth keeping.” | Honest offline examples, documented scope, and local recovery instructions. |

An initial evaluation should recruit by workflow. A good candidate already keeps work notes in local text files, has a repeatable drafting task, and can run a CLI. Users seeking a hosted team review queue, email integration, or workflow execution have a different job. The external audiences are hypotheses for owner-led evaluation. [Product scope](../../README.md) · [Market assessment](market-and-swot.md)

## Positioning statement

For a solo operator with project notes in local files, Looping Box is a local drafting CLI that turns those notes into working material and keeps a source trail. The default pass works offline and writes ordinary local files. A match on a configured review term holds the source for a decision. A permitted approval makes the source eligible for local drafting and prints the next command.

## Message hierarchy

1. **Result** — Get a working draft from a folder of local text files. Show the output before discussing architecture.
2. **Traceability** — Inspect the draft beside the input path and provenance. Show a concrete path or source excerpt.
3. **Review boundary** — Explain why a configured term caused a hold, who decides, and what “Approved for drafting” permits.
4. **Operating mode** — Offline by default. Default drafting assembles excerpts. The example launcher's optional `--free` mode sends eligible synthetic excerpts to OpenRouter's free router for the initial draft. Saved example runs show the draft, returned model, and cost when reported. [Example guide](../50-offline-examples.md#try-a-free-openrouter-draft)
5. **Resumption** — Use saved state, review records, and diagnostics to see what happened and what is pending. Describe specific behaviors when making a recovery claim.

## Proof to show

| Message | Show | Follow with |
| --- | --- | --- |
| “Traceable drafts from local files” | A synthetic `notes.txt`, the saved Markdown, and the adjacent provenance JSON | Ask the viewer to identify the source without help. |
| “Recognized wording goes to review” | `Deploy the site.` in a separate fixture and the pending review entry | Describe configurable matching and its limits. |
| “Approved for drafting” | A permitted decision with a note, the printed next command, and the resulting local draft | Point to the recorded decision and the output. |
| “Offline by default” | A fixture run with all optional model roles disabled | Explain that the offline draft assembles excerpts. |
| “Try a workflow that fits your notes” | One of 50 runnable examples, such as example 12 for comparing JSON responses or example 45 for resuming after approval | Show how to repeat the example in its own temporary workspace. |
| “See what an optional model run produced” | Example 22's saved draft and hold marker alongside its returned model and reported cost in `--runs 22` | Explain which synthetic excerpt was eligible and where the local record lives. |
| “Pick it back up” | Return to the same workspace and inspect status, report, or the last draft | Ask what the operator would do next. |

The draft, source path, review status, and decision evidence are the demonstration. An architecture diagram supports that sequence. [Offline walkthrough](../DEMOS.md) · [50 runnable examples](../50-offline-examples.md) · [Claims register](claims-and-evidence.md)
