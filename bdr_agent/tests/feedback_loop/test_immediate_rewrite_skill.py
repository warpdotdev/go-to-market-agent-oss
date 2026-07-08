from pathlib import Path
import re
import unittest


BDR_AGENT_ROOT = Path(__file__).parents[2]
IMMEDIATE_REWRITE_SKILL_PATH = BDR_AGENT_ROOT / "skills" / "immediate-rewrite" / "SKILL.md"


def skill_text() -> str:
    return IMMEDIATE_REWRITE_SKILL_PATH.read_text()


def frontmatter(path: Path) -> dict[str, str]:
    lines = path.read_text().splitlines()
    if not lines or lines[0] != "---":
        raise ValueError(f"{path} is missing frontmatter")
    closing_index = lines[1:].index("---") + 1
    parsed = {}
    for line in lines[1:closing_index]:
        key, value = line.split(":", 1)
        parsed[key.strip()] = value.strip()
    return parsed


class BdrImmediateRewriteSkillContractTest(unittest.TestCase):
    def test_skill_exists_with_triggering_metadata(self) -> None:
        self.assertTrue(IMMEDIATE_REWRITE_SKILL_PATH.exists())
        metadata = frontmatter(IMMEDIATE_REWRITE_SKILL_PATH)

        self.assertEqual(metadata["name"], "bdr-immediate-rewrite")
        self.assertIn("Slack-thread", metadata["description"])
        self.assertIn("@BDR Agent", metadata["description"])
        self.assertIn("immediate rewrite", metadata["description"])

    def test_runtime_sources_are_limited_to_slack_brief_drafts_guides_and_feedback(self) -> None:
        skill = skill_text()

        self.assertIn("Slack thread context", skill)
        self.assertIn("The linked or full lead brief and all ranked drafts", skill)
        self.assertIn("references/outreach_positioning_guide.md", skill)
        self.assertIn("references/outreach_style_guide.md", skill)
        self.assertIn("The exact Slack feedback text", skill)
        self.assertIn("Use only these sources", skill)

        forbidden_source_mentions = [
            "src/bdr_agent/feedback_loop/README.md",
            "src/bdr_agent/feedback_loop/dry_run.py",
            "src/bdr_agent/feedback_loop/",
            "whole feedback-loop directory",
            "read the feedback-loop",
        ]
        for forbidden in forbidden_source_mentions:
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, skill)

    def test_skill_requires_alternate_draft_first_rewrite_behavior(self) -> None:
        skill = skill_text()

        self.assertIn("Read the lead brief and all ranked drafts before writing", skill)
        self.assertIn("Consider the existing rank 2 and rank 3 drafts before drafting from scratch", skill)
        self.assertIn("If an alternate ranked draft already satisfies the feedback", skill)

    def test_skill_preserves_template_boundaries(self) -> None:
        skill = skill_text()

        self.assertIn("No greeting line", skill)
        self.assertIn("No sign-off", skill)
        self.assertIn("No sender name", skill)
        self.assertIn("Rewrite only the body content", skill)

    def test_skill_encodes_immediate_safe_writeback_without_second_approval(self) -> None:
        skill = skill_text()
        normalized = re.sub(r"\s+", " ", skill).lower()

        self.assertIn("approval to write back", skill)
        self.assertIn("Do not require a second explicit approval", skill)
        self.assertIn("ai_hook_intro", skill)
        self.assertIn("ai_personalized_at", skill)
        self.assertIn("All unrelated fields remain forbidden", skill)
        self.assertNotIn("preview-before-writeback", normalized)
        self.assertNotIn("requires preview before writeback", normalized)

    def test_missing_metadata_behavior_is_narrow(self) -> None:
        skill = skill_text()

        self.assertIn("Ask a narrow clarifying question only when", skill)
        self.assertIn("cannot identify exactly one lead/output/HubSpot record", skill)
        self.assertIn("post the preview in Slack and explicitly skip HubSpot writeback", skill)


if __name__ == "__main__":
    unittest.main()
