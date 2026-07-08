import unittest

from plg_upsell.dagster import core


class PlgDagsterCoreTests(unittest.TestCase):
    def test_routing_motion_and_sequence_intent_by_tier(self):
        self.assertEqual(core.routing_motion_for_tier("tier_1"), "manual_bdr")
        self.assertEqual(core.routing_motion_for_tier("tier_2"), "held")
        self.assertEqual(core.routing_motion_for_tier("tier_3"), "marketing_touch")

        tier_1 = core.sequence_intent_for_tier("tier_1")
        self.assertTrue(tier_1["should_enroll"])
        self.assertEqual(tier_1["sequence_id"], core.PRODUCT_QUALIFIED_SEQUENCE_ID)
        self.assertEqual(tier_1["sequence_name"], core.PRODUCT_QUALIFIED_SEQUENCE_NAME)
        self.assertEqual(tier_1["enrollment_mode"], "manual")

        tier_2 = core.sequence_intent_for_tier("tier_2")
        self.assertFalse(tier_2["should_enroll"])
        self.assertIsNone(tier_2["sequence_id"])
        self.assertIsNone(tier_2["sequence_name"])
        self.assertIsNone(tier_2["enrollment_mode"])

        tier_3 = core.sequence_intent_for_tier("tier_3")
        self.assertFalse(tier_3["should_enroll"])
        self.assertIsNone(tier_3["sequence_id"])

    def test_pql_owner_maps_ae_to_paired_bdr(self):
        self.assertEqual(core.pql_owner_for_crm_owner(core.AE_OWNER_1), core.BDR_OWNER_A)
        self.assertEqual(core.pql_owner_for_crm_owner(core.AE_OWNER_2), core.BDR_OWNER_A)
        self.assertEqual(core.pql_owner_for_crm_owner(core.AE_OWNER_3), core.BDR_OWNER_B)
        self.assertEqual(core.pql_owner_for_crm_owner(core.AE_OWNER_4), core.BDR_OWNER_B)

    def test_pql_owner_preserves_bdr_and_unknown_owner(self):
        self.assertEqual(core.pql_owner_for_crm_owner(core.BDR_OWNER_A), core.BDR_OWNER_A)
        self.assertEqual(core.pql_owner_for_crm_owner(core.BDR_OWNER_B), core.BDR_OWNER_B)
        self.assertEqual(core.pql_owner_for_crm_owner("some_other_owner"), "some_other_owner")
        self.assertIsNone(core.pql_owner_for_crm_owner(None))

    def test_ae_owner_for_company_routing_uses_engineering_tiers(self):
        self.assertEqual(
            core.ae_owner_for_company_routing({"eng_count_bucket": "5k+"}),
            core.AE_OWNER_3,
        )
        self.assertEqual(
            core.ae_owner_for_company_routing({"number_of_engineers_clay": "1200"}),
            core.AE_OWNER_4,
        )
        self.assertEqual(
            core.ae_owner_for_company_routing({"eng_count_bucket": "50-500"}),
            core.AE_OWNER_4,
        )
        self.assertEqual(core.ae_owner_for_company_routing({}), core.AE_OWNER_4)

    def test_company_enrichment_ready_accepts_status_boolean_or_headcount(self):
        self.assertTrue(core.company_enrichment_ready({"clay_enrichment_status": "SUCCESS"}))
        self.assertTrue(core.company_enrichment_ready({"clay_enrichment_status": "PARTIAL SUCCESS"}))
        self.assertTrue(core.company_enrichment_ready({"enriched_by_clay": "true"}))
        self.assertTrue(core.company_enrichment_ready({"number_of_engineers_clay": "123"}))
        self.assertFalse(core.company_enrichment_ready({}))

    def test_should_request_company_enrichment_only_for_unqueued_unready_tier_1(self):
        self.assertTrue(core.should_request_company_enrichment("tier_1", {}))
        self.assertFalse(core.should_request_company_enrichment("tier_2", {}))
        self.assertFalse(core.should_request_company_enrichment("tier_1", {"company_clay_enrichment_queue": "true"}))
        self.assertFalse(core.should_request_company_enrichment("tier_1", {"number_of_engineers_clay": "42"}))

    def test_build_company_enrichment_properties(self):
        props = core.build_company_enrichment_properties(123)
        self.assertEqual(props["company_clay_enrichment_queue"], "true")
        self.assertEqual(props["ready_for_enrichment"], "true")
        self.assertEqual(props["pqa_enriched_at"], "123")

    def test_extract_apollo_engineering_headcount(self):
        org = {
            "departmental_head_count": {
                "engineering": 4,
                "information_technology": 2,
                "sales": 10,
            }
        }
        self.assertEqual(core.extract_apollo_engineering_headcount(org), 6)

    def test_build_company_apollo_enrichment_properties(self):
        props = core.build_company_apollo_enrichment_properties(
            {
                "departmental_head_count": {
                    "engineering": {"count": 3},
                    "marketing": 8,
                }
            },
            now_ms=123,
        )
        self.assertEqual(props["number_of_engineers_clay"], "3")
        self.assertEqual(props["pqa_enriched_at"], "123")

    def test_build_company_apollo_enrichment_properties_without_headcount(self):
        props = core.build_company_apollo_enrichment_properties(
            {"departmental_head_count": {"sales": 10}},
            now_ms=123,
        )
        self.assertNotIn("number_of_engineers_clay", props)
        self.assertEqual(props["pqa_enriched_at"], "123")

    def test_build_contact_properties_omits_enrichment_flags_by_default(self):
        props = core.build_contact_properties(
            {
                "pql_score": 82.4,
                "pql_champion_rank": 1,
                "pql_is_team_admin": True,
                "pql_ai_credit_usage_30d": 1234,
                "pql_activity_frequency": 12,
                "pql_hit_credit_limit_14d": True,
            },
            now_ms=123,
        )

        self.assertEqual(props["pql_score"], "82.4")
        self.assertEqual(props["pql_champion_rank"], "1")
        self.assertNotIn("available_for_enrichment", props)
        self.assertNotIn("clay_enrichment_queue", props)
        self.assertNotIn("latest_lead_source_detailed", props)

    def test_build_contact_properties_can_trigger_current_enrichment_workflow(self):
        sequence = core.sequence_intent_for_tier("tier_1")
        props = core.build_contact_properties(
            {
                "pql_score": 82.4,
                "pql_champion_rank": 1,
                "pql_is_team_admin": True,
                "pql_ai_credit_usage_30d": 1234,
                "pql_activity_frequency": 12,
                "pql_hit_credit_limit_14d": True,
            },
            now_ms=123,
            request_enrichment=True,
            sequence_intent=sequence,
            run_id="run-1",
        )

        self.assertEqual(props["available_for_enrichment"], "true")
        self.assertEqual(props["clay_enrichment_queue"], "true")
        self.assertEqual(props["plg_sequence_enrollment_requested"], "true")
        self.assertEqual(props["plg_sequence_id"], core.PRODUCT_QUALIFIED_SEQUENCE_ID)
        self.assertEqual(props["plg_sequence_name"], core.PRODUCT_QUALIFIED_SEQUENCE_NAME)
        self.assertEqual(props["plg_sequence_enrollment_mode"], "manual")
        self.assertEqual(props["plg_sequence_requested_at"], "123")
        self.assertEqual(props["plg_sequence_sync_run_id"], "run-1")

    def test_tier_2_has_no_bdr_workflow_or_sequence_intent(self):
        sequence = core.sequence_intent_for_tier("tier_2")
        self.assertFalse(sequence["should_enroll"])
        self.assertFalse(core.should_trigger_bdr_workflow("tier_2"))

        props = core.build_contact_properties(
            {
                "pql_score": 70,
                "pql_champion_rank": 1,
                "pql_is_team_admin": True,
                "pql_ai_credit_usage_30d": 1234,
                "pql_activity_frequency": 12,
                "pql_hit_credit_limit_14d": True,
            },
            now_ms=123,
            request_enrichment=core.should_trigger_bdr_workflow("tier_2"),
            sequence_intent=sequence,
            run_id="run-1",
        )

        self.assertNotIn("available_for_enrichment", props)
        self.assertNotIn("clay_enrichment_queue", props)
        self.assertNotIn("plg_sequence_enrollment_requested", props)

    def test_build_lead_properties_carries_tier_1_sequence_intent(self):
        props = core.build_lead_properties(
            {
                "email_domain": "example.com",
                "company_name": "Example",
                "pqa_score": 91,
            },
            {
                "user_email": "champion@example.com",
                "pql_score": 88,
                "pql_champion_rank": 1,
            },
            company_id="company-1",
            contact_id="contact-1",
            now_ms=123,
            run_id="run-1",
            hubspot_owner_id=core.BDR_OWNER_A,
        )

        self.assertEqual(props["pqa_tier"], "tier_1")
        self.assertEqual(props["pqa_routing_motion"], "manual_bdr")
        self.assertEqual(props["plg_sequence_enrollment_requested"], "true")
        self.assertEqual(props["plg_sequence_id"], core.PRODUCT_QUALIFIED_SEQUENCE_ID)
        self.assertEqual(props["plg_sequence_enrollment_mode"], "manual")
        self.assertEqual(props["plg_enrichment_requested"], "true")
        self.assertEqual(props["plg_route_via_workflow"], "true")
        self.assertEqual(props["plg_account_routing_workflow_id"], core.ACCOUNT_ROUTING_WORKFLOW_ID)
        self.assertEqual(props["plg_contact_routing_workflow_id"], core.CONTACT_ROUTING_WORKFLOW_ID)
        self.assertEqual(props["lead_source_detailed"], "Product Qualified Lead (PQL)")
        self.assertEqual(props["lead_source_simplified"], "Product Qualified")
        self.assertEqual(props["hubspot_owner_id"], core.BDR_OWNER_A)
        self.assertNotIn("latest_lead_source_detailed", props)
        self.assertNotIn("latest_lead_source_simplified", props)

    def test_build_lead_properties_keeps_tier_3_out_of_sequences(self):
        props = core.build_lead_properties(
            {
                "email_domain": "example.com",
                "company_name": "Example",
                "pqa_score": 40,
            },
            {
                "user_email": "champion@example.com",
                "pql_score": 45,
                "pql_champion_rank": 1,
            },
            company_id=None,
            contact_id=None,
            now_ms=123,
            run_id="run-1",
        )

        self.assertEqual(props["pqa_tier"], "tier_3")
        self.assertEqual(props["pqa_routing_motion"], "marketing_touch")
        self.assertEqual(props["plg_sequence_enrollment_requested"], "false")
        self.assertEqual(props["plg_sequence_id"], "")
        self.assertEqual(props["plg_route_via_workflow"], "false")

    def test_choose_lead_champions_adds_top_admin_when_distinct(self):
        champions = [
            {
                "user_email": "power@example.com",
                "pql_champion_rank": 1,
                "pql_is_team_admin": False,
            },
            {
                "user_email": "admin@example.com",
                "pql_champion_rank": 2,
                "pql_is_team_admin": True,
            },
            {
                "user_email": "other-admin@example.com",
                "pql_champion_rank": 3,
                "pql_is_team_admin": True,
            },
        ]

        selected = core.choose_lead_champions(champions)

        self.assertEqual([c["user_email"] for c in selected], ["power@example.com", "admin@example.com"])

    def test_choose_lead_champions_dedupes_primary_admin(self):
        champions = [
            {
                "user_email": "admin@example.com",
                "pql_champion_rank": 1,
                "pql_is_team_admin": True,
            },
            {
                "user_email": "other@example.com",
                "pql_champion_rank": 2,
                "pql_is_team_admin": False,
            },
        ]

        selected = core.choose_lead_champions(champions)

        self.assertEqual([c["user_email"] for c in selected], ["admin@example.com"])

    def test_lead_role_for_admin_and_primary_champions(self):
        primary = {"user_email": "power@example.com", "pql_is_team_admin": False}
        admin = {"user_email": "admin@example.com", "pql_is_team_admin": True}
        primary_admin = {"user_email": "owner@example.com", "pql_is_team_admin": True}

        self.assertEqual(core.lead_role_for_champion(primary, primary=primary), "primary_champion")
        self.assertEqual(core.lead_role_for_champion(admin, primary=primary), "admin_champion")
        self.assertEqual(
            core.lead_role_for_champion(primary_admin, primary=primary_admin),
            "primary_admin_champion",
        )


if __name__ == "__main__":
    unittest.main()
