# Threat Model

What Looping Box defends, against what, and where it stops.

## Trusted
- The code, `config/` (SOP, action classes, limits), and the operator's shell.
- The signing key (`$LOOPING_BOX_REVIEW_KEY` / `~/.config/looping-box/review.key`).

## Untrusted
- **Inbox content** (may contain prompt injection or action language).
- **Model output** (advisory text only; it is never executed, never gated-through).
- Anything in `staging/`, `cache/`, `logs/` that is not signature- or chain-verified.

## Enforced
- **Path containment**: configured paths and symlink targets must resolve under the root; symlinks are skipped.
- **Gate before dedup**: a flagged file is gated by (path, content hash, reasons), so identical content at a new path is gated again.
- **Per-item gating**: held items are never drafted or sent to a model; clean siblings proceed.
- **Oversized inputs** (`max_file_bytes`) are never read; they are held.
- **Signed decisions**: an approval/rejection counts only if its HMAC verifies. Forged, edited, or wrong-key records are ignored (gate stays closed) and reported as `decision_unverifiable` warnings. A key file readable by group/other is refused.
- **Action classes**: `forbidden` cannot be approved; `blocked` needs `--allow-blocked`; unknown reasons default to `review_required`.
- **Human-act friction**: approving needs a TTY and typed confirmation unless explicitly overridden (recorded).
- **Serialized state changes**: one lock covers phase 1, supervisor, and review; JSON writes are atomic and fsynced.
- **Audit**: phase 1 runs, supervisor runs, and decisions are logged with a hash chain.

## Known limits (accepted, stated plainly)
- **The gate is a keyword tripwire.** Matching is normalized and whole-word, but it only knows its keyword list. Paraphrase, other languages, and encodings can evade it. It is a seatbelt, not a guarantee.
- **One key per user, not per workspace.** Decisions are bound to the review id (path + content hash + reasons), not to a workspace identity, so a record copied between workspaces with the same relative path and content would verify. Use a separate `LOOPING_BOX_REVIEW_KEY` per workspace if that matters.
- **Same-user attacker**: a process running as you can read the signing key, set the non-interactive override, or edit logs. The HMAC and TTY check stop accidents, hand-edits, and naive agents, not a determined local process.
- **Audit logs are tamper-evident, not tamper-proof**: a full rewrite of a log (with a recomputed chain) is not detected; anchor the head hash elsewhere if that matters.
- **Model verifier is advisory** and promptable by the excerpt it judges; the deterministic checks only prove payload integrity. Approval is a human decision; the verifier does not decide safety.
- **Approval does not execute anything.** There are no executors; "approved" means "released into the local draft pipeline".
- **Excerpts go to OpenRouter** for any role with a model configured. `context_builder`/`execution_engine` exclude gated items; the `verifier` role receives the gated item's excerpt at approval time. Secrets are not redacted.

## Reporting
Open an issue or contact the maintainer. Do not post secrets or live keys.
