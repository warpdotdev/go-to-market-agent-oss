import unittest
from datetime import UTC, datetime

from bdr_agent.stages.company_research.config import (
    HYDRATION_HYDRATED,
    HYDRATION_MISSING_REQUIRED_COMPANY_CONTEXT,
    HYDRATION_NOT_READY,
)
from bdr_agent.stages.company_research.hydration import (
    build_hydration_result,
    build_hydration_result_from_webhook_payload,
    load_hydration_query,
    merge_hydration_results,
    to_json_safe,
)


def hydrated_row(**overrides) -> dict:
    row = {
        "lead_id": "lead_123",
        "lead_created_at": "2026-05-18T00:00:00Z",
        "hubspot_owner_id": "100000000010",
        "contact_id": "contact_123",
        "contact_email": "person@example.com",
        "contact_first_name": "Ada",
        "contact_last_name": "Lovelace",
        "contact_job_title": "CTO",
        "contact_associated_company_id": "company_123",
        "company_id": "company_123",
        "company_name": "Example",
        "company_email_domain": "Example.com",
        "company_alternative_email_domain": None,
        "company_website": "https://www.example.com",
        "company_industry": "Software",
        "company_num_employees": 100,
        "company_icp_tier": "tier_1",
    }
    row.update(overrides)
    return row


def webhook_payload(**overrides) -> dict:
    payload = {
        "lead_id": "lead_123",
        "lead_created_at": "2026-05-18T00:00:00Z",
        "lead_owner_id": "100000000010",
        "contact_id": "contact_123",
        "contact_email": "person@example.com",
        "contact_first_name": "Ada",
        "contact_last_name": "Lovelace",
        "contact_job_title": "CTO",
        "company_id": "company_123",
        "company_name": "Example",
        "company_domain": "Example.com",
        "company_alternative_domain": None,
        "company_website": "https://www.example.com",
        "company_industry": "Software",
        "company_num_employees": 100,
        "company_icp_tier": "tier_1",
    }
    payload.update(overrides)
    return payload


