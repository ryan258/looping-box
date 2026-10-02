> **Historical (moved to `docs/history/`).** Kept for the reasoning behind each idea; `ROADMAP.md` is the live list.
>
> **Status (2026-10-02):** the forward-looking items from this document now live in
> `ROADMAP.md` (single list). Section 7's open issues and the correctness bugs found in
> the later review are fixed (see `CHANGELOG.md`). One correction to §4.1: an MCP
> server must **not** expose `approve_review` — an MCP permission prompt is not an
> independent human gate, and it would make agent self-approval one call. Expose read
> tools plus "propose"/`ingest_text` only. Kept here for the reasoning behind each idea.

# Update Ideas — Looping Box as a Tool for AI Professionals

Written 2026-07-10 after a full-repo review (57/57 tests passing; source
~2,200 lines across `phase1`, `worker`, `supervisor`, `review`, `model`,
`action_policy`, `_util`, `schema`).

## Where the project stands

The core is genuinely solid: deterministic file ingestion with SHA-256
dedup, fail-closed path containment, a two-worker pipeline
(`context_builder` → `execution_engine`), a one-shot supervisor with
resource limits and rollback, a human review gate with signed decision
records, append-only audit logs, and an optional per-role model layer that
degrades to fully-offline behavior. Two prior audits (`REVIEW.md`,
`docs/history/2026-07-07-audit.md`) fixed nearly everything they found.

What it is today: a **demo-quality proof of the "human stays in the loop"
architecture**. What it is not yet: a tool an AI professional would install
and use daily. The gap is not correctness — it's capability, ergonomics,
and integration. That's what this document proposes.

Ideas are grouped into seven themes. Each item states the problem, a
proposal, and a rough effort tag: **S** (hours), **M** (a day or two),
**L** (a week+). A prioritized shortlist is at the end.

---

## 1. Model layer: from "optional demo feature" to professional-grade

The current `model.py` is deliberately minimal (stdlib `urllib`, no
retries, one provider, no accounting). For an audience that lives in model
APIs, this layer is the product.

### 1.1 Retries with backoff and model fallback chains — **M**

**Problem:** One transient 429/5xx from OpenRouter fails the whole worker
run (`ModelError` → `status: failed`). The `ponytail:` comment in
`model.py` already names this ceiling.

**Proposal:** Add bounded retry (e.g. 3 attempts, exponential backoff,
retry only on 429/5xx/timeout) inside `complete()`. Allow
`MODEL_<ROLE>=primary-model,fallback-model` so a role degrades to a
cheaper/different model instead of failing. Record which model actually
answered in the output (the `model` field already exists for this).

### 1.2 Token and cost accounting — **M**

**Problem:** Nothing records what a run cost. Professionals will not adopt
a model-calling tool that can't answer "what did yesterday cost me?"

**Proposal:** OpenRouter returns `usage` in every response; capture
`prompt_tokens`/`completion_tokens` per call, append a
`logs/transactions/model_calls.jsonl` audit line (role, model, tokens,
latency, response hash), and add `looping-box-supervisor --status` output
plus a `looping-box costs` subcommand that aggregates by day/role/model.
This reuses the existing append-only audit pattern.

### 1.3 Response caching — **S**

**Problem:** Re-running after a block/rollback re-pays for identical
prompts. Idempotency is keyed on input hash + model id, but only at the
worker level — a rolled-back run's model spend is lost.

**Proposal:** Cache completions in `cache/model/<sha256(role+model+prompt)>.json`
and return the cached response on exact match. Deterministic, offline-safe,
and it makes retries after resource-limit blocks free. Add a
`MODEL_CACHE=off` escape hatch for when fresh sampling is wanted.

### 1.4 Native Anthropic API support alongside OpenRouter — **M**

**Problem:** Everything routes through OpenRouter. Professionals often
have direct provider keys (and org policies requiring them), and features
like prompt caching are only available first-party.

**Proposal:** Keep the transport injectable as it is, but branch on
`ANTHROPIC_API_KEY`: when set and the role's model is a `claude-*` id,
call the Anthropic Messages API directly; otherwise keep the
OpenRouter/OpenAI-compatible path. The existing `_transport` seam means
tests stay offline unchanged.

### 1.5 Configurable prompts per role — **S**

**Problem:** Prompts are hardcoded string literals inside `worker.py` and
`review.py`. An AI professional's first instinct is to tune the prompt,
and today that means editing source.

**Proposal:** Move prompts to `config/prompts/<role>.md` with `{items}`,
`{excerpt}`-style placeholders (`str.format`, no template engine). Missing
file → current built-in default, so nothing breaks. Also expose per-role
`temperature` and an optional system prompt via `.env`
(`TEMP_EXECUTION_ENGINE=0.3`, `SYSTEM_VERIFIER=...`).

