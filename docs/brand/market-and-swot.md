# Market context and SWOT

**November 12, 2026 · Internal planning · 0.2.0 private local candidate**

**Make a draft. Keep the trail.**

Traceable drafts from local files · Local drafting CLI

## Where Looping Box fits

Looping Box gives Ryan a repeatable way to turn supported files in a local inbox into a Markdown working draft and see which source material contributed to it. One command runs one pass. Recognized outward-action wording is held for review, and an eligible approval releases the source into local drafting. In the default offline workflow, the draft collects compacted excerpts. Model assistance is optional and enabled roles can send source excerpts or context to a configured endpoint. [Current workflow and supported inputs](../../README.md) · [Threat model](../THREAT-MODEL.md)

The primary audience is Ryan. Optimize for his existing file habits, minimal typing, understandable status, and easy resumption after a break. Terminal-comfortable solo maintainers, documentation writers, and local-first workflow builders with text-folder backlogs are a secondary audience hypothesis. Their likely job is preparing the same kind of draft repeatedly from a changing set of files and being able to check which material it used. One-off rewriting, file organization, and connected-app automation have different requirements and well-established alternatives.

Lead with the draft and the source trail. Show the review step as concrete evidence of where the workflow pauses and what approval means. The distinctive proposition is the combination of a model-free default, an inspectable file trail, and a narrow drafting workflow. Each capability has substitutes on its own.

## Alternatives someone might already use

The fit judgments in this table are strategic inferences from documented workflows. Products were compared by their stated behavior on November 12, 2026.