class HydrationTest(unittest.TestCase):
    def test_load_hydration_query_reads_reference_sql(self) -> None:
        query = load_hydration_query()

        self.assertIn("from `example-gcp-project.analytics.crm_leads`", query)
        self.assertIn("@lead_id", query)
        self.assertIn("lead.hubspot_owner_id", query)

    def test_to_json_safe_converts_datetime_to_isoformat(self) -> None:
        value = datetime(2026, 5, 18, 4, 0, tzinfo=UTC)

        self.assertEqual(to_json_safe(value), "2026-05-18T04:00:00+00:00")

    def test_no_row_is_not_ready_missing_lead(self) -> None:
        result = build_hydration_result(None)

        self.assertEqual(result.hydration_status, HYDRATION_NOT_READY)
        self.assertEqual(result.missing_fields, ["lead"])

    def test_missing_contact_is_not_ready(self) -> None:
        result = build_hydration_result(hydrated_row(contact_id=None))

        self.assertEqual(result.hydration_status, HYDRATION_NOT_READY)
        self.assertEqual(result.missing_fields, ["contact"])
        self.assertEqual(result.lead["lead_id"], "lead_123")

    def test_missing_company_is_not_ready(self) -> None:
        result = build_hydration_result(hydrated_row(company_id=None))

        self.assertEqual(result.hydration_status, HYDRATION_NOT_READY)
        self.assertEqual(result.missing_fields, ["company"])
        self.assertEqual(result.contact["contact_id"], "contact_123")

    def test_missing_company_domain_or_website_is_missing_required_context(self) -> None:
        result = build_hydration_result(
            hydrated_row(
                company_email_domain=None,
                company_alternative_email_domain=None,
                company_website=None,
            )
        )

        self.assertEqual(result.hydration_status, HYDRATION_MISSING_REQUIRED_COMPANY_CONTEXT)
        self.assertEqual(result.missing_fields, ["company_domain_or_website"])

    def test_company_backed_domain_is_hydrated(self) -> None:
        result = build_hydration_result(hydrated_row())

        self.assertEqual(result.hydration_status, HYDRATION_HYDRATED)
        self.assertEqual(result.missing_fields, [])
        self.assertEqual(result.resolved_company_domain, "example.com")
        self.assertEqual(result.resolved_company_domain_source, "company.email_domain")
        self.assertEqual(result.lead["hubspot_owner_id"], "100000000010")
        self.assertEqual(result.company["company_id"], "company_123")

    def test_webhook_payload_is_hydrated_without_bigquery_row(self) -> None:
        result = build_hydration_result_from_webhook_payload(webhook_payload())

        self.assertEqual(result.hydration_status, HYDRATION_HYDRATED)
        self.assertEqual(result.missing_fields, [])
        self.assertEqual(result.resolved_company_domain, "example.com")
        self.assertEqual(result.resolved_company_domain_source, "webhook.company_domain")
        self.assertEqual(result.lead["lead_id"], "lead_123")
        self.assertEqual(result.contact["contact_id"], "contact_123")
        self.assertEqual(result.company["company_id"], "company_123")

    def test_webhook_payload_uses_company_website_domain_fallback(self) -> None:
        result = build_hydration_result_from_webhook_payload(
            webhook_payload(company_domain=None, company_alternative_domain=None)
        )

        self.assertEqual(result.hydration_status, HYDRATION_HYDRATED)
        self.assertEqual(result.resolved_company_domain, "example.com")
        self.assertEqual(result.resolved_company_domain_source, "webhook.company_website")

    def test_webhook_payload_missing_contact_or_company_is_not_ready(self) -> None:
        result = build_hydration_result_from_webhook_payload(
            webhook_payload(contact_id=None, company_id=None)
        )

        self.assertEqual(result.hydration_status, HYDRATION_NOT_READY)
        self.assertEqual(result.missing_fields, ["contact", "company"])

    def test_webhook_payload_missing_domain_or_website_is_missing_required_context(self) -> None:
        result = build_hydration_result_from_webhook_payload(
            webhook_payload(
                company_domain=None,
                company_alternative_domain=None,
                company_website=None,
            )
        )

        self.assertEqual(result.hydration_status, HYDRATION_MISSING_REQUIRED_COMPANY_CONTEXT)
        self.assertEqual(result.missing_fields, ["company_domain_or_website"])

    def test_merge_uses_webhook_values_before_bigquery_fallback(self) -> None:
        webhook_result = build_hydration_result_from_webhook_payload(
            webhook_payload(
                contact_email="fresh@example.com",
                company_domain="fresh-example.com",
                company_website="",
            )
        )
        fallback_result = build_hydration_result(
            hydrated_row(
                contact_email="stale@example.com",
                company_email_domain="stale-example.com",
                company_website="https://www.stale-example.com",
            )
        )

        result = merge_hydration_results(primary=webhook_result, fallback=fallback_result)

        self.assertEqual(result.hydration_status, HYDRATION_HYDRATED)
        self.assertEqual(result.contact["email"], "fresh@example.com")
        self.assertEqual(result.company["email_domain"], "fresh-example.com")
        self.assertEqual(result.resolved_company_domain, "fresh-example.com")
        self.assertEqual(result.resolved_company_domain_source, "webhook.company_domain")

    def test_merge_fills_blank_webhook_values_from_bigquery_fallback(self) -> None:
        webhook_result = build_hydration_result_from_webhook_payload(
            webhook_payload(
                contact_id="",
                company_id="",
                company_domain="",
                company_website="",
            )
        )
        fallback_result = build_hydration_result(hydrated_row())

        result = merge_hydration_results(primary=webhook_result, fallback=fallback_result)

        self.assertEqual(result.hydration_status, HYDRATION_HYDRATED)
        self.assertEqual(result.missing_fields, [])
        self.assertEqual(result.contact["contact_id"], "contact_123")
        self.assertEqual(result.company["company_id"], "company_123")
        self.assertEqual(result.resolved_company_domain, "example.com")
        self.assertEqual(result.resolved_company_domain_source, "company.email_domain")


if __name__ == "__main__":
    unittest.main()
