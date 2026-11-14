# Recorded OpenRouter examples

On November 12, 2026, three numbered Looping Box examples ran on arm64 macOS with Python 3.14.9. The checkout was the 0.2.0 candidate with local changes on top of `e6d165d`. The examples used synthetic inputs and OpenRouter's `openrouter/free` route for drafting.

| Example | Model OpenRouter returned | Reported cost | Result |
| --- | --- | --- | --- |
| 17 · Reproduce a zero-value configuration bug | `liquid/lfm-2.5-2.6b:free` | $0.00 | Three source-linked draft entries |
| 22 · Pause a quotation with unresolved consent | `recursive/flint-v1.0:free` | $0.00 | One drafted interview summary and a held quotation request |
| 36 · Make a voiceover pickup sheet | `liquid/lfm-3-1.7b:free` | $0.00 | Two source-linked draft entries for the pickup and original take |

The cost values are from these three provider responses. The router can return a different model on another run. [Free router](https://openrouter.ai/docs/guides/routing/routers/free-router) · [Example inputs](../../../demos/offline-examples.json)

## Example 22 · One draft and one held source

The sample files included a short interview summary and a request to publish a named quotation while consent was pending. Looping Box drafted from the eligible summary. The configured term `publish` held the quotation request. The saved draft contained the following source section and local hold marker.

```markdown
## inbox/interview-summary.md

**Local Working Note — context_package**

- **Source:** Routed input / interview summary (untrusted data between markers).
- **Key detail:** Volunteer described a **four-week pilot**.
- **Public recap guidance:** Do not state pilot dates directly from this note; source dates from the **event calendar**.
- **Action:** Confirm event calendar dates before drafting public recap language.

**Status:** No specific dates established in source input.

- HELD for review: inbox/quote-permission.txt
```

The model supplied the working note. Looping Box added the source heading and `HELD` line. The pending quotation request stayed outside the draft-model input.

## Reopen the evidence

From this checkout, list the saved model runs and locate the November 12 records. A numbered lookup opens the latest saved run for that example.

```sh
python3.14 demos/examples.py --runs
python3.14 demos/examples.py --runs 22
```

The original three drafts, provenance, and call metadata are in `logs/openrouter-examples/20261112T205906Z-7oe3xjuu/` on the operator's checkout. The live runs used a temporary call recorder. The current launcher writes this evidence automatically for `--free` runs. Its automatic logging was checked with simulated provider responses, including failure, missing cost, and a held-only run with no provider request. [Saved run guide](../../50-offline-examples.md#review-saved-free-runs)
