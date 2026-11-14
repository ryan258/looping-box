# Threat model — 0.2.0

Looping Box assumes one trusted operator account on a local macOS/Linux
filesystem. Operator code, shell, and configuration are trusted; inbox text and
model responses are untrusted data. The product creates local drafts and evidence.
It does not execute source/model instructions, send the messages described in
inputs, deploy, transfer money, or make Git commits. Optional enabled model roles
send data to the configured HTTP(S) endpoint.

## Boundaries and controls

- Runtime validators check config and persisted artifacts, supported versions,
  required fields, limits, cross-field relationships, and relative paths. Invalid
  evidence fails visibly; unknown configuration keys are rejected.
- The selected root may resolve through a user-chosen symlink. Descendant links
  used by internal operations are rejected; input links are skipped. This is
  preexisting-path containment, not defense against hostile same-user races.
- POSIX `flock` coordinates cooperating mutators. The persistent lock file's age
  cannot authorize stealing it. Read-only inspection and worker dry run do not
  create a lock or change workspace evidence.
- Approval rechecks current source and policy. New approvals sign both the
  payload and verifier digests. Existing signed records remain readable with
  the evidence they originally bound. HMAC detects unsigned or edited records
  without the key. The terminal prompt records interactive confirmation.
- Audit hash chains detect many edits and reorderings. Append verifies the
  existing chain. Diagnostics also compare retained, newly signed decisions
  with the review log to identify missing events.
- Atomic replacement preserves individual complete files. Artifacts precede
  successful-consumption markers; detected inconsistencies fail visibly.

## OpenRouter example runs

The numbered examples use synthetic inputs. With `--free`, the launcher uses
the checkout's `.env` key and sends eligible excerpts to `openrouter/free` for
the first draft. Context building, the review verifier, and the follow-up shell
run offline. Held excerpts stay outside the draft-model input.

The launcher saves draft snapshots and call metadata in the local Git-ignored
`logs/openrouter-examples/` directory. `--runs` reads those records offline.
The call log records the returned model, response identifier, and reported
usage and cost when available. The workspace's hash-linked workflow audit
records are kept in `logs/transactions/`. [Example guide](50-offline-examples.md#review-saved-free-runs)

## Accepted limitations

1. **Keyword misses and false positives.** Unlisted language, encodings,
   obfuscation, and paraphrases can evade the gate. It is not a semantic safety
   certification. Empty gate configuration is allowed with a doctor warning.
2. **User-scoped keys.** Keys may be reused across workspaces; matching records
   and payloads can verify under the same key elsewhere. Attribution is not
   authenticated identity. A process running as the operator can bypass controls.
3. **Unanchored audit history.** Complete chain recomputation, valid-suffix
   removal, or deletion of all logs cannot be detected without an external anchor.
4. **Unbounded overall ingestion and storage.** Oversized files are streamed for
   hashing; they still cause disk reads. Global scan count/I/O and disk growth
   are not capped. Per-worker limits do not establish global bounds.
5. **Post-return runtime checks.** A slow call is not forcibly interrupted at
   the configured worker budget. Requests/charges already made cannot be undone.
   There is no hard subprocess deadline or total memory cap.
6. **Excerpt-only drafts.** Default excerpts are at most 500 characters. Offline
   drafting copies excerpts; full-content understanding and draft quality are
   not guaranteed. Oversized approved inputs supply empty excerpts.
7. **Operator-managed retention.** State, history, archives, logs, and old reviews
   accumulate. There is no automatic compaction or encrypted storage.
8. **Partial crash recovery.** Individual writes are atomic; multi-file updates
   are not a database transaction. Arbitrary crashes/state rollback can cause
   replay or inconsistent evidence. General exactly-once behavior is not promised.
9. **Limited operating environment.** Hostile same-user races, Windows, network
   filesystems, multiple machines, and distributed workers are unsupported.
10. **Model uncertainty and disclosure.** Prompts are not complete injection
    defenses. Advisory verifier passes do not establish safety or factual
    accuracy. Enabled roles send unredacted excerpts/context/review content to
    the chosen endpoint; there is no automatic secret redaction.

Keep keys and `.env` private. Review content before enabling a model role, and
preserve evidence when diagnostics fail. No benchmark, SLA, regulatory
conformance, automated quality threshold, or accessibility certification is
claimed. See [RECOVERY.md](RECOVERY.md) for bounded operator remedies.
