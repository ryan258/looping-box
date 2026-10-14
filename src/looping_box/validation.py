"""T01 contracts: pure recursive validation and narrowly specified normalization.

Filesystem containment, source freshness and cryptographic binding are owned by
subsequent tasks. Path validation here is lexical; historical paths need not exist.
Schemas are defined here so source and installed runtime do not depend on docs/.
The identical, self-contained JSON documents are mirrored in docs and templates.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

from .schema import validate

WORKERS = ["context_builder", "execution_engine"]
STATUSES = ["idle", "complete", "blocked", "failed"]
CLASSES = ["safe_local_transform", "review_required", "blocked", "forbidden"]
EDGES = [("phase1_delta", "context_builder"), ("context_builder", "execution_engine")]


class ValidationError(ValueError):
    """Stable error code, artifact identity and field path, without input values."""

    def __init__(self, artifact: str, detail: str, *, code: str = "invalid_artifact") -> None:
        self.code = code
        self.artifact = artifact
        super().__init__(f"{code}: {artifact}: {detail}")


def ref(name: str) -> dict[str, Any]:
    return {"$ref": f"#/$defs/{name}"}


def array(item: dict[str, Any], **limits: Any) -> dict[str, Any]:
    return {"type": "array", "items": item, **limits}


def obj(properties: dict[str, Any], *, optional: tuple[str, ...] = (), strict: bool = False) -> dict[str, Any]:
    return {"type": "object", "properties": properties,
            "required": [key for key in properties if key not in optional],
            "additionalProperties": not strict}


def nullable(rule: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [rule, {"type": "null"}]}


def mapping(item: dict[str, Any], keys: dict[str, Any] | None = None) -> dict[str, Any]:
    result = {"type": "object", "additionalProperties": item}
    if keys is not None:
        result["propertyNames"] = keys
    return result


STRING = {"type": "string"}
NONBLANK = {"type": "string", "pattern": r"\S"}
HASH = {"type": "string", "pattern": r"^[0-9a-f]{64}(?![\s\S])"}
REVIEW_ID = {"type": "string", "pattern": r"^review-[0-9a-f]{16}(?![\s\S])"}
TIMESTAMP = {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z(?![\s\S])", "format": "date-time"}
PATH = {"type": "string", "pattern": r"^(?!/)(?![A-Za-z]:)(?!.*\\)(?!.*(?:^|/)\.{1,2}(?:/|$))[^/\x00-\x1f]+(?:/[^/\x00-\x1f]+)*(?![\s\S])"}
# JSON Schema regexes use JSON/ECMAScript escapes, not Python-only \x escapes.
PATH["pattern"] = PATH["pattern"].replace(r"\x00", r"\u0000").replace(r"\x1f", r"\u001f")
NAT = {"type": "integer", "minimum": 0}
POSITIVE = {"type": "integer", "minimum": 1}
BOOL = {"type": "boolean"}
WORKER = {"type": "string", "enum": WORKERS}
STATUS = {"type": "string", "enum": STATUSES}
CLASS = {"type": "string", "enum": CLASSES}
DELTA_ID = {"type": "string", "pattern": r"^phase1-delta-[A-Za-z0-9]+(?:-(?:[2-9]|[1-9][0-9]+))?(?![\s\S])"}

DEFS: dict[str, dict[str, Any]] = {
    "Path": PATH, "Hash": HASH, "Timestamp": TIMESTAMP, "ReviewID": REVIEW_ID,
    "WorkerID": WORKER, "Status": STATUS, "Class": CLASS,
    "ProcessedFile": obj({"sha256": ref("Hash"), "size_bytes": NAT, "processed_at": ref("Timestamp")}),
    "FirstSeen": obj({"first_seen_path": ref("Path"), "first_seen_at": ref("Timestamp")}),
    "DeltaSummary": obj({"scanned": NAT, "changed": NAT, "skipped": NAT, "requires_review": BOOL}),
    "IngestionRun": obj({"generated_at": ref("Timestamp"), "delta_path": nullable(ref("Path")), "summary": ref("DeltaSummary")}),
    "Change": obj({"relative_path": ref("Path"), "sha256": ref("Hash"), "size_bytes": NAT,
                   "matched_routes": array(STRING), "review_reasons": array(STRING), "excerpt": STRING,
                   "approved_review": ref("ReviewID")}, optional=("approved_review",)),
    "Skip": obj({"relative_path": ref("Path"), "sha256": ref("Hash"),
                 "reason": {"enum": ["already_processed", "review_decision_recorded"]}}),
    "Warning": obj({"code": STRING, "review_id": ref("ReviewID"), "relative_path": ref("Path"), "message": STRING}),
    "Gate": obj({"status": {"enum": ["clear", "pending_review"]}, "payload": nullable(ref("Path")), "reasons": array(STRING)}),
    "ContextItem": obj({"relative_path": ref("Path"), "sha256": ref("Hash"), "matched_routes": array(STRING),
                        "excerpt": STRING, "source_delta": ref("Path"), "approved_review": ref("ReviewID")},
                       optional=("approved_review",)),
    "DraftItem": obj({"relative_path": ref("Path"), "matched_routes": array(STRING), "draft": STRING,
                      "approved_review": ref("ReviewID"), "model": STRING, "response_sha256": ref("Hash")},
                     optional=("approved_review", "model", "response_sha256")),
    "Error": obj({"code": NONBLANK, "message": STRING}),
    "WorkerSummary": obj({"status": ref("Status"), "last_run_at": ref("Timestamp"), "last_output": ref("Path")}),
    "Edge": {"oneOf": [obj({"from": {"const": source}, "to": {"const": target}}) for source, target in EDGES]},
    "Recovery": obj({"last_error": nullable(STRING), "blocked_reason": nullable(STRING),
                     "pending_review_payload": nullable(ref("Path")), "operator_action_required": BOOL}),
    "SupervisorRun": obj({"generated_at": ref("Timestamp"), "status": ref("Status"),
                          "plan": array(ref("WorkerID")), "source_deltas": array(ref("Path"))}),
}
DEFS["Gate"]["if"] = {"properties": {"status": {"const": "clear"}}}
DEFS["Gate"]["then"] = {"properties": {"payload": {"type": "null"}, "reasons": {"maxItems": 0}}}
DEFS["BlockedItem"] = obj({**DEFS["ContextItem"]["properties"], "review_reasons": array(STRING, minItems=1),
                           "pending_review_payload": ref("Path")}, optional=("approved_review",))
DEFS["DraftItem"]["dependentRequired"] = {"model": ["response_sha256"], "response_sha256": ["model"]}


def artifact(marker: str, properties: dict[str, Any], *, optional: tuple[str, ...] = ()) -> dict[str, Any]:
    return obj({"schema": {"const": "looping-box." + marker}, **properties}, optional=optional)


RAW_SCHEMAS = {
    "phase1_state": artifact("phase1.state.v1", {
        "processed_files": mapping(ref("ProcessedFile"), ref("Path")),
        "processed_hashes": mapping(ref("FirstSeen"), ref("Hash")), "runs": array(ref("IngestionRun")),
        "present_paths": array(ref("Path")), "last_run_at": ref("Timestamp"), "last_delta_path": nullable(ref("Path")),
    }, optional=("present_paths", "last_run_at", "last_delta_path")),
    "phase1_delta": artifact("phase1.delta.v1", {
        "run_id": DELTA_ID, "generated_at": ref("Timestamp"),
        "sop": obj({"path": ref("Path"), "sha256": ref("Hash"), "name": STRING}),
        "inputs": obj({"root": {"type": "string", "pattern": r"^/"}, "input_dir": ref("Path")}),
        "summary": ref("DeltaSummary"), "changes": array(ref("Change")), "skipped": array(ref("Skip")),
        "warnings": array(ref("Warning")), "reconciliation": obj({
            "scanned_paths": array(ref("Path"), uniqueItems=True), "removed_paths": array(ref("Path"), uniqueItems=True)}),
        "boundary_gate": ref("Gate"), "delta_path": ref("Path"),
    }, optional=("warnings", "reconciliation")),
    "pending_review_index": artifact("pending-review-index.v1", {
        "generated_at": ref("Timestamp"), "latest": {"anyOf": [ref("Path"), {"const": ""}]},
        "reviews": array(ref("Path"), uniqueItems=True)}),
    "review_payload": artifact("review-payload.v1", {
        "review_id": ref("ReviewID"), "generated_at": ref("Timestamp"), "source": {"const": "phase1"},
        "notification_message": STRING, "action_class": ref("Class"), "risk_reasons": array(STRING),
        "source_items": array(ref("Change"), minItems=1, maxItems=1), "generated_artifacts": array(ref("Path")),
        "verifier": obj({"required": BOOL, "status": {"enum": ["pending", "passed", "failed", "not_applicable"]},
                         "result": nullable(ref("Path"))}), "suggested_verification": array(STRING)}),
    "review_record": artifact("review-record.v2", {
        "review_id": ref("ReviewID"), "generated_at": ref("Timestamp"), "decision": {"enum": ["approved", "rejected"]},
        "note": NONBLANK, "approver": NONBLANK, "interactive": BOOL, "payload_sha256": ref("Hash"),
        "verifier_result": nullable(ref("Path")), "signature": ref("Hash")}),
    "verifier_result": artifact("verifier-result.v1", {
        "review_id": ref("ReviewID"), "generated_at": ref("Timestamp"), "status": {"enum": ["passed", "failed"]},
        "checks": array(obj({"name": STRING, "status": {"enum": ["passed", "failed"]}, "message": STRING}), minItems=1)}),
    "worker_state": artifact("worker.state.v1", {
        "worker_id": ref("WorkerID"), "consumed_inputs": array(ref("Path")), "last_run_at": ref("Timestamp"),
        "last_context_sha256": ref("Hash"), "last_model": nullable(STRING),
    }, optional=("last_run_at", "last_context_sha256", "last_model")),
    "context_package": artifact("worker.context-package.v1", {
        "worker_id": {"const": "context_builder"}, "generated_at": ref("Timestamp"), "status": {"enum": ["complete", "blocked"]},
        "source_deltas": array(ref("Path")), "items": array(ref("ContextItem")), "blocked_inputs": array(ref("BlockedItem")),
        "synthesis": obj({"text": STRING, "model": STRING, "response_sha256": ref("Hash")}),
    }, optional=("synthesis",)),
    "execution_draft": artifact("execution-draft.v1", {
        "generated_at": ref("Timestamp"), "source_context": ref("Path"), "source_context_sha256": ref("Hash"),
        "items": array(ref("DraftItem")), "held_for_review": array(ref("Path"))}),
    "worker_output": artifact("worker.output.v1", {
        "worker_id": ref("WorkerID"), "run_id": STRING, "generated_at": ref("Timestamp"), "status": ref("Status"),
        "inputs": obj({"source_deltas": array(ref("Path"))}), "outputs": obj({"artifacts": array(ref("Path"))}),
        "errors": array(ref("Error")), "runtime_seconds": {"type": "number", "minimum": 0},
        "planned_actions": array(obj({"action": {"enum": ["quarantine", "write", "archive"]},
                                      "source": ref("Path"), "destination": ref("Path")}, optional=("source",))),
    }, optional=("runtime_seconds", "planned_actions")),
    "world_state": artifact("world-state.v1", {
        "registered_loops": array(STRING), "worker_states": mapping(ref("WorkerSummary"), ref("WorkerID")),
        "observed_deltas": array(ref("Path")), "dirty": BOOL, "dependencies": array(ref("Edge")),
        "recovery": ref("Recovery"), "runs": array(ref("SupervisorRun")), "leases": {"type": "object"},
        "last_run_at": nullable(ref("Timestamp")),
    }, optional=("last_run_at",)),
    "supervisor_blocked": artifact("supervisor.blocked.v1", {
        "generated_at": ref("Timestamp"), "reason": {"enum": ["file_count_limit", "payload_size_limit", "worker_timeout", "routing_disabled"]},
        "message": STRING, "source_deltas": array(ref("Path"))}),
    "supervisor_run": artifact("supervisor.run.v1", {
        "generated_at": ref("Timestamp"), "status": ref("Status"), "plan": array(ref("WorkerID")),
        "worker_outputs": array(ref("WorkerOutput")), "recovery": ref("Recovery")}),
}
DEFS["WorkerOutput"] = RAW_SCHEMAS["worker_output"]
AUDIT_COMMON = {"generated_at": ref("Timestamp"), "prev": {"anyOf": [ref("Hash"), {"const": ""}]}, "hash": ref("Hash")}
AUDIT_FIELDS = {
    "phase1.run": {"run_id": DELTA_ID, "changed": NAT, "skipped": NAT, "gate": {"enum": ["clear", "pending_review"]},
                   "review_ids": array(ref("ReviewID")), "released_review_ids": array(ref("ReviewID")), "warnings": array(STRING)},
    "supervisor.run": {"status": ref("Status"), "plan": array(ref("WorkerID")), "source_deltas": array(ref("Path")),
                       "worker_statuses": mapping(ref("Status"), ref("WorkerID")), "blocked_reason": nullable(STRING)},
    "review.approved": {"review_id": ref("ReviewID"), "approver": NONBLANK, "interactive": BOOL,
                        "note": NONBLANK, "payload_sha256": ref("Hash")},
}
AUDIT_FIELDS["review.rejected"] = AUDIT_FIELDS["review.approved"]
RAW_SCHEMAS["audit_event"] = artifact("audit-event.v1", {**AUDIT_COMMON, "event": {"enum": list(AUDIT_FIELDS)}})
RAW_SCHEMAS["audit_event"]["allOf"] = [
    {"if": {"properties": {"event": {"const": event}}}, "then": obj(fields)}
    for event, fields in AUDIT_FIELDS.items()
]
CONFIG_SCHEMAS = {
    "phase1_ingestion": obj({
        "name": STRING, "allowed_extensions": array({"type": "string", "pattern": r"^\..+$"}, uniqueItems=True),
        "routes": array(obj({"label": NONBLANK, "keywords": array(NONBLANK)}, strict=True)),
        "boundary_gate": obj({"requires_review_keywords": array(NONBLANK, uniqueItems=True), "notification_message": STRING}, strict=True),
        "max_excerpt_chars": {"type": "integer", "minimum": 3}, "max_file_bytes": POSITIVE,
    }, strict=True),
    "action_classes": obj({"schema": {"const": "looping-box.action-classes.v1"},
        "default_class": {"enum": CLASSES[1:]},
        "classes": obj({name: array(NONBLANK, uniqueItems=True) for name in CLASSES}, optional=tuple(CLASSES), strict=True)}, strict=True),
    "super_loop": obj({"schema": {"const": "looping-box.super-loop.config.v1"},
        "max_files_per_cycle": POSITIVE, "max_payload_bytes": POSITIVE,
        "max_worker_runtime_seconds": {"type": "number", "exclusiveMinimum": 0, "maximum": sys.float_info.max}, "stale_lock_seconds": POSITIVE,
        "workers": array(ref("WorkerID"), uniqueItems=True),
        "dependencies": array({"oneOf": [obj({"from": {"const": a}, "to": {"const": b}}, strict=True) for a, b in EDGES]}, uniqueItems=True),
    }, strict=True),
}
RAW_SCHEMAS.update(CONFIG_SCHEMAS)


def _collect_definitions(node: Any, needed: dict[str, Any]) -> None:
    if isinstance(node, dict):
        reference = node.get("$ref", "")
        if reference.startswith("#/$defs/"):
            key = reference[len('#/$defs/'):]
            if key not in needed:
                needed[key] = copy.deepcopy(DEFS[key])
                _collect_definitions(needed[key], needed)
        for child in node.values():
            _collect_definitions(child, needed)
    elif isinstance(node, list):
        for child in node:
            _collect_definitions(child, needed)


def schema_documents() -> dict[str, dict[str, Any]]:
    """Self-contained standard JSON Schema documents with only referenced defs."""
    result = {}
    for name, raw in RAW_SCHEMAS.items():
        document = {"$schema": "https://json-schema.org/draft/2020-12/schema", "title": name, **copy.deepcopy(raw)}
        needed: dict[str, Any] = {}

        _collect_definitions(document, needed)
        if needed:
            document["$defs"] = needed
        document["$comment"] = (
            "Runtime also checks cross-field relationships: pending latest/last, disjoint reconciliation, "
            "unique config labels/terms and consistent workers/edges; policy coverage is checked across configs. "
            "Numbers are finite; integer fields exclude booleans. Paths are lexical; filesystem checks are separate."
        )
        result[name] = document
    return result


SCHEMAS = schema_documents()


def validate_data(data: Any, kind: str, artifact_name: str, *, input_dir: str = "inbox",
                  worker_id: str | None = None) -> dict[str, Any]:
    """Return a copy, normalizing only documented optional legacy artifact fields."""
    code = "invalid_config" if kind in CONFIG_SCHEMAS else "invalid_artifact"
    rule = SCHEMAS[kind]
    try:
        validate(data, rule)
        _relationships(data, kind)
        if worker_id is not None and data["worker_id"] != worker_id:
            raise ValueError("$.worker_id: does not match this worker")
        result = copy.deepcopy(data)
    except (ValueError, RecursionError) as exc:
        raise ValidationError(artifact_name, str(exc) if not isinstance(exc, RecursionError) else "$: nesting too deep", code=code) from exc
    if kind == "phase1_delta":
        result.setdefault("warnings", [])
        result.setdefault("reconciliation", {"scanned_paths": [], "removed_paths": []})
    elif kind == "phase1_state" and "present_paths" not in result:
        prefix = input_dir.rstrip("/") + "/"
        result["present_paths"] = [name for name in result["processed_files"] if name.startswith(prefix)]
    elif kind == "world_state":
        result.setdefault("last_run_at", None)
    elif kind == "worker_output":
        result.setdefault("planned_actions", [])
    return result


def _relationships(data: dict[str, Any], kind: str) -> None:
    if kind == "pending_review_index" and data["latest"] != (data["reviews"][-1] if data["reviews"] else ""):
        raise ValueError("$.latest: must equal the last review or empty string")
    if kind == "phase1_delta" and "reconciliation" in data:
        rec = data["reconciliation"]
        if set(rec["scanned_paths"]) & set(rec["removed_paths"]):
            raise ValueError("$.reconciliation.removed_paths: overlaps scanned_paths")
    if kind == "phase1_ingestion":
        labels = [route["label"] for route in data["routes"]]
        extensions = [ext.lower() for ext in data["allowed_extensions"]]
        if len(labels) != len(set(labels)):
            raise ValueError("$.routes: duplicate labels")
        if len(extensions) != len(set(extensions)):
            raise ValueError("$.allowed_extensions: duplicate extensions")
    if kind == "action_classes" and "classes" in data:
        seen: set[str] = set()
        for name, terms in data["classes"].items():
            for index, term in enumerate(terms):
                key = term.lower()
                if key in seen:
                    raise ValueError(f"$.classes.{name}[{index}]: duplicate or ambiguous mapping")
                seen.add(key)
    if kind == "super_loop" and "workers" in data and "dependencies" in data:
        for index, edge in enumerate(data["dependencies"]):
            if edge["to"] not in data["workers"] or (edge["from"] != "phase1_delta" and edge["from"] not in data["workers"]):
                raise ValueError(f"$.dependencies[{index}]: edge refers to a disabled worker")


def validate_policy(sop: dict[str, Any], classes: dict[str, Any], artifact_name: str) -> None:
    """Validate individually checked configs together without changing gate terms."""
    mapped = {term.lower() for terms in classes["classes"].values() for term in terms}
    for index, term in enumerate(sop["boundary_gate"]["requires_review_keywords"]):
        if term.lower() not in mapped:
            raise ValidationError(artifact_name, f"$.boundary_gate.requires_review_keywords[{index}]: no explicit action class", code="invalid_config")


def read_validated(path: Path, kind: str, **options: Any) -> dict[str, Any]:
    """Read one contract-bearing JSON object; parsing errors include the artifact."""
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except (ValueError, RecursionError) as exc:
        code = "invalid_config" if kind in CONFIG_SCHEMAS else "invalid_artifact"
        raise ValidationError(str(path), "$: invalid JSON", code=code) from exc
    return validate_data(data, kind, str(path), **options)


def validate_review_arguments(review_id: Any, generated_at: Any, decision: Any,
                              note: Any, approver: Any, interactive: Any) -> None:
    """Validate decision inputs with the same field contracts as the final record."""
    values = dict(review_id=review_id, generated_at=generated_at, decision=decision,
                  note=note, approver=approver, interactive=interactive)
    for field, value in values.items():
        rule = {**SCHEMAS["review_record"]["properties"][field],
                "$defs": SCHEMAS["review_record"]["$defs"]}
        try:
            validate(value, rule, "$." + field)
        except ValueError as exc:
            raise ValidationError("review decision", str(exc)) from exc
