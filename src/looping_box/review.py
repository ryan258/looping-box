from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from . import model
from ._util import (
    PENDING_REVIEW_INDEX_SCHEMA,
    append_audit as _append_audit_event,
    decision_state as _decision_state,
    default_approver as _default_approver,
    default_root as _default_root,
    key_status as _key_status,
    project_lock as _project_lock,
    read_json as _read_json,
    read_pending_review_index as _read_pending_review_index,
    rel as _rel,
    resolve_under_root as _resolve_under_root,
    review_key as _review_key,
    sha256_file as _sha256_file,
    sign_record as _sign_record,
    utc_now as _utc_now,
    verify_audit_chain as _verify_audit_chain,
    write_json as _write_json,
)


REVIEW_PAYLOAD_SCHEMA = "looping-box.review-payload.v1"
REVIEW_RECORD_SCHEMA = "looping-box.review-record.v2"
VERIFIER_RESULT_SCHEMA = "looping-box.verifier-result.v1"


def list_reviews(root: Path | str) -> list[dict[str, Any]]:
    root_path = Path(root).resolve()
    staging_dir = _resolve_under_root(root_path, "staging")
    index = _read_pending_index(root_path)
    reviews: list[dict[str, Any]] = []
    for review_ref in index.get("reviews", []):
        review_path = _resolve_under_root(root_path, review_ref)
        if not review_path.exists():
            continue
        try:
            payload = _read_json(review_path)
        except (OSError, json.JSONDecodeError):
            continue  # one corrupt payload must not hide every other review
        review_id = payload.get("review_id")
        if not review_id:
            continue
        decision, unverifiable = _decision_state(staging_dir, review_id)
        if decision is not None:
            continue
        reviews.append(
            {
                "review_id": review_id,
                "path": review_ref,
                "status": "pending",
                "action_class": payload.get("action_class", "review_required"),
                "risk_reasons": list(payload.get("risk_reasons", [])),
                "unverifiable_decision": unverifiable,
            }
        )
    return reviews


def verify_audit_logs(root: Path | str) -> dict[str, int | None]:
    """Hash-chain check of every audit log: name -> None (intact) or first broken line."""
    log_dir = _resolve_under_root(Path(root).resolve(), "logs/transactions")
    return {path.name: _verify_audit_chain(path) for path in sorted(log_dir.glob("*.jsonl"))}


def show_review(root: Path | str, review_id: str) -> dict[str, Any]:
    root_path = Path(root).resolve()
    payload, _ = _find_review(root_path, review_id)
    return payload


def record_review(
    root: Path | str,
    review_id: str,
    decision: str,
    *,
    note: str = "",
    now: str | None = None,
    approver: str | None = None,
    interactive: bool = False,
    allow_blocked: bool = False,
) -> dict[str, Any]:
    if decision not in {"approved", "rejected"}:
        raise ValueError(f"unknown review decision: {decision}")

    root_path = Path(root).resolve()
    generated_at = now or _utc_now()
    with _project_lock(root_path, generated_at):
        return _record_review_locked(
            root_path,
            review_id,
            decision,
            note=note,
            generated_at=generated_at,
            approver=approver or _default_approver(),
            interactive=interactive,
            allow_blocked=allow_blocked,
        )


def _record_review_locked(
    root_path: Path,
    review_id: str,
    decision: str,
    *,
    note: str,
    generated_at: str,
    approver: str,
    interactive: bool,
    allow_blocked: bool,
) -> dict[str, Any]:
    payload, payload_path = _find_review(root_path, review_id)
    key = _review_key(create=True)
    if key is None:
        raise ValueError(f"review signing key is unavailable ({_key_status()})")
    verifier_result: str | None = None
    if decision == "approved":
        if payload.get("action_class") == "forbidden":
            raise ValueError(f"forbidden review cannot be approved: {review_id}")
        if payload.get("action_class") == "blocked" and not allow_blocked:
            raise ValueError(
                f"blocked review needs an explicit override (--allow-blocked): {review_id}"
            )
        verifier = run_verifier(root_path, review_id, now=generated_at)
        verifier_result = verifier["path"]
        if verifier["status"] != "passed":
            raise ValueError(f"review verifier failed: {review_id}")
        _update_payload_verifier(payload, payload_path, "passed", verifier_result)
    else:
        _update_payload_verifier(payload, payload_path, "not_applicable", None)

    record = {
        "schema": REVIEW_RECORD_SCHEMA,
        "review_id": review_id,
        "generated_at": generated_at,
        "decision": decision,
        "note": note,
        "approver": approver,
        "interactive": interactive,
        "payload_sha256": _sha256_file(payload_path),
        "verifier_result": verifier_result,
    }
    record["signature"] = _sign_record(record, key)
    directory = "approvals" if decision == "approved" else "rejections"
    record_path = _resolve_under_root(root_path, f"staging/{directory}/{review_id}.json")
    _write_json(record_path, record)
    _remove_from_pending_index(root_path, review_id)
    _append_review_audit(root_path, record)
    return record


