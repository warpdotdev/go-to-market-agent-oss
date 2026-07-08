"""Readers for company research outputs consumed by the lead brief stage."""

from __future__ import annotations

import json
from typing import Any

from bdr_agent.stages.company_research.config import (
    COMPANY_RESEARCH_OUTPUTS_TABLE,
    GCP_PROJECT_ID,
    bigquery_table_id,
)


def load_company_research_output(
    *,
    lead_id: str,
    company_research_output_id: str | None = None,
    company_research_output: dict | None = None,
    company_research_bigquery_table: str | None = None,
    bigquery_client: Any | None = None,
) -> dict:
    """Load explicit company research output, otherwise latest completed row for lead_id."""
    if company_research_output is not None:
        return company_research_output
    table_id = company_research_bigquery_table or bigquery_table_id(COMPANY_RESEARCH_OUTPUTS_TABLE)
    if company_research_output_id:
        return fetch_company_research_output_by_output_id(
            output_id=company_research_output_id,
            table_id=table_id,
            client=bigquery_client,
        )
    return fetch_latest_company_research_output_for_lead(
        lead_id=lead_id,
        table_id=table_id,
        client=bigquery_client,
    )


def fetch_company_research_output_by_output_id(
    *,
    output_id: str,
    table_id: str,
    client: Any | None = None,
) -> dict:
    if client is None:
        from google.cloud import bigquery

        client = bigquery.Client(project=GCP_PROJECT_ID)
    query = f"""
select *
from `{table_id}`
where output_id = @output_id
limit 1
"""
    rows = list(client.query(query, job_config=_build_query_config(output_id=output_id)).result(max_results=1))
    if not rows:
        raise ValueError(f"No company research output found for output_id={output_id}")
    return company_research_output_from_row(rows[0])


def fetch_latest_company_research_output_for_lead(
    *,
    lead_id: str,
    table_id: str,
    client: Any | None = None,
) -> dict:
    if client is None:
        from google.cloud import bigquery

        client = bigquery.Client(project=GCP_PROJECT_ID)
    query = f"""
select *
from `{table_id}`
where cast(lead_id as string) = @lead_id
  and coalesce(research_status, '') not in ('failed', 'not_ready')
order by created_at desc
limit 1
"""
    rows = list(client.query(query, job_config=_build_query_config(lead_id=lead_id)).result(max_results=1))
    if not rows:
        raise ValueError(f"No completed company research output found for lead_id={lead_id}")
    return company_research_output_from_row(rows[0])


def _build_query_config(*, output_id: str | None = None, lead_id: str | None = None) -> Any | None:
    try:
        from google.cloud import bigquery
    except ModuleNotFoundError:
        return None
    parameters = []
    if output_id is not None:
        parameters.append(bigquery.ScalarQueryParameter("output_id", "STRING", output_id))
    if lead_id is not None:
        parameters.append(bigquery.ScalarQueryParameter("lead_id", "STRING", lead_id))
    return bigquery.QueryJobConfig(query_parameters=parameters)


def company_research_output_from_row(row: Any) -> dict:
    row_dict = _row_to_dict(row)
    if row_dict.get("company_research_output_json"):
        return _json_object(row_dict["company_research_output_json"])

    company_context = _json_object(row_dict.get("company_context_json") or {})
    return {
        "schema_version": row_dict.get("schema_version"),
        "stage": "company_research",
        "trigger_source": row_dict.get("trigger_source"),
        "run_id": row_dict.get("run_id"),
        "output_id": row_dict.get("output_id"),
        "generated_at": _string_or_none(row_dict.get("created_at")),
        "lead": company_context.get("lead", {"lead_id": row_dict.get("lead_id")}),
        "contact": company_context.get("contact", {"contact_id": row_dict.get("contact_id")}),
        "company": company_context.get("company", {"company_id": row_dict.get("company_id")}),
        "hydration": company_context.get(
            "hydration",
            {
                "resolved_company_domain": row_dict.get("resolved_company_domain"),
                "hydration_status": row_dict.get("hydration_status"),
                "missing_fields": [],
            },
        ),
        "tier_1_internal_metrics": _json_object(row_dict.get("tier_1_internal_metrics_json") or {}),
        "tier_2_public_company_research": _json_object(
            row_dict.get("tier_2_public_research_json") or {}
        ),
        "tier_3_external_research": _json_object(row_dict.get("tier_3_external_research_json") or {}),
        "reuse": _json_object(row_dict.get("reuse_json") or {}),
        "storage": {
            "status": "persisted",
            "gcs_uri": row_dict.get("gcs_uri"),
            "bigquery_table": row_dict.get("bigquery_table")
            or bigquery_table_id(COMPANY_RESEARCH_OUTPUTS_TABLE),
            "bigquery_row_id": row_dict.get("output_id"),
        },
    }


def _row_to_dict(row: Any) -> dict:
    if isinstance(row, dict):
        return row
    if hasattr(row, "items"):
        return dict(row.items())
    return dict(row)


def _json_object(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        decoded = json.loads(value)
        if not isinstance(decoded, dict):
            raise ValueError("Expected JSON object")
        return decoded
    raise ValueError(f"Expected JSON object-compatible value, got {type(value).__name__}")


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)
