# Looping Box

A local, file-system-bounded automation loop: drop files in `inbox/`, get local
drafts out, and anything that reads like an outward action is **held for a human**
instead of processed. Nothing is ever sent, deployed, or committed by this tool.
Offline by default; no dependencies beyond Python 3.9+.

> The boundary gate is a keyword **tripwire, not a guarantee**: it will miss
> action language it has no keyword for. See [docs/THREAT-MODEL.md](docs/THREAT-MODEL.md).

## Quickstart

```sh
python3 -m pip install -e .          # installs `looping-box` (optional; ./startday.sh works without it)
echo "Project docs: summarize the backlog." > inbox/notes.txt
looping-box run                      # ingest (phase 1) + one supervisor pass  (== ./startday.sh)
looping-box status
looping-box doctor                   # read-only health check with remedies
```

`looping-box` also fronts `review`, `worker`, `phase1` and `supervisor`
(`looping-box --help`). The older `looping-box-*` scripts still work.

**Exit codes** (`run`, `supervisor --once`, `worker`, `./startday.sh`): `0` ok, `1`
failed (e.g. a model outage; rerun after fixing) or busy (another run holds the lock),
`2` blocked (a held review or a resource limit needs you). Scripts and cron can branch
on `2` without parsing output.

**Your own workspace** (instead of running inside this repo): the package bundles
its default config, so after `pip install` you can scaffold anywhere:

```sh
looping-box init ~/my-workspace      # creates inbox/, config/, cache/, staging/, logs/ (never overwrites)
cd ~/my-workspace && looping-box doctor && looping-box run
```

`./startday.sh` prints the delta path and `review=clear|pending_review`, then runs
the workers (`context_builder` → `execution_engine`) once. (With a model role
enabled, that supervisor pass makes network calls; offline otherwise.) Drafts land in
`cache/workers/execution_engine/draft.md`. Set `LOOPING_BOX_ROOT` to use another
workspace directory (every CLI honors it, as does `--root`).

Hands-on walkthroughs: `./demo-1.sh`, `./demo-2.sh`, `./demo-3.sh` (each runs in a
throwaway workspace and is repeatable) and [docs/DEMOS.md](docs/DEMOS.md).

## Reviewing held items

```sh
looping-box review list
looping-box review show <review_id>
looping-box review approve <review_id> --note "why"   # interactive terminal; asks you to confirm
looping-box review reject  <review_id> --note "why"
```

- Decisions are **HMAC-signed** with a key from `$LOOPING_BOX_REVIEW_KEY` or
  `~/.config/looping-box/review.key` (created `0600` on first decision; a key file
  readable by others is refused). Keep it outside the workspace. A hand-written or
  edited record does not clear the gate, and a record that fails verification is
  reported as a warning (delta, `--status`, `review list`) rather than ignored
  silently. `looping-box-review key` shows which key is in use and whether it is usable.
- Each record stores the approver (`$LOOPING_BOX_APPROVER` or your login name), the
  note, and whether it was interactive. Approving needs a TTY unless
  `LOOPING_BOX_ALLOW_NONINTERACTIVE=1` (recorded). This is friction, not a security
  boundary against a process running as you.
- `blocked`-class items (credentials, secrets, production, ...) also need `--allow-blocked`.
  `forbidden` items can only be rejected.
- **Approving releases the item once** into the pipeline as ordinary work; it does
  not perform the requested action. Rejecting records the decision only.

`looping-box-review audit` verifies the hash chain of every audit log.

See [docs/RECOVERY.md](docs/RECOVERY.md) for clearing a pending review or a
resource-limit block.

## Layout

- `config/sops/phase1_ingestion.json` — routes, gate keywords, `max_excerpt_chars`,
  `max_file_bytes` (larger inputs are held, never read). `config/sops/*.md` is the
  human-readable SOP.
- `config/action_classes.json` — maps each gate keyword to a class
  (`safe_local_transform`, `review_required`, `blocked`, `forbidden`). Every gate
  keyword must be listed (a test enforces it).
- `config/super_loop.json` — worker routing and cycle limits
  (`max_files_per_cycle`, `max_payload_bytes`, `max_worker_runtime_seconds`).
- `inbox/` — input (`.md`, `.txt`, `.json`); git-ignored except `.gitkeep`.
- `cache/state/`, `cache/deltas/` (+ `archive/`), `cache/workers/<id>/` (+ `history/`
  keeps earlier batches), `cache/verifiers/`, `cache/supervisor/` — runtime state, git-ignored.
- `staging/` — `pending_review.json` index, `reviews/`, `approvals/`, `rejections/`.
- `logs/transactions/{phase1,supervisor,review}.jsonl` — append-only, **hash-chained**
  audit logs (tamper-evident, not tamper-proof; they are local files).
- `.world_state.json` (git-ignored) — supervisor history and recovery status.
- `docs/schemas/` — JSON schemas for the generated artifacts.

## Verify

```sh
python3 -m unittest discover -s tests
ruff check src tests      # optional
```

## Model access (optional)

Every role (`context_builder`, `execution_engine`, `verifier`) runs deterministically
offline by default. To enable one, copy `.env.example` to `.env` and set
`OPENROUTER_API_KEY` plus `MODEL_<ROLE>`. File excerpts are then sent to OpenRouter
for that role. `context_builder` and `execution_engine` never see held (gated) items,
but the `verifier` role does: it receives the held item's excerpt when you approve.
Tests and demos never hit the
network. `.env` is git-ignored; keep it `chmod 600`. Only `OPENROUTER_*` and `MODEL_*`
entries are read from it, and `OPENROUTER_BASE_URL` must be `http(s)://`.

## Guarantees (and their limits)

- Identical content is not reprocessed across sequential runs.
- Worker context is the SOP's bounded excerpts only; boundary-gated items are
  never drafted or sent to a model, while clean items in the same batch are.
- Gated language is held for review rather than processed. *Limit:* keyword-based.
- Decisions are signed, attributed, and audit-logged. *Limit:* same-user processes can read the key.
- phase 1, the supervisor, review decisions, and a bare `looping-box worker` pass are serialized by one project lock.
- The pending-review list is rebuilt from the inbox every run: removing a held file, or
  editing out its trigger language, clears its review.
- A batch that was ingested but not yet drafted (e.g. execution failed during a model
  outage) is carried into the next context package rather than replaced by it.
- Deterministic resource-limit blocks (file count, payload size) persist across
  reruns until the operator changes the limit. Worker-runtime blocks roll back
  local worker output and retry the same work; a transient timeout can clear on a rerun.
- A model outage becomes a visible `failed` status and is retried on the next run.
