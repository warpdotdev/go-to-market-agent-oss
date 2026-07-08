import io
import json
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from bdr_agent.stages.company_research.config import (
    COMPANY_RESEARCH_OUTPUTS_TABLE,
    GCS_ARTIFACT_BUCKET,
    GCS_ARTIFACT_PREFIX,
    POSITIONING_TAXONOMY_VERSION,
    TIER_2_STRATEGY,
    bigquery_table_id,
)
from bdr_agent.stages.company_research import cli as company_research_cli
from bdr_agent.stages.company_research.run import run_company_research


class FakeQueryJob:
    def __init__(self, rows) -> None:
        self.rows = rows

    def result(self, max_results=None):
        return self.rows[:max_results]


class FakeBigQueryClient:
    def __init__(self, rows) -> None:
        self.rows = rows
        self.called = False

    def query(self, query_text, job_config=None):
        self.called = True
        return FakeQueryJob(self.rows)

class FakePersistenceBigQueryClient:
    def __init__(self) -> None:
        self.ensured = []
        self.inserted = []

    def ensure_table(self, *, table_id, definition):
        self.ensured.append((table_id, definition))

    def insert_rows_json(self, table_id, rows):
        self.inserted.append((table_id, rows))
        return []


class FakeBlob:
    def __init__(self, name) -> None:
        self.name = name
        self.uploads = []

    def upload_from_string(self, payload, content_type=None):
        self.uploads.append({"payload": payload, "content_type": content_type})


class FakeBucket:
    def __init__(self, name) -> None:
        self.name = name
        self.blobs = {}

    def blob(self, name):
        blob = FakeBlob(name)
        self.blobs[name] = blob
        return blob


class FakeStorageClient:
    def __init__(self) -> None:
        self.buckets = {}

    def bucket(self, name):
        bucket = FakeBucket(name)
        self.buckets[name] = bucket
        return bucket

class FakeStageCompletionResponse:
    status_code = 202

    def raise_for_status(self):
        return None


class FakeStageCompletionClient:
    def __init__(self) -> None:
        self.posts = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.posts.append(
            {
                "url": url,
                "headers": headers,
                "json": json,
                "timeout": timeout,
            }
        )
        return FakeStageCompletionResponse()


class FailingBigQueryClient:
    def query(self, query_text, job_config=None):
        raise AssertionError("BigQuery lookup should not run")


class FakeExaResponse:
    def __init__(self, payload) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self.payload


class FakeExaClient:
    def __init__(self, payload) -> None:
        self.payload = payload
        self.calls = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return FakeExaResponse(self.payload)


class FailingExaClient:
    def post(self, url, headers=None, json=None, timeout=None):
        raise AssertionError("Exa research should not run")


def hydrated_row(**overrides) -> dict:
    row = {
        "lead_id": "123",
        "lead_created_at": "2026-05-18T00:00:00Z",
        "contact_id": "contact_123",
        "contact_email": "person@example.com",
        "contact_first_name": "Ada",
        "contact_last_name": "Lovelace",
        "contact_job_title": "CTO",
        "contact_associated_company_id": "company_123",
        "company_id": "company_123",
        "company_name": "Example",
        "company_email_domain": "example.com",
        "company_alternative_email_domain": None,
        "company_website": None,
        "company_industry": "Software",
        "company_num_employees": 100,
        "company_icp_tier": "tier_1",
    }
    row.update(overrides)
    return row


def tier_1_metrics_row(**overrides) -> dict:
    row = {
        "email_domain": "example.com",
        "metrics_as_of": datetime(2026, 5, 18, tzinfo=UTC),
        "is_enterprise_domain": False,
        "is_public_email_domain": False,
        "has_product_usage": True,
        "has_recent_product_usage": True,
        "has_paid_signal": False,
        "data_notes": [],
        "known_users_total": 50,
        "active_users_30d": 42,
        "avg_wau_last_4_weeks": 12.5,
    }
    row.update(overrides)
    return row


