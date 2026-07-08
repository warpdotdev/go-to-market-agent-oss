import json
from pathlib import Path
import tempfile
import unittest

from bdr_agent.stages.outreach_composer.local_preview import (
    PREVIEW_CASES,
    analyze_body,
    render_compact_prompt,
    selected_cases,
    write_prompts,
)


class OutreachComposerLocalPreviewTest(unittest.TestCase):
    def test_cases_include_representative_leads(self) -> None:
        self.assertIn("example_senior_engineer", PREVIEW_CASES)
        self.assertIn("fixture_vp_engineering", PREVIEW_CASES)
        self.assertIn("platform_orchestration_representative", PREVIEW_CASES)
        self.assertIn("memory_evals_representative", PREVIEW_CASES)

    def test_render_compact_prompt_uses_oz_stage_fields_without_full_research_payload(self) -> None:
        prompt = render_compact_prompt(PREVIEW_CASES["example_senior_engineer"])

        self.assertIn("LEAD_ID=lead_456", prompt)
        self.assertIn("BDR_AGENT_STAGE=lead_brief", prompt)
        self.assertIn("SOURCE_STAGE=company_research", prompt)
        self.assertIn("COMPANY_RESEARCH_OUTPUT_ID=", prompt)
        self.assertNotIn("tier_2_public_company_research", prompt)
        self.assertNotIn("brief_markdown", prompt)

    def test_write_prompts_creates_case_prompts_and_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = write_prompts(Path(tmpdir), selected_cases(["example_senior_engineer"]))

            self.assertEqual({path.name for path in paths}, {"example_senior_engineer.prompt.txt", "README.md"})
            self.assertIn("Example / senior engineer", (Path(tmpdir) / "README.md").read_text())

    def test_analyze_body_flags_generic_and_concrete_bridge_language(self) -> None:
        generic = analyze_body(
            "[Your product] helps engineering teams with the infrastructure side of agent work.\n\n"
            "It makes agent workflows easier to run, review, and control."
        )
        concrete = analyze_body(
            "I saw Example's engineering post about AI agents.\n\n"
            "[Your product] gives teams cloud agents with GitHub triggers and run transcripts so the work is easier to inspect."
        )

        self.assertIn("infrastructure side", generic["generic_bridge_phrases"])
        self.assertIn("run, review, and control", generic["generic_bridge_phrases"])
        self.assertEqual(generic["concrete_bridge_terms"], [])
        self.assertTrue(concrete["source_specific_opener"])
        self.assertIn("cloud agents", concrete["concrete_bridge_terms"])
        self.assertIn("GitHub", concrete["concrete_bridge_terms"])
        self.assertIn("transcripts", concrete["concrete_bridge_terms"])
        self.assertEqual(concrete["generic_bridge_phrases"], [])

    def test_preview_after_packet_can_be_validated_by_loader(self) -> None:
        packet = {
            "brief_markdown": "# Lead brief: Ada | Example\n\n## Lead details\n- **Lead:** Ada",
            "email_body_drafts": [
                {
                    "rank": 1,
                    "label": "Concrete bridge",
                    "why_this_may_work": "It connects a public source to a concrete product surface.",
                    "body": (
                        "I saw Example's engineering post about AI agents.\n\n"
                        "[Your product] gives teams cloud agents with GitHub triggers and run transcripts so the work is easier to inspect."
                    ),
                    "source_refs": ["https://example.com/engineering/ai-agents"],
                },
                {
                    "rank": 2,
                    "label": "Terminal workflow",
                    "why_this_may_work": "It maps developer workflow evidence to a concrete product surface.",
                    "body": (
                        "I noticed Example has been writing about developer productivity.\n\n"
                        "[Your product] keeps AI work close to the developer workflow, while longer-running tasks can move into shared cloud runs."
                    ),
                    "source_refs": ["https://example.com/engineering/ai-agents"],
                },
                {
                    "rank": 3,
                    "label": "Evals angle",
                    "why_this_may_work": "It uses repeatability without overclaiming.",
                    "body": (
                        "I saw the AI agents post from Example's engineering team.\n\n"
                        "[Your product] is focused on making recurring agent work easier to review with memory and regression checks before teams rely on it."
                    ),
                    "source_refs": ["https://example.com/engineering/ai-agents"],
                },
            ],
            "evaluation": {"status": "passed", "notes": "Local preview packet is valid."},
            "rewrite": {"attempted": False, "reason": None},
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "packet.json"
            path.write_text(json.dumps(packet))
            from bdr_agent.stages.outreach_composer.local_preview import load_body_from_packet

            self.assertIn("cloud agents", load_body_from_packet(path))


if __name__ == "__main__":
    unittest.main()
