import importlib
from pathlib import Path
import subprocess
import sys
import unittest


REPO_ROOT = Path(__file__).parents[2]


class StageRuntimeNamespaceTest(unittest.TestCase):
    def test_company_research_stage_namespace_exposes_runtime_surface(self) -> None:
        package = importlib.import_module("bdr_agent.stages.company_research")
        config = importlib.import_module("bdr_agent.stages.company_research.config")
        run = importlib.import_module("bdr_agent.stages.company_research.run")
        storage = importlib.import_module("bdr_agent.stages.company_research.storage")
        schemas = importlib.import_module("bdr_agent.stages.company_research.schemas")

        self.assertEqual(package.STAGE, "company_research")
        self.assertEqual(package.STAGE, config.STAGE)
        self.assertEqual(package.SCHEMA_VERSION, config.SCHEMA_VERSION)
        self.assertIs(package.run_company_research, run.run_company_research)
        self.assertTrue(callable(storage.persist_company_research_result))
        self.assertTrue(callable(schemas.build_minimal_company_research_output))

    def test_outreach_composer_stage_namespace_exposes_runtime_surface(self) -> None:
        package = importlib.import_module("bdr_agent.stages.outreach_composer")
        config = importlib.import_module("bdr_agent.stages.outreach_composer.config")
        run = importlib.import_module("bdr_agent.stages.outreach_composer.run")
        storage = importlib.import_module("bdr_agent.stages.outreach_composer.storage")
        validation = importlib.import_module("bdr_agent.stages.outreach_composer.validation")

        self.assertEqual(package.CANONICAL_STAGE, "outreach_composer")
        self.assertEqual(package.STAGE, config.STAGE)
        self.assertEqual(package.SCHEMA_VERSION, config.SCHEMA_VERSION)
        self.assertIs(package.run_lead_brief, run.run_lead_brief)
        self.assertIs(package.run_outreach_composer, run.run_lead_brief)
        self.assertTrue(callable(storage.persist_lead_brief_result))
        self.assertTrue(callable(validation.normalize_lead_brief_packet))

    def test_company_research_stage_cli_module_delegates_to_runtime_help(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "bdr_agent.stages.company_research.cli", "--help"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0)
        self.assertIn("Run BDR company research", result.stdout)
        self.assertIn("--lead-id", result.stdout)

    def test_outreach_composer_stage_cli_module_delegates_to_runtime_help(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "bdr_agent.stages.outreach_composer.cli", "--help"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0)
        self.assertIn("Persist a skill-authored BDR lead brief", result.stdout)
        self.assertIn("--lead-id", result.stdout)


if __name__ == "__main__":
    unittest.main()
