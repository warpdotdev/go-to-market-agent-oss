"""Tier 2 reuse eligibility helpers."""

from __future__ import annotations
from copy import deepcopy

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
from typing import Any

from bdr_agent.stages.company_research.config import (
    COMPANY_RESEARCH_OUTPUTS_TABLE,
    GCP_PROJECT_ID,
    POSITIONING_TAXONOMY_VERSION,
    SCHEMA_VERSION,
    TIER_2_FRESHNESS_DAYS,
    TIER_2_STRATEGY,
    VALID_TIER_2_REUSE_STATUSES,
    bigquery_table_id,
)


@dataclass(frozen=True)
class PriorTier2Output:
    resolved_company_domain: str
    tier_2_status: str
    generated_at: datetime
    strategy: str
    positioning_taxonomy_version: str
    is_readable: bool
    run_id: str
    output_id: str
    tier_2_public_research: dict


@dataclass(frozen=True)
class ReuseDecision:
    is_reusable: bool
    reason: str | None
    prior_run_id: str | None = None
    prior_output_id: str | None = None


@dataclass(frozen=True)
class Tier2ReuseLookupResult:
    decision: ReuseDecision
    prior_output: PriorTier2Output | None = None
    query_row_count: int = 0


PRIOR_TIER_2_OUTPUTS_QUERY = f"""
select
  output_id,
  run_id,
  resolved_company_domain,
  tier_2_public_research_json,
  schema_version,
  created_at
from `{bigquery_table_id(COMPANY_RESEARCH_OUTPUTS_TABLE)}`
where resolved_company_domain = @domain
  and schema_version = @schema_version
  and created_at >= @created_after
order by created_at desc
limit @limit
"""


def evaluate_tier_2_reuse(
    *,
    current_domain: str,
    prior_output: PriorTier2Output | None,
    now: datetime | None = None,
) -> ReuseDecision:
    if prior_output is None:
        return ReuseDecision(is_reusable=False, reason="no_prior_output")

    if prior_output.resolved_company_domain != current_domain:
        return ReuseDecision(is_reusable=False, reason="domain_mismatch")
    if not prior_output.is_readable:
        return ReuseDecision(is_reusable=False, reason="prior_output_unreadable")

    if prior_output.tier_2_status not in VALID_TIER_2_REUSE_STATUSES:
        return ReuseDecision(is_reusable=False, reason="prior_output_not_reusable")

    if prior_output.strategy != TIER_2_STRATEGY:
        return ReuseDecision(is_reusable=False, reason="strategy_version_changed")

    if prior_output.positioning_taxonomy_version != POSITIONING_TAXONOMY_VERSION:
        return ReuseDecision(is_reusable=False, reason="taxonomy_version_changed")

    comparison_time = now or datetime.now(UTC)
    if prior_output.generated_at.tzinfo is None:
        generated_at = prior_output.generated_at.replace(tzinfo=UTC)
    else:
        generated_at = prior_output.generated_at

    if generated_at < comparison_time - timedelta(days=TIER_2_FRESHNESS_DAYS):
        return ReuseDecision(is_reusable=False, reason="prior_output_stale")

    return ReuseDecision(
        is_reusable=True,
        reason=None,
        prior_run_id=prior_output.run_id,
        prior_output_id=prior_output.output_id,
    )


def find_reusable_tier_2_output(
    *,
    current_domain: str,
    client: Any | None = None,
    now: datetime | None = None,
    limit: int = 10,
) -> Tier2ReuseLookupResult:
    prior_outputs = fetch_prior_tier_2_outputs(
        current_domain=current_domain,
        client=client,
        now=now,
        limit=limit,
    )
    if not prior_outputs:
        return Tier2ReuseLookupResult(
            decision=ReuseDecision(is_reusable=False, reason="no_prior_output"),
            query_row_count=0,
        )

    first_non_reusable_decision = None
    first_prior_output = prior_outputs[0]
    for prior_output in prior_outputs:
        decision = evaluate_tier_2_reuse(
            current_domain=current_domain,
            prior_output=prior_output,
            now=now,
        )
        if decision.is_reusable:
            return Tier2ReuseLookupResult(
                decision=decision,
                prior_output=prior_output,
                query_row_count=len(prior_outputs),
            )
        if first_non_reusable_decision is None:
            first_non_reusable_decision = decision

    return Tier2ReuseLookupResult(
        decision=first_non_reusable_decision
        or ReuseDecision(is_reusable=False, reason="no_reusable_prior_output"),
        prior_output=first_prior_output,
        query_row_count=len(prior_outputs),
    )