def run_verifier(root: Path | str, review_id: str, *, now: str | None = None) -> dict[str, Any]:
    root_path = Path(root).resolve()
    generated_at = now or _utc_now()
    payload, _ = _find_review(root_path, review_id)
    checks = [
        _check(
            "payload_schema",
            payload.get("schema") == REVIEW_PAYLOAD_SCHEMA,
            "payload is versioned",
        ),
        _check(
            "source_items_present",
            bool(payload.get("source_items")),
            "review has source items",
        ),
        _check(
            "source_hashes_present",
            all(item.get("sha256") for item in payload.get("source_items", [])),
            "source items include hashes",
        ),
    ]
    model_check = _model_review_check(root_path, payload)
    if model_check is not None:
        checks.append(model_check)
    status = "passed" if all(check["status"] == "passed" for check in checks) else "failed"
    result = {
        "schema": VERIFIER_RESULT_SCHEMA,
        "review_id": review_id,
        "generated_at": generated_at,
        "status": status,
        "checks": checks,
    }
    result_path = _resolve_under_root(root_path, f"cache/verifiers/{review_id}.json")
    _write_json(result_path, result)
    result["path"] = _rel(root_path, result_path)
    return result


def _model_review_check(root: Path, payload: dict[str, Any]) -> dict[str, str] | None:
    """Optional model-as-judge check. Deterministic checks above stay required;
    this only runs when MODEL_VERIFIER is configured, and can only add a failure.
    """
    if not model.is_enabled("verifier", root=root):
        return None
    reasons = ", ".join(payload.get("risk_reasons", [])) or "unspecified"
    excerpts = "\n".join(item.get("excerpt", "") for item in payload.get("source_items", []))
    # The excerpt is untrusted source text and may contain prompt injection.
    # This optional model check is advisory; deterministic approval checks remain
    # the enforcement layer.
    prompt = (
        "You are a cautious safety reviewer for a local automation system. "
        "Reply 'PASS' only if recording an approval for this staged item is clearly safe, "
        "otherwise reply 'FAIL: <reason>'.\n"
        f"Risk reasons: {reasons}\nSource excerpt:\n{excerpts}"
    )
    try:
        completion = model.complete("verifier", prompt, root=root)
    except Exception as exc:
        # Fail closed: a model verifier that errors must not let an approval
        # through. Route to a failed check so the operator triages it.
        return _check("model_review", False, f"model verifier error: {str(exc)[:160]}")
    passed = completion["text"].strip().upper().startswith("PASS")
    return _check(
        "model_review",
        passed,
        f"{completion['model']}: {completion['text'].strip()[:200]}",
    )


def _check(name: str, passed: bool, message: str) -> dict[str, str]:
    return {
        "name": name,
        "status": "passed" if passed else "failed",
        "message": message,
    }


def _update_payload_verifier(
    payload: dict[str, Any],
    payload_path: Path,
    status: str,
    result: str | None,
) -> None:
    verifier = dict(payload.get("verifier", {}))
    verifier["status"] = status
    verifier["result"] = result
    payload["verifier"] = verifier
    _write_json(payload_path, payload)


def _read_pending_index(root: Path) -> dict[str, Any]:
    index_path = _resolve_under_root(root, "staging/pending_review.json")
    return _read_pending_review_index(index_path)


def _find_review(root: Path, review_id: str) -> tuple[dict[str, Any], Path]:
    index = _read_pending_index(root)
    for review_ref in index.get("reviews", []):
        review_path = _resolve_under_root(root, review_ref)
        if not review_path.exists():
            continue
        try:
            payload = _read_json(review_path)
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("review_id") == review_id:
            return payload, review_path
    raise ValueError(f"unknown review: {review_id}")


