import unittest
from unittest.mock import patch

from bdr_agent.stages.company_research.config import EXA_SEARCH_URL
from bdr_agent.stages.company_research.public_research import (
    POSITIONING_QUERY_SPECS,
    build_exa_search_payload,
    run_fresh_tier_2_public_research,
)


class FakeExaResponse:
    def __init__(self, payload) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self.payload


class FakeExaClient:
    def __init__(self, payloads) -> None:
        self.payloads = list(payloads)
        self.calls = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return FakeExaResponse(self.payloads.pop(0))


class PublicResearchTest(unittest.TestCase):
    def test_build_exa_search_payload_is_domain_scoped_with_highlights(self) -> None:
        payload = build_exa_search_payload(
            resolved_company_domain="https://www.example.com/",
            query_spec=POSITIONING_QUERY_SPECS[0],
            num_results=2,
        )

        self.assertIn("site:example.com", payload["query"])
        self.assertEqual(payload["includeDomains"], ["example.com"])
        self.assertEqual(payload["numResults"], 2)
        self.assertEqual(payload["contents"], {"highlights": True})

    def test_missing_bdr_specific_exa_key_returns_error_without_calling_client(self) -> None:
        client = FakeExaClient([])

        with patch.dict("os.environ", {}, clear=True):
            result = run_fresh_tier_2_public_research(
                resolved_company_domain="example.com",
                client=client,
            )

        self.assertEqual(result["status"], "error")
        self.assertIn("BDR_AGENT_EXA_API_KEY", result["errors"][0])
        self.assertEqual(client.calls, [])

    def test_success_maps_company_owned_highlights_to_findings_and_attempts(self) -> None:
        client = FakeExaClient(
            [
                {
                    "requestId": "request_1",
                    "costDollars": {"search": 0.007, "contents": 0.001},
                    "results": [
                        {
                            "url": "https://example.com/engineering/agentic-development",
                            "title": "Agentic development",
                            "publishedDate": "2026-05-01T00:00:00Z",
                            "highlights": [" Example uses AI agents to improve developer productivity. "],
                            "highlightScores": [0.91],
                        },
                        {
                            "url": "https://external.example.net/blog",
                            "title": "External result",
                            "highlights": ["This should be ignored."],
                        },
                    ],
                }
            ]
        )

        result = run_fresh_tier_2_public_research(
            resolved_company_domain="example.com",
            company_name="Example",
            client=client,
            api_key="fake-test-key",
            max_queries=1,
            num_results=2,
        )

        self.assertEqual(result["status"], "found")
        self.assertEqual(result["external_service_cost_dollars"], 0.008)
        self.assertEqual(len(result["source_attempts"]), 1)
        self.assertEqual(result["source_attempts"][0]["status"], "success")
        self.assertEqual(result["source_attempts"][0]["result_count"], 2)
        self.assertEqual(result["source_attempts"][0]["kept_findings"], 1)
        self.assertEqual(result["findings"][0]["finding_id"], "tier2_001")
        self.assertEqual(result["findings"][0]["source_type"], "engineering_blog")
        self.assertEqual(result["findings"][0]["confidence"], "high")
        self.assertEqual(
            result["findings"][0]["evidence_quote"],
            "Example uses AI agents to improve developer productivity.",
        )
        self.assertEqual(client.calls[0]["url"], EXA_SEARCH_URL)
        self.assertEqual(client.calls[0]["headers"]["x-api-key"], "fake-test-key")

    def test_search_success_without_kept_company_owned_findings_is_not_found(self) -> None:
        client = FakeExaClient(
            [
                {
                    "requestId": "request_1",
                    "costDollars": 0.002,
                    "results": [
                        {
                            "url": "https://external.example.net/blog",
                            "title": "External result",
                            "highlights": ["External AI agent content."],
                        }
                    ],
                }
            ]
        )

        result = run_fresh_tier_2_public_research(
            resolved_company_domain="example.com",
            client=client,
            api_key="fake-test-key",
            max_queries=1,
        )

        self.assertEqual(result["status"], "not_found")
        self.assertEqual(result["source_attempts"][0]["result_count"], 1)
        self.assertEqual(result["source_attempts"][0]["kept_findings"], 0)


if __name__ == "__main__":
    unittest.main()
