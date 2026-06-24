import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from looping_box.phase1 import run_phase1
from looping_box.schema import validate

SCHEMA_DIR = ROOT / "docs" / "schemas"
DELTA_SCHEMA = json.loads((SCHEMA_DIR / "phase1_delta.schema.json").read_text())
PENDING_REVIEW_INDEX_SCHEMA = json.loads((SCHEMA_DIR / "pending_review_index.schema.json").read_text())
REVIEW_PAYLOAD_SCHEMA = json.loads((SCHEMA_DIR / "review_payload.schema.json").read_text())


def _make_sop(root: Path) -> None:
    (root / "config" / "sops").mkdir(parents=True)
    (root / "inbox").mkdir()
    (root / "config" / "sops" / "phase1_ingestion.json").write_text(
        json.dumps(
            {
                "name": "Phase 1 Ingestion",
                "allowed_extensions": [".md", ".txt"],
                "routes": [{"label": "docs", "keywords": ["docs"]}],
                "boundary_gate": {
                    "requires_review_keywords": ["deploy"],
                    "notification_message": "Review required",
                },
                "max_excerpt_chars": 120,
            }
        ),
        encoding="utf-8",
    )


class SchemaFixtureTests(unittest.TestCase):
    def test_generated_delta_matches_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_sop(root)
            (root / "inbox" / "notes.md").write_text("docs note", encoding="utf-8")

            delta = run_phase1(root, now="2026-06-24T12:00:00Z")
            validate(delta, DELTA_SCHEMA)  # raises on mismatch

    def test_generated_boundary_payload_matches_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_sop(root)
            (root / "inbox" / "release.txt").write_text("please deploy", encoding="utf-8")

            run_phase1(root, now="2026-06-24T12:00:00Z")
            index = json.loads((root / "staging" / "pending_review.json").read_text())
            validate(index, PENDING_REVIEW_INDEX_SCHEMA)
            payload = json.loads((root / index["latest"]).read_text())
            validate(payload, REVIEW_PAYLOAD_SCHEMA)

    def test_malformed_delta_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_sop(root)
            (root / "inbox" / "notes.md").write_text("docs note", encoding="utf-8")

            delta = run_phase1(root, now="2026-06-24T12:00:00Z")
            del delta["summary"]["changed"]  # drop a required field
            with self.assertRaises(ValueError):
                validate(delta, DELTA_SCHEMA)

    def test_wrong_type_is_rejected(self):
        broken = {
            "schema": "looping-box.phase1.delta.v1",
            "run_id": "x",
            "generated_at": "x",
            "delta_path": "x",
            "sop": {"path": "x", "sha256": "x", "name": "x"},
            "inputs": {"root": "x", "input_dir": "x"},
            "summary": {
                "scanned": "not-an-int",  # wrong type
                "changed": 0,
                "skipped": 0,
                "requires_review": False,
            },
            "changes": [],
            "skipped": [],
            "boundary_gate": {"status": "clear", "payload": None, "reasons": []},
        }
        with self.assertRaises(ValueError):
            validate(broken, DELTA_SCHEMA)


if __name__ == "__main__":
    unittest.main()
