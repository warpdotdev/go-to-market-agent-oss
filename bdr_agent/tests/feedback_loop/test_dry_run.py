import unittest

from bdr_agent.feedback_loop.dry_run import (
    ALLOWED_IMMEDIATE_WRITEBACK_FIELDS,
    CREATED_AT_PROPERTY_NAME,
    HOOK_PROPERTY_NAME,
    classify_feedback_event,
    classify_feedback_scope,
    run_dry_run,
)


class FeedbackLoopDryRunTest(unittest.TestCase):
    def test_no_signal_event_skips_silently(self) -> None:
        result = classify_feedback_event(
            {
                "scenario_id": "no_signal",
                "original_draft": "Original body.\n\nSecond paragraph.",
                "feedback_text": "",
                "reactions": [],
            }
        )

        self.assertEqual(result["action"], "skip")
        self.assertEqual(result["skip_reason"], "no_signal")
        self.assertFalse(result["has_signal"])
        self.assertTrue(result["should_end_silently"])
        self.assertFalse(result["should_create_guide_pr"])

    def test_thumbs_up_records_signal_without_rewrite_or_writeback(self) -> None:
        result = classify_feedback_event(
            {
                "scenario_id": "thumbs_up",
                "original_draft": "Original body.\n\nSecond paragraph.",
                "feedback_text": "",
                "reactions": [{"name": "+1", "user": "U_BDR"}],
            }
        )

        self.assertEqual(result["action"], "record_reaction_signal")
        self.assertEqual(result["reaction_signals"][0]["signal"], "positive")
        self.assertFalse(result["safe_writeback"]["writeback_requested"])
        self.assertEqual(result["guide_target"], "none")

    def test_explicit_rewrite_routes_to_style_and_allows_immediate_safe_writeback(self) -> None:
        result = classify_feedback_event(
            {
                "scenario_id": "explicit_rewrite",
                "original_draft": "Our product is in a related area.\n\nCurious to chat?",
                "feedback_text": "@bdr-agent rewrite with a more human opener and softer CTA.",
                "rewrite_body": "I saw Example's AI agent launch.\n\nOur platform may be relevant because it can run cloud agents from Slack. Worth comparing notes?",
                "reactions": [{"name": "pencil2"}],
                "thread_contract": {
                    "lead_id": "lead_123",
                    "lead_brief_output_id": "output_123",
                    "hubspot_object_type": "contact",
                    "hubspot_object_id": "contact_123",
                    "hook_property_name": HOOK_PROPERTY_NAME,
                    "slack_channel_id": "C_REVIEW",
                    "slack_thread_ts": "1716240000.000100",
                },
            }
        )

        self.assertEqual(result["action"], "rewrite_and_writeback_if_safe")
        self.assertEqual(result["feedback_scope"], "style")
        self.assertEqual(result["guide_target"], "bdr_style_guide")
        self.assertFalse(result["safe_writeback"]["requires_second_explicit_approval"])
        self.assertTrue(result["safe_writeback"]["can_writeback_immediately"])
        self.assertTrue(result["safe_writeback"]["should_post_preview"])
        self.assertEqual(
            result["safe_writeback"]["allowed_field_updates"],
            list(ALLOWED_IMMEDIATE_WRITEBACK_FIELDS),
        )
        self.assertIn(HOOK_PROPERTY_NAME, result["safe_writeback"]["allowed_field_updates"])
        self.assertIn(CREATED_AT_PROPERTY_NAME, result["safe_writeback"]["allowed_field_updates"])
        self.assertIn("firstname", result["safe_writeback"]["forbidden_field_updates"])
        self.assertTrue(result["safe_writeback"]["preserve_template_boundaries"])

    def test_explicit_rewrite_with_missing_record_posts_preview_and_skips_writeback(self) -> None:
        result = classify_feedback_event(
            {
                "scenario_id": "ambiguous_rewrite",
                "original_draft": "Original body.\n\nSecond paragraph.",
                "feedback_text": "@bdr-agent rewrite this to be less generic.",
                "rewrite_body": "I saw Example's AI agent note.\n\nOur platform could help run those workflows with reviewable Slack-triggered sessions.",
                "thread_contract": {
                    "lead_id": "lead_123",
                    "lead_brief_output_id": "output_123",
                    "slack_channel_id": "C_REVIEW",
                    "slack_thread_ts": "1716240000.000100",
                },
            }
        )

        self.assertEqual(result["action"], "rewrite_and_writeback_if_safe")
        self.assertTrue(result["safe_writeback"]["writeback_requested"])
        self.assertTrue(result["safe_writeback"]["should_post_preview"])
        self.assertTrue(result["safe_writeback"]["preview_when_writeback_unsafe"])
        self.assertFalse(result["safe_writeback"]["can_writeback_immediately"])
        self.assertEqual(
            result["safe_writeback"]["missing_metadata_behavior"],
            "ask_only_if_no_single_lead_output_or_hubspot_record",
        )

    def test_positioning_feedback_routes_to_positioning_guide(self) -> None:
        route = classify_feedback_scope(
            "Make the product bridge concrete with cloud agents, Slack triggers, and less stale product messaging."
        )

        self.assertEqual(route["scope"], "positioning")
        self.assertEqual(route["guide_target"], "positioning_guide")

    def test_lead_specific_and_redundant_feedback_does_not_change_guides(self) -> None:
        for event in [
            {
                "scenario_id": "lead_specific",
                "feedback_kind": "lead_specific",
                "feedback_text": "For this lead only, avoid the webinar because I already know them.",
                "original_draft": "Original body.\n\nSecond paragraph.",
            },
            {
                "scenario_id": "redundant",
                "feedback_text": "The guide already says not to say infrastructure side.",
                "original_draft": "Original body.\n\nSecond paragraph.",
            },
        ]:
            with self.subTest(event=event["scenario_id"]):
                result = classify_feedback_event(event)
                self.assertIn(result["feedback_scope"], {"lead_specific", "redundant"})
                self.assertEqual(result["guide_target"], "none")
                self.assertFalse(result["durable_learning_candidate"])
                self.assertFalse(result["should_create_guide_pr"])

    def test_default_dry_run_covers_expected_scenarios(self) -> None:
        results = run_dry_run()

        self.assertEqual(
            [result["scenario_id"] for result in results],
            ["no_signal", "thumbs_up", "explicit_rewrite", "lead_specific_redundant"],
        )
        self.assertEqual(results[0]["action"], "skip")
        self.assertEqual(results[1]["action"], "record_reaction_signal")
        self.assertEqual(results[2]["action"], "rewrite_and_writeback_if_safe")
        self.assertEqual(results[3]["guide_target"], "none")


if __name__ == "__main__":
    unittest.main()
