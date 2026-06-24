from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .action_policy import classify_action
from .phase1 import _rel, _resolve_under_root, _sha256_file, _utc_now, _write_json


PENDING_REVIEW_INDEX_SCHEMA = "looping-box.pending-review-index.v1"
REVIEW_PAYLOAD_SCHEMA = "looping-box.review-payload.v1"
REVIEW_RECORD_SCHEMA = "looping-box.review-record.v1"
VERIFIER_RESULT_SCHEMA = "looping-box.verifier-result.v1"

def list_reviews(root: Path | str) -> list[dict[str, Any]]:
    root_path = Path(root).resolve()
    index = _read_pending_index(root_path)
    reviews: list[dict[str, Any]] = []
    for review_ref in index.get("reviews", []):
        review_path = _resolve_under_root(root_path, review_ref)
        if not review_path.exists():
            continue
        payload = _read_json(review_path)
        review_id = payload.get("review_id")
        if not review_id or _decision_record_exists(root_path, review_id):
            continue
        reviews.append(
            {
                "review_id": review_id,
                "path": review_ref,
                "status": "pending",
                "action_class": payload.get("action_class", "review_required"),
                "risk_reasons": list(payload.get("risk_reasons", [])),
            }
        )
    return reviews


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
) -> dict[str, Any]:
    if decision not in {"approved", "rejected"}:
        raise ValueError(f"unknown review decision: {decision}")

    root_path = Path(root).resolve()
    generated_at = now or _utc_now()
    payload, payload_path = _find_review(root_path, review_id)
    verifier_result: str | None = None
    if decision == "approved":
        verifier = run_verifier(root_path, review_id, now=generated_at)
        verifier_result = verifier["path"]
        if verifier["status"] != "passed":
            raise ValueError(f"review verifier failed: {review_id}")

    record = {
        "schema": REVIEW_RECORD_SCHEMA,
        "review_id": review_id,
        "generated_at": generated_at,
        "decision": decision,
        "note": note,
        "payload_sha256": _sha256_file(payload_path),
        "verifier_result": verifier_result,
    }
    directory = "approvals" if decision == "approved" else "rejections"
    record_path = _resolve_under_root(root_path, f"staging/{directory}/{review_id}.json")
    _write_json(record_path, record)
    _remove_from_pending_index(root_path, review_id)
    _append_review_audit(root_path, generated_at, decision, review_id)
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


def _check(name: str, passed: bool, message: str) -> dict[str, str]:
    return {
        "name": name,
        "status": "passed" if passed else "failed",
        "message": message,
    }


def _read_pending_index(root: Path) -> dict[str, Any]:
    index_path = _resolve_under_root(root, "staging/pending_review.json")
    if not index_path.exists():
        return {
            "schema": PENDING_REVIEW_INDEX_SCHEMA,
            "generated_at": "",
            "latest": "",
            "reviews": [],
        }
    index = _read_json(index_path)
    if index.get("schema") != PENDING_REVIEW_INDEX_SCHEMA:
        raise ValueError(f"unsupported pending review index: {index.get('schema')!r}")
    index.setdefault("reviews", [])
    return index


def _find_review(root: Path, review_id: str) -> tuple[dict[str, Any], Path]:
    index = _read_pending_index(root)
    for review_ref in index.get("reviews", []):
        review_path = _resolve_under_root(root, review_ref)
        if not review_path.exists():
            continue
        payload = _read_json(review_path)
        if payload.get("review_id") == review_id:
            return payload, review_path
    raise ValueError(f"unknown review: {review_id}")


def _decision_record_exists(root: Path, review_id: str) -> bool:
    return (
        _resolve_under_root(root, f"staging/approvals/{review_id}.json").exists()
        or _resolve_under_root(root, f"staging/rejections/{review_id}.json").exists()
    )


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


def _append_review_audit(root: Path, generated_at: str, decision: str, review_id: str) -> None:
    audit_path = _resolve_under_root(root, "logs/transactions/review.jsonl")
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "schema": "looping-box.audit-event.v1",
        "generated_at": generated_at,
        "event": f"review.{decision}",
        "review_id": review_id,
    }
    with audit_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect or record Looping Box review decisions.")
    parser.add_argument("--root", default=".", help="Project root. Defaults to the current directory.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="List pending reviews.")
    show_parser = subparsers.add_parser("show", help="Show one review payload.")
    show_parser.add_argument("review_id")

    decision_commands = {"approve": "approved", "reject": "rejected"}
    for command, decision in decision_commands.items():
        decision_parser = subparsers.add_parser(command, help=f"Record a {decision} decision.")
        decision_parser.add_argument("review_id")
        decision_parser.add_argument("--note", default="")

    args = parser.parse_args()
    if args.command == "list":
        for review in list_reviews(args.root):
            print(f"{review['review_id']} {review['action_class']} {review['path']}")
        return 0
    if args.command == "show":
        print(json.dumps(show_review(args.root, args.review_id), indent=2, sort_keys=True))
        return 0
    record = record_review(args.root, args.review_id, decision_commands[args.command], note=args.note)
    print(f"{record['decision']}: {record['review_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
