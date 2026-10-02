# Looping Box Roadmap

The local file-system loop is complete and hardened (see `CHANGELOG.md`). This file is
the single forward-looking list. It stays local-first: no daemon, no network state, and
no automatic commits, pushes, deploys, or messages unless an item below says otherwise
and ships behind explicit human approval.

Effort: **S** hours, **M** a day or two, **L** a week+.

## Next release: "trustworthy gate" (implemented, tested, and bundled)
Per-item gating, approval release, signed attributed decisions, hash-chained audit
(+ `looping-box-review audit`), normalized whole-word matcher, shared lock, size cap,
retry-after-outage, live status, unverifiable-decision warnings, key-permission check,
repeatable demos, CI, then the second review pass (CHANGELOG: carry-over after outage,
phantom-review fix, gate gaps, approval hardening, exit codes). All tests pass locally;
decide the LICENSE, then cut 0.1.0 before starting anything below.

## Adoption (after the release above)
- `[x]` `looping-box` umbrella CLI (`run`, `status`, `review`, `worker`, `doctor`, `init`); `report` still open. **S**
- `[x]` `looping-box doctor`: config drift, locks, recovery state, pending/unverifiable reviews, quarantined deltas, key and `.env` permissions, audit chain. Model reachability (network) is deliberately not checked. **S**
- `[ ]` `looping-box report [--since]`: audit-log + decisions as one markdown summary. **S**
- `[x]` `looping-box init` scaffold (defaults bundled as package data). **S**
- `[ ]` PyPI publication: blocked on a LICENSE decision (and a `license` field in pyproject). **S**
- `[ ]` Model layer: retries/backoff + fallback chain, token/cost accounting, response cache, prompts in `config/prompts/`. **M**
- `[ ]` Secret redaction before any outbound model prompt. **M**
- `[ ]` Full-content processing with chunking, more input types (csv, html, code; optional pdf extra). **M**
- `[ ]` State compaction for `processed_*`. **S**
- `[ ]` Interactive review loop (`looping-box-review interactive`) and notification hook on gate trip. **M**

## Platform
- `[ ]` Kernel-backed project lock (`flock`/`msvcrt`) with no time-based steal; enforced worker deadline (thread/subprocess). **M**
- `[ ]` Pluggable workers (registry in `config/workers/`, entry points). **L**
- `[ ]` Watch mode (foreground polling, stops at any block; needs `load_env` reload). **L**
- `[ ]` Optional model classifier that can only raise severity; optional verifier ensemble (advisory). **M**

## Only after the above holds
- `[ ]` MCP server: read-only tools plus "propose"/`ingest_text`. **Do not expose approve** — an MCP permission prompt is not an independent human gate. **L**
- `[ ]` Approval-released executors (`git_commit`, `file_move` first): one action per approved review, run exactly once, logged. **L**
- `[ ]` Multi-machine / network state stores. **L**
