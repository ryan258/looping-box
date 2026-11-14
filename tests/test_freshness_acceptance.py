"""T07 / A17–A21: current evidence before disclosure, bounded offline model stubs."""
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from looping_box import model, phase1, review, scaffold, supervisor, worker
from looping_box.eligibility import SourceReingestRequired
from looping_box.validation import validate_data

STAMP = "2026-10-13T12:00:00Z"
KEY = "fake-api-key-SECRET-SENTINEL"


class FreshnessAcceptanceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.number = 0
        env = patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.base / "keys"),
                                      "LOOPING_BOX_REVIEW_KEY": "fake-review-key"}, clear=True)
        env.start(); self.addCleanup(env.stop)
        model._env_loaded_for.clear()
        self.addCleanup(model._env_loaded_for.clear)
        self.calls = []
        transport = patch.object(model, "_transport", side_effect=AssertionError("live provider forbidden"))
        transport.start(); self.addCleanup(transport.stop)
        self.new_workspace()

    def new_workspace(self):
        self.number += 1
        self.root = self.base / f"workspace-{self.number}"
        scaffold.init_workspace(self.root)
        return self.root

    def ingest(self, name="one.txt", text="Project docs one.", now=STAMP):
        (self.root / "inbox" / name).write_text(text)
        return phase1.run_phase1(self.root, now=now)

    def run_worker(self, name="context_builder", **kwargs):
        out = worker.run_worker(self.root, name, now=STAMP, **kwargs)
        validate_data(out, "worker_output", "output", worker_id=name)
        return out

    def seed(self, count=1, execute=False):
        self.ingest()
        if count == 2: self.ingest("two.txt", "Project docs two.")
        self.assertEqual(self.run_worker()["status"], "complete")
        if execute: self.assertEqual(self.run_worker("execution_engine")["status"], "complete")

    def enable(self, role="execution_engine", model_id="stub/execution"):
        os.environ["OPENROUTER_API_KEY"] = KEY
        os.environ["MODEL_" + role.upper()] = model_id

    def transport(self, content="MODEL DRAFT"):
        def send(url, headers, body, timeout):
            data = json.loads(body)
            self.calls.append(data)
            self.assertEqual(headers["Authorization"], "Bearer " + os.environ["OPENROUTER_API_KEY"])
            self.assertTrue(url.endswith("/chat/completions"))
            self.assertEqual(data["temperature"], 0.0)
            if isinstance(content, Exception): raise content
            value = content(data) if callable(content) else content
            return json.dumps({"model": data["model"], "choices": [{"message": {"content": value}}]})
        return patch.object(model, "_transport", side_effect=send)

    def batch(self, *names):
        return json.dumps({"drafts": [{"relative_path": "inbox/" + name, "draft": "DRAFT " + name} for name in names]})

    def saved(self, wid="execution_engine"):
        directory = self.root / "cache/workers" / wid
        return {name: (directory / name).read_bytes() if (directory / name).exists() else None
                for name in (("draft.json", "draft.md", "state.json") if wid == "execution_engine"
                             else ("context_package.json", "context_package.md", "state.json"))}

    def package(self):
        return json.loads((self.root / "cache/workers/context_builder/context_package.json").read_text())

    def test_execution_outage_then_new_file_preserves_both_once(self):
        self.seed(); self.enable()
        with self.transport(RuntimeError("offline outage")):
            self.assertEqual(self.run_worker("execution_engine")["status"], "failed")
        self.ingest("two.txt", "Project docs two.")
        self.assertEqual(self.run_worker()["status"], "complete")
        self.assertEqual([x["relative_path"] for x in self.package()["items"]], ["inbox/one.txt", "inbox/two.txt"])
        self.calls.clear()
        with self.transport(self.batch("one.txt", "two.txt")):
            self.assertEqual(self.run_worker("execution_engine")["status"], "complete")
            self.assertEqual(self.run_worker("execution_engine")["status"], "idle")
        self.assertEqual(len(self.calls), 1)

    def test_latest_numeric_suffix_beats_lexical_filename_order(self):
        for number in range(11): self.ingest(text=f"Project docs revision {number}.")
        self.assertEqual(self.run_worker()["status"], "complete")
        items = self.package()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["excerpt"], "Project docs revision 10.")
        self.assertTrue(items[0]["source_delta"].endswith("-11.json"))

    def test_latest_timestamp_beats_file_order_and_removal_can_be_superseded(self):
        self.ingest(text="Project docs later.", now="2026-10-13T13:00:00Z")
        self.ingest(text="Project docs older.", now=STAMP)
        (self.root / "inbox/one.txt").write_text("Project docs later.")
        self.assertEqual(self.run_worker()["status"], "complete")
        self.assertEqual(self.package()["items"][0]["excerpt"], "Project docs later.")
        self.new_workspace()
        self.ingest(); (self.root / "inbox/one.txt").unlink()
        phase1.run_phase1(self.root, now=STAMP)
        self.ingest(text="Project docs restored new version.")
        self.assertEqual(self.run_worker()["status"], "complete")
        self.assertEqual(self.package()["items"][0]["excerpt"], "Project docs restored new version.")

    def test_fresh_same_path_replaces_undrafted_excerpt(self):
        self.seed()
        self.ingest(text="Project docs replacement.")
        self.assertEqual(self.run_worker()["status"], "complete")
        self.assertEqual([x["excerpt"] for x in self.package()["items"]], ["Project docs replacement."])

    def test_reingested_skip_refreshes_only_existing_undrafted_item(self):
        self.seed()
        sop_path = self.root / "config/sops/phase1_ingestion.json"
        sop = json.loads(sop_path.read_text())
        sop["max_excerpt_chars"] = 10
        sop_path.write_text(json.dumps(sop))
        self.assertEqual(self.run_worker()["errors"][-1]["code"], "source_reingest_required")
        scanned = phase1.run_phase1(self.root, now=STAMP)
        self.assertEqual(scanned["changes"], [])
        self.assertEqual(self.run_worker()["status"], "complete")
        self.assertEqual([item["excerpt"] for item in self.package()["items"]], ["Project..."])
        self.assertEqual(self.run_worker("execution_engine")["status"], "complete")
        prior_context, prior_draft = self.saved("context_builder"), self.saved()
        phase1.run_phase1(self.root, now=STAMP)
        self.assertEqual(self.run_worker()["status"], "idle")
        self.assertEqual(self.saved("context_builder")["context_package.json"], prior_context["context_package.json"])
        self.assertEqual(self.run_worker("execution_engine")["status"], "idle")
        self.assertEqual(self.saved(), prior_draft)

    def test_refreshing_skip_carries_the_current_sop_even_if_excerpt_is_unchanged(self):
        source = self.root / "inbox/one.txt"
        source.write_text("Project docs one.")
        custom_reference = "config/sops/custom.json"
        (self.root / custom_reference).write_bytes(
            (self.root / "config/sops/phase1_ingestion.json").read_bytes()
        )
        custom_delta = phase1.run_phase1(self.root, now=STAMP, sop_path=custom_reference)
        self.assertEqual(self.run_worker()["status"], "complete")
        self.assertEqual(self.package()["items"][0]["source_sop"], custom_delta["sop"])

        default_delta = phase1.run_phase1(self.root, now=STAMP)
        self.assertEqual(default_delta["changes"], [])
        self.assertEqual(self.run_worker()["status"], "complete")
        item = self.package()["items"][0]
        self.assertNotIn("source_sop", item)
        self.assertEqual(item["source_delta"], custom_delta["delta_path"])
        self.assertEqual(self.run_worker("execution_engine")["status"], "complete")

    def prepare_permissive_context(self):
        config = self.root / "config/sops"
        permissive = json.loads((config / "phase1_ingestion.json").read_text())
        restrictive = json.loads(json.dumps(permissive))
        permissive["boundary_gate"]["requires_review_keywords"].remove("deploy")
        for name in ("phase1_ingestion", "permissive"):
            (config / f"{name}.json").write_text(json.dumps(permissive))
        (config / "restrictive.json").write_text(json.dumps(restrictive))
        (self.root / "inbox/one.txt").write_text("Please deploy this release.")
        phase1.run_phase1(self.root, now=STAMP, sop_path="config/sops/permissive.json")
        self.assertEqual(self.run_worker()["status"], "complete")

    def test_direct_execution_rejects_newer_hold_or_rejection_from_other_sop(self):
        for newer_evidence in ("held", "rejected", "archived", "corrupt", "custom_delta_dir", "aged_out"):
            with self.subTest(newer_evidence=newer_evidence):
                self.new_workspace()
                self.prepare_permissive_context()
                prior = self.saved()

                delta_dir = "a" if newer_evidence in {"custom_delta_dir", "aged_out"} else "cache/deltas"
                later = STAMP if newer_evidence == "custom_delta_dir" else "2026-10-13T12:01:00Z"
                settings = dict(now=later, sop_path="config/sops/restrictive.json", delta_dir=delta_dir)
                held = phase1.run_phase1(self.root, **settings)
                if newer_evidence != "held":
                    rid = review.list_reviews(self.root)[0]["review_id"]
                    review.record_review(self.root, rid, "rejected", note="Reject fixture", now=later)
                    rejected = phase1.run_phase1(self.root, **settings) if newer_evidence != "custom_delta_dir" else None
                    self.assertEqual(review.list_reviews(self.root), [])
                    if newer_evidence in {"archived", "aged_out"}:
                        archive = self.root / delta_dir / "archive"
                        archive.mkdir()
                        for delta in (held, rejected):
                            path = self.root / delta["delta_path"]
                            path.replace(archive / path.name)
                    if newer_evidence == "corrupt":
                        (self.root / held["delta_path"]).write_text("{unreadable newer authority")
                    if newer_evidence == "aged_out":
                        state_path = self.root / "cache/state/phase1_state.json"
                        legacy = json.loads(state_path.read_text())
                        legacy.pop("custom_delta_dirs")
                        state_path.write_text(json.dumps(legacy))
                        for _ in range(51):
                            phase1.run_phase1(self.root, input_dir="spare", now=later)
                        state = json.loads(state_path.read_text())
                        self.assertEqual(state["custom_delta_dirs"], ["a"])
                        self.assertTrue(all(run["delta_path"] is None for run in state["runs"]))
                self.enable()
                with patch.object(model, "_transport") as transport:
                    output = self.run_worker("execution_engine")
                self.assertEqual(output["status"], "failed")
                self.assertEqual(output["errors"][0]["code"], "source_reingest_required")
                self.assertEqual(self.saved(), prior)
                transport.assert_not_called()
                os.environ.pop("MODEL_EXECUTION_ENGINE")

    def archive_rejected_hold(self, stamp=STAMP, **options):
        hold = phase1.run_phase1(self.root, now=stamp, sop_path="config/sops/restrictive.json",
                                 delta_dir="alternate/deltas", **options)
        review_id = review.list_reviews(self.root)[0]["review_id"]
        review.record_review(self.root, review_id, "rejected", note="Reject fixture", now=stamp)
        path = self.root / hold["delta_path"]
        archive = path.parent / "archive"
        archive.mkdir()
        path.replace(archive / path.name)

    def test_policy_refresh_skip_does_not_reauthorize_archived_rejection(self):
        for retry_at in (STAMP, "2026-10-13T12:01:00Z"):
            with self.subTest(retry_at=retry_at):
                self.new_workspace()
                self.prepare_permissive_context()
                self.archive_rejected_hold()
                retry = phase1.run_phase1(self.root, now=retry_at)
                self.assertEqual(retry["changes"], [])
                self.assertEqual(retry["skipped"][0]["reason"], "already_processed")
                with patch.dict(os.environ):
                    self.enable("context_builder", "stub/context")
                    self.enable()
                    with patch.object(model, "_transport") as transport:
                        self.assertEqual(self.run_worker("execution_engine")["status"], "failed")
                        self.assertEqual(supervisor.run_supervisor(self.root, now=retry_at)["status"], "complete")
                        transport.assert_not_called()
                self.assertEqual(self.package()["items"], [])

    def test_archived_veto_allows_unrelated_work_and_matches_dry_run(self):
        self.prepare_permissive_context()
        # Build the prior context under the default SOP, so the retry cannot
        # hide the veto behind a policy/provenance change.
        phase1.run_phase1(self.root, now=STAMP)
        self.assertEqual(self.run_worker()["status"], "complete")
        self.archive_rejected_hold()
        self.ingest("clean.txt", "Project docs unrelated safe sibling.", now="2026-10-13T12:01:00Z")
        with patch.dict(os.environ):
            self.enable("context_builder", "stub/context")
            self.enable()
            before = self.saved("context_builder")
            with patch.object(model, "_transport") as transport:
                preview = self.run_worker(dry_run=True)
                transport.assert_not_called()
            self.assertEqual(self.saved("context_builder"), before)
            with self.transport("OFFLINE DRAFT"):
                result = supervisor.run_supervisor(self.root, now=STAMP)
        self.assertEqual(preview["status"], result["worker_outputs"][0]["status"])
        self.assertEqual(result["status"], "complete")
        self.assertEqual([item["relative_path"] for item in self.package()["items"]], ["inbox/clean.txt"])
        self.assertEqual(len(self.calls), 2)
        self.assertNotIn("Please deploy this release.", json.dumps(self.calls))

    def test_context_dry_run_fails_on_unreadable_newer_archive_evidence(self):
        self.prepare_permissive_context()
        self.archive_rejected_hold("2026-10-13T12:01:00Z")
        archive = self.root / "alternate/deltas/archive"
        next(archive.glob("*.json")).write_text("{broken archived delta")
        self.ingest("clean.txt", "Project docs unrelated safe sibling.", now="2026-10-13T12:02:00Z")
        before = self.saved("context_builder")
        with patch.object(model, "_transport") as transport:
            preview = self.run_worker(dry_run=True)
            self.assertEqual(self.saved("context_builder"), before)
            actual = self.run_worker()
            transport.assert_not_called()
        for output in (preview, actual):
            self.assertEqual(output["status"], "failed")
            self.assertEqual(output["errors"][0]["code"], "source_reingest_required")

    def test_unregistered_legacy_rejection_blocks_direct_execution(self):
        self.prepare_permissive_context()
        self.archive_rejected_hold("2026-10-13T12:01:00Z", state_path="alternate/state.json")
        state_path = self.root / "cache/state/phase1_state.json"
        legacy = json.loads(state_path.read_text())
        del legacy["custom_delta_dirs"]
        legacy.pop("same_time_delta_order", None)
        state_path.write_text(json.dumps(legacy))
        self.enable()
        with patch.object(model, "_transport") as transport:
            out = self.run_worker("execution_engine")
        self.assertEqual(out["status"], "failed")
        self.assertEqual(out["errors"][0]["code"], "source_reingest_required")
        transport.assert_not_called()

    def test_late_rejection_vetoes_partially_published_clean_delta(self):
        for reject_at in ("2026-10-13T11:59:59Z", STAMP, "2026-10-13T12:01:00Z"):
            with self.subTest(reject_at=reject_at):
                self.new_workspace()
                default_sop = self.root / "config/sops/phase1_ingestion.json"
                restrictive_sop = default_sop.with_name("restrictive.json")
                restrictive_sop.write_bytes(default_sop.read_bytes())
                sop = json.loads(default_sop.read_bytes())
                sop["boundary_gate"]["requires_review_keywords"].remove("deploy")
                default_sop.write_text(json.dumps(sop))
                (self.root / "inbox/one.txt").write_text("Please deploy this release.")
                hold = phase1.run_phase1(self.root, now=STAMP, sop_path="config/sops/restrictive.json",
                                         delta_dir="alternate/deltas")
                path = self.root / hold["delta_path"]
                archive = path.parent / "archive"
                archive.mkdir()
                path.replace(archive / path.name)
                write_json = phase1._write_json

                def fail_pending_index(path, data, write=write_json):
                    if path.name == "pending_review.json":
                        raise OSError("interrupted pending-index write")
                    return write(path, data)

                with patch.object(phase1, "_write_json", side_effect=fail_pending_index):
                    with self.assertRaisesRegex(OSError, "interrupted pending-index write"):
                        phase1.run_phase1(self.root, now=STAMP)
                self.assertTrue(list((self.root / "cache/deltas").glob("*.json")))
                review_id = review.list_reviews(self.root)[0]["review_id"]
                review.record_review(self.root, review_id, "rejected", note="Late rejection", now=reject_at)
                with patch.dict(os.environ):
                    self.enable("context_builder", "stub/context")
                    self.enable()
                    with patch.object(model, "_transport") as transport:
                        self.assertEqual(supervisor.run_supervisor(self.root, now=reject_at)["status"], "complete")
                        transport.assert_not_called()
                self.assertEqual(self.package()["items"], [])

    def test_alternate_hold_authority_survives_state_write_failures_and_custom_state(self):
        for state_write in ("registration", "final", "custom"):
            with self.subTest(state_write=state_write):
                self.new_workspace()
                self.prepare_permissive_context()
                canonical = self.root / "cache/state/phase1_state.json"
                previous = json.loads(canonical.read_text())
                settings = {"now": "2026-10-13T12:01:00Z", "sop_path": "config/sops/restrictive.json",
                            "delta_dir": "alternate/deltas"}
                original = phase1._write_json

                def interrupt(path, data, *, target=canonical, stamp=settings["now"],
                              fail_final=state_write == "final", write_json=original):
                    advances_run = data.get("last_run_at") == stamp
                    if path == target and advances_run == fail_final:
                        raise OSError("fixture state write failure")
                    return write_json(path, data)

                if state_write == "custom":
                    # Registration must preserve legacy optional fields and canonical run history.
                    previous.pop("present_paths")
                    canonical.write_text(json.dumps(previous))
                    phase1.run_phase1(self.root, state_path="alternate/state.json", **settings)
                else:
                    with patch.object(phase1, "_write_json", side_effect=interrupt):
                        with self.assertRaisesRegex(OSError, "state write failure"):
                            phase1.run_phase1(self.root, **settings)

                canonical_state = json.loads(canonical.read_text())
                if state_write == "registration":
                    self.assertEqual(canonical_state, previous)
                    self.assertEqual(review.list_reviews(self.root), [])
                    self.assertEqual(list((self.root / settings["delta_dir"]).glob("*.json")), [])
                    continue
                self.assertEqual(canonical_state.pop("custom_delta_dirs"), [settings["delta_dir"]])
                published, = (self.root / settings["delta_dir"]).glob("*.json")
                self.assertEqual(canonical_state.pop("same_time_delta_order"),
                                 {settings["now"]: [published.relative_to(self.root).as_posix()]})
                self.assertEqual(canonical_state, previous)
                pending = review.list_reviews(self.root)[0]
                review.record_review(self.root, pending["review_id"], "rejected", note="Reject fixture",
                                     now=settings["now"])
                self.assertEqual(review.list_reviews(self.root), [])
                prior = self.saved()
                with patch.dict(os.environ):
                    self.enable()
                    with patch.object(model, "_transport") as transport:
                        output = self.run_worker("execution_engine")
                self.assertEqual(output["status"], "failed")
                self.assertEqual(output["errors"][0]["code"], "source_reingest_required")
                self.assertEqual(self.saved(), prior)
                transport.assert_not_called()

    def test_same_second_custom_queue_retries_preserve_the_committed_draft(self):
        for legacy_order in (False, True):
            with self.subTest(legacy_order=legacy_order):
                self.new_workspace()
                sop = self.root / "config/sops/phase1_ingestion.json"
                restrictive = json.loads(sop.read_text())
                (sop.parent / "restrictive.json").write_text(json.dumps(restrictive))
                restrictive["boundary_gate"]["requires_review_keywords"].remove("deploy")
                sop.write_text(json.dumps(restrictive))
                (self.root / "inbox/one.txt").write_text("Please deploy this release.")
                hold = phase1.run_phase1(self.root, now=STAMP, sop_path="config/sops/restrictive.json",
                                         delta_dir="alternate/deltas", state_path="alternate/state.json")
                rid = review.list_reviews(self.root)[0]["review_id"]
                review.record_review(self.root, rid, "rejected", note="Reject fixture", now=STAMP)
                origin = phase1.run_phase1(self.root, now=STAMP)
                self.assertEqual(supervisor.run_supervisor(self.root, now=STAMP)["status"], "complete")
                before = self.saved()
                before_context = self.saved("context_builder")
                later = "2026-10-13T12:02:00Z"
                for _ in range(51):
                    phase1.run_phase1(self.root, input_dir="spare", now=later)
                state_path = self.root / "cache/state/phase1_state.json"
                state = json.loads(state_path.read_text())
                self.assertEqual(state["same_time_delta_order"][STAMP], [hold["delta_path"], origin["delta_path"]])
                self.assertTrue(all(run["delta_path"] is None for run in state["runs"]))
                if legacy_order:
                    state.pop("same_time_delta_order")
                    state_path.write_text(json.dumps(state))
                    direct = self.run_worker("execution_engine")
                    self.assertEqual(direct["status"], "failed")
                    self.assertIn("cannot order same-time scans", direct["errors"][0]["message"])

                retry = phase1.run_phase1(self.root, now="2026-10-13T12:03:00Z")
                self.assertEqual(retry["skipped"][0]["reason"], "already_processed")
                with patch.object(model, "generate_if_enabled") as generate:
                    self.assertEqual(supervisor.run_supervisor(self.root, now=later)["status"], "idle")
                    self.assertFalse(worker.execution_needs_run(self.root))
                    generate.assert_not_called()
                self.assertEqual(self.saved(), before)
                context = self.saved("context_builder")
                self.assertEqual(context["context_package.json"], before_context["context_package.json"])
                self.assertEqual(context["context_package.md"], before_context["context_package.md"])
                self.assertEqual(bool(json.loads(context["state.json"]).get("retired_context_sha256")), legacy_order)
                self.assertFalse((self.root / retry["delta_path"]).exists())
                if legacy_order:
                    with patch.dict(os.environ):
                        self.enable()
                        with patch.object(model, "_transport") as transport:
                            self.assertEqual(self.run_worker("execution_engine")["status"], "idle")
                            transport.assert_not_called()
                        self.ingest(text="Project docs updated.", now="2026-10-13T12:04:00Z")
                        with self.transport(self.batch("one.txt")):
                            self.assertEqual(supervisor.run_supervisor(self.root, now=later)["status"], "complete")
                    self.assertEqual(len(self.calls), 1)
                    self.assertNotIn("retired_context_sha256", json.loads(self.saved("context_builder")["state.json"]))

    def test_failed_same_second_order_reservation_cannot_move_retry_before_the_context(self):
        self.prepare_permissive_context()
        before = self.saved()
        alternate = {"now": STAMP, "sop_path": "config/sops/restrictive.json", "delta_dir": "alternate/deltas"}
        write_json = phase1._write_json

        def fail_delta(path, data):
            if path.parent == self.root / alternate["delta_dir"]:
                raise OSError("fixture delta publication failure")
            return write_json(path, data)

        with patch.object(phase1, "_write_json", side_effect=fail_delta):
            with self.assertRaisesRegex(OSError, "delta publication failure"):
                phase1.run_phase1(self.root, **alternate)
        (self.root / "inbox/one.txt").write_text("Please deploy the updated release.")
        origin = phase1.run_phase1(self.root, now=STAMP)
        self.assertEqual(self.run_worker()["status"], "complete")
        self.assertEqual(self.package()["items"][0]["source_delta"], origin["delta_path"])
        hold = phase1.run_phase1(self.root, **alternate)
        self.assertTrue(hold["run_id"].endswith("-2"))
        rid = review.list_reviews(self.root)[0]["review_id"]
        review.record_review(self.root, rid, "rejected", note="Reject fixture", now=STAMP)
        with patch.object(model, "generate_if_enabled") as generate:
            output = self.run_worker("execution_engine")
            self.assertEqual(output["status"], "failed")
            self.assertEqual(output["errors"][0]["code"], "source_reingest_required")
            generate.assert_not_called()
        self.assertEqual(self.saved(), before)

    def test_legacy_unordered_veto_is_retired_without_redisclosing_saved_context(self):
        sop = self.root / "config/sops/phase1_ingestion.json"
        restrictive = json.loads(sop.read_text())
        (sop.parent / "restrictive.json").write_text(json.dumps(restrictive))
        restrictive["boundary_gate"]["requires_review_keywords"].remove("deploy")
        sop.write_text(json.dumps(restrictive))
        self.ingest(text="Please deploy this release.")
        self.assertEqual(supervisor.run_supervisor(self.root, now=STAMP)["status"], "complete")
        prior = self.saved()

        phase1.run_phase1(self.root, now=STAMP, sop_path="config/sops/restrictive.json", delta_dir="alternate/deltas")
        rid = review.list_reviews(self.root)[0]["review_id"]
        review.record_review(self.root, rid, "rejected", note="Reject fixture", now=STAMP)
        later = "2026-10-13T12:02:00Z"
        for _ in range(51):
            phase1.run_phase1(self.root, input_dir="spare", now=later)
        state_path = self.root / "cache/state/phase1_state.json"
        state = json.loads(state_path.read_text())
        state.pop("same_time_delta_order")
        state_path.write_text(json.dumps(state))

        self.enable()
        with patch.object(model, "_transport") as transport:
            self.assertEqual(self.run_worker("execution_engine")["status"], "failed")
            phase1.run_phase1(self.root, now="2026-10-13T12:03:00Z")
            self.assertEqual(supervisor.run_supervisor(self.root, now=later)["status"], "idle")
            self.assertEqual(self.run_worker("execution_engine")["status"], "idle")
            transport.assert_not_called()
        self.assertEqual(self.saved(), prior)

    def test_duplicate_edit_supersedes_stale_undrafted_content(self):
        self.seed()
        duplicate = "Project docs second source."
        self.ingest("two.txt", duplicate)
        result = self.ingest(text=duplicate)
        self.assertEqual(result["changes"], [])
        self.assertEqual(self.run_worker()["status"], "complete")
        self.assertEqual([item["relative_path"] for item in self.package()["items"]], ["inbox/two.txt"])
        self.assertEqual(self.run_worker("execution_engine")["status"], "complete")

    def test_new_hold_suppresses_queued_and_carried_clean_content(self):
        for carry in (False, True):
            self.new_workspace()
            self.ingest(text="Project docs OLD-CLEAN-SENTINEL.")
            if carry: self.run_worker()
            self.ingest(text="please deploy HELD-SENTINEL")
            self.ingest("two.txt", "Project docs clean survivor.")
            self.enable("context_builder", "stub/context"); self.enable()
            self.calls.clear()
            with self.transport():
                self.assertEqual(self.run_worker()["status"], "blocked")
                self.assertEqual(self.run_worker("execution_engine")["status"], "complete")
            self.assertEqual(self.package()["items"][0]["relative_path"], "inbox/two.txt")
            self.assertEqual(self.package()["blocked_inputs"][0]["relative_path"], "inbox/one.txt")
            self.assertEqual(len(self.calls), 1)
            sent = json.dumps(self.calls)
            self.assertNotIn("OLD-CLEAN-SENTINEL", sent); self.assertNotIn("HELD-SENTINEL", sent)
            draft = json.loads((self.root / "cache/workers/execution_engine/draft.json").read_text())
            self.assertEqual(draft["held_for_review"], ["inbox/one.txt"])
            os.environ.pop("MODEL_CONTEXT_BUILDER")

    def test_carried_changed_missing_or_newly_gated_source_blocks_new_batch(self):
        for change in ("bytes", "missing", "policy"):
            self.new_workspace(); self.seed()
            prior = self.saved("context_builder")
            if change == "bytes": (self.root / "inbox/one.txt").write_text("different source")
            if change == "missing": (self.root / "inbox/one.txt").unlink()
            if change == "policy": self.gate_docs()
            # Inject a valid unrelated delta without rescanning the carried source.
            fresh = self.root / "cache/deltas/fresh.json"
            data = json.loads(next((self.root / "cache/deltas").glob("*.json")).read_text())
            data["changes"] = []; data["reconciliation"] = {"scanned_paths": [], "removed_paths": []}
            data["summary"] = {"scanned": 0, "changed": 0, "skipped": 0, "requires_review": False}
            data["skipped"] = []
            data["delta_path"] = "cache/deltas/fresh.json"
            fresh.write_text(json.dumps(data))
            with patch.object(model, "generate_if_enabled") as generate:
                output = self.run_worker()
            self.assertEqual(output["status"], "failed", change)
            self.assertEqual(output["errors"][-1]["code"], "source_reingest_required")
            self.assertEqual(self.saved("context_builder"), prior)
            self.assertTrue(fresh.exists()); generate.assert_not_called()

    def gate_docs(self):
        path = self.root / "config/sops/phase1_ingestion.json"
        sop = json.loads(path.read_text()); sop["boundary_gate"]["requires_review_keywords"].append("docs")
        path.write_text(json.dumps(sop))
        path = self.root / "config/action_classes.json"
        policy = json.loads(path.read_text())
        policy["classes"]["safe_local_transform"].remove("docs")
        policy["classes"]["review_required"].append("docs")
        validate_data(policy, "action_classes", str(path))
        path.write_text(json.dumps(policy))

    def test_policy_reingestion_and_removal_reconcile_undrafted_work(self):
        self.seed(); self.gate_docs()
        self.assertEqual(self.run_worker()["status"], "failed")
        phase1.run_phase1(self.root, now=STAMP)
        self.assertEqual(self.run_worker()["status"], "blocked")
        self.assertEqual(self.package()["items"], [])
        self.new_workspace(); self.seed()
        (self.root / "inbox/one.txt").unlink()
        self.assertEqual(self.run_worker()["status"], "failed")
        delta = phase1.run_phase1(self.root, now=STAMP)
        self.assertEqual(delta["changes"], [])
        self.assertEqual(delta["reconciliation"]["removed_paths"], ["inbox/one.txt"])
        with patch.object(model, "generate_if_enabled") as generate:
            self.assertEqual(self.run_worker()["status"], "complete")
            self.assertEqual(self.run_worker("execution_engine")["status"], "complete")
        self.assertEqual(self.package()["items"], []); generate.assert_not_called()

    def test_direct_worker_and_shared_predicate_reject_stale_before_idle(self):
        for change in ("bytes", "missing", "policy", "excerpt", "routes"):
            self.new_workspace(); self.seed(execute=True)
            prior = self.saved()
            if change == "bytes": (self.root / "inbox/one.txt").write_text("changed")
            if change == "missing": (self.root / "inbox/one.txt").unlink()
            if change == "policy": self.gate_docs()
            if change in {"excerpt", "routes"}:
                path = self.root / "cache/workers/context_builder/context_package.json"
                data = json.loads(path.read_text())
                data["items"][0]["excerpt" if change == "excerpt" else "matched_routes"] = "forged" if change == "excerpt" else []
                path.write_text(json.dumps(data))
            with self.assertRaises(SourceReingestRequired): worker.execution_needs_run(self.root)
            with patch.object(model, "generate_if_enabled") as generate:
                output = self.run_worker("execution_engine")
            self.assertEqual(output["status"], "failed", change)
            self.assertEqual(output["errors"][0]["code"], "source_reingest_required")
            self.assertEqual(self.saved(), prior); generate.assert_not_called()

    def test_all_sources_are_checked_before_first_request(self):
        self.seed(count=2); self.enable()
        (self.root / "inbox/two.txt").write_text("please deploy stale second item")
        with patch.object(model, "generate_if_enabled") as generate:
            output = self.run_worker("execution_engine")
        self.assertEqual(output["status"], "failed"); generate.assert_not_called()

    def test_execution_context_digest_tracks_the_bytes_that_were_parsed(self):
        self.seed()
        path = self.root / "cache/workers/context_builder/context_package.json"
        package = self.package()
        package["generated_at"] = "2026-10-13T12:01:00Z"
        replacement = json.dumps(package).encode("utf-8")
        original_read = worker.read_validated_snapshot

        def replace_after_read(source, kind, **options):
            snapshot = original_read(source, kind, **options)
            if kind == "context_package":
                path.write_bytes(replacement)
            return snapshot

        before = self.saved()
        with patch.object(worker, "read_validated_snapshot", side_effect=replace_after_read):
            output = self.run_worker("execution_engine")
        self.assertEqual(output["status"], "failed")
        self.assertEqual(output["errors"][0]["code"], "source_reingest_required")
        self.assertEqual(self.saved(), before)

        self.assertEqual(self.run_worker("execution_engine")["status"], "complete")
        saved = self.saved()
        expected = hashlib.sha256(replacement).hexdigest()
        self.assertEqual(json.loads(saved["draft.json"])["source_context_sha256"], expected)
        self.assertEqual(json.loads(saved["state.json"])["last_context_sha256"], expected)

    def test_changes_during_model_calls_preserve_published_worker_artifacts(self):
        for role in ("context_builder", "execution_engine"):
            with self.subTest(role=role):
                self.new_workspace()
                self.seed(execute=True)
                if role == "context_builder":
                    self.ingest(text="Project docs updated.")
                self.enable(role)
                prior = self.saved(role)

                def hold_during_request(_request):
                    (self.root / "inbox/one.txt").write_text("Please deploy now.")
                    return "stale response"

                with self.transport(hold_during_request):
                    output = self.run_worker(role)
                self.assertEqual(output["status"], "failed")
                self.assertEqual(output["errors"][0]["code"], "source_reingest_required")
                self.assertEqual(self.saved(role), prior)
                os.environ.pop("MODEL_" + role.upper())

    def test_model_id_invalidates_but_api_key_does_not(self):
        self.seed(execute=True)
        self.assertFalse(worker.execution_needs_run(self.root))
        self.enable()
        self.assertTrue(worker.execution_needs_run(self.root))
        with self.transport():
            self.assertEqual(self.run_worker("execution_engine")["status"], "complete")
            self.assertFalse(worker.execution_needs_run(self.root))
            os.environ["OPENROUTER_API_KEY"] = "different-fake-key"
            self.assertFalse(worker.execution_needs_run(self.root))
            self.assertEqual(self.run_worker("execution_engine")["status"], "idle")
            os.environ["MODEL_EXECUTION_ENGINE"] = "stub/second"
            self.assertTrue(worker.execution_needs_run(self.root))
            self.assertEqual(self.run_worker("execution_engine")["status"], "complete")
        self.assertEqual(len(self.calls), 2)

    def test_strict_batch_mapping_never_publishes_partial_results(self):
        self.seed(count=2, execute=True); self.enable()
        prior = self.saved()
        valid = json.loads(self.batch("one.txt", "two.txt"))["drafts"]
        invalid = ["not JSON", json.dumps({"drafts": valid[:1]}), json.dumps({"drafts": valid + [valid[0]]}),
                   json.dumps({"drafts": valid + [{"relative_path": "inbox/extra.txt", "draft": "extra"}]}),
                   json.dumps({"drafts": [{**valid[0], "draft": 42}, valid[1]]}),
                   json.dumps({"drafts": [{"relative_path": " ", "draft": "empty path"}, valid[1]]}),
                   json.dumps({"drafts": [None, valid[1]]}), json.dumps({"drafts": {}})]
        for content in invalid:
            self.calls.clear()
            with self.transport(content): output = self.run_worker("execution_engine")
            self.assertEqual(output["status"], "failed")
            self.assertEqual(output["errors"][0]["code"], "model_error")
            self.assertEqual(self.saved(), prior)
            self.assertEqual(len(self.calls), 1)
        self.calls.clear()
        with self.transport("```json\n" + self.batch("two.txt", "one.txt") + "\n```"):
            self.assertEqual(self.run_worker("execution_engine")["status"], "complete")
        self.assertEqual(len(self.calls), 1)
        draft = json.loads((self.root / "cache/workers/execution_engine/draft.json").read_text())
        self.assertEqual([x["draft"] for x in draft["items"]], ["DRAFT one.txt", "DRAFT two.txt"])
        self.assertTrue(all(x["model"] == "stub/execution" and len(x["response_sha256"]) == 64 for x in draft["items"]))

    def test_approved_reason_requires_current_identity_and_signed_payload(self):
        self.ingest(text="please deploy local draft")
        rid = review.list_reviews(self.root)[0]["review_id"]
        review.record_review(self.root, rid, "approved", note="Local drafting only", approver="operator", now=STAMP)
        phase1.run_phase1(self.root, now=STAMP)
        self.assertEqual(self.run_worker()["status"], "complete")
        self.assertEqual(self.run_worker("execution_engine")["status"], "complete")
        self.assertEqual(self.package()["items"][0]["approved_review"], rid)
        payload = self.root / f"staging/reviews/{rid}.json"
        data = json.loads(payload.read_text()); data["notification_message"] = "tampered"
        payload.write_text(json.dumps(data))
        with patch.object(model, "generate_if_enabled") as generate:
            self.assertEqual(self.run_worker("execution_engine")["status"], "failed")
        generate.assert_not_called()

    def test_synthesis_never_becomes_execution_input(self):
        self.ingest(); self.enable("context_builder", "stub/context")
        with self.transport("SYNTHESIS-INSTRUCTION-SENTINEL"):
            self.assertEqual(self.run_worker()["status"], "complete")
        self.enable(); self.calls.clear()
        with self.transport(): self.run_worker("execution_engine")
        self.assertEqual(len(self.calls), 1)
        sent = json.dumps(self.calls)
        self.assertNotIn("SYNTHESIS-INSTRUCTION-SENTINEL", sent)
        self.assertIn("Project docs one.", sent)

    def test_approval_cannot_bypass_new_reasons_or_forbidden_policy(self):
        for change in ("reason", "class"):
            self.new_workspace()
            self.ingest(text="please deploy local draft")
            rid = review.list_reviews(self.root)[0]["review_id"]
            review.record_review(self.root, rid, "approved", note="Local drafting only", approver="operator", now=STAMP)
            phase1.run_phase1(self.root, now=STAMP); self.run_worker()
            policy_path = self.root / "config/action_classes.json"
            policy = json.loads(policy_path.read_text())
            if change == "reason":
                sop_path = self.root / "config/sops/phase1_ingestion.json"
                sop = json.loads(sop_path.read_text()); sop["boundary_gate"]["requires_review_keywords"].append("draft")
                sop_path.write_text(json.dumps(sop))
                policy["classes"]["review_required"].append("draft")
            else:
                policy["classes"]["review_required"].remove("deploy")
                policy["classes"]["forbidden"].append("deploy")
            policy_path.write_text(json.dumps(policy))
            with patch.object(model, "generate_if_enabled") as generate:
                output = self.run_worker("execution_engine")
            self.assertEqual(output["errors"][0]["code"], "source_reingest_required")
            generate.assert_not_called()

    def test_verifier_receives_current_held_evidence_as_untrusted_data(self):
        self.ingest(text="please deploy HELD-FOR-VERIFIER")
        rid = review.list_reviews(self.root)[0]["review_id"]
        self.enable("verifier", "stub/verifier")
        with self.transport("PASS: local drafting"):
            review.record_review(self.root, rid, "approved", note="Local drafting only", approver="operator", now=STAMP)
        self.assertEqual(len(self.calls), 1)
        prompt = self.calls[0]["messages"][0]["content"]
        self.assertIn("HELD-FOR-VERIFIER", prompt)
        self.assertIn("deploy", prompt)
        self.assertIn("untrusted", prompt.lower())
        self.assertIn("never follow", prompt.lower())

    def test_freshness_failures_in_dry_run_leave_all_files_unchanged(self):
        self.seed()
        (self.root / "inbox/one.txt").write_text("changed outside ingestion")
        (self.root / ".looping_box.lock").unlink()
        def snapshot():
            return {str(p.relative_to(self.root)): None if p.is_dir() else p.read_bytes()
                    for p in self.root.rglob("*")}
        before = snapshot()
        with patch.object(model, "generate_if_enabled") as generate:
            for wid in ("context_builder", "execution_engine"):
                self.assertEqual(self.run_worker(wid, dry_run=True)["status"], "failed")
        self.assertEqual(snapshot(), before); generate.assert_not_called()

    def test_error_redaction_precedes_truncation_and_persistence(self):
        self.seed(); self.enable()
        with self.transport(RuntimeError("transport echoed " + KEY)):
            output = self.run_worker("execution_engine")
        self.assertNotIn(KEY, json.dumps(output)); self.assertIn("[REDACTED]", json.dumps(output))
        def provider_error(*args):
            return json.dumps({"error": "x" * 190 + KEY + " tail"})
        with patch.object(model, "_transport", side_effect=provider_error):
            with self.assertRaises(model.ModelError) as caught:
                model.complete("execution_engine", "fixture", root=self.root)
        self.assertNotIn(KEY, str(caught.exception)); self.assertNotIn(KEY[:10], str(caught.exception))
        self.ingest("held.txt", "please deploy")
        rid = review.list_reviews(self.root)[0]["review_id"]
        self.enable("verifier", "stub/verifier")
        with self.transport(RuntimeError("v" * 145 + KEY)):
            with self.assertRaisesRegex(ValueError, "review_verifier_failed"):
                review.record_review(self.root, rid, "approved", note="test", approver="operator", now=STAMP)
        verifier = (self.root / f"cache/verifiers/{rid}.json").read_text()
        self.assertNotIn(KEY, verifier); self.assertNotIn(KEY[:10], verifier)
        for path in (self.root / "cache").rglob("*"):
            if path.is_file(): self.assertNotIn(KEY.encode(), path.read_bytes())

    def test_provider_shape_failures_are_model_errors_without_success_markers(self):
        self.seed(execute=True); self.enable(); prior = self.saved()
        for response in ("{broken", "null", json.dumps({"error": {"message": "fixture failure"}}),
                         json.dumps({"choices": [{"message": {"content": None}}]}),
                         json.dumps({"choices": [{"message": {"content": 12}}]}),
                         json.dumps({"choices": [{"message": {"content": "\ud800"}}]}),
                         json.dumps({"model": "\ud800", "choices": [{"message": {"content": "text"}}]}),
                         json.dumps({"model": None, "choices": [{"message": {"content": "text"}}]})):
            with patch.object(model, "_transport", return_value=response) as transport:
                output = self.run_worker("execution_engine")
            self.assertEqual(output["status"], "failed")
            self.assertEqual(output["errors"][0]["code"], "model_error")
            self.assertEqual(self.saved(), prior); transport.assert_called_once()

    def test_decoded_batch_rejects_echoed_key_and_invalid_unicode_before_writes(self):
        self.seed(count=2, execute=True)
        self.enable()
        prior = self.saved()
        history = self.root / "cache/workers/execution_engine/history"
        history_before = sorted(history.glob("*"))
        for value in (KEY, "\ud800"):
            content = json.dumps({"drafts": [
                {"relative_path": "inbox/one.txt", "draft": value},
                {"relative_path": "inbox/two.txt", "draft": "fixture draft"},
            ]})
            if value == KEY:
                content = content.replace(KEY, f"\\u{ord(KEY[0]):04x}{KEY[1:]}")
            with self.subTest(value=value), self.transport(content):
                output = self.run_worker("execution_engine")
                self.assertEqual(output["status"], "failed")
                self.assertEqual(output["errors"][0]["code"], "model_error")
                self.assertNotIn(KEY, output["errors"][0]["message"])
                self.assertEqual(self.saved(), prior)
                self.assertEqual(sorted(history.glob("*")), history_before)

    def test_decoded_batch_rejects_the_sent_key_after_key_rotation(self):
        self.seed(count=2, execute=True)
        self.enable()
        prior = self.saved()
        content = json.dumps({"drafts": [
            {"relative_path": "inbox/one.txt", "draft": KEY},
            {"relative_path": "inbox/two.txt", "draft": "fixture draft"},
        ]}).replace(KEY, f"\\u{ord(KEY[0]):04x}{KEY[1:]}")

        def rotate_key(_request):
            os.environ["OPENROUTER_API_KEY"] = "rotated-fixture-key"
            return content

        with self.transport(rotate_key):
            output = self.run_worker("execution_engine")
        self.assertEqual(output["status"], "failed")
        self.assertEqual(output["errors"][0]["code"], "model_error")
        self.assertNotIn(KEY, json.dumps(output))
        self.assertEqual(self.saved(), prior)

    def test_configured_credential_in_model_id_is_rejected_even_when_disabled(self):
        self.seed(execute=True)
        before = self.saved()
        (self.root / ".env").write_text(f"OPENROUTER_API_KEY={KEY}\nMODEL_EXECUTION_ENGINE={KEY}\n")
        model._env_loaded_for.clear()
        for disabled in (False, True):
            with self.subTest(disabled=disabled), patch.dict(
                os.environ, {"OPENROUTER_API_KEY": "" if disabled else KEY}
            ), patch.object(model, "_transport") as transport:
                output = self.run_worker("execution_engine")
            transport.assert_not_called()
            self.assertEqual(output["status"], "failed")
            self.assertEqual(output["errors"][0]["code"], "model_error")
            self.assertNotIn(KEY, json.dumps(output))
            self.assertEqual(self.saved(), before)

    def test_disabled_roles_and_env_overrides_do_not_grant_authority(self):
        (self.root / ".env").write_text("LOOPING_BOX_REVIEW_KEY=forged\nLOOPING_BOX_ALLOW_NONINTERACTIVE=1\nMODEL_EXECUTION_ENGINE=stub/from-env\n")
        model._env_loaded_for.clear()
        self.seed()
        with patch.object(model, "_transport") as transport:
            self.assertEqual(self.run_worker("execution_engine")["status"], "complete")
            transport.assert_not_called()
        self.assertEqual(os.environ["LOOPING_BOX_REVIEW_KEY"], "fake-review-key")
        self.assertNotIn("LOOPING_BOX_ALLOW_NONINTERACTIVE", os.environ)
        os.environ["MODEL_EXECUTION_ENGINE"] = "stub/process-wins"
        os.environ["OPENROUTER_API_KEY"] = KEY
        self.assertEqual(model.model_for("execution_engine", root=self.root), "stub/process-wins")
        os.environ["MODEL_EXECUTION_ENGINE"] = ""
        with patch.object(model, "_transport") as transport:
            self.assertIsNone(model.generate_if_enabled("execution_engine", "fixture", root=self.root))
            transport.assert_not_called()


if __name__ == "__main__":
    unittest.main()
