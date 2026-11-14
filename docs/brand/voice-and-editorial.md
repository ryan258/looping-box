# Voice and editorial guide

## Voice

Write like a practitioner showing someone a tool at their own terminal. Say what went in, what came out, and what the person can do next. Use first person for a signed founder note when Ryan has reviewed it. Use second person for instructions. Use Looping Box as the subject when describing what the program actually does.

| Quality | Apply it | Example |
| --- | --- | --- |
| Plainspoken | Put the outcome in the first sentence and use familiar verbs. | “Drop a text file into the inbox. Run one pass.” |
| Precise | Name the trigger, artifact, and scope of a decision. | “The source contains ‘deploy’ and is held for review.” |
| Calm | State what happened, then offer one concrete next step. | “This item is held. Open the review entry to see why.” |
| Human | Acknowledge the time and effort of resuming. | “When you come back, open the draft and check the source path beside it.” |

Use the tagline in a hero, cover, or social card once. Follow it with the descriptor or a sentence that explains the job. Give each page a direct next action. A demonstration page can say **See the offline walkthrough**. A private evaluation invite can say **Try the sample workspace**.

## Preferred product language

| Write | Meaning |
| --- | --- |
| **Source file** or **inbox file** | An input file inside the selected workspace. |
| **Draft** or **working draft** | Local Markdown output. The offline workflow assembles compacted source excerpts. |
| **Source trail** | Inspectable evidence connecting output to inputs and, where relevant, a recorded review. |
| **Pass** | One invocation that ingests the inbox and attempts to draft. |
| **Held for review** | A heading or sentence for a source held under the workspace policy. Use **Needs review** in a compact status label. Name recognized outward-action wording when that is the reason. |
| **Approved for drafting** | A permitted review decision releases eligible source into the local draft pipeline. |
| **Offline by default** | Runtime with optional model roles disabled. Installation can need build tooling. |
| **Optional model assistance** | A configured role can send included data to its endpoint. |
| **Free OpenRouter example** | A numbered sample run started with `--free`. Eligible sample excerpts go to the free router for the initial draft. |
| **Saved example run** | The local draft snapshot and call metadata from the example launcher. Use **reported cost** for the amount returned by OpenRouter and **cost unavailable** when the amount is missing. |
| **Private release candidate** | The 0.2.0 status label to carry into any evaluation materials. |

Use “human review” in copy and “review” in labels. Use **gate**, **verifier**, **provenance**, and **HMAC** where operator or developer readers need the exact term. Define them in general-audience materials or point to the relevant technical documentation. Before using “secure,” “private,” “audit,” “safe,” “AI,” or “automatic” as a benefit, check the exact claim in the [claims register](claims-and-evidence.md).

## Status and help copy

These are examples for collateral or future UI writing. When showing the terminal itself, use the actual release candidate's output and include its meaning in a caption.

| State | Main line | Next action |
| --- | --- | --- |
| Draft available | **Draft saved.** | Open the Markdown draft. See the adjacent JSON for the source trail. |
| Pending decision | **Held for review.** | List the pending items and inspect the matching review. |
| Decision recorded | **Approved for drafting.** | Copy the next command printed for the approved workspace. |
| Free example finished | **Draft saved.** | Open the original draft and reported cost with `--runs`. |
| Declined decision | **Review rejected.** | See the decision note in the review record. |
| Busy | **Another run is using this workspace.** | Let it finish, then run the command again. |
| Evidence issue | **Looping Box could not verify the saved evidence.** | Run `looping-box doctor` and follow the reported recovery steps. |

Use a sentence for a status when it needs an object or action. Preserve exit codes, file paths, review IDs, and command text verbatim when quoting a verified capture. In plain prose, use “macOS,” “Linux,” “Markdown,” “JSON,” and “OpenRouter.” Use backticks for commands, paths, extensions, and output fields. Use active voice, sentence case, and numerals for version numbers and measurable amounts.

## Low-effort and accessible communication

- Give each step one action. Keep a whole command on one copyable line when it fits. Keep notes, flags, and argument order in the example so the reader can adapt it once.
- Prefer an inline text walkthrough to a screenshot-only lesson. If sharing video, provide captions and a short transcript that includes the actual commands and outcomes.
- Use real heading order, descriptive link labels, and high-contrast captions. Convey held, approved, and drafted states with words. If a document uses a diagram, explain the branching in surrounding text.
- For demo captures, use synthetic data, enlarge the terminal, and crop to the few lines the viewer needs. Make the source path and decision text readable.
- Describe what an image conveys. For the social card, use “Looping Box. Make a draft. Keep the trail. Traceable drafts from local files. Offline by default.” A logo immediately beside the same visible name can be decorative.

## Editorial review

Before handing off a new asset, read the headline and caption aloud, verify the product and version terms, match the visible workflow against the [walkthrough](../DEMOS.md), and check the specific promise against the [claims register](claims-and-evidence.md). Keep a source and version next to any measurement or terminal capture.
