"""Tier 1 internal product-usage evidence lookup."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from bdr_agent.stages.company_research.config import GCP_PROJECT_ID

TIER_1_METRICS_QUERY_PATH = (
    Path(__file__).resolve().parents[4]
    / "sql"
    / "queries"
    / "tier_1_metrics_query.sql"
)

TIER_1_METRIC_FIELDS = (
    "known_users_total",
    "non_fraud_users_total",
    "active_users_30d",
    "active_users_90d",
    "signup_users_30d",
    "signup_users_90d",
    "first_signup_date",
    "latest_signup_date",
    "latest_observed_product_activity_at",
    "avg_wau_last_4_weeks",
    "peak_wau_last_12_weeks",
    "active_weeks_last_12_weeks",
    "latest_active_week",
    "ai_feature_users_30d",
    "ai_feature_users_90d",
    "ai_requests_30d",
    "ai_requests_90d",
    "usage_units_30d",
    "usage_units_90d",
    "usage_units_per_ai_user_30d",
    "ai_prompts_30d",
    "saved_objects_30d",
    "limit_hits_14d",
    "users_hitting_limits_14d",
    "reload_dollars_90d",
    "reload_count_90d",
    "users_upgraded_90d",
    "paid_users_any",
    "users_on_active_subscription",
    "teams_total",
    "active_subscription_teams",
    "active_standard_teams",
    "paid_plan_seats",
    "active_team_members",
    "team_members_using_ai",
    "active_automations",
    "active_documents",
    "active_team_weeks_last_month",
    "plan_types",
    "new_domain_members_30d",
    "team_invites_30d",
)


@dataclass(frozen=True)
class Tier1InternalMetricsResult:
    status: str
    email_domain: str
    metrics_as_of: str | None = None
    is_enterprise_domain: bool | None = None
    is_public_email_domain: bool | None = None
    has_product_usage: bool | None = None
    has_recent_product_usage: bool | None = None
    has_paid_signal: bool | None = None
    data_notes: list[str] | None = None
    metrics: dict | None = None


def load_tier_1_metrics_query(path: Path = TIER_1_METRICS_QUERY_PATH) -> str:
    return path.read_text().replace("example-gcp-project", GCP_PROJECT_ID)


def fetch_tier_1_internal_metrics(
    *,
    resolved_company_domain: str,
    client: Any | None = None,
    query: str | None = None,
    project_id: str = GCP_PROJECT_ID,
) -> Tier1InternalMetricsResult:
    if client is None:
        try:
            from google.cloud import bigquery
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "google-cloud-bigquery is required for Tier 1 internal metrics lookup. "
                "Install repository requirements or inject a BigQuery client for testing."
            ) from exc

        client = bigquery.Client(project=project_id)

    query_text = query or load_tier_1_metrics_query()
    job_config = _build_query_job_config(resolved_company_domain=resolved_company_domain)
    rows = list(client.query(query_text, job_config=job_config).result(max_results=1))
    if not rows:
        return not_found_tier_1_internal_metrics(resolved_company_domain)

    return tier_1_internal_metrics_from_row(rows[0], fallback_domain=resolved_company_domain)


def not_found_tier_1_internal_metrics(resolved_company_domain: str) -> Tier1InternalMetricsResult:
    return Tier1InternalMetricsResult(
        status="not_found",
        email_domain=resolved_company_domain,
        metrics=None,
    )


def tier_1_internal_metrics_from_row(
    row: Any,
    *,
    fallback_domain: str | None = None,
) -> Tier1InternalMetricsResult:
    row_dict = _row_to_dict(row)
    email_domain = str(row_dict.get("email_domain") or fallback_domain or "")
    metrics = {
        field: _json_safe_value(row_dict.get(field))
        for field in TIER_1_METRIC_FIELDS
        if row_dict.get(field) is not None
    }
    return Tier1InternalMetricsResult(
        status="found",
        email_domain=email_domain,
        metrics_as_of=_isoformat_or_none(row_dict.get("metrics_as_of")),
        is_enterprise_domain=_bool_or_none(row_dict.get("is_enterprise_domain")),
        is_public_email_domain=_bool_or_none(row_dict.get("is_public_email_domain")),
        has_product_usage=_bool_or_none(row_dict.get("has_product_usage")),
        has_recent_product_usage=_bool_or_none(row_dict.get("has_recent_product_usage")),
        has_paid_signal=_bool_or_none(row_dict.get("has_paid_signal")),
        data_notes=_list_or_none(row_dict.get("data_notes")),
        metrics=metrics or None,
    )


def apply_tier_1_internal_metrics(
    output: dict,
    result: Tier1InternalMetricsResult,
) -> None:
    output["tier_1_internal_metrics"] = {
        "status": result.status,
        "email_domain": result.email_domain,
        "metrics_as_of": result.metrics_as_of,
        "is_enterprise_domain": result.is_enterprise_domain,
        "is_public_email_domain": result.is_public_email_domain,
        "has_product_usage": result.has_product_usage,
        "has_recent_product_usage": result.has_recent_product_usage,
        "has_paid_signal": result.has_paid_signal,
        "data_notes": result.data_notes,
        "metrics": result.metrics,
    }


def _build_query_job_config(*, resolved_company_domain: str) -> Any | None:
    try:
        from google.cloud import bigquery
    except ModuleNotFoundError:
        return None

    return bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("resolved_company_domain", "STRING", resolved_company_domain),
        ],
    )


def _row_to_dict(row: Any) -> dict:
    if isinstance(row, dict):
        return row
    if hasattr(row, "items"):
        return dict(row.items())
    return dict(row)


def _isoformat_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value)


def _bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)

def _list_or_none(value: Any) -> list | None:
    if value is None:
        return None
    return list(value)


def _json_safe_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, list | tuple):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_safe_value(item) for key, item in value.items()}
    return value
