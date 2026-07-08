import unittest
from datetime import UTC, datetime, timedelta

from bdr_agent.stages.company_research.config import (
    POSITIONING_TAXONOMY_VERSION,
    TIER_2_STRATEGY,
)
from bdr_agent.stages.company_research.reuse import (
    PriorTier2Output,
    apply_tier_2_reuse_lookup,
    evaluate_tier_2_reuse,
    fetch_prior_tier_2_outputs,
    find_reusable_tier_2_output,
    prior_tier_2_output_from_row,
)


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


def prior_output(**overrides) -> PriorTier2Output:
    defaults = {
        "resolved_company_domain": "example.com",
        "tier_2_status": "found",
        "generated_at": datetime(2026, 5, 18, tzinfo=UTC) - timedelta(days=1),
        "strategy": TIER_2_STRATEGY,
        "positioning_taxonomy_version": POSITIONING_TAXONOMY_VERSION,
        "is_readable": True,
        "run_id": "run_1",
        "output_id": "output_1",
        "tier_2_public_research": {
            "status": "found",
            "strategy": TIER_2_STRATEGY,
            "positioning_taxonomy_version": POSITIONING_TAXONOMY_VERSION,
            "findings": [{"fact": "Example uses [your product]."}],
            "source_attempts": [{"query": "site:example.com"}],
            "external_service_cost_dollars": 0.014,
        },
    }
    defaults.update(overrides)
    return PriorTier2Output(**defaults)


def prior_row(**overrides) -> dict:
    defaults = {
        "resolved_company_domain": "example.com",
        "tier_2_public_research_json": {
            "status": "found",
            "strategy": TIER_2_STRATEGY,
            "positioning_taxonomy_version": POSITIONING_TAXONOMY_VERSION,
            "findings": [{"fact": "Example uses [your product]."}],
            "source_attempts": [{"query": "site:example.com"}],
            "external_service_cost_dollars": 0.014,
        },
        "created_at": datetime(2026, 5, 18, tzinfo=UTC) - timedelta(days=1),
        "run_id": "run_1",
        "output_id": "output_1",
        "schema_version": "bdr_agent_company_research.v1",
    }
    defaults.update(overrides)
    return defaults


