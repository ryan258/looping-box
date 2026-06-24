import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from looping_box.schema import validate

SCHEMA_DIR = ROOT / "docs" / "schemas"
CONTRACT_DIR = ROOT / "docs" / "contracts"


class WorkerContractTests(unittest.TestCase):
    def test_worker_contract_examples_validate(self):
        schema = json.loads((SCHEMA_DIR / "worker_output.schema.json").read_text())

        valid_payload = json.loads((CONTRACT_DIR / "worker_output.example.json").read_text())
        validate(valid_payload, schema)

        invalid_payload = dict(valid_payload)
        invalid_payload["status"] = "surprising"
        with self.assertRaises(ValueError):
            validate(invalid_payload, schema)

    def test_worker_runtime_directories_are_preserved(self):
        expected_paths = [
            ROOT / "config" / "workers",
            ROOT / "cache" / "workers" / "context_builder",
            ROOT / "cache" / "workers" / "execution_engine",
            ROOT / "logs" / "transactions",
        ]

        for path in expected_paths:
            with self.subTest(path=path):
                self.assertTrue(path.is_dir())
                self.assertTrue((path / ".gitkeep").exists())


if __name__ == "__main__":
    unittest.main()