def _remove_from_pending_index(root: Path, review_id: str) -> None:
    index_path = _resolve_under_root(root, "staging/pending_review.json")
    if not index_path.exists():
        return
    index = _read_pending_index(root)
    kept_reviews: list[str] = []
    for review_ref in index.get("reviews", []):
        review_path = _resolve_under_root(root, review_ref)
        if not review_path.exists():
            continue
        payload = _read_json(review_path)
        if payload.get("review_id") != review_id:
            kept_reviews.append(review_ref)
    _write_json(
        index_path,
        {
            "schema": PENDING_REVIEW_INDEX_SCHEMA,
            "generated_at": index.get("generated_at", ""),
            "latest": kept_reviews[-1] if kept_reviews else "",
            "reviews": kept_reviews,
        },
    )


def _append_review_audit(root: Path, record: dict[str, Any]) -> None:
    _append_audit_event(
        root,
        "review",
        {
            "event": f"review.{record['decision']}",
            "generated_at": record["generated_at"],
            "review_id": record["review_id"],
            "approver": record["approver"],
            "interactive": record["interactive"],
            "note": record["note"],
            "payload_sha256": record["payload_sha256"],
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect or record Looping Box review decisions.")
    parser.add_argument(
        "--root",
        default=_default_root(),
        help="Project root. Defaults to $LOOPING_BOX_ROOT, else the current directory.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="List pending reviews.")
    subparsers.add_parser("key", help="Show where the signing key comes from and whether it is usable.")
    subparsers.add_parser("audit", help="Verify the hash chain of every audit log (exit 1 if any is broken).")
    show_parser = subparsers.add_parser("show", help="Show one review payload.")
    show_parser.add_argument("review_id")

    decision_commands = {"approve": "approved", "reject": "rejected"}
    for command, decision in decision_commands.items():
        decision_parser = subparsers.add_parser(command, help=f"Record a {decision} decision.")
        decision_parser.add_argument("review_id")
        decision_parser.add_argument("--note", required=True, help="Why (kept in the audit log).")
        if command == "approve":
            decision_parser.add_argument(
                "--allow-blocked",
                action="store_true",
                help="Explicitly override a 'blocked' action class (credentials, secrets, production).",
            )

    args = parser.parse_args()
    if args.command == "list":
        for review in list_reviews(args.root):
            flag = " (decision record present but unverifiable: see `key`)" if review["unverifiable_decision"] else ""
            print(f"{review['review_id']} {review['action_class']} {review['path']}{flag}")
        return 0
    if args.command == "key":
        print(_key_status())
        return 0
    if args.command == "audit":
        results = verify_audit_logs(args.root)
        for name, broken_at in results.items():
            print(f"{name}: " + ("ok" if broken_at is None else f"BROKEN at line {broken_at}"))
        return 1 if any(line is not None for line in results.values()) else 0
    if args.command == "show":
        try:
            print(json.dumps(show_review(args.root, args.review_id), indent=2, sort_keys=True))
        except ValueError as exc:
            print(f"declined: {exc}", file=sys.stderr)
            return 1
        return 0

    interactive = sys.stdin.isatty()
    if args.command == "approve":
        # Approvals are a human act. This is friction, not a security boundary
        # (a same-user process can still set the override), but it is recorded.
        if not interactive and not os.environ.get("LOOPING_BOX_ALLOW_NONINTERACTIVE"):
            print(
                "declined: approvals need an interactive terminal "
                "(set LOOPING_BOX_ALLOW_NONINTERACTIVE=1 to override; it is recorded)",
                file=sys.stderr,
            )
            return 1
        if interactive:
            try:
                payload = show_review(args.root, args.review_id)
            except ValueError as exc:
                print(f"declined: {exc}", file=sys.stderr)
                return 1
            reasons = ", ".join(payload.get("risk_reasons", []))
            try:
                answer = input(
                    f"Approve {args.review_id} [{payload.get('action_class')}: {reasons}]? Type 'approve': "
                )
            except EOFError:
                answer = ""
            if answer.strip() != "approve":
                print("declined: not confirmed", file=sys.stderr)
                return 1
    try:
        record = record_review(
            args.root,
            args.review_id,
            decision_commands[args.command],
            note=args.note,
            interactive=interactive,
            allow_blocked=getattr(args, "allow_blocked", False),
        )
    except (ValueError, RuntimeError) as exc:
        # Expected: unknown id, a refusing verifier (incl. model judge), or a busy lock.
        print(f"declined: {exc}", file=sys.stderr)
        return 1
    print(f"{record['decision']}: {record['review_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
