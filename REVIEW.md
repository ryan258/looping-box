# Review status — 0.2.0 local candidate

This document separates implemented behavior from remaining acceptance work.
The controlling build contract is the existing sibling `looping-box-spec/spec.md`
and its bounded task list. The broader source review is retained in
[PROJECT-REVIEW-2026-10-13.md](docs/PROJECT-REVIEW-2026-10-13.md) as historical
findings and proposals; it is not evidence that current acceptance is complete.

## Current implementation scope

T01–T09 cover recursive runtime validation, contained paths, persistent kernel
locking and atomic replacement, consistent snapshots/removals, signed review
integrity, read-only worker planning, freshness/carry-over/model invalidation,
supervisor recovery, and structured diagnostics/audit/reporting. T10 aligns
commands, package data/version, scaffold validation, and operator documentation.
The new command acceptance fixtures exercise isolated subprocesses and wrapper
behavior; source presence is not proof of runtime success.

Previous review items about time-based lock stealing, split hash/text reads,
missing schema families, and a test-only validator are superseded by those
changes. They should not remain on a future-feature list. Resource budget checks
still happen after worker return; that is an accepted limit, not a hard deadline.

## Remaining acceptance work

T10's focused CLI/scaffold results were supplied as successful. T11 added the
A01–A31 map, installed-wheel helper, isolated fixtures, and required CI matrix.
The original detailed aggregate transcript reported **194 tests, 4 failures,
6 errors, 1 expected installed-wheel skip**; that failed result remains recorded.

T12 has recorded that failed result in [acceptance-results.md](acceptance-results.md).
A bounded reopening repairs worker config validation before lock-diagnostic
mutation and stale validation/freshness fixtures. Subsequent operator results
passed all three focused repair suites: validation 22, freshness 19, and worker
intake 15 tests. The repaired-tree aggregate then passed: **194 tests in 34.153s,
OK (skipped=1), exit 0**, on Python 3.14.7 and
`macOS-27.0.1-arm64-arm-64bit-Mach-O`. The skip is the installed-wheel scenario.
The subsequent full suite failed: **301 tests in 39.526s, 4 failures, 1 skip,
exit 1**. Repository-wide Ruff passed with exit 0. The failures are legacy
fixture/expectation mismatches with required SOP initialization, review error
semantics, and current-source freshness. A bounded T11 repair changes only
`tests/test_regressions.py` and `tests/test_worker.py`, preserving the rejection,
busy-lock, and deterministic-draft assertions and adding explicit missing-source
preservation checks. Both focused operator checks passed: **19 regression
tests in 0.289s and 8 worker tests in 0.143s, each exit 0**. The full offline
suite was then executed on Python 3.14.7 (`macOS-27.0.1-arm64-arm-64bit-Mach-O`):
**301 tests in 39.191s, OK (skipped=1), exit 0**. An isolated virtual environment
outside the checkout verified the built wheel with `LOOPING_BOX_TEST_CLI`:
**2 tests in 2.007s, OK, exit 0**. Repository-wide Ruff checks passed with exit 0.

GitHub Actions platform-matrix runs (Ubuntu 3.9/3.12/3.14, macOS 3.12) and the
manual real-terminal approval procedure remain operator/CI verifications.
Exact Python/OS versions and wheel identity accompany the local results. No live
provider call or draft-quality certification is required or implied by local acceptance.

No staging, commit, tag, publication, or license choice follows automatically
from these tasks. Documentation describes intended current behavior and explicit
limitations; it does not certify the release.

## Accepted limitations

The keyword gate can miss language or over-hold innocent text. Keys are user-scoped
across workspaces, attribution is not identity, and same-user attackers are outside
the model. Hash chains lack an external anchor. Oversized files still require
streamed reads; ingestion count/I/O and disk retention are not globally bounded.
Only excerpts reach drafts. Runtime checks are post-return and model requests
cannot be rolled back. No general multi-file crash transaction, Windows,
network-filesystem, or distributed support is claimed. See the complete
[threat model](docs/THREAT-MODEL.md).

## Owner decisions and deferred proposals

License and public distribution are optional owner decisions. The broader idea
catalog, sales-oriented material, and old demos are exploratory/historical, not
part of 0.2.0 acceptance or a commercial requirement. Future enhancements are
listed in [ROADMAP.md](ROADMAP.md); none expand this bounded implementation task.
