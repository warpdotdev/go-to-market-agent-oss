import unittest
from unittest import mock

from plg_upsell.scripts import scoring_sync


class ScoringSyncTests(unittest.TestCase):
    def test_resolve_company_id_does_not_create_for_ineligible_domain(self):
        with mock.patch.object(
            scoring_sync,
            "hubspot_request",
            return_value={"results": []},
        ) as request_mock:
            company_id, is_new = scoring_sync.resolve_company_id(
                "closed-won.example",
                cache={},
                allow_create=False,
            )

        self.assertIsNone(company_id)
        self.assertFalse(is_new)
        request_mock.assert_called_once()
        self.assertEqual(request_mock.call_args.args[0], "POST")
        self.assertEqual(request_mock.call_args.args[1], "/crm/v3/objects/companies/search")

    def test_resolve_company_id_uses_existing_ineligible_company(self):
        with mock.patch.object(
            scoring_sync,
            "hubspot_request",
            return_value={"results": [{"id": "123", "properties": {"domain": "closed-won.example"}}]},
        ):
            cache = {}
            company_id, is_new = scoring_sync.resolve_company_id(
                "closed-won.example",
                cache=cache,
                allow_create=False,
            )

        self.assertEqual(company_id, "123")
        self.assertFalse(is_new)
        self.assertEqual(cache, {"closed-won.example": "123"})

    def test_zero_score_deprioritizes_active_company(self):
        payload = scoring_sync.build_company_payload(
            "123",
            {
                "pqa_score": 0,
                "pqa_avg_wau": 12,
                "pqa_ai_credits_30d": 1000,
                "pqa_wow_growth": -0.5,
                "pqa_users_hitting_limits_14d": 0,
                "pqa_reload_spend_14d": 0,
                "pqa_free_to_paid_30d": 0,
                "pqa_new_members_14d": 0,
                "is_eligible": False,
                "ineligibility_reason": "entered_enterprise_pipeline",
            },
            {
                "pqa_score": "70.3",
                "pqa_status": "active",
                "pqa_weeks_above_threshold": "0",
                "pqa_weeks_below_threshold": "0",
            },
            now_ms=1234567890,
        )

        self.assertEqual(payload["properties"]["pqa_score"], "0.0")
        self.assertEqual(payload["properties"]["pqa_tier"], "tier_3")
        self.assertEqual(payload["properties"]["pqa_status"], "deprioritized")
        self.assertEqual(payload["_meta"]["prev_status"], "active")
        self.assertEqual(payload["_meta"]["new_status"], "deprioritized")

    def test_resolve_contact_id_ignores_legacy_enrichment_and_lead_source_flags(self):
        responses = [
            {"results": []},
            {"id": "456"},
        ]
        with mock.patch.object(
            scoring_sync,
            "hubspot_request",
            side_effect=responses,
        ) as request_mock:
            contact_id, is_new = scoring_sync.resolve_contact_id(
                "champion@example.com",
                enable_enrichment=True,
                tag_as_pql=True,
            )

        self.assertEqual(contact_id, "456")
        self.assertTrue(is_new)
        create_call = request_mock.call_args_list[1]
        self.assertEqual(create_call.args[0], "POST")
        self.assertEqual(create_call.args[1], "/crm/v3/objects/contacts")
        self.assertEqual(create_call.args[2]["properties"], {"email": "champion@example.com"})

if __name__ == "__main__":
    unittest.main()