def prior_tier_2_row(**overrides) -> dict:
    row = {
        "resolved_company_domain": "example.com",
        "tier_2_public_research_json": {
            "status": "found",
            "strategy": TIER_2_STRATEGY,
            "positioning_taxonomy_version": POSITIONING_TAXONOMY_VERSION,
            "findings": [
                {
                    "finding_id": "tier2_001",
                    "fact": "Reusable prior Tier 2 fact.",
                    "source_url": "https://example.com/blog/prior",
                }
            ],
            "source_attempts": [{"query": "site:example.com prior"}],
            "external_service_cost_dollars": 0.014,
        },
        "created_at": datetime.now(UTC) - timedelta(days=1),
        "run_id": "prior_run",
        "output_id": "prior_output",
    }
    row.update(overrides)
    return row


class RunTest(unittest.TestCase):
    def test_no_webhook_payload_uses_bigquery_fallback(self) -> None:
        with patch(
            "bdr_agent.stages.company_research.run.fetch_hydration_row",
            return_value=hydrated_row(),
        ) as fetch_hydration:
            result = run_company_research(
                lead_id="123",
                trigger_source="inbound_oz_campaign_pdf_download",
                source_system="hubspot_workflow",
                hubspot_workflow_id="0000000000",
                dry_run=True,
                skip_tier_1_internal_metrics=True,
                skip_tier_2_reuse_lookup=True,
                skip_tier_2_public_research=True,
            )

        self.assertEqual(result["status"], "hydration_complete")
        self.assertEqual(result["context_source"], "webhook_payload_with_bigquery_fallback")
        self.assertEqual(result["output"]["contact"]["contact_id"], "contact_123")
        self.assertEqual(result["output"]["company"]["company_id"], "company_123")
        fetch_hydration.assert_called_once_with(lead_id="123")

    def test_missing_webhook_and_missing_bigquery_row_returns_not_ready(self) -> None:
        with patch(
            "bdr_agent.stages.company_research.run.fetch_hydration_row",
            return_value=None,
        ):
            result = run_company_research(
                lead_id="123",
                trigger_source="inbound_oz_campaign_pdf_download",
                source_system="hubspot_workflow",
                hubspot_workflow_id="0000000000",
                dry_run=True,
                tier_2_reuse_client=FailingBigQueryClient(),
            )

        self.assertEqual(result["status"], "not_ready")
        self.assertEqual(result["context_source"], "webhook_payload_bigquery_fallback_missing")
        self.assertEqual(result["output"]["lead"]["lead_id"], "123")
        self.assertEqual(
            result["output"]["hydration"]["missing_fields"],
            ["contact", "company"],
        )

    def test_cli_returns_zero_for_research_complete_status(self) -> None:
        stdout = io.StringIO()
        with (
            patch.object(
                company_research_cli,
                "run_company_research",
                return_value={"status": "research_complete"},
            ),
            redirect_stdout(stdout),
        ):
            exit_code = company_research_cli.main(
                [
                    "--lead-id",
                    "123",
                    "--trigger-source",
                    "inbound_oz_campaign_pdf_download",
                    "--source-system",
                    "hubspot_workflow",
                    "--hubspot-workflow-id",
                    "0000000000",
                    "--dry-run",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout.getvalue())["status"], "research_complete")

    def test_webhook_payload_returns_hydration_complete_without_bigquery_hydration(self) -> None:
        with patch("bdr_agent.stages.company_research.run.fetch_hydration_row") as fetch_hydration:
            result = run_company_research(
                lead_id="123",
                trigger_source="inbound_oz_campaign_pdf_download",
                source_system="hubspot_workflow",
                hubspot_workflow_id="0000000000",
                dry_run=True,
                webhook_payload={
                    "lead_id": "123",
                    "contact_id": "contact_123",
                    "contact_email": "person@example.com",
                    "contact_first_name": "Ada",
                    "contact_last_name": "Lovelace",
                    "contact_job_title": "CTO",
                    "company_id": "company_123",
                    "company_name": "Example",
                    "company_domain": "Example.com",
                },
                skip_tier_1_internal_metrics=True,
                skip_tier_2_reuse_lookup=True,
                skip_tier_2_public_research=True,
            )

        self.assertEqual(result["status"], "hydration_complete")
        self.assertEqual(result["context_source"], "webhook_payload")
        self.assertEqual(result["output"]["hydration"]["resolved_company_domain"], "example.com")
        self.assertEqual(
            result["output"]["hydration"]["resolved_company_domain_source"],
            "webhook.company_domain",
        )
        fetch_hydration.assert_not_called()

    def test_partial_webhook_payload_uses_bigquery_only_for_missing_fields(self) -> None:
        with patch(
            "bdr_agent.stages.company_research.run.fetch_hydration_row",
            return_value=hydrated_row(
                contact_email="stale@example.com",
                company_email_domain="stale-example.com",
            ),
        ):
            result = run_company_research(
                lead_id="123",
                trigger_source="inbound_oz_campaign_pdf_download",
                source_system="hubspot_workflow",
                hubspot_workflow_id="0000000000",
                dry_run=True,
                webhook_payload={
                    "lead_id": "123",
                    "contact_email": "fresh@example.com",
                    "company_domain": "fresh-example.com",
                },
                skip_tier_1_internal_metrics=True,
                skip_tier_2_reuse_lookup=True,
                skip_tier_2_public_research=True,
            )

        self.assertEqual(result["status"], "hydration_complete")
        self.assertEqual(result["context_source"], "webhook_payload_with_bigquery_fallback")
        self.assertEqual(result["output"]["contact"]["contact_id"], "contact_123")
        self.assertEqual(result["output"]["contact"]["email"], "fresh@example.com")
        self.assertEqual(result["output"]["company"]["company_id"], "company_123")
        self.assertEqual(result["output"]["hydration"]["resolved_company_domain"], "fresh-example.com")
        self.assertEqual(
            result["output"]["hydration"]["resolved_company_domain_source"],
            "webhook.company_domain",
        )

    def test_injected_hydrated_row_returns_hydration_complete(self) -> None:
        result = run_company_research(
            lead_id="123",
            trigger_source="inbound_oz_campaign_pdf_download",
            source_system="hubspot_workflow",
            hubspot_workflow_id="0000000000",
            dry_run=True,
            hydration_row=hydrated_row(),
            skip_tier_1_internal_metrics=True,
            skip_tier_2_reuse_lookup=True,
            skip_tier_2_public_research=True,
        )

        self.assertEqual(result["status"], "hydration_complete")
        self.assertFalse(result["skip_bigquery"])
        self.assertEqual(result["context_source"], "injected_hydration_row")
        self.assertIsNone(result["failure_reason"])
        self.assertEqual(result["output"]["hydration"]["resolved_company_domain"], "example.com")
        self.assertEqual(result["output"]["storage"]["status"], "dry_run_not_persisted")
        self.assertIsNotNone(result["started_at"])
        self.assertIsNotNone(result["completed_at"])
        self.assertIsNotNone(result["duration_seconds"])
        self.assertGreaterEqual(result["duration_seconds"], 0)

    def test_dry_run_marks_storage_not_persisted_without_writes(self) -> None:
        persistence_client = FakePersistenceBigQueryClient()
        storage_client = FakeStorageClient()
        stage_completion_client = FakeStageCompletionClient()

        result = run_company_research(
            lead_id="123",
            trigger_source="inbound_oz_campaign_pdf_download",
            source_system="hubspot_workflow",
            hubspot_workflow_id="0000000000",
            dry_run=True,
            hydration_row=hydrated_row(),
            skip_tier_1_internal_metrics=True,
            skip_tier_2_reuse_lookup=True,
            skip_tier_2_public_research=True,
            persistence_bigquery_client=persistence_client,
            persistence_storage_client=storage_client,
            stage_completion_client=stage_completion_client,
        )

        self.assertEqual(result["output"]["storage"]["status"], "dry_run_not_persisted")
        self.assertEqual(result["stage_completion"]["status"], "skipped")
        self.assertEqual(result["stage_completion"]["reason"], "dry_run")
        self.assertEqual(persistence_client.ensured, [])
        self.assertEqual(persistence_client.inserted, [])
        self.assertEqual(storage_client.buckets, {})
        self.assertEqual(stage_completion_client.posts, [])

    def test_persist_writes_artifact_and_bigquery_rows_only_when_explicit(self) -> None:
        persistence_client = FakePersistenceBigQueryClient()
        storage_client = FakeStorageClient()

        result = run_company_research(
            lead_id="123",
            trigger_source="inbound_oz_campaign_pdf_download",
            source_system="hubspot_workflow",
            hubspot_workflow_id="0000000000",
            dry_run=False,
            hydration_row=hydrated_row(),
            tier_1_metrics_client=FakeBigQueryClient([]),
            skip_tier_2_reuse_lookup=True,
            skip_tier_2_public_research=True,
            persist=True,
            persistence_bigquery_client=persistence_client,
            persistence_storage_client=storage_client,
        )

        storage = result["output"]["storage"]
        self.assertEqual(storage["status"], "persisted")
        self.assertEqual(storage["bigquery_table"], bigquery_table_id(COMPANY_RESEARCH_OUTPUTS_TABLE))
        self.assertTrue(storage["gcs_uri"].startswith(f"gs://{GCS_ARTIFACT_BUCKET}/{GCS_ARTIFACT_PREFIX}/"))
        self.assertEqual(len(persistence_client.inserted), 3)
        run_row = persistence_client.inserted[0][1][0]
        self.assertEqual(run_row["started_at"], result["started_at"])
        self.assertEqual(run_row["completed_at"], result["completed_at"])
        self.assertEqual(run_row["duration_seconds"], result["duration_seconds"])
        self.assertIn(GCS_ARTIFACT_BUCKET, storage_client.buckets)
        self.assertEqual(result["stage_completion"]["status"], "skipped")
        self.assertEqual(result["stage_completion"]["reason"], "webhook_url_not_configured")

    def test_persist_sends_stage_completion_when_configured(self) -> None:
        persistence_client = FakePersistenceBigQueryClient()
        storage_client = FakeStorageClient()
        stage_completion_client = FakeStageCompletionClient()

        result = run_company_research(
            lead_id="123",
            trigger_source="inbound_oz_campaign_pdf_download",
            source_system="hubspot_workflow",
            hubspot_workflow_id="0000000000",
            dry_run=False,
            hydration_row=hydrated_row(),
            tier_1_metrics_client=FakeBigQueryClient([]),
            skip_tier_2_reuse_lookup=True,
            skip_tier_2_public_research=True,
            persist=True,
            persistence_bigquery_client=persistence_client,
            persistence_storage_client=storage_client,
            stage_completion_webhook_url="https://budserver.example/webhooks/bdr-agent-stage-completion",
            stage_completion_webhook_secret="secret",
            stage_completion_client=stage_completion_client,
        )

        self.assertEqual(result["status"], "research_complete")
        self.assertEqual(result["stage_completion"]["status"], "sent")
        self.assertEqual(result["stage_completion"]["http_status"], 202)
        self.assertEqual(len(stage_completion_client.posts), 1)
        payload = stage_completion_client.posts[0]["json"]
        self.assertEqual(payload["workflow"], "bdr_agent")
        self.assertEqual(payload["source_stage"], "company_research")
        self.assertEqual(payload["next_stage"], "lead_brief")
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(
            payload["idempotency_key"],
            f"company_research:{result['run_id']}:{result['output_id']}:lead_brief",
        )

    def test_persisted_tier_2_error_does_not_send_stage_completion(self) -> None:
        persistence_client = FakePersistenceBigQueryClient()
        storage_client = FakeStorageClient()
        stage_completion_client = FakeStageCompletionClient()

        with patch.dict("os.environ", {}, clear=True):
            result = run_company_research(
                lead_id="123",
                trigger_source="inbound_oz_campaign_pdf_download",
                source_system="hubspot_workflow",
                hubspot_workflow_id="0000000000",
                dry_run=False,
                hydration_row=hydrated_row(),
                tier_1_metrics_client=FakeBigQueryClient([]),
                tier_2_reuse_client=FakeBigQueryClient([]),
                persist=True,
                persistence_bigquery_client=persistence_client,
                persistence_storage_client=storage_client,
                stage_completion_webhook_url="https://budserver.example/webhooks/bdr-agent-stage-completion",
                stage_completion_webhook_secret="secret",
                stage_completion_client=stage_completion_client,
            )

        self.assertEqual(result["status"], "tier_2_error")
        self.assertEqual(result["stage_completion"]["status"], "skipped")
        self.assertEqual(result["stage_completion"]["reason"], "status_tier_2_error")
        self.assertEqual(stage_completion_client.posts, [])
        self.assertEqual(len(persistence_client.inserted), 3)

    def test_dry_run_and_persist_are_mutually_exclusive(self) -> None:
        with self.assertRaises(ValueError):
            run_company_research(
                lead_id="123",
                trigger_source="inbound_oz_campaign_pdf_download",
                source_system="hubspot_workflow",
                hubspot_workflow_id="0000000000",
                dry_run=True,
                hydration_row=hydrated_row(),
                persist=True,
            )

    def test_injected_hydrated_row_marks_reusable_tier_2_prior_output(self) -> None:
        result = run_company_research(
            lead_id="123",
            trigger_source="inbound_oz_campaign_pdf_download",
            source_system="hubspot_workflow",
            hubspot_workflow_id="0000000000",
            dry_run=True,
            hydration_row=hydrated_row(),
            tier_1_metrics_client=FakeBigQueryClient([]),
            tier_2_reuse_client=FakeBigQueryClient([prior_tier_2_row()]),
            tier_2_public_research_client=FailingExaClient(),
        )

        self.assertEqual(result["status"], "research_complete")
        self.assertEqual(result["output"]["reuse"]["reuse_status"], "partial_reuse")
        self.assertEqual(
            result["output"]["reuse"]["reused_tiers"],
            ["tier_2_public_company_research"],
        )
        self.assertEqual(result["output"]["reuse"]["reused_from_run_id"], "prior_run")
        self.assertEqual(result["output"]["reuse"]["reused_from_output_id"], "prior_output")
        tier_2 = result["output"]["tier_2_public_company_research"]
        self.assertEqual(result["status"], "research_complete")
        self.assertEqual(tier_2["status"], "found")
        self.assertEqual(tier_2["reuse_status"], "reused")
        self.assertEqual(tier_2["findings"][0]["fact"], "Reusable prior Tier 2 fact.")
        self.assertEqual(tier_2["source_attempts"], [{"query": "site:example.com prior"}])
        self.assertEqual(tier_2["external_service_cost_dollars"], 0.014)
        self.assertEqual(tier_2["incremental_external_service_cost_dollars"], 0)
        self.assertEqual(tier_2["reused_from_run_id"], "prior_run")
        self.assertEqual(tier_2["reused_from_output_id"], "prior_output")

    def test_injected_hydrated_row_populates_tier_1_internal_metrics(self) -> None:
        result = run_company_research(
            lead_id="123",
            trigger_source="inbound_oz_campaign_pdf_download",
            source_system="hubspot_workflow",
            hubspot_workflow_id="0000000000",
            dry_run=True,
            hydration_row=hydrated_row(),
            tier_1_metrics_client=FakeBigQueryClient([tier_1_metrics_row()]),
            skip_tier_2_reuse_lookup=True,
            skip_tier_2_public_research=True,
        )

        self.assertEqual(result["status"], "research_complete")
        self.assertEqual(result["output"]["tier_1_internal_metrics"]["status"], "found")
        self.assertEqual(result["output"]["tier_1_internal_metrics"]["email_domain"], "example.com")
        self.assertEqual(result["output"]["tier_1_internal_metrics"]["metrics"]["active_users_30d"], 42)

    def test_injected_hydrated_row_marks_tier_1_not_found(self) -> None:
        result = run_company_research(
            lead_id="123",
            trigger_source="inbound_oz_campaign_pdf_download",
            source_system="hubspot_workflow",
            hubspot_workflow_id="0000000000",
            dry_run=True,
            hydration_row=hydrated_row(),
            tier_1_metrics_client=FakeBigQueryClient([]),
            skip_tier_2_reuse_lookup=True,
            skip_tier_2_public_research=True,
        )

        self.assertEqual(result["status"], "research_complete")
        self.assertEqual(result["output"]["tier_1_internal_metrics"]["status"], "not_found")
        self.assertIsNone(result["output"]["tier_1_internal_metrics"]["metrics"])

    def test_injected_hydrated_row_records_tier_1_error_without_failing_run(self) -> None:
        result = run_company_research(
            lead_id="123",
            trigger_source="inbound_oz_campaign_pdf_download",
            source_system="hubspot_workflow",
            hubspot_workflow_id="0000000000",
            dry_run=True,
            hydration_row=hydrated_row(),
            tier_1_metrics_client=FailingBigQueryClient(),
            skip_tier_2_reuse_lookup=True,
            skip_tier_2_public_research=True,
        )

        self.assertEqual(result["status"], "tier_1_error")
        self.assertEqual(result["output"]["tier_1_internal_metrics"]["status"], "error")
        self.assertIn("BigQuery lookup should not run", result["output"]["tier_1_internal_metrics"]["error"])
        self.assertIn("BigQuery lookup should not run", result["failure_reason"])

    def test_injected_no_row_returns_not_ready(self) -> None:
        result = run_company_research(
            lead_id="123",
            trigger_source="inbound_oz_campaign_pdf_download",
            source_system="hubspot_workflow",
            hubspot_workflow_id="0000000000",
            dry_run=True,
            hydration_row=None,
            tier_2_reuse_client=FailingBigQueryClient(),
        )

        self.assertEqual(result["status"], "not_ready")
        self.assertEqual(
            result["output"]["hydration"]["missing_fields"],
            ["lead"],
        )

    def test_injected_hydrated_row_runs_fresh_tier_2_when_no_reuse_exists(self) -> None:
        fake_exa_client = FakeExaClient(
            {
                "requestId": "exa_request_123",
                "costDollars": {"search": 0.007},
                "results": [
                    {
                        "url": "https://www.example.com/blog/ai-agents",
                        "title": "AI agents at Example",
                        "publishedDate": "2026-05-01T00:00:00Z",
                        "highlights": ["Example is building AI agents for engineering productivity."],
                        "highlightScores": [0.9],
                    }
                ],
            }
        )

        with patch.dict("os.environ", {"BDR_AGENT_EXA_API_KEY": "fake-test-key"}, clear=False):
            result = run_company_research(
                lead_id="123",
                trigger_source="inbound_oz_campaign_pdf_download",
                source_system="hubspot_workflow",
                hubspot_workflow_id="0000000000",
                dry_run=True,
                hydration_row=hydrated_row(),
                tier_1_metrics_client=FakeBigQueryClient([]),
                tier_2_reuse_client=FakeBigQueryClient([]),
                tier_2_public_research_client=fake_exa_client,
            )

        tier_2 = result["output"]["tier_2_public_company_research"]
        self.assertEqual(result["status"], "research_complete")
        self.assertEqual(tier_2["status"], "found")
        self.assertEqual(tier_2["reuse_status"], "fresh")
        self.assertEqual(tier_2["external_service_cost_dollars"], 0.014)
        self.assertEqual(len(tier_2["source_attempts"]), 2)
        self.assertEqual(tier_2["source_attempts"][0]["request_id"], "exa_request_123")
        self.assertEqual(tier_2["findings"][0]["source_url"], "https://www.example.com/blog/ai-agents")
        self.assertEqual(fake_exa_client.calls[0]["json"]["contents"], {"highlights": True})
        self.assertEqual(fake_exa_client.calls[0]["json"]["includeDomains"], ["example.com"])

    def test_injected_hydrated_row_records_tier_2_error_as_run_status(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            result = run_company_research(
                lead_id="123",
                trigger_source="inbound_oz_campaign_pdf_download",
                source_system="hubspot_workflow",
                hubspot_workflow_id="0000000000",
                dry_run=True,
                hydration_row=hydrated_row(),
                tier_1_metrics_client=FakeBigQueryClient([]),
                tier_2_reuse_client=FakeBigQueryClient([]),
            )

        self.assertEqual(result["status"], "tier_2_error")
        self.assertEqual(result["output"]["tier_2_public_company_research"]["status"], "error")
        self.assertIn("BDR_AGENT_EXA_API_KEY", result["failure_reason"])

    def test_stale_prior_tier_2_output_falls_back_to_fresh_exa(self) -> None:
        fake_exa_client = FakeExaClient(
            {
                "requestId": "fresh_exa_request",
                "costDollars": 0.001,
                "results": [
                    {
                        "url": "https://example.com/blog/fresh",
                        "title": "Fresh AI agents",
                        "highlights": ["Fresh Exa fact about AI agents."],
                    }
                ],
            }
        )

        with patch.dict("os.environ", {"BDR_AGENT_EXA_API_KEY": "fake-test-key"}, clear=False):
            result = run_company_research(
                lead_id="123",
                trigger_source="inbound_oz_campaign_pdf_download",
                source_system="hubspot_workflow",
                hubspot_workflow_id="0000000000",
                dry_run=True,
                hydration_row=hydrated_row(),
                tier_1_metrics_client=FakeBigQueryClient([]),
                tier_2_reuse_client=FakeBigQueryClient(
                    [prior_tier_2_row(created_at=datetime(2026, 4, 1, tzinfo=UTC))]
                ),
                tier_2_public_research_client=fake_exa_client,
            )

        self.assertEqual(result["output"]["reuse"]["reuse_status"], "fresh")
        self.assertEqual(result["output"]["reuse"]["non_reuse_reason"], "prior_output_stale")
        tier_2 = result["output"]["tier_2_public_company_research"]
        self.assertEqual(tier_2["reuse_status"], "fresh")
        self.assertEqual(tier_2["findings"][0]["source_url"], "https://example.com/blog/fresh")
        self.assertEqual(len(fake_exa_client.calls), 2)

    def test_version_mismatch_prior_tier_2_output_falls_back_to_fresh_exa(self) -> None:
        fake_exa_client = FakeExaClient(
            {
                "requestId": "fresh_exa_request",
                "costDollars": 0.001,
                "results": [
                    {
                        "url": "https://example.com/blog/fresh",
                        "title": "Fresh AI agents",
                        "highlights": ["Fresh Exa fact about AI agents."],
                    }
                ],
            }
        )

        with patch.dict("os.environ", {"BDR_AGENT_EXA_API_KEY": "fake-test-key"}, clear=False):
            result = run_company_research(
                lead_id="123",
                trigger_source="inbound_oz_campaign_pdf_download",
                source_system="hubspot_workflow",
                hubspot_workflow_id="0000000000",
                dry_run=True,
                hydration_row=hydrated_row(),
                tier_1_metrics_client=FakeBigQueryClient([]),
                tier_2_reuse_client=FakeBigQueryClient(
                    [
                        prior_tier_2_row(
                            tier_2_public_research_json={
                                "status": "found",
                                "strategy": "old_strategy",
                                "positioning_taxonomy_version": POSITIONING_TAXONOMY_VERSION,
                                "findings": [{"fact": "Old fact should not be copied."}],
                            }
                        )
                    ]
                ),
                tier_2_public_research_client=fake_exa_client,
            )

        self.assertEqual(result["output"]["reuse"]["reuse_status"], "fresh")
        self.assertEqual(result["output"]["reuse"]["non_reuse_reason"], "strategy_version_changed")
        tier_2 = result["output"]["tier_2_public_company_research"]
        self.assertEqual(tier_2["reuse_status"], "fresh")
        self.assertNotEqual(tier_2["findings"][0]["fact"], "Old fact should not be copied.")
        self.assertEqual(len(fake_exa_client.calls), 2)

    def test_malformed_prior_tier_2_output_is_not_copied_and_falls_back_to_fresh_exa(self) -> None:
        fake_exa_client = FakeExaClient(
            {
                "requestId": "fresh_exa_request",
                "costDollars": 0.001,
                "results": [
                    {
                        "url": "https://example.com/blog/fresh",
                        "title": "Fresh AI agents",
                        "highlights": ["Fresh Exa fact about AI agents."],
                    }
                ],
            }
        )

        with patch.dict("os.environ", {"BDR_AGENT_EXA_API_KEY": "fake-test-key"}, clear=False):
            result = run_company_research(
                lead_id="123",
                trigger_source="inbound_oz_campaign_pdf_download",
                source_system="hubspot_workflow",
                hubspot_workflow_id="0000000000",
                dry_run=True,
                hydration_row=hydrated_row(),
                tier_1_metrics_client=FakeBigQueryClient([]),
                tier_2_reuse_client=FakeBigQueryClient([prior_tier_2_row(tier_2_public_research_json="{")]),
                tier_2_public_research_client=fake_exa_client,
            )

        self.assertEqual(result["output"]["reuse"]["reuse_status"], "fresh")
        self.assertEqual(result["output"]["reuse"]["non_reuse_reason"], "prior_output_unreadable")
        tier_2 = result["output"]["tier_2_public_company_research"]
        self.assertEqual(tier_2["reuse_status"], "fresh")
        self.assertEqual(tier_2["findings"][0]["source_url"], "https://example.com/blog/fresh")
        self.assertEqual(len(fake_exa_client.calls), 2)


if __name__ == "__main__":
    unittest.main()
