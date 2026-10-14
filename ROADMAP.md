# Looping Box roadmap

The active deliverable is a private, local, installable 0.2.0 candidate: one-pass
inbox ingestion, review, traceable drafts, and recoverable operational evidence.
[REVIEW.md](REVIEW.md) tracks implementation versus acceptance. There is no claim
that the release is complete, hardened against every threat, or published.

## Current contract status (0.2.0 local candidate)

1. T10 command/scaffold changes accepted against current files.
2. T11 acceptance coverage completed: full 301-test offline suite passing (`OK, skipped=1`), isolated installed-wheel acceptance passing (`2 tests, OK`), and Ruff lint clean.
3. T12 evidence and operator handoff recorded in [acceptance-results.md](acceptance-results.md) and [REVIEW.md](REVIEW.md); unresolved platform matrix/CI runs and owner publication decisions remain unverified.

Kernel locking, runtime schemas, source snapshot consistency, contained access,
review payload binding, freshness/removals, and read-only diagnostics belong to
this candidate. They are no longer future-feature proposals. Hard interruption
of a slow worker is still deferred; measured runtime bounds do not stop a call.

## Later proposals, each needing its own scope

| Opportunity | Proposed direction | Constraint to preserve |
|---|---|---|
| Less review effort | Accessible review queue with short, voice-friendly commands | Explicit decision, nonblank note, provenance, and terminal semantics |
| Less outbound disclosure | Preview/redact model prompts before opting into a role | No silent claim that redaction finds every secret |
| Larger sources | Chunking and additional input formats | Bound memory/I/O and retain source provenance |
| Predictable provider use | Cost accounting, response cache, deliberate retry/backoff | No automatic spend or concealed retries |
| Less disk growth | Operator-controlled retention and compaction | Preserve recovery, decision, and audit evidence |
| Better gate evaluation | Labeled local examples with measured misses/false positives | Keyword/model judgments remain limited evidence |
| Stronger isolation | Separate process deadlines and stronger transaction recovery | Explicit platform and crash guarantees |
| Workspace isolation | Optional workspace-bound signing keys/records | Intentional migration, never silent invalidation |

Watch mode, pluggable workers, read-only integrations, and distributed storage
are outside 0.2.0. Any executor would be a separate product/security decision:
current approval permits drafting only. Public distribution and licensing need
owner authorization and do not block personal local use. No commercial roadmap,
SLA, or accessibility certification is assumed.
