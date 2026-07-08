import unittest
from datetime import UTC, datetime
from decimal import Decimal

from bdr_agent.stages.company_research.internal_metrics import (
    apply_tier_1_internal_metrics,
    fetch_tier_1_internal_metrics,
    load_tier_1_metrics_query,
    not_found_tier_1_internal_metrics,
    tier_1_internal_metrics_from_row,
)
from bdr_agent.stages.company_research.schemas import build_minimal_company_research_output


class FakeQueryJob:
    def __init__(self, rows) -> None:
        self.rows = rows
        self.max_results = None

    def result(self, max_results=None):
        self.max_results = max_results
        return self.rows[:max_results]


class FakeBigQueryClient:
    def __init__(self, rows) -> None:
        self.rows = rows
        self.query_text = None
        self.job_config = None
        self.query_job = None

    def query(self, query_text, job_config=None):
        self.query_text = query_text
        self.job_config = job_config
        self.query_job = FakeQueryJob(self.rows)
        return self.query_job


def metrics_row(**overrides) -> dict:
    row = {
        "email_domain": "example.com",
        "metrics_as_of": datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
        "is_enterprise_domain": False,
        "is_public_email_domain": False,
        "has_product_usage": True,
        "has_recent_product_usage": True,
        "has_paid_signal": True,
        "data_notes": ["example_note"],
        "known_users_total": 100,
        "non_fraud_users_total": 98,
        "active_users_30d": 42,
        "active_users_90d": 55,
        "signup_users_30d": 4,
        "signup_users_90d": 9,
        "first_signup_date": datetime(2025, 1, 1, tzinfo=UTC),
        "latest_signup_date": datetime(2026, 5, 1, tzinfo=UTC),
        "latest_observed_product_activity_at": datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
        "avg_wau_last_4_weeks": Decimal("12.5"),
        "peak_wau_last_12_weeks": 20,
        "active_weeks_last_12_weeks": 8,
        "latest_active_week": datetime(2026, 5, 10, tzinfo=UTC),
        "ai_feature_users_30d": 12,
        "ai_feature_users_90d": 18,
        "ai_requests_30d": 120,
        "ai_requests_90d": 240,
        "usage_units_30d": Decimal("1000"),
        "usage_units_90d": Decimal("2000"),
        "usage_units_per_ai_user_30d": Decimal("83.33"),
        "ai_prompts_30d": 80,
        "saved_objects_30d": 3,
        "limit_hits_14d": 5,
        "users_hitting_limits_14d": 2,
        "reload_dollars_90d": Decimal("199.99"),
        "reload_count_90d": 1,
        "users_upgraded_90d": 1,
        "paid_users_any": 5,
        "users_on_active_subscription": 4,
        "teams_total": 2,
        "active_subscription_teams": 1,
        "active_standard_teams": 1,
        "paid_plan_seats": 10,
        "active_team_members": 6,
        "team_members_using_ai": 3,
        "active_automations": 2,
        "active_documents": 1,
        "active_team_weeks_last_month": Decimal("4"),
        "plan_types": ["team"],
        "new_domain_members_30d": 7,
        "team_invites_30d": 8,
    }
    row.update(overrides)
    return row


class InternalMetricsTest(unittest.TestCase):
    def test_load_tier_1_metrics_query_uses_interim_raw_domain_sources(self) -> None:
        query = load_tier_1_metrics_query()

        self.assertIn("@resolved_company_domain", query)
        self.assertIn("internal product-usage evidence query", query)
        self.assertIn("analytics.companies", query)
        self.assertIn("example-gcp-project.analytics.users", query)
        self.assertIn("example-gcp-project.analytics.weekly_active_users", query)
        self.assertIn("example-gcp-project.analytics.usage_events", query)
        self.assertIn("example-gcp-project.analytics.crm_deals", query)
        self.assertIn("returns zero rows when there is no internal data", query)
        self.assertIn("has_recent_product_usage", query)
        self.assertIn("has_paid_signal", query)
        self.assertNotIn("plg_is_eligible", query.lower())
        self.assertNotIn("plg_ineligibility_reason", query.lower())
        self.assertNotIn("where false", query.lower())
        self.assertNotIn("pql_score", query.lower())
        self.assertNotIn("percent_rank", query.lower())

    def test_tier_1_internal_metrics_from_row_maps_raw_metrics(self) -> None:
        result = tier_1_internal_metrics_from_row(metrics_row())

        self.assertEqual(result.status, "found")
        self.assertEqual(result.email_domain, "example.com")
        self.assertEqual(result.metrics_as_of, "2026-05-18T12:00:00+00:00")
        self.assertFalse(result.is_enterprise_domain)
        self.assertFalse(result.is_public_email_domain)
        self.assertTrue(result.has_product_usage)
        self.assertTrue(result.has_recent_product_usage)
        self.assertTrue(result.has_paid_signal)
        self.assertEqual(result.data_notes, ["example_note"])
        self.assertEqual(result.metrics["known_users_total"], 100)
        self.assertEqual(result.metrics["active_users_30d"], 42)
        self.assertEqual(result.metrics["avg_wau_last_4_weeks"], 12.5)
        self.assertEqual(result.metrics["usage_units_30d"], 1000)

    def test_fetch_tier_1_internal_metrics_returns_not_found_for_no_row(self) -> None:
        client = FakeBigQueryClient([])

        result = fetch_tier_1_internal_metrics(
            resolved_company_domain="example.com",
            client=client,
            query="select @resolved_company_domain where false",
        )

        self.assertEqual(result, not_found_tier_1_internal_metrics("example.com"))
        self.assertEqual(client.query_text, "select @resolved_company_domain where false")
        self.assertEqual(client.query_job.max_results, 1)

    def test_fetch_tier_1_internal_metrics_returns_found_result(self) -> None:
        result = fetch_tier_1_internal_metrics(
            resolved_company_domain="example.com",
            client=FakeBigQueryClient([metrics_row()]),
            query="select 1",
        )

        self.assertEqual(result.status, "found")
        self.assertEqual(result.metrics["new_domain_members_30d"], 7)

    def test_apply_tier_1_internal_metrics_updates_output_block(self) -> None:
        output = build_minimal_company_research_output(
            lead_id="lead_123",
            trigger_source="inbound_oz_campaign_pdf_download",
            hydration_status="hydrated",
            resolved_company_domain="example.com",
            resolved_company_domain_source="company.email_domain",
        )
        result = tier_1_internal_metrics_from_row(metrics_row())

        apply_tier_1_internal_metrics(output, result)

        self.assertEqual(output["tier_1_internal_metrics"]["status"], "found")
        self.assertEqual(output["tier_1_internal_metrics"]["email_domain"], "example.com")
        self.assertEqual(output["tier_1_internal_metrics"]["metrics"]["limit_hits_14d"], 5)


if __name__ == "__main__":
    unittest.main()
