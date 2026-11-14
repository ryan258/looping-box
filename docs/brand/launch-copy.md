# Looping Box launch copy

Copy deck for the private 0.2.0 local candidate. Lead with an offline draft and its source path. Use the offline walkthrough for a first look and the 50 runnable examples for someone with checkout access. Before sharing externally, Ryan confirms the destination, access, licensing language, and release status. [Claims and evidence](claims-and-evidence.md)

## Identity and reusable descriptions

| Placement | Copy |
| --- | --- |
| Product name | Looping Box |
| Descriptor | Traceable drafts from local files |
| Tagline | Make a draft. Keep the trail. |
| Category | Local drafting CLI |
| Short blurb | Looping Box turns a folder of text files into working drafts with source paths. It holds files that match configured review terms and runs offline by default. |
| Long blurb | Looping Box is a local drafting CLI for people who work from folders of project notes, backlogs, and decision records. Put Markdown, text, or JSON files into the inbox and run one pass. The default offline draft assembles short excerpts and shows which files they came from. When a file matches configured review terms, you can inspect it and record a decision. A permitted approval makes the source eligible for local drafting and prints the next command. Choose from 50 runnable examples to see the workflow in a temporary workspace. |
| Example collection blurb | Try a case that fits your work. The 50 runnable examples cover software maintenance, writing and teaching, personal admin, creative work, and recovery. Each example shows what Looping Box produced and gives you a follow-up to try. |
| One line for a speaker | Looping Box gives you a working draft you can trace back to the files that produced it. |

## Website copy

**Title tag**  
Looping Box · Traceable drafts from local files

**Meta description and social description**  
Looping Box is a local drafting CLI. Turn text files into traceable working drafts and review files that match configured terms. Offline by default.

**Hero**  
Traceable drafts from local files  
Drop Markdown, text, or JSON files into an inbox and run one pass. Looping Box gathers excerpts into a working draft and shows you which files they came from. Files that match configured review terms wait for your decision.

**Primary link**  
See the offline walkthrough

**Secondary link**  
Browse 50 runnable examples

**Three proof points**

- **Start with a folder.** Put supported files in your inbox and run `looping-box run`.
- **See what needs review.** Inspect a held file and record a decision with a note. A permitted approval releases it for local drafting.
- **Follow the draft back to its source.** Open the Markdown draft for the working notes. The adjacent JSON links the draft to its context.

**Offline and provider note**  
The default workflow runs offline and assembles excerpts. The example launcher's optional `--free` mode sends eligible synthetic excerpts to OpenRouter's free router for the initial draft. The saved run shows the draft, model, and cost when reported. Separately configured model roles can send unredacted prompt data to their configured endpoints and incur provider charges.

**Availability note**  
Version 0.2.0 is a local candidate for one operator on macOS or Linux with Python 3.9–3.14. Full platform verification is pending.

