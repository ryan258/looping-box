# Offline demo transcript

This is an edited capture from the Looping Box 0.2.0 working tree with local changes on top of `e6d165d`, on November 12, 2026, using Python 3.14.9 on arm64 macOS. It shows two synthetic inputs in a fresh temporary workspace with every optional model role disabled. Approval used a scripted fixture and a demo-only key. See the [walkthrough](../../DEMOS.md) for interactive approval.

## A plain note becomes a draft

The input was `inbox/notes.txt`. After `looping-box run` exited 0, the saved Markdown contained the following text.

```markdown
# Execution Draft

Source context: cache/workers/context_builder/context_package.json

## inbox/notes.txt

Project docs: summarize the backlog.
```

The draft shows the source path and repeats the excerpt. The matching provenance file is `cache/workers/execution_engine/draft.json`.

## A configured word sends an item to review

The second input was `inbox/action.txt`, containing `Deploy the site.` The next run exited 2. Selected terminal lines follow.

```text
summary: 1 changed, 1 skipped, review=pending_review
BOUNDARY GATE: review required before outward action.
status: blocked
```

The wording in the second line is the CLI's gate message. `looping-box review list` showed a pending ID with class `review_required`. `looping-box review show` for that ID identified `inbox/action.txt` and the matched term `deploy`. The source stayed out of the draft's item sections.

## A fixture decision releases the source for drafting

The fixture approval recorded the note `Reviewed for local drafting`. The next run exited 0 and the saved Markdown contained the source path, review reference, and excerpt.

```markdown
## inbox/action.txt

_Released by approved review review-3a3aada822f63534._

Deploy the site.
```

The signed decision file was present in `staging/approvals/`. The sentence “Deploy the site” was the text of the resulting draft. This capture covers the illustrated offline workflow on one environment. The [release review](../../../REVIEW.md) tracks broader acceptance.
