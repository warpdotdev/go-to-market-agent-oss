import json
from pathlib import Path
import unittest


BDR_AGENT_ROOT = Path(__file__).resolve().parents[2]
SKILL_PATH = BDR_AGENT_ROOT / "skills" / "self-improvement" / "SKILL.md"
EVALS_PATH = (
    BDR_AGENT_ROOT / "skills" / "self-improvement" / "evals" / "evals.json"
)


class BdrSelfImprovementSkillTest(unittest.TestCase):
    def test_skill_preserves_self_improvement_safety_boundaries(self) -> None:
        content = SKILL_PATH.read_text()

        self.assertIn("BDR Self-Improvement Agent", content)
        self.assertIn("references/outreach_positioning_guide.md", content)
        self.assertIn("references/outreach_style_guide.md", content)
        self.assertIn("Open a PR only when a guide change is warranted", content)
        self.assertIn("End silently without a PR", content)
        self.assertIn("No HubSpot writeback permission is required", content)
        self.assertIn("Do not create:", content)
        self.assertIn("a third durable pattern library", content)
        self.assertNotIn("src/bdr_agent/feedback_loop/README.md", content)

    def test_skill_pins_canonical_positioning_sources(self) -> None:
        content = SKILL_PATH.read_text()

        self.assertIn("Canonical positioning sources", content)
        self.assertIn("[your-marketing-repo]", content)
        self.assertIn("Do not explore unrelated marketing pages", content)
        self.assertIn("Homepage", content)
        self.assertIn("Enterprise page", content)
        self.assertIn("Platform page", content)

    def test_eval_prompts_cover_no_signal_learning_and_source_updates(self) -> None:
        evals = json.loads(EVALS_PATH.read_text())
        evals_text = EVALS_PATH.read_text()

        self.assertEqual(evals["skill_name"], "bdr-self-improvement")
        self.assertGreaterEqual(len(evals["evals"]), 5)
        self.assertNotIn("src/bdr_agent/feedback_loop/README.md", evals_text)

        prompts = "\n".join(item["prompt"] for item in evals["evals"])
        expected_outputs = "\n".join(item["expected_output"] for item in evals["evals"])
        assertions = "\n".join(
            assertion
            for item in evals["evals"]
            for assertion in item.get("assertions", [])
        )

        all_eval_text = "\n".join((prompts, expected_outputs, assertions))

        self.assertIn("no-signal", all_eval_text)
        self.assertIn("positioning guide", expected_outputs)
        self.assertIn("marketing repo", prompts)
        self.assertIn("https://example.com/platform", prompts)
        self.assertIn("landing-v2.md", all_eval_text)
        self.assertIn("lead-specific", expected_outputs)
        self.assertIn("Does not attempt HubSpot writeback.", assertions)
        self.assertIn("Opens at most one PR.", assertions)


if __name__ == "__main__":
    unittest.main()
