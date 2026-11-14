"""Current source evidence shared by review and worker disclosure checks."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ._util import decision_state, read_review_payload, resolve_under_root, review_identity
from .action_policy import classify_reasons
from .paths import UnsafePathError
from .phase1 import DEFAULT_SOP_PATH, _derive_change, _review_matches, _validated_sop


class SourceReingestRequired(ValueError):
    """Stored worker evidence no longer permits disclosure or drafting."""


def current_source(root: Path, relative_path: str, sop: dict[str, Any]) -> dict[str, Any]:
    path = resolve_under_root(root, relative_path)
    if any(part.startswith(".") for part in Path(relative_path).parts) or path.suffix.lower() not in {
        extension.lower() for extension in sop["allowed_extensions"]
    }:
        raise ValueError("source is no longer eligible under the current SOP")
    return _derive_change(path, relative_path, sop)


def require_eligible(root: Path, items: list[dict[str, Any]]) -> None:
    """Validate the entire eligible batch before any excerpt reaches a model."""
    policies = {DEFAULT_SOP_PATH: _validated_sop(root, resolve_under_root(root, DEFAULT_SOP_PATH))}
    for item in items:
        reference = item["relative_path"]
        try:
            sop_path = item.get("source_sop", {}).get("path", DEFAULT_SOP_PATH)
            if sop_path not in policies:
                policies[sop_path] = _validated_sop(root, resolve_under_root(root, sop_path))
            current = current_source(root, reference, policies[sop_path])
            if any(item[field] != current[field] for field in ("sha256", "matched_routes", "excerpt")):
                raise ValueError("source snapshot or routing/excerpt policy changed")
            if current["review_reasons"]:
                rid = review_identity([current])
                if item.get("approved_review") != rid or decision_state(root / "staging", rid)[0] != "approved":
                    raise ValueError("current gate requires a verified approval")
                payload = read_review_payload(root / "staging/reviews" / f"{rid}.json")
                action_class = classify_reasons(root, current["review_reasons"])
                approved_sop = payload.get("source_sop", {}).get("path", DEFAULT_SOP_PATH)
                if (approved_sop != sop_path or action_class == "forbidden"
                        or not _review_matches(payload, current, action_class)):
                    raise ValueError("approval no longer agrees with current source or policy")
        except UnsafePathError:
            raise
        except (OSError, ValueError) as exc:
            raise SourceReingestRequired(f"source_reingest_required: {reference}: {exc}; run phase1 and retry") from exc
