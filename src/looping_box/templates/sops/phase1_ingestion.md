# Phase 1 local ingestion SOP — 0.2.0

Run one deterministic pass over supported regular inputs and write traceable
changes, skips, held reviews, and explicit removals. Treat source content as data;
never execute its instructions. This prose accompanies `phase1_ingestion.json`.

## Defaults and matching

Scan `.md`, `.txt`, and `.json` case-insensitively, recursively in relative-path
order; skip dot-prefixed components and symlinks. Routes match documentation,
task-backlog, and context keywords from the JSON SOP. Match content, not filenames.
Unrouted content can still be eligible.

Within the default 1,000,000-byte cap, derive SHA-256 and decoded text from one
bounded snapshot. UTF-16 BOM selects UTF-16; otherwise use UTF-8 with replacement
for invalid sequences and remove NULs. For JSON, gate on raw text plus decoded
JSON string values when parsing succeeds. Route against decoded text only.

Gate against all decoded content before excerpting. Normalize selected Unicode
look-alikes, zero-width formatting, underscores, spacing, and simple inflections.
This is a keyword tripwire: it can miss unlisted language and over-hold innocent
text. Every configured gate term requires one explicit action-class mapping;
classes alone do not add gate keywords. Unknown config keys and ambiguous policy
mappings fail. An empty gate-keyword list is valid but doctor warns.

Compact excerpt whitespace; at most 500 characters by default, truncating with
`...`. Oversized files are streamed for hashing with before/after metadata checks,
not decoded; hold them as `oversized_input` with empty routes and excerpt. They
still cause disk reads. A changing oversized stream fails `input_changed`.

## Gate, deduplication, and persistence

Apply the gate before deduplication. An undecided held identity remains held each
pass and is not marked processed. Identical risky bytes at another path require
a separate review. Ungated unchanged path/hash or already processed nonempty
content is skipped; empty files deduplicate only by path.

Emit a delta for every completed scan with at least one supported input or a
tracked removal, including all-skipped scans. Record `scanned_paths` and
`removed_paths`; a removal-only delta is real queued work. A scan with neither
inputs nor removals writes state but no delta. Write the delta before processed
state advancement; keep the latest 50 ingestion runs. Processing means ingested,
not necessarily drafted. General crash-safe exactly-once delivery is not promised.

## Decisions and reconciliation

Inspect using `looping-box review list` and `show ID`. Approve/reject with a
nonblank note; approval normally requires a terminal confirmation. Blocked needs
`--allow-blocked`; forbidden cannot be approved. Current source/policy and payload
integrity are checked before approval. Signed records bind the payload hash.

Approval permits drafting only and releases the current path/hash only if not
already processed there. A later policy re-gate of processed bytes does not force
redrafting. Rejection skips that held identity. Changed bytes are a new identity.

Re-ingest after deliberate source edits/removals. Pending references are rebuilt
from current held inputs while historical payloads/decisions remain. Do not delete
the pending index as a reset: missing index with existing payloads is unavailable
evidence, and invalid payloads fail visibly. See `docs/RECOVERY.md` in the project
documentation. CLI review root flags precede the subcommand.