For an authorized public page, verify that the destinations are accessible to the audience. [Offline walkthrough](../DEMOS.md#a-clean-offline-pass) · [50 runnable examples](../50-offline-examples.md) · [Review behavior](../../README.md#reviews-and-optional-models)

## Repository introduction draft

> Looping Box is a local drafting CLI for Markdown, text, and JSON files. Put files into an inbox and run one pass. The offline draft assembles excerpts and includes their source paths. When a file matches configured review terms, Looping Box holds it for a decision. A permitted approval releases an eligible file for local drafting. The tool saves the draft and review evidence in ordinary files you can inspect in the workspace.
>
> Version 0.2.0 is a private local candidate. Start with the offline walkthrough or choose one of 50 runnable examples. The optional OpenRouter example mode sends eligible synthetic excerpts for drafting and saves a local record you can review later.

Pair the introduction with the setup and demo that fit the recipient. [Setup](../../README.md#start-offline) · [50 runnable examples](../50-offline-examples.md) · [Threat model](../THREAT-MODEL.md)

## 90-second demo

Follow the [disposable offline setup](../DEMOS.md#set-up-the-offline-workspace). Keep that Bash session open, leave model roles disabled, and use fixture text only. Set the terminal to a readable size and open the draft in a text editor for easy comparison. Timings are edit targets for a rehearsed recording.

| Time | Show | Say |
| --- | --- | --- |
| 0–12 s | The inbox and a plain text note | “I have a short backlog note. I want a draft I can come back to and trace to that file.” |
| 12–32 s | Run one pass. Point to `inbox/notes.txt` and its excerpt in `draft.md`. | “One pass leaves a Markdown draft. This is the original excerpt, and the heading tells me where it came from.” |
| 32–55 s | Add a second file containing `Deploy the site.` Run again and show the review list. | “This file says ‘deploy,’ one of the configured review terms. It’s held. I can open the review record and decide whether to include the file in a draft.” |
| 55–78 s | Show the review and the approval prompt. Type `approve`, show the success message, and run the printed command. | “I’m approving this source for local drafting. The CLI gives me the next command for this workspace.” |
| 78–90 s | Point to the source path and approval reference in the draft. | “Here’s the excerpt, and here’s the review that released it. The files stay here in the workspace. Make a draft. Keep the trail.” |

Use this sequence after setup. Run the chunks separately so the expected blocked result in the second chunk is visible and the operator can inspect and confirm the decision in the third.

```sh
printf '%s\n' 'Project docs: backlog notes for the release checklist.' > "$demo_root/inbox/notes.txt"
looping-box run --root "$demo_root"
cat "$demo_root/cache/workers/execution_engine/draft.md"
```

```sh
printf '%s\n' 'Deploy the site.' > "$demo_root/inbox/action.txt"
looping-box run --root "$demo_root"  # Expected exit 2 for the held fixture.
looping-box review --root "$demo_root" list
```

```sh
looping-box review --root "$demo_root" show REVIEW_ID
looping-box review --root "$demo_root" approve REVIEW_ID --note 'Reviewed for local drafting'
```

Replace `REVIEW_ID` with the ID from the list. Read the prompt and type `approve` in the demo terminal to confirm the fixture decision. Copy the printed `looping-box run --root ...` command, run it, and inspect the artifacts. The [interactive walkthrough](../DEMOS.md#a-held-item-with-a-real-human-decision) shows the exact prompt and success wording.

```sh
cat "$demo_root/cache/workers/execution_engine/draft.md"
looping-box report --root "$demo_root"
```

### Example evidence

This fragment illustrates the expected offline draft after the held fixture has been released. `REVIEW_ID` represents the run-specific value. Use the [checked offline transcript](collateral/offline-demo-transcript.md) when quoting this example or preparing a screen capture.

```markdown
# Execution Draft

Source context: cache/workers/context_builder/context_package.json

## inbox/action.txt

_Released by approved review REVIEW_ID._

Deploy the site.
```

The draft gives the source path and approval reference. The adjacent `draft.json` preserves the source-context path and digest. The signed decision lives in `staging/approvals/` and review activity appears in `logs/transactions/review.jsonl`. For a scripted walkthrough, the existing `./demo-3.sh` records a fixture decision with the noninteractive option. Label that capture “scripted fixture approval.” [Demo script](../../demo-3.sh) · [Draft renderer](../../src/looping_box/worker.py)

### Optional model-assisted demo

Example 22 shows a summary about a four-week pilot alongside a named quotation with unresolved consent. Show the offline version first. If the viewer wants to see model assistance, explain that `--free` sends the eligible summary excerpt to OpenRouter's free router for the initial draft. The quotation stays held for review. Run the live version from the checkout with a configured OpenRouter key. [Free OpenRouter example](../50-offline-examples.md#try-a-free-openrouter-draft)

```sh
python3.14 demos/examples.py 22
python3.14 demos/examples.py 22 --free
```

To show a saved run, open the draft snapshot and point to the model and reported cost. Read the hold marker below the draft and show the sample source it refers to. The review command reads local files. [Review saved free runs](../50-offline-examples.md#review-saved-free-runs)

```sh
python3.14 demos/examples.py --runs 22
```

## Launch note draft

**Subject**  
Looping Box · A draft you can trace back to the source

Looping Box is a local drafting CLI for folders of project notes and backlog files. Put a text file into the inbox, run one pass, and open the Markdown draft. Its headings show which files the excerpts came from.

If a file matches a configured term such as `deploy`, it waits for review. You can inspect the source, record your decision with a note, and release it into drafting. The draft and the decision are files you can open later.

The private 0.2.0 local candidate runs offline by default. The walkthrough uses two sample files to show what gets drafted and what waits for a decision. The checkout also has 50 runnable examples, including an optional model-assisted first pass with a saved run record.

**Link label**  
See the offline walkthrough

## Private evaluation invitation

**Subject**  
Would you try one Looping Box walkthrough?

I’m looking for feedback on Looping Box, a small command-line tool that turns local text files into drafts with source paths. If you work from a folder of notes or backlog files, I’d like to show you one short example.

We’ll use two sample files. One becomes an offline draft. The other contains a configured review term and waits for a decision. I’d like to see where you look first, what you expect “approve” to do, and whether the draft gives you a useful place to start.

A brief reply is plenty. If you’re interested, I can show you the demo or share the walkthrough. You can also pick an example that fits your work, like reviewing a draft quotation or getting back to a paused task.

## Social drafts

**Short post**

> Put a text file in the inbox. Run one pass. Open a draft with the source path beside the excerpt. Looping Box holds files that match configured review terms. Offline by default. Make a draft. Keep the trail.

**Long post**

> A working draft is easier to pick up when it shows where the material came from.
>
> Looping Box starts with a local folder. Add a Markdown or text file and run one pass. The offline draft gives you the excerpt and the source path. When a file matches a configured word like `deploy`, you can open the review, decide whether to include it, and leave a note for later.
>
> The private 0.2.0 local candidate saves the draft and review evidence in ordinary files. The offline walkthrough follows two sample notes through the process, and the checkout includes 50 runnable examples to try in temporary workspaces.

Attach an approved walkthrough link when there is a shareable destination. Use one of these alt text drafts only when it matches the actual visual. Include a transcript with the demo video.

| Visual | Alt text |
| --- | --- |
| Social cards | Looping Box. Make a draft. Keep the trail. Traceable drafts from local files. Offline by default. |
| Workflow diagram | A local inbox feeds a draft with source paths. A file that matches a configured term waits for review. A permitted approval releases it for drafting. |
| Terminal capture | Looping Box shows `inbox/action.txt` held for review. The file contains the configured term “deploy.” |
| Draft capture | A Markdown draft shows `inbox/action.txt`, the excerpt “Deploy the site,” and the review ID that released it for drafting. |

## Press boilerplate

Looping Box is a local drafting CLI created by Ryan Johnson. It takes Markdown, text, and JSON files from a workspace inbox and writes traceable working drafts. The default workflow runs offline and holds files that match configured terms for a human decision. The private 0.2.0 candidate is designed for one operator on a local macOS or Linux filesystem. The checkout includes 50 runnable examples and an optional OpenRouter drafting demo with a local run record.

Use the owner-approved media destination and contact in any external kit. A useful media bundle contains an approved wordmark, a fixture screenshot with alt text, the one-line description, and the threat-model link.

## FAQ and objection responses

**Will it summarize an entire folder offline?**  
The offline mode assembles excerpts and source paths from supported inputs. The default excerpt limit is 500 characters per file. A separately configured model can help draft from eligible excerpts.

**Will it send or deploy after I approve?**  
Approval releases an eligible source for local drafting. Sending or deploying is a separate action you perform in the appropriate tool.

**What happens if the gate misses a phrase?**  
The gate matches configured wording, so a paraphrase can pass and an incidental match can hold a file. The tool’s workflow still ends at preparing drafts and review evidence.

**What does it do with my data?**  
The default runtime works offline and stores draft and review evidence in the workspace. If you enable a model role, its requests can include unredacted excerpts, context, or review material. The optional verifier can receive held material during an approval attempt after deterministic checks. Provider charges may apply.

**What happens when I run a free OpenRouter example?**  
The launcher sends eligible synthetic excerpts to OpenRouter's free router for the initial draft. Pending held excerpts stay out of that request. The draft and call metadata are saved locally. Run `python3.14 demos/examples.py --runs` to review them. A reported zero cost, an unavailable cost, and a run with no provider request appear as distinct results. [Example guide](../50-offline-examples.md#try-a-free-openrouter-draft)

**What does the audit trail prove?**  
The local records can link a draft to source context and a signed review decision. The hash chain can reveal some changes to the log. Approver names are local attribution, and the chain has no external anchor. For anything beyond that, use the [threat model](../THREAT-MODEL.md).

**Why use this instead of keeping the notes in a folder?**  
Keep your original folder. Looping Box adds a repeatable draft pass, a list of files held by your configured terms, and a local trail you can inspect later. Try one folder first and see whether that helps.

**How do I get it? Can my team adopt it?**  
The current artifact is a private local candidate for one operator. Ask about an authorized demonstration or evaluation. Any shared deployment, public download, or licensing terms would need their own decision.

## Demo and interview prompts

- Start with one recognizable note and one decision. Ask the viewer to say what they think will happen before the run, and what they think approval means before showing the result.
- Ask “Where would you look to find the original text?” and “What would you check if you came back tomorrow?” Observe whether the viewer can identify the source path, the pending item, and the next step.
- Let participants choose screen sharing, written replies, or a short call. Offer the fixture commands and a plain-text transcript so they can review the flow at their own pace.
- Record the date, candidate revision, demo mode, and exact question separately from your interpretation. Capture real output only from a fixture workspace. Confirm permission before quoting a participant or reusing their recording.