class ReuseTest(unittest.TestCase):
    def test_fresh_matching_prior_tier_2_output_is_reusable(self) -> None:
        decision = evaluate_tier_2_reuse(
            current_domain="example.com",
            prior_output=prior_output(),
            now=datetime(2026, 5, 18, tzinfo=UTC),
        )

        self.assertTrue(decision.is_reusable)
        self.assertEqual(decision.prior_run_id, "run_1")
        self.assertEqual(decision.prior_output_id, "output_1")

    def test_stale_prior_tier_2_output_is_not_reusable(self) -> None:
        decision = evaluate_tier_2_reuse(
            current_domain="example.com",
            prior_output=prior_output(generated_at=datetime(2026, 4, 1, tzinfo=UTC)),
            now=datetime(2026, 5, 18, tzinfo=UTC),
        )

        self.assertFalse(decision.is_reusable)
        self.assertEqual(decision.reason, "prior_output_stale")

    def test_strategy_mismatch_is_not_reusable(self) -> None:
        decision = evaluate_tier_2_reuse(
            current_domain="example.com",
            prior_output=prior_output(strategy="old_strategy"),
            now=datetime(2026, 5, 18, tzinfo=UTC),
        )

        self.assertFalse(decision.is_reusable)
        self.assertEqual(decision.reason, "strategy_version_changed")

    def test_unreadable_prior_tier_2_output_is_not_reusable(self) -> None:
        decision = evaluate_tier_2_reuse(
            current_domain="example.com",
            prior_output=prior_output(is_readable=False, tier_2_status="unreadable"),
            now=datetime(2026, 5, 18, tzinfo=UTC),
        )

        self.assertFalse(decision.is_reusable)
        self.assertEqual(decision.reason, "prior_output_unreadable")

    def test_prior_tier_2_output_from_row_parses_json_string(self) -> None:
        row = prior_row(
            tier_2_public_research_json=(
                '{"status":"found","strategy":"exa_positioning_guided_company_owned_search_v1",'
                '"positioning_taxonomy_version":"product_positioning_research_v1"}'
            )
        )

        output = prior_tier_2_output_from_row(row)

        self.assertTrue(output.is_readable)
        self.assertEqual(output.tier_2_status, "found")
        self.assertEqual(output.strategy, TIER_2_STRATEGY)

    def test_prior_tier_2_output_from_row_marks_malformed_json_unreadable(self) -> None:
        output = prior_tier_2_output_from_row(prior_row(tier_2_public_research_json="{"))

        self.assertFalse(output.is_readable)

    def test_fetch_prior_tier_2_outputs_queries_bigquery_client(self) -> None:
        client = FakeBigQueryClient([prior_row()])

        outputs = fetch_prior_tier_2_outputs(
            current_domain="example.com",
            client=client,
            now=datetime(2026, 5, 18, tzinfo=UTC),
            limit=3,
        )

        self.assertEqual(len(outputs), 1)
        self.assertIn("bdr_agent_company_research_outputs", client.query_text)
        self.assertEqual(client.query_job.max_results, 3)

    def test_find_reusable_tier_2_output_returns_first_reusable_candidate(self) -> None:
        client = FakeBigQueryClient(
            [
                prior_row(tier_2_public_research_json={"status": "not_found"}),
                prior_row(run_id="run_2", output_id="output_2"),
            ]
        )

        lookup = find_reusable_tier_2_output(
            current_domain="example.com",
            client=client,
            now=datetime(2026, 5, 18, tzinfo=UTC),
        )

        self.assertTrue(lookup.decision.is_reusable)
        self.assertEqual(lookup.decision.prior_run_id, "run_2")
        self.assertEqual(lookup.query_row_count, 2)

    def test_find_reusable_tier_2_output_returns_no_prior_output_for_empty_result(self) -> None:
        lookup = find_reusable_tier_2_output(
            current_domain="example.com",
            client=FakeBigQueryClient([]),
            now=datetime(2026, 5, 18, tzinfo=UTC),
        )

        self.assertFalse(lookup.decision.is_reusable)
        self.assertEqual(lookup.decision.reason, "no_prior_output")

    def test_apply_tier_2_reuse_lookup_updates_reuse_metadata_only(self) -> None:
        output = {
            "reuse": {
                "reuse_key": "example.com",
                "reuse_status": "not_reusable",
                "reused_tiers": [],
                "reused_from_run_id": None,
                "reused_from_output_id": None,
                "reused_at": None,
                "non_reuse_reason": None,
            }
        }
        lookup = find_reusable_tier_2_output(
            current_domain="example.com",
            client=FakeBigQueryClient([prior_row()]),
            now=datetime(2026, 5, 18, tzinfo=UTC),
        )

        apply_tier_2_reuse_lookup(output, lookup, reused_at="2026-05-18T00:00:00+00:00")

        self.assertEqual(output["reuse"]["reuse_status"], "partial_reuse")
        self.assertEqual(output["reuse"]["reused_tiers"], ["tier_2_public_company_research"])
        self.assertEqual(output["reuse"]["reused_from_run_id"], "run_1")
        self.assertEqual(output["reuse"]["reused_from_output_id"], "output_1")

    def test_apply_tier_2_reuse_lookup_copies_reusable_tier_2_block(self) -> None:
        output = {
            "tier_2_public_company_research": {
                "status": "not_run",
                "strategy": TIER_2_STRATEGY,
                "positioning_taxonomy_version": POSITIONING_TAXONOMY_VERSION,
                "findings": [],
                "source_attempts": [],
                "external_service_cost_dollars": 0,
            },
            "reuse": {
                "reuse_key": "example.com",
                "reuse_status": "not_reusable",
                "reused_tiers": [],
                "reused_from_run_id": None,
                "reused_from_output_id": None,
                "reused_at": None,
                "non_reuse_reason": None,
            },
        }
        lookup = find_reusable_tier_2_output(
            current_domain="example.com",
            client=FakeBigQueryClient([prior_row()]),
            now=datetime(2026, 5, 18, tzinfo=UTC),
        )

        apply_tier_2_reuse_lookup(output, lookup, reused_at="2026-05-18T00:00:00+00:00")

        tier_2 = output["tier_2_public_company_research"]
        self.assertEqual(tier_2["status"], "found")
        self.assertEqual(tier_2["reuse_status"], "reused")
        self.assertEqual(tier_2["findings"], [{"fact": "Example uses [your product]."}])
        self.assertEqual(tier_2["source_attempts"], [{"query": "site:example.com"}])
        self.assertEqual(tier_2["external_service_cost_dollars"], 0.014)
        self.assertEqual(tier_2["incremental_external_service_cost_dollars"], 0)
        self.assertEqual(tier_2["reused_from_run_id"], "run_1")
        self.assertEqual(tier_2["reused_from_output_id"], "output_1")
        self.assertEqual(tier_2["reused_at"], "2026-05-18T00:00:00+00:00")
        self.assertEqual(tier_2["original_generated_at"], "2026-05-17T00:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
