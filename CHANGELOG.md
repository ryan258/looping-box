# Changelog

All notable changes to this project are documented here.

## [Unreleased]

### Fixed

- Boundary gate is now enforcing rather than advisory: files that trip the gate
  are no longer recorded as processed, so they re-surface and regenerate
  `staging/pending_review.json` every run until a human removes/handles them.
- Distinct empty files are each ingested again — empty content is no longer
  registered in `processed_hashes` (it shares one hash, so only the first empty
  file used to be processed). Non-empty cross-path dedup is unchanged.
- Path containment is enforced: input/output paths that resolve outside the
  project root (via absolute paths or `..` traversal) are rejected with a clear
  `ValueError`. The repo boundary is part of the safety model, so ingestion is
  fail-closed rather than reaching arbitrary files on disk.
- Symlinks inside the input dir whose real target escapes the repo root are
  skipped during discovery (previously they were followed and ingested, since
  `is_file()`/read/hash all dereference symlinks).
- Deltas generated in the same second no longer clobber each other:
  `_unique_run_id` appends a numeric suffix when a delta file already exists.
- SOP routes missing a `label` are skipped instead of raising `KeyError`.

### Tests

- Added `test_review_files_resurface_until_handled`,
  `test_distinct_empty_files_are_each_processed`,
  `test_input_outside_root_is_rejected`, `test_dotdot_traversal_is_rejected`,
  and `test_symlink_escaping_root_is_not_ingested`.