### 1.6 Structured-output hardening — **S**

**Problem:** `execution_engine` asks for JSON in prose and strips code
fences by hand. This works until a model chats before the fence.

**Proposal:** Where the provider supports it, send
`response_format: {"type": "json_object"}` (OpenRouter passes this
through). Keep `_strip_code_fences` as the fallback. One field in the
request body.

---

## 2. Pipeline capability: process real work, not just excerpts

### 2.1 Full-content processing with chunking — **M**

**Problem:** Workers only ever see a 500-char excerpt
(`max_excerpt_chars`). That's fine for routing but useless for real
summarization/drafting — the model layer literally cannot read the
document it's asked to draft notes about.

**Proposal:** Keep the excerpt for deltas/review payloads (bounded,
human-readable), but let workers read the full source file (path is
already containment-checked) with a configurable byte cap and simple
chunk-and-join for oversized files. The existing `max_payload_bytes`
supervisor limit already bounds the blast radius.

### 2.2 Pluggable workers — **L**

**Problem:** `run_worker` is a hardcoded if/elif over exactly two worker
ids, yet `config/super_loop.json` already pretends workers and
dependencies are configuration. Adding a "classifier" or "report writer"
worker today means editing `worker.py` and `supervisor.py`.

**Proposal:** A worker registry: `config/workers/<id>.json` declares
`{"input": "...", "output_dir": "...", "prompt": "...", "model_role": "..."}`
for declarative model-transform workers, and a Python entry-point hook
(`[project.entry-points."looping_box.workers"]`) for custom code. The
supervisor already routes by the config's `dependencies` list — it just
needs to stop assuming the two built-ins. This is the single biggest step
from "demo" to "framework".

### 2.3 More input types — **M**

**Problem:** Only `.md`, `.txt`, `.json` are ingested. AI professionals'
inboxes are full of PDFs, CSVs, HTML exports, and code.

**Proposal:** Tiered: (a) trivially add `.csv`, `.html`, `.py`, `.yaml`
as text (config-only change plus an extraction step that strips HTML
tags); (b) optional PDF text extraction behind an extras dependency
(`pip install looping-box[pdf]` → `pypdf`), skipped with a clear delta
error when not installed. Keep binary formats out of the offline core.

### 2.4 Per-item gating instead of batch-blocking — **M**

**Problem:** One flagged file in an inbox of ten stalls drafting for the
other nine (`REVIEW.md` documents this as intended fail-closed behavior).
At real usage volume this is review-fatigue-by-architecture: the safe 90%
waits on the risky 10%.

**Proposal:** `context_builder` already separates `items` from
`blocked_inputs`. Emit the package with `status: "partial"` and let
`execution_engine` draft the clean items while blocked ones wait on
review. The gate stays fail-closed *per item* — nothing flagged is ever
processed — but safe work flows. Guard with a config flag
(`"strict_batch_blocking": true` default) so current behavior remains the
default until proven.

### 2.5 Watch mode (bounded daemon) — **L**

**Problem:** The loop must be manually invoked; the roadmap defers "daemon
mode" wholesale. But "drop a file, see it processed" is the core UX, and
`./startday.sh` per file gets old fast.

**Proposal:** `looping-box watch` — a foreground polling loop
(`time.sleep`, no watchdog dependency) that runs the existing one-shot
supervisor pass when `inbox/` mtime changes, prints each pass summary, and
exits on Ctrl-C or on `operator_action_required` (never loops past a
block). Prerequisite: fix `load_env`'s process-lifetime cache
(`model.py` already carries the `ponytail:` note saying exactly this).
This honors the "runs once and stops" spirit — each pass is still the
same idempotent one-shot — while removing the retyping.

---

## 3. Review and governance: the differentiating feature, upgraded

The boundary gate is the sell (see `docs/sales-demos.md`). These make it
scale past three demo files.

### 3.1 Interactive review TUI — **M**

**Problem:** The review flow is list → copy id → show → approve, four
commands with manual id pasting. Fine for demos, tedious at volume.

**Proposal:** `looping-box-review interactive`: print each pending payload
(reasons, excerpt, action class) and prompt `[a]pprove / [r]eject /
[s]kip / [q]uit` with an inline note. Stdlib `input()`, no curses. Batch
approval of same-reason items (`approve-all --reason docs --note ...`)
falls out of the same loop.

### 3.2 Smarter action classification — **M**

**Problem:** Substring keyword matching is the intended high-recall
ceiling ("remove" matches "removed", "send" matches "sender"). At
professional volume the false-positive review fatigue that `REVIEW.md`
says to watch for *will* arrive.

