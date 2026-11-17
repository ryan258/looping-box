# Changelog

## 0.2.0 — unreleased local candidate

This entry describes the implementation target and current changes. Acceptance
results are recorded separately; no complete release verification or publication
is claimed.

### Runtime and persistence

- Validate configuration and persisted artifact contracts at runtime, including
  required fields, supported versions, finite limits, policy coverage, and paths.
- Contain internal file access; reject symlink descendants and unsafe artifact
  references. Input discovery skips links and dot-prefixed components.
- Replace time-based lock stealing with nonblocking POSIX `flock`. Keep the lock
  inode after release; atomically replace individual JSON/Markdown files.
- Derive hashes/text from one bounded snapshot; stream oversized hashes without
  decoding. Track presence and emit explicit removal events.
- Revalidate review source/policy, bind signed decisions to the payload hash,
  and expose invalid or unavailable queue evidence instead of a false empty queue.
- Validate/quarantine worker intake, preserve prior nonempty outputs, and make
  worker dry run filesystem/network read-only.
- Reconcile latest held/removed content, revalidate undrafted carry-over, and
  invalidate execution work when the configured execution-model ID changes.
- Align supervisor planning, resource rollback, consumed-delta archival, live
  review status, and recovery. Runtime limits remain post-return checks.
- Verify audit chains before append; derive report/audit exits from structured
  findings. Doctor and status inspect local evidence without repairs or network.

### Commands, packaging, and documentation

- Set package version to 0.2.0 and declare Python 3.9–3.15, Linux/macOS support.
  Bundle schemas alongside workspace defaults with no runtime dependencies.
- Preserve umbrella, module, and four legacy console entry points. Resolve roots
  using explicit flag, environment, then cwd; init uses positional DIR/cwd and
  the checkout wrapper defaults to its own directory.
- Reject custom ingestion path flags on `run` and `startday.sh`; keep them on
  low-level `phase1`. Review root flags precede the review subcommand.
- Validate existing and bundled scaffold configuration before initialization,
  preserving existing files. Blank example key/model settings keep copied
  examples offline until deliberately configured.
- Replace stale lock-reset and overbroad safety/verification claims in operator
  documentation. Add focused command, alias, wrapper, and resource fixtures.

### Upgrade notes

Back up workspace evidence while no mutator is active before changing versions.
Unknown artifact versions fail visibly; no automatic evidence migration or
repair is provided. Do not delete the persistent lock file to recover a busy
run. Reconcile deliberate source/policy changes by ingestion. Approval after
re-gating already processed bytes does not force redrafting. Removed sources
suppress older queued/carry-over work, with retained historical evidence.

## 0.1.0 — historical baseline

The original local pipeline introduced ingestion, keyword routing/review gates,
context and draft workers, a one-pass supervisor, HMAC decisions, audit chains,
optional model roles, umbrella commands, workspace scaffolding, and exploratory
demos. Subsequent pre-0.2 review work added gate normalization, carry-over,
operator reporting, and recovery improvements.

Historical source and review notes remain in Git history and `docs/history/`.
Their earlier guarantees and verification statements do not establish 0.2.0
behavior or acceptance. Current commands and limits are documented in README,
RECOVERY, and THREAT-MODEL.
