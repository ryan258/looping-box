import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from looping_box.action_policy import classify_action
from looping_box.phase1 import run_phase1
from looping_box.review import list_reviews, record_review
from looping_box.schema import validate

SCHEMA_DIR = ROOT / "docs" / "schemas"
REVIEW_RECORD_SCHEMA = json.loads((SCHEMA_DIR / "review_record.schema.json").read_text())
VERIFIER_RESULT_SCHEMA = json.loads((SCHEMA_DIR / "verifier_result.schema.json").read_text())


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
                    "requires_review_keywords": ["deploy", "secret"],
                    "notification_message": "Review required",
                },
                "max_excerpt_chars": 120,
            }
        ),
        encoding="utf-8",
    )


class ReviewTests(unittest.TestCase):
    def test_phase1_reuses_stable_review_for_unchanged_pending_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_sop(root)
            (root / "inbox" / "release.txt").write_text("please deploy", encoding="utf-8")

            run_phase1(root, now="2026-06-24T12:00:00Z")
            run_phase1(root, now="2026-06-24T12:01:00Z")

            index = json.loads((root / "staging" / "pending_review.json").read_text())
            self.assertEqual(index["schema"], "looping-box.pending-review-index.v1")
            self.assertEqual(len(index["reviews"]), 1)

            index_schema = json.loads((SCHEMA_DIR / "pending_review_index.schema.json").read_text())
            payload_schema = json.loads((SCHEMA_DIR / "review_payload.schema.json").read_text())
            validate(index, index_schema)
            for review_path in index["reviews"]:
                payload = json.loads((root / review_path).read_text())
                validate(payload, payload_schema)
                self.assertEqual(payload["action_class"], "review_required")
                self.assertEqual(payload["source_items"][0]["relative_path"], "inbox/release.txt")

    def test_approval_suppresses_future_gate_for_same_source_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_sop(root)
            (root / "inbox" / "release.txt").write_text("please deploy", encoding="utf-8")
            first = run_phase1(root, now="2026-06-24T12:00:00Z")
            review = list_reviews(root)[0]
            record_review(
                root,
                review["review_id"],
                "approved",
                note="Handled manually",
                now="2026-06-24T12:05:00Z",
            )

            second = run_phase1(root, now="2026-06-24T12:10:00Z")

            self.assertEqual(first["boundary_gate"]["status"], "pending_review")
            self.assertEqual(second["boundary_gate"]["status"], "clear")
            self.assertEqual(second["summary"]["changed"], 0)
            self.assertEqual(second["summary"]["skipped"], 1)
            self.assertEqual(second["skipped"][0]["reason"], "review_decision_recorded")
            self.assertEqual(list_reviews(root), [])
            index = json.loads((root / "staging" / "pending_review.json").read_text())
            self.assertEqual(index["reviews"], [])
            self.assertEqual(index["latest"], "")

    def test_review_decision_does_not_suppress_identical_content_at_new_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_sop(root)
            (root / "inbox" / "release.txt").write_text("please deploy", encoding="utf-8")
            run_phase1(root, now="2026-06-24T12:00:00Z")
            review = list_reviews(root)[0]
            record_review(
                root,
                review["review_id"],
                "rejected",
                note="Not allowed here",
                now="2026-06-24T12:05:00Z",
            )
            run_phase1(root, now="2026-06-24T12:10:00Z")

            (root / "inbox" / "release-copy.txt").write_text("please deploy", encoding="utf-8")
            result = run_phase1(root, now="2026-06-24T12:15:00Z")

            self.assertEqual(result["boundary_gate"]["status"], "pending_review")
            self.assertEqual(result["summary"]["changed"], 1)
            self.assertEqual(result["changes"][0]["relative_path"], "inbox/release-copy.txt")
            new_review = list_reviews(root)[0]
            self.assertNotEqual(new_review["review_id"], review["review_id"])
            self.assertEqual(new_review["risk_reasons"], ["deploy"])

    def test_phase1_uses_action_class_config_for_review_payloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_sop(root)
            (root / "config" / "action_classes.json").write_text(
                json.dumps(
                    {
                        "schema": "looping-box.action-classes.v1",
                        "default_class": "review_required",
                        "classes": {
                            "safe_local_transform": [],
                            "review_required": [],
                            "blocked": ["deploy"],
                            "forbidden": [],
                        },
                    }
                ),
                encoding="utf-8",
            )
            (root / "inbox" / "release.txt").write_text("please deploy", encoding="utf-8")

            run_phase1(root, now="2026-06-24T12:00:00Z")

            review = list_reviews(root)[0]
            self.assertEqual(review["action_class"], "blocked")

    def test_forbidden_action_class_cannot_be_approved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_sop(root)
            (root / "config" / "action_classes.json").write_text(
                json.dumps(
                    {
                        "schema": "looping-box.action-classes.v1",
                        "default_class": "review_required",
                        "classes": {
                            "safe_local_transform": [],
                            "review_required": [],
                            "blocked": [],
                            "forbidden": ["deploy"],
                        },
                    }
                ),
                encoding="utf-8",
            )
            (root / "inbox" / "release.txt").write_text("please deploy", encoding="utf-8")
            run_phase1(root, now="2026-06-24T12:00:00Z")
            review = list_reviews(root)[0]

            with self.assertRaises(ValueError):
                record_review(
                    root,
                    review["review_id"],
                    "approved",
                    note="Should not approve",
                    now="2026-06-24T12:05:00Z",
                )

            self.assertFalse((root / "staging" / "approvals" / f"{review['review_id']}.json").exists())
            self.assertEqual(list_reviews(root)[0]["review_id"], review["review_id"])

    def test_review_records_approval_with_verifier_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_sop(root)
            (root / "inbox" / "release.txt").write_text("please deploy", encoding="utf-8")
            run_phase1(root, now="2026-06-24T12:00:00Z")
            review = list_reviews(root)[0]
            review_path = root / review["path"]

            record = record_review(
                root,
                review["review_id"],
                "approved",
                note="Handled manually",
                now="2026-06-24T12:05:00Z",
            )

            self.assertEqual(record["decision"], "approved")
            self.assertTrue((root / record["verifier_result"]).exists())
            self.assertEqual(list_reviews(root), [])
            validate(record, REVIEW_RECORD_SCHEMA)
            verifier_result = json.loads((root / record["verifier_result"]).read_text())
            validate(verifier_result, VERIFIER_RESULT_SCHEMA)
            payload = json.loads(review_path.read_text())
            self.assertEqual(payload["verifier"]["status"], "passed")
            self.assertEqual(payload["verifier"]["result"], record["verifier_result"])

    def test_review_records_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_sop(root)
            (root / "inbox" / "release.txt").write_text("please deploy", encoding="utf-8")
            run_phase1(root, now="2026-06-24T12:00:00Z")
            review = list_reviews(root)[0]
            review_path = root / review["path"]

            record = record_review(
                root,
                review["review_id"],
                "rejected",
                note="Not allowed",
                now="2026-06-24T12:05:00Z",
            )

            self.assertEqual(record["decision"], "rejected")
            self.assertTrue((root / "staging" / "rejections" / f"{review['review_id']}.json").exists())
            payload = json.loads(review_path.read_text())
            self.assertEqual(payload["verifier"]["status"], "not_applicable")
            self.assertIsNone(payload["verifier"]["result"])

    def test_unknown_action_defaults_to_review_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(classify_action(root, "surprising-action"), "review_required")

    def test_review_list_tolerates_unsupported_pending_index_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "staging").mkdir()
            (root / "staging" / "pending_review.json").write_text(
                json.dumps({"schema": "wrong", "reviews": ["staging/reviews/x.json"]}),
                encoding="utf-8",
            )

            self.assertEqual(list_reviews(root), [])


if __name__ == "__main__":
    unittest.main()