**Proposal:** Layered classification: keywords stay as the tripwire
(recall), then an optional `MODEL_CLASSIFIER` role reviews the flagged
excerpt and can *raise* severity or annotate ("mentions 'deploy' in a
historical sense") — never lower it below `review_required`. The human
sees the annotation in `review show` and decides faster. Model can only
add caution, so the fail-closed property is preserved.

### 3.3 Secret redaction before model calls — **M**

**Problem:** Excerpts are sent verbatim to OpenRouter when a model role is
enabled. A pasted API key inside an ingested note ships to a third party.
(The gate keyword "secret"/"credential" mitigates only if the text uses
those words.)

**Proposal:** A small regex pass (AWS keys, `sk-`-style tokens, PEM
headers, `password=`) that masks matches to `[REDACTED:<type>]` in every
outbound prompt and in stored excerpts. ~30 lines plus tests; huge trust
win, and it belongs in the security story on the sales page.

### 3.4 Approved-action executors (staged outbox) — **L**

**Problem:** Today an approval records a decision — and then nothing
happens. The honest sales answer ("it prepares and stages work for a
human") is also the ceiling: professionals want approval to *release* the
action, not merely log consent.

**Proposal:** An `outbox/` stage: workers can emit action requests
(e.g. `{"action": "git_commit", "message": ...}`,
`{"action": "email_draft", ...}`). Every action request is
review-gated by its action class; on approval, `looping-box execute
<review_id>` runs the matching executor exactly once and records the
result in the audit log. Ship two executors first: `git_commit` (local,
reversible) and `file_move`. Email/Slack/deploy executors come later,
each an explicit opt-in. This is the roadmap's "automatic commits,
pushes…" item done safely: never automatic, always approval-released,
one at a time.

### 3.5 Audit report generator — **S**

**Problem:** The append-only logs exist but "hand it to an auditor" means
handing over raw JSONL.

**Proposal:** `looping-box report [--since DATE]` renders
`logs/transactions/*.jsonl` + decision records into one markdown summary:
runs, items processed, gates tripped, decisions (who/when/note), model
spend (once 1.2 lands). Pure read-only formatting.

### 3.6 Ensemble verification (roadmap item, opt-in) — **M**

**Problem:** The single model verifier is advisory and prompt-injectable
(noted in `review.py`). The roadmap defers "model-family ensemble
verification as a required dependency" — the *required* part is what was
rightly deferred.

**Proposal:** Allow `MODEL_VERIFIER` to be a comma list; run each model,
require unanimous PASS to add a passing check (any FAIL or error → failed
check, current fail-closed semantics). Injection has to beat N diverse
models instead of one. Off by default, still advisory on top of the
deterministic checks.

---

## 4. Integrations: meet AI professionals where they work

### 4.1 MCP server — **L**

**Problem:** The natural users of this tool live in Claude Code / MCP
clients, and there's no way to reach the review queue from there.

**Proposal:** A `looping-box mcp` stdio server exposing tools:
`list_pending_reviews`, `show_review`, `approve_review`, `reject_review`,
`ingest_text` (writes into `inbox/`), `run_pass`, `status`. All of these
are existing functions — the server is a thin adapter. This turns the
boundary gate into something an agent workflow can *query* while a human
still holds the approve keys (the MCP client's own permission prompt adds
a second human gate). Biggest reach-per-effort integration on this list.

### 4.2 Notification hooks — **S**

**Problem:** A pending review is only discovered by running the CLI. The
sales FAQ promises "nothing falls through the cracks", but only if the
operator remembers to look.

**Proposal:** An optional `on_pending_review` command in
`config/super_loop.json` (e.g. a `terminal-notifier` or `curl` line) that
the supervisor invokes with the review id when a gate trips. One
`subprocess.run` with the id as an argument, no shell interpolation of
content. Off by default.

### 4.3 Git-aware ingestion — **M**

**Problem:** For developer users, the most interesting "inbox" is a repo:
changed files, diffs, commit messages.

**Proposal:** `looping-box ingest-diff [REF]` — writes `git diff` output
(per file) into `inbox/` as text files, so review notes/summaries flow
through the existing pipeline and the boundary gate screens the diff for
action language. No git library needed; `subprocess` + the existing
ingestion path.

---

## 5. Operations and hygiene

### 5.1 State compaction — **S**

**Problem:** `processed_files` and `processed_hashes` in
`cache/state/phase1_state.json` grow forever (flagged in `REVIEW.md`).
Deltas archive now, but phase-1 state doesn't.

**Proposal:** Cap `processed_hashes` with simple LRU semantics (keep the
most recent N=5,000, config-keyed), and prune `processed_files` entries
whose paths no longer exist during each run. Deterministic, bounded, and
the worst case of eviction is one redundant re-process, which dedup makes
harmless.

### 5.2 `looping-box doctor` — **S**

**Problem:** Recovery knowledge lives in `docs/RECOVERY.md` prose. When
something is blocked, the operator has to diagnose by reading docs.

**Proposal:** One command that checks: required dirs exist, world-state
schema readable, stale locks, pending reviews, quarantined `.bad` deltas,
blocked recovery payloads, `.env` configured-but-unreachable models — and
prints the matching RECOVERY.md remedy for each finding. Read-only.

### 5.3 `looping-box init` + PyPI publication — **M**

**Problem:** Adoption today requires cloning this repo — the tool and the
workspace are the same directory. Professionals want
`pip install looping-box && looping-box init ~/my-workspace`.

**Proposal:** `init` scaffolds the directory tree and default configs into
a target dir (the `.gitkeep` layout, `config/*.json`, `.env.example`).
All code already takes `--root`, so the separation is mostly packaging:
publish to PyPI, move demo scripts to `examples/`. This also unlocks
multiple independent workspaces per user.

### 5.4 Single umbrella CLI — **S**

**Problem:** Four console scripts (`looping-box-phase1`, `-worker`,
`-supervisor`, `-review`) plus `startday.sh` is a lot of surface for one
tool.

**Proposal:** One `looping-box` entry point with subcommands (`run`,
`status`, `review`, `worker`, `watch`, `doctor`, `report`, `init`), the
existing scripts kept as aliases for one release. Argparse subparsers
already exist in `review.py` — extend the pattern.

---

## 6. Documentation and positioning

### 6.1 An honest "for AI professionals" README rewrite — **S**

**Problem:** The README describes internals ("Loop of Loops framework
phases") rather than the user story. The sales doc has the story but is
aimed at prospects, not practitioners.

**Proposal:** Restructure: what it does in 3 bullets → 60-second
quickstart → the guarantee (fail-closed gate, offline default, audit
trail) → architecture diagram (one ASCII drawing of
inbox → delta → context → draft → gate → decision) → extension points.
Move phase terminology to `docs/ARCHITECTURE.md`.

### 6.2 Threat-model document — **S**

**Problem:** The safety claims (fail-closed, containment, injection
posture) are scattered across CHANGELOG entries, review docs, and code
comments. Security-minded adopters need one page.

**Proposal:** `docs/THREAT-MODEL.md`: what's trusted (configs, code),
what isn't (inbox content, model output), the enforced boundaries (path
containment, symlink policy, gate-before-dedup, forbidden-class refusal),
and the known accepted risks (advisory model verifier, substring recall
tradeoff). Most of the content already exists — it needs collecting.

---

## 7. Known open issues to fix regardless of direction

Carried forward from prior reviews; still open as of this writing:

- **Timeout blocks can self-clear** (`docs/history/2026-07-07-audit.md` M2): a transient
  slow run blocks, rolls back, and a later rerun may clear it silently,
  contradicting the "never silently self-clear" guarantee for that one
  limit type. Cheapest honest fix: soften the README/RECOVERY claim to
  "deterministic limits never self-clear; timeout blocks retry identical
  work". Stricter fix: refuse runs while `operator_action_required` is
  set until `super_loop.json`'s hash changes. — **S**
- **`load_env` process-lifetime cache** blocks watch mode (2.5) and any
  long-lived process; make `load_env(force=True)` available and call it
  per pass. — **S**
- **Verifier prompt injection** (`review.py`): partially mitigated by
  fail-closed + comment; ensemble (3.6) and redaction (3.3) are the real
  reductions.
- **`schema.py` mini-validator** is fine for contract tests, but if
  schemas become a public extension surface (2.2), switch to `jsonschema`
  as its own `ponytail:` comment already suggests. — **S**

---

## Suggested order of attack

Highest leverage first, respecting dependencies:

| # | Item | Why first | Effort |
|---|------|-----------|--------|
| 1 | 5.4 umbrella CLI + 5.3 init/PyPI | Everything else lands behind one installable front door | M |
| 2 | 1.1 retries + 1.2 cost accounting | Table stakes for anyone using the model layer seriously | M |
| 3 | 2.1 full-content processing | Makes worker output actually useful | M |
| 4 | 3.1 review TUI + 4.2 notifications | Makes the gate livable at volume | M |
| 5 | 4.1 MCP server | Puts the tool inside AI professionals' daily environment | L |
| 6 | 2.2 pluggable workers | Converts the pipeline from demo to framework | L |
| 7 | 3.4 approved-action executors | Closes the loop: approval releases action | L |

Items 7.x (open issues) are small and can be folded into whichever
milestone touches their file first.