| Alternative | Documented workflow | When it fits |
| --- | --- | --- |
| A text editor, folders, or a personal shell script | The operator opens, copies, and labels excerpts manually or writes a script around their folder. This is the status quo to compare against in an actual trial. | Infrequent drafts and stable conventions can make this sufficient. Looping Box has to earn repeat use through intake, review, and source lookup. |
| Hazel | Watches selected folders on macOS and runs rules to organize, rename, tag, archive, or upload files. [Noodlesoft product guide](https://www.noodlesoft.com/) | Continuous folder organization and configurable file operations. Looping Box fits a deliberate pass that prepares a draft. |
| Simon Willison’s LLM CLI | Adds on-disk text files to model prompts as fragments and logs prompts and responses to SQLite by default. [Fragments](https://llm.datasette.io/en/stable/fragments.html) · [Logging](https://llm.datasette.io/en/stable/logging.html) | Ad hoc model prompts, model-assisted writing, or a composable CLI pipeline. Looping Box packages an inbox pass, a held review, and a local draft together. |
| Open WebUI | Local documents can be added to chat or a knowledge base, and retrieval can surface sources as chat citations. Offline mode disables specific automatic network activity and depends on having the relevant models locally. [Document retrieval and citations](https://docs.openwebui.com/features/chat-conversations/rag/) · [Offline configuration](https://docs.openwebui.com/reference/env-configuration/#offline_mode) | Interactive questions or writing with a model and document collection. Looping Box fits repeatable, source-excerpt drafting without a required model. |
| n8n | Supports self-hosting and can pause configured AI Agent tools for human review. Approval allows the selected tool to execute. [Hosting](https://docs.n8n.io/deploy/host-n8n.md) · [Human review for tools](https://docs.n8n.io/build/integrate-ai/ai-examples/human-in-the-loop-for-tools) | Custom automations that connect tools and can act after approval. A Looping Box approval only makes eligible material available to the draft writer. |
| AI by Zapier | Can add connected-app actions to an AI step. An optional per-tool setting pauses the run for approval before using the configured action. [Tool setup and approval](https://help.zapier.com/hc/en-us/articles/45863491098893-Add-tools-to-your-AI-by-Zapier-step) | Connected-app actions with configurable human review. Looping Box fits files and decisions the operator can inspect on local disk in the default workflow. |

## SWOT

### Strengths · Internal evidence

- **A lightweight default.** One deliberate pass runs with the Python standard library. The default draft needs no model or key. [Offline quickstart and commands](../../README.md)
- **Traceable work.** The workflow produces a readable Markdown draft and adjacent JSON provenance. Local state, history, diagnostics, and activity reports help the operator inspect the run and pick up later. [Draft and recovery evidence](../../README.md)
- **A clear review decision.** The CLI can explain why recognized language was held, record a signed decision, and release eligible material for drafting. The product’s outward-action boundary is easy to show with a fixture. [Walkthrough](../DEMOS.md) · [Threat model](../THREAT-MODEL.md)
- **A concrete evaluation path.** The checkout includes 50 runnable examples. A viewer can compare an offline excerpt with an optional model-assisted draft and use the saved run record to inspect the model and reported cost. [Examples](../50-offline-examples.md) · [Recorded runs](collateral/openrouter-example-transcript.md)

### Weaknesses · Internal evidence

- **First-run and review effort.** The CLI assumes some terminal familiarity. The normal review flow asks for an ID, a note, and typed terminal confirmation, which matters for Ryan’s workflow. [Review commands](../../README.md) · [Review effort proposal](../../ROADMAP.md)
- **Narrow input and draft scope.** The shipped input formats are `.md`, `.txt`, and `.json`. The default 500-character excerpt limit and excerpt assembly constrain usefulness for full-document synthesis. [Processing defaults](../../README.md) · [Excerpt limits](../THREAT-MODEL.md)
- **Bounded review and evidence.** Configured language matching can miss phrases or over-hold routine text. Local integrity checks have a limited threat model and the audit chain has no external anchor. [Threat model](../THREAT-MODEL.md)
- **Candidate acceptance.** The project targets one operator on local macOS or Linux storage. Recorded offline acceptance exists for a specific environment. The project review still lists the CI platform matrix and real-terminal procedure as outstanding. [Acceptance record](../../acceptance-results.md) · [Review status](../../REVIEW.md)
- **Model variability.** A free-model run depends on provider availability and rate limits. Model selection and draft wording can vary, so the value of a draft still depends on the operator's review. [Example guide](../50-offline-examples.md#try-a-free-openrouter-draft)

### Opportunities · Hypotheses to validate

- **A recurring drafting job.** A weekly project handoff or documentation backlog may provide enough repeat use to justify a dedicated command. Measure with a real folder and compare against the operator’s existing process.
- **An easier first pass.** A disposable demo, a copyable short command, and a captioned walkthrough may make the workflow usable with less typing. A shorter review flow is a separate roadmap proposal. [Demo scenarios](../DEMOS.md) · [Review effort proposal](../../ROADMAP.md)
- **An evidence-led introduction.** A numbered example lets a viewer choose a workflow close to their own. A private walkthrough could establish whether a terminal-comfortable solo maintainer values the source trail, understands the held item, and can find a previous draft. [Example guide](../50-offline-examples.md)

### Threats · External evidence and implications

- **Established substitutes.** Existing editors, folder tools, model interfaces, and automation platforms can cover parts of the same job. Review controls and local modes are familiar ideas, which makes general claims easy to overlook. The specific drafting sequence needs to show its value. [n8n human review](https://docs.n8n.io/build/integrate-ai/ai-examples/human-in-the-loop-for-tools) · [Hazel](https://www.noodlesoft.com/) · [Open WebUI retrieval](https://docs.openwebui.com/features/chat-conversations/rag/)
- **Category confusion.** “Looping” may imply continuous watching. “Approval” can imply that an action will execute. Viewers may also assume that a free example runs entirely offline or that the example log covers every model role. Check comprehension against the actual command and data path. [One-pass workflow and optional models](../../README.md) · [Saved run guide](../50-offline-examples.md#review-saved-free-runs)
- **Discoverability and optional distribution.** The name can surface unrelated audio and electrical results. Platform evidence, license selection, and a distribution decision affect any public discovery effort. [Deligo electrical product](https://www.deligo.co.uk/files/LB1/Deligo_Spec_Sheet_LB1.pdf) · [Publication decisions](../../REVIEW.md)

## Prioritized responses

| Priority and pairing | Response | Evidence that changes the plan |
| --- | --- | --- |
| 1 · Strengths + opportunities | Use one real documentation or project-handoff folder over two separate sessions. Compare the result with the current manual workflow. Record first useful draft, source lookup, and successful resumption. | If Ryan still builds the draft manually or rarely consults the source trail, try a narrower recurring job before expanding the audience. |
| 2 · Strengths + threats | Use a three-part demo to show the draft, the source trail, and a held source released to drafting. Ask the observer to say what the program did, what approval permits, and when a provider receives text. | If they expect an action to execute, an ongoing watcher, or a generated offline summary, revise the headline and walkthrough before reusing the copy. |
| 3 · Weaknesses + opportunities | Observe where installation, review-ID reuse, or terminal confirmation creates extra effort. Start with copyable commands and a saved resume card. | If shell setup or review prompts still dominate the task, prioritize operator experience and retest before broadening outreach. |
| 4 · Weaknesses + threats | Keep the candidate and its evidence visible in distribution copy. Treat model-assisted drafting as its own optional demonstration. Consider public sharing after the owner decides on licensing and distribution. | If matrix coverage or real-terminal behavior changes, update claims from the new evidence. If an audience requires authenticated reviewers, broader formats, or team operations, reconsider audience fit. |

## Discoverability and method

Keep **Looping Box** as the working name. Use **Looping Box · Traceable drafts from local files** in page titles and pair the name with the descriptor on first mention. Put **local drafting CLI** in the supporting description. Use `looping-box` for the actual command. “One pass at a time” is a concise way to describe the repeatable workflow in demos.

A light web search on November 12, 2026, used the exact name and the hyphenated command with drafting, CLI, automation, and package terms. Sampled results included electrical fittings and audio-related material. That establishes a search ambiguity worth addressing with a descriptor. Trademark clearance, domain and handle availability, and package namespace availability remain open for any public rollout. [Deligo product sheet](https://www.deligo.co.uk/files/LB1/Deligo_Spec_Sheet_LB1.pdf) · [Polyphone forum example](https://www.polyphone.io/en/forum/help/978-why-is-the-looping-box-gray) · [USPTO guidance on clearance search scope](https://www.uspto.gov/trademarks/search/comprehensive-clearance-search-similar-trademarks)

Internal findings use the candidate README, review record, demos, threat model, and roadmap. External findings use first-party documentation and product pages for selected substitutes. The review establishes positioning hypotheses and documented comparisons. Demand, market size, conversion, user preference, and comparative performance remain unmeasured.
