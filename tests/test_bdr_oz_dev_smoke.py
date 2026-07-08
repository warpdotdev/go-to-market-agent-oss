import importlib.util
from pathlib import Path
import sys
import unittest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "bdr_oz_dev_smoke.py"
SPEC = importlib.util.spec_from_file_location("bdr_oz_dev_smoke", SCRIPT_PATH)
bdr_oz_dev_smoke = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules["bdr_oz_dev_smoke"] = bdr_oz_dev_smoke
SPEC.loader.exec_module(bdr_oz_dev_smoke)


class BdrOzDevSmokeTest(unittest.TestCase):
    def test_validate_test_id_rejects_missing_id_for_write_checks(self) -> None:
        with self.assertRaises(ValueError):
            bdr_oz_dev_smoke.validate_test_id(None, required_for="GCS write/read checks")

    def test_validate_test_id_rejects_unsafe_path_components(self) -> None:
        with self.assertRaises(ValueError):
            bdr_oz_dev_smoke.validate_test_id("../unsafe", required_for="GCS write/read checks")

    def test_skill_load_paths_have_frontmatter(self) -> None:
        result = bdr_oz_dev_smoke.check_skill_load_paths()

        self.assertEqual(result["skill_count"], 2)
        self.assertEqual(
            [skill["name"] for skill in result["skills"]],
            ["bdr-company-research", "bdr-outreach-composer"],
        )

    def test_step2_persistence_requires_test_id_and_lead_id(self) -> None:
        parser = bdr_oz_dev_smoke.build_parser()
        args = parser.parse_args(["--allow-step2-persist", "--test-id", "smoke-test-001"])

        with self.assertRaises(ValueError):
            bdr_oz_dev_smoke.run_smoke(args)

    def test_default_smoke_reports_skipped_writes_when_network_checks_are_disabled(self) -> None:
        parser = bdr_oz_dev_smoke.build_parser()
        args = parser.parse_args(
            [
                "--skip-dependency-imports",
                "--skip-bigquery-read",
                "--skip-exa-key-check",
            ]
        )

        report = bdr_oz_dev_smoke.run_smoke(args)

        self.assertEqual(report["status"], "passed")
        self.assertFalse(report["stage_completion_webhook_called"])
        self.assertFalse(report["hubspot_writes_performed"])
        persist_check = next(
            check for check in report["checks"] if check["name"] == "step2_persist_skip_stage_completion"
        )
        self.assertEqual(persist_check["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
