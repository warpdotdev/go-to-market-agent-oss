import unittest

from bdr_agent.stages.company_research.config import STAGE_COMPLETION_HEADER_NAME
from bdr_agent.stages.company_research.stage_completion import (
    ACCEPTED_NEXT_STAGE_ALIASES,
    CANONICAL_NEXT_STAGE,
    LEGACY_NEXT_STAGE,
    build_stage_completion_payload,
    normalize_next_stage_contract,
    send_stage_completion,
)
from bdr_agent.stages.company_research.storage import build_company_research_gcs_uri
from bdr_agent.stages.company_research.schemas import build_minimal_company_research_output


class FakeResponse:
    status_code = 202

    def raise_for_status(self):
        return None


class FakeFailingResponse:
    status_code = 500

    def raise_for_status(self):
        raise RuntimeError("server error")


class FakeWebhookClient:
    def __init__(self, response=None) -> None:
        self.response = response or FakeResponse()
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
        return self.response


def persisted_result() -> dict:
    output = build_minimal_company_research_output(
        lead_id="lead_123",
        trigger_source="inbound_oz_campaign_pdf_download",
        hydration_status="hydrated",
        resolved_company_domain="example.com",
        resolved_company_domain_source="company.email_domain",
        lead={"lead_id": "lead_123"},
        contact={"contact_id": "contact_123", "associated_company_id": "company_123"},
        company={"company_id": "company_123", "company_name": "Example"},
    )
    output["storage"] = {
        "status": "persisted",
        "gcs_uri": build_company_research_gcs_uri(output=output),
        "bigquery_table": "example-gcp-project.gtm_agents.bdr_agent_company_research_outputs",
        "bigquery_row_id": output["output_id"],
    }
    return {
        "status": "research_complete",
        "stage": output["stage"],
        "lead_id": "lead_123",
        "source_system": "hubspot_workflow",
        "hubspot_workflow_id": "0000000000",
        "dry_run": False,
        "skip_bigquery": False,
        "run_id": output["run_id"],
        "output_id": output["output_id"],
        "output": output,
        "failure_reason": None,
    }


class StageCompletionTest(unittest.TestCase):
    def test_next_stage_keeps_legacy_runtime_value_with_outreach_composer_alias(self) -> None:
        self.assertEqual(CANONICAL_NEXT_STAGE, "outreach_composer")
        self.assertEqual(LEGACY_NEXT_STAGE, "lead_brief")
        self.assertEqual(ACCEPTED_NEXT_STAGE_ALIASES, ("outreach_composer", "lead_brief"))
        self.assertEqual(normalize_next_stage_contract("lead_brief"), "lead_brief")
        self.assertEqual(normalize_next_stage_contract("outreach_composer"), "outreach_composer")

    def test_build_stage_completion_payload_matches_agent_orchestrator_contract(self) -> None:
        result = persisted_result()

        payload = build_stage_completion_payload(result=result)

        self.assertEqual(payload["workflow"], "bdr_agent")
        self.assertEqual(payload["source_stage"], "company_research")
        self.assertEqual(payload["next_stage"], "lead_brief")
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(
            payload["idempotency_key"],
            f"company_research:{result['run_id']}:{result['output_id']}:lead_brief",
        )
        self.assertEqual(payload["lead_id"], "lead_123")
        self.assertEqual(payload["contact_id"], "contact_123")
        self.assertEqual(payload["company_id"], "company_123")
        self.assertEqual(payload["resolved_company_domain"], "example.com")
        self.assertEqual(payload["company_research_run_id"], result["run_id"])
        self.assertEqual(payload["company_research_output_id"], result["output_id"])
        self.assertEqual(payload["company_research_gcs_uri"], result["output"]["storage"]["gcs_uri"])
        self.assertEqual(payload["bigquery_row_id"], result["output_id"])

    def test_build_stage_completion_payload_accepts_canonical_next_stage(self) -> None:
        result = persisted_result()

        payload = build_stage_completion_payload(result=result, next_stage="outreach_composer")

        self.assertEqual(payload["next_stage"], "outreach_composer")
        self.assertEqual(
            payload["idempotency_key"],
            f"company_research:{result['run_id']}:{result['output_id']}:outreach_composer",
        )

    def test_send_stage_completion_skips_without_url(self) -> None:
        client = FakeWebhookClient()

        result = send_stage_completion(
            result=persisted_result(),
            webhook_url="",
            webhook_secret="secret",
            client=client,
        )

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "webhook_url_not_configured")
        self.assertEqual(client.posts, [])

    def test_send_stage_completion_skips_without_secret(self) -> None:
        client = FakeWebhookClient()

        result = send_stage_completion(
            result=persisted_result(),
            webhook_url="https://budserver.example/webhooks/bdr-agent-stage-completion",
            webhook_secret="",
            client=client,
        )

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "webhook_secret_not_configured")
        self.assertEqual(client.posts, [])

    def test_send_stage_completion_posts_payload_with_secret_header(self) -> None:
        client = FakeWebhookClient()

        result = send_stage_completion(
            result=persisted_result(),
            webhook_url="https://budserver.example/webhooks/bdr-agent-stage-completion",
            webhook_secret="secret",
            client=client,
        )

        self.assertEqual(result["status"], "sent")
        self.assertEqual(result["http_status"], 202)
        self.assertEqual(len(client.posts), 1)
        post = client.posts[0]
        self.assertEqual(post["headers"][STAGE_COMPLETION_HEADER_NAME], "secret")
        self.assertEqual(post["json"]["source_stage"], "company_research")
        self.assertEqual(post["json"]["next_stage"], "lead_brief")

    def test_send_stage_completion_reports_failed_http_response(self) -> None:
        client = FakeWebhookClient(response=FakeFailingResponse())

        result = send_stage_completion(
            result=persisted_result(),
            webhook_url="https://budserver.example/webhooks/bdr-agent-stage-completion",
            webhook_secret="secret",
            client=client,
        )

        self.assertEqual(result["status"], "failed")
        self.assertIn("server error", result["error"])
        self.assertEqual(len(client.posts), 1)


if __name__ == "__main__":
    unittest.main()