def fetch_prior_tier_2_outputs(
    *,
    current_domain: str,
    client: Any | None = None,
    now: datetime | None = None,
    limit: int = 10,
) -> list[PriorTier2Output]:
    if client is None:
        try:
            from google.cloud import bigquery
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "google-cloud-bigquery is required for Tier 2 reuse lookup. "
                "Install repository requirements or inject a BigQuery client for testing."
            ) from exc

        client = bigquery.Client(project=GCP_PROJECT_ID)

    comparison_time = now or datetime.now(UTC)
    created_after = comparison_time - timedelta(days=TIER_2_FRESHNESS_DAYS)
    job_config = _build_query_job_config(
        current_domain=current_domain,
        created_after=created_after,
        limit=limit,
    )
    query_job = client.query(PRIOR_TIER_2_OUTPUTS_QUERY, job_config=job_config)
    rows = query_job.result(max_results=limit)
    return [prior_tier_2_output_from_row(row) for row in rows]


def prior_tier_2_output_from_row(row: Any) -> PriorTier2Output:
    row_dict = _row_to_dict(row)
    tier_2_data = _json_object(row_dict.get("tier_2_public_research_json"))
    is_readable = tier_2_data is not None
    tier_2_data = tier_2_data or {}
    return PriorTier2Output(
        resolved_company_domain=str(row_dict.get("resolved_company_domain") or ""),
        tier_2_status=tier_2_data.get("status", "unreadable"),
        generated_at=_as_datetime(row_dict.get("created_at")),
        strategy=tier_2_data.get("strategy", ""),
        positioning_taxonomy_version=tier_2_data.get("positioning_taxonomy_version", ""),
        is_readable=is_readable,
        run_id=str(row_dict.get("run_id") or ""),
        output_id=str(row_dict.get("output_id") or ""),
        tier_2_public_research=tier_2_data,
    )


def apply_tier_2_reuse_lookup(
    output: dict,
    lookup: Tier2ReuseLookupResult,
    *,
    reused_at: str | None = None,
) -> None:
    reuse = output["reuse"]
    decision = lookup.decision
    if decision.is_reusable:
        reused_at_value = reused_at or datetime.now(UTC).isoformat()
        reuse.update(
            {
                "reuse_status": "partial_reuse",
                "reused_tiers": ["tier_2_public_company_research"],
                "reused_from_run_id": decision.prior_run_id,
                "reused_from_output_id": decision.prior_output_id,
                "reused_at": reused_at_value,
                "non_reuse_reason": None,
            }
        )
        prior_tier_2 = lookup.prior_output.tier_2_public_research if lookup.prior_output else None
        if prior_tier_2 is not None and "tier_2_public_company_research" in output:
            tier_2_public_research = deepcopy(prior_tier_2)
            tier_2_public_research.update(
                {
                    "reuse_status": "reused",
                    "reused_from_run_id": decision.prior_run_id,
                    "reused_from_output_id": decision.prior_output_id,
                    "reused_at": reused_at_value,
                    "original_generated_at": _datetime_iso(lookup.prior_output.generated_at),
                    "incremental_external_service_cost_dollars": 0,
                }
            )
            output["tier_2_public_company_research"].update(tier_2_public_research)
    else:
        reuse.update(
            {
                "reuse_status": "not_reusable",
                "reused_tiers": [],
                "reused_from_run_id": None,
                "reused_from_output_id": None,
                "reused_at": None,
                "non_reuse_reason": decision.reason,
            }
        )


def _build_query_job_config(*, current_domain: str, created_after: datetime, limit: int) -> Any | None:
    try:
        from google.cloud import bigquery
    except ModuleNotFoundError:
        return None

    return bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("domain", "STRING", current_domain),
            bigquery.ScalarQueryParameter("schema_version", "STRING", SCHEMA_VERSION),
            bigquery.ScalarQueryParameter("created_after", "TIMESTAMP", created_after),
            bigquery.ScalarQueryParameter("limit", "INT64", limit),
        ],
    )


def _row_to_dict(row: Any) -> dict:
    if isinstance(row, dict):
        return row
    if hasattr(row, "items"):
        return dict(row.items())
    return dict(row)


def _json_object(value: Any) -> dict | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return None
        return decoded if isinstance(decoded, dict) else None
    return None


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    raise ValueError(f"Cannot convert value to datetime: {value!r}")


def _datetime_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()
