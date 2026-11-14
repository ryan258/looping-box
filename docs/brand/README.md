# Looping Box brand and marketing kit

**Version 1.1 · 12 November 2026 · Owner review**

Looping Box makes traceable drafts from local files. This kit turns that product promise into a brand system, a demonstration, and a set of materials Ryan can reuse when showing the project. The kit describes the private 0.2.0 candidate and supports optional, owner-led sharing.

> **Make a draft. Keep the trail.**

![Looping Box horizontal logo in ink and cobalt](assets/looping-box-lockup.svg)

| Open | Use it to |
| --- | --- |
| [Brand strategy](brand-strategy.md) | Explain the product, choose audiences, and keep the message centered on drafting and traceability |
| [Visual identity](visual-identity.md) · [Brand board](assets/brand-board.png) | Apply the logo, palette, typography, and visual system |
| [Voice and editorial guide](voice-and-editorial.md) | Write a README, demo, status message, accessibility description, or announcement |
| [Product experience guide](product-experience.md) | Keep CLI examples, review steps, and resume instructions consistent |
| [Claims and evidence](claims-and-evidence.md) | Check technical promises, candidate status, and model or review language |
| [Market and SWOT](market-and-swot.md) | Review adjacent alternatives, positioning risks, and the most useful next tests |
| [Marketing playbook](marketing-playbook.md) | Run a small evaluation, reuse campaign templates, and measure repeat value |
| [Launch copy](launch-copy.md) | Copy approved-for-review language for introductions, email, social, demos, and press |
| [50 runnable examples](../50-offline-examples.md) | Pick a synthetic workflow, run it offline, or opt in to a free OpenRouter draft |
| [Offline demo transcript](collateral/offline-demo-transcript.md) | See a checked example of the draft, held item, and scripted fixture decision |
| [Recorded OpenRouter examples](collateral/openrouter-example-transcript.md) | See observed model output, a held source, and reported costs from three synthetic runs |
| [Landing-page prototype](preview/index.html) | Preview the story and visual system as a standalone local page |
| [One-sheet PDF](collateral/looping-box-one-sheet.pdf) · [Editable source](collateral/one-sheet.html) | Walk someone through the product on one page |

## Start here

- **One sentence** — Looping Box turns local text files into drafts you can trace back to their sources, with a review step for recognized outward-action wording.
- **One proof** — Show the Markdown draft next to its source path, then show what happens to an input that says “Deploy the site.”
- **One link** — Send an evaluator to the numbered examples or the offline walkthrough once Ryan has chosen what to share. [Examples](../50-offline-examples.md) · [Walkthrough](../DEMOS.md)

Use the three-part sales demo to show the source, the hold, and the signed approval for local drafting. If a viewer wants to see model assistance, show example 22 with `--free` and open its saved draft and reported cost with `--runs 22`. The offline workflow and optional OpenRouter example are both available in the checkout. [Sales demos](../sales-demos.md) · [Recorded example](collateral/openrouter-example-transcript.md)

Keep the short descriptor beside the name on first contact. The tagline can carry a visual, while the accompanying copy explains the local CLI. When the product changes, update the claims register first, then refresh affected copy, the prototype, and exports.

The [product README](../../README.md), [release review](../../REVIEW.md), and [threat model](../THREAT-MODEL.md) govern implementation and release claims. The visual and messaging choices here are proposed for Ryan's review.

The prototype and one-sheet use locally bundled Inter and IBM Plex Mono with their [Inter](preview/fonts/OFL-Inter.txt) and [IBM Plex Mono](preview/fonts/OFL-IBM-Plex-Mono.txt) font licenses.

To rebuild the tagged one-sheet with WeasyPrint from the repository root, run `weasyprint --pdf-tags docs/brand/collateral/one-sheet.html docs/brand/collateral/looping-box-one-sheet.pdf`.
