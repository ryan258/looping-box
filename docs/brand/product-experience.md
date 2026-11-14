# Product experience guide

Looping Box should feel like a short routine someone can leave and resume. These standards guide command examples, documentation, and future interface decisions. Use the [product README](../../README.md) for the current command and exit-code contracts.

## A repeatable operator routine

| Moment | What to put first | Current tool or artifact |
| --- | --- | --- |
| Start | One invocation from the known workspace | `looping-box run` |
| Inspect | Whether the pass completed, is idle, is blocked, or failed | `looping-box status` |
| Open the result | The saved Markdown location and the adjacent evidence | `cache/workers/execution_engine/draft.md` and `draft.json` |
| Decide | The held source, the matched wording, and what a decision permits | `looping-box review list` and `looping-box review show REVIEW_ID` |
| Return | The current state and the next action | `looping-box status` · [Recovery guide](../RECOVERY.md) |
| Try a sample | A short number, the expected first-pass result, and one follow-up | `python3.14 demos/examples.py 45 --shell` · [Example guide](../50-offline-examples.md) |
| Reopen a model-assisted sample | Its original draft, returned model, and reported cost | `python3.14 demos/examples.py --runs 22` · [Run history](../50-offline-examples.md#review-saved-free-runs) |

Examples should set the workspace once and reuse it. Explain how to see or choose the root near the first command. Put `--root` before the review subcommand when a review example needs it. Use a short alias or text expansion the operator can already invoke when they ask for one. Preserve the explicit review ID, note, and confirmation. Read back what the choice permits before asking the person to confirm it.

## Terminal output and documentation

Lead with a result or a state, follow with the supporting path or reason, and give one concrete next action. Keep human-readable messages and machine-readable contracts aligned. Screenshots should show the exact CLI, while surrounding documentation can explain its terms.

| Situation | Copy pattern for future surfaces | Operational detail |
| --- | --- | --- |
| New draft | **Draft saved.** Open the Markdown draft or inspect its source evidence. | A complete pass can also be idle. Use “Draft saved” when a draft was written. |
| Held item | **Held for review.** Inspect the source and the matched term. | `run` can exit 2 for this documented state. The review list supplies the ID and payload path. |
| Permitted decision | **Approved for drafting.** Copy the printed command for the next pass. | The note and signature attach to the review evidence. The command includes the workspace path. |
| Busy workspace | **Another run is using this workspace.** Wait for it to finish, then retry. | Follow the recovery guide if inspection shows an evidence problem. |
| Interrupted or unavailable evidence | **The saved evidence needs inspection.** Run `looping-box doctor` for the next step. | Preserve the workspace evidence when following a recovery procedure. |

Use sentence case in headings and navigation. Put commands and literal output in monospace, preferably as selectable text. Show one task per command block and identify placeholders outside the block. Keep status words visible when color, screen space, or terminal formatting changes.

## Resume card

Save this alongside a workspace or include it at the end of a walkthrough. Replace the three bracketed fields once.

```text
Workspace: [local root]
Last result: [draft saved, held item, idle, or failed]
Next action: [one step]
Status: looping-box status
Draft: cache/workers/execution_engine/draft.md
Review queue: looping-box review list
```

The sample commands assume the current directory is the workspace or that its root is already selected. Use the [voice guide](voice-and-editorial.md) for captions and status language and the [visual guide](visual-identity.md) for size, color, and focus treatments.
