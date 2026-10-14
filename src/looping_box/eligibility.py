"""Current source evidence shared by review and worker disclosure checks."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ._util import decision_state, read_review_payload, resolve_under_root, review_identity
from .action_policy import classify_reasons
from .paths import UnsafePathError
from .phase1 import _excerpt, _gate_text, _match_keywords, _match_routes, _snapshot_input, _validated_sop


class SourceReingestRequired(ValueError):
    """Stored worker evidence no longer permits disclosure or drafting."""


def current_source(root: Path, relative_path: str, sop: dict[str, Any]) -> dict[str, Any]:
    path = resolve_under_root(root, relative_path)
    if any(part.startswith(".") for part in Path(relative_path).parts) or path.suffix.lower() not in {
        extension.lower() for extension in sop["allowed_extensions"]
    }:
        raise ValueError("source is no longer eligible under the current SOP")
    digest, size, text = _snapshot_input(path, sop["max_file_bytes"])
    if text is None:
        routes, reasons, excerpt = [], ["oversized_input"], ""
    else:
        routes = _match_routes(text, sop["routes"])
        reasons = _match_keywords(_gate_text(path, text), sop["boundary_gate"]["requires_review_keywords"])
        excerpt = _excerpt(text, sop["max_excerpt_chars"])
    return {"relative_path": relative_path, "sha256": digest, "size_bytes": size,
            "matched_routes": routes, "review_reasons": reasons, "excerpt": excerpt}


def require_eligible(root: Path, items: list[dict[str, Any]]) -> None:
    """Validate the entire eligible batch before any excerpt reaches a model."""
    sop = _validated_sop(root, resolve_under_root(root, "config/sops/phase1_ingestion.json"))
    for item in items:
        reference = item["relative_path"]
        try:
            current = current_source(root, reference, sop)
            if any(item[field] != current[field] for field in ("sha256", "matched_routes", "excerpt")):
                raise ValueError("source snapshot or routing/excerpt policy changed")
            if current["review_reasons"]:
                rid = review_identity([current])
                if item.get("approved_review") != rid or decision_state(root / "staging", rid)[0] != "approved":
                    raise ValueError("current gate requires a verified approval")
                payload = read_review_payload(root / "staging/reviews" / f"{rid}.json")
                action_class = classify_reasons(root, current["review_reasons"])
                if (action_class == "forbidden" or payload["action_class"] != action_class
                        or any(payload["source_items"][0][field] != value for field, value in current.items())):
                    raise ValueError("approval no longer agrees with current source or policy")
        except UnsafePathError:
            raise
        except (OSError, ValueError) as exc:
            raise SourceReingestRequired(f"source_reingest_required: {reference}: {exc}; run phase1 and retry") from exc
