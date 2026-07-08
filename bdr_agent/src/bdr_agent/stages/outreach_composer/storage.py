"""Storage row builders and persistence helpers for lead brief outputs."""

from __future__ import annotations

import json
from typing import Any

from bdr_agent.common.oz_metadata import runtime_oz_metadata
from bdr_agent.stages.company_research.config import GCP_PROJECT_ID, HOOKS_TABLE, OUTPUTS_TABLE, RUNS_TABLE, bigquery_table_id
from bdr_agent.stages.company_research.storage import validate_bigquery_rows
from bdr_agent.outreach_writeback.config import (
    DEFAULT_POSITIONING_SNAPSHOT_VERSION,
    DEFAULT_STYLE_PROFILE_ID,
    DEFAULT_STYLE_PROFILE_VERSION,
    GENERATION_STATUS_QUALITY_PASSED,
    HOOK_PROPERTY_NAME,
    HOOK_STATUS_QUALITY_PASSED,
    SCHEMA_VERSION as HOOK_SCHEMA_VERSION,
    SOURCES_PROPERTY_NAME,
    WRITEBACK_STATUS_NOT_ATTEMPTED,
    WRITER_MODE_CANDIDATE_GENERATION,
)
from bdr_agent.stages.outreach_composer.config import (
    CONTENT_KIND_EMAIL_BODY,
    OUTPUT_TYPE,
    SCHEMA_VERSION,
    STAGE,
)

SLACK_DELIVERY_MARKER_STAGE = "lead_brief_slack_delivery"
SLACK_DELIVERY_OUTPUT_TYPE = "slack_review_notification"
SLACK_DELIVERY_IDEMPOTENCY_PREFIX = "lead_brief_slack"

def json_field(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


def build_run_metadata_row(
    *,
    result: dict,
    oz_run_id: str | None = None,
    oz_run_link: str | None = None,
    oz_session_link: str | None = None,
    oz_credits_used: float | None = None,
) -> dict:
    result_oz_metadata = result.get("oz_metadata") or {}
    return {
        "run_id": result["run_id"],
        "stage": _result_stage(result),
        "trigger_source": result["trigger_source"],
        "lead_id": result["lead_id"],
        "contact_id": result.get("contact_id"),
        "company_id": result.get("company_id"),
        "resolved_company_domain": result.get("resolved_company_domain"),
        "started_at": result.get("started_at"),
        "completed_at": result["completed_at"],
        "duration_seconds": result.get("duration_seconds"),
        "status": result["status"],
        "failure_reason": result.get("failure_reason"),
        "oz_run_id": oz_run_id if oz_run_id is not None else result_oz_metadata.get("oz_run_id"),
        "oz_run_link": oz_run_link if oz_run_link is not None else result_oz_metadata.get("oz_run_link"),
        "oz_session_link": oz_session_link if oz_session_link is not None else result_oz_metadata.get("oz_session_link"),
        "oz_credits_used": oz_credits_used if oz_credits_used is not None else result_oz_metadata.get("oz_credits_used"),
        "external_service_costs": json_field({}),
        "created_at": result["completed_at"],
    }


def build_output_index_row(*, result: dict) -> dict:
    return {
        "output_id": result["output_id"],
        "run_id": result["run_id"],
        "stage": _result_stage(result),
        "lead_id": result["lead_id"],
        "contact_id": result.get("contact_id"),
        "company_id": result.get("company_id"),
        "resolved_company_domain": result.get("resolved_company_domain"),
        "output_type": OUTPUT_TYPE,
        "schema_version": SCHEMA_VERSION,
        "bigquery_table": bigquery_table_id(HOOKS_TABLE),
        "bigquery_row_id": result["email_draft_ids"][0] if result.get("email_draft_ids") else None,
        "gcs_uri": result["lead_brief_gcs_uri"],
        "created_at": result["completed_at"],
    }


def build_email_body_hook_rows(
    *,
    result: dict,
    packet: dict,
    writeback: dict,
) -> list[dict]:
    rows = []
    sources_url = result.get("lead_brief_url") or result["lead_brief_gcs_uri"]
    for draft in sorted(packet["email_body_drafts"], key=lambda item: item["rank"]):
        selected = draft["rank"] == 1
        hook_id = result["email_draft_ids"][draft["rank"] - 1]
        now = result["completed_at"]
        rows.append(
            {
                "hook_id": hook_id,
                "output_id": result["output_id"],
                "run_id": result["run_id"],
                "lead_id": result["lead_id"],
                "contact_id": result.get("contact_id"),
                "company_id": result.get("company_id"),
                "resolved_company_domain": result.get("resolved_company_domain"),
                "company_research_output_id": result.get("company_research_output_id"),
                "synthesis_output_id": None,
                "synthesis_gcs_uri": None,
                "lead_brief_output_id": result["output_id"],
                "lead_brief_gcs_uri": result["lead_brief_gcs_uri"],
                "content_kind": CONTENT_KIND_EMAIL_BODY,
                "email_rank": draft["rank"],
                "email_label": draft["label"],
                "why_this_may_work": draft["why_this_may_work"],
                "selected_for_hubspot": selected,
                "lead_brief_eval_json": packet["evaluation"],
                "ai_hook_sources_url": sources_url,
                "style_profile_id": DEFAULT_STYLE_PROFILE_ID,
                "style_profile_version": DEFAULT_STYLE_PROFILE_VERSION,
                "style_profile_fallback_reason": None,
                "positioning_snapshot_version": DEFAULT_POSITIONING_SNAPSHOT_VERSION,
                "positioning_pillar": None,
                "positioning_value_prop": None,
                "writer_mode": WRITER_MODE_CANDIDATE_GENERATION,
                "candidate_hook_text": draft["body"],
                "final_hook_text": draft["body"],
                "generation_status": GENERATION_STATUS_QUALITY_PASSED,
                "rewrite_attempted": bool(packet.get("rewrite", {}).get("attempted")),
                "rewrite_reason": packet.get("rewrite", {}).get("reason"),
                "lint_result_json": {"status": "passed", "stage": _result_stage(result)},
                "critic_result_json": packet["evaluation"],
                "candidate_generation_idempotency_key": (
                    f"lead_brief_email:{result['lead_id']}:{result['output_id']}:{draft['rank']}"
                ),
                "hook_text": draft["body"],
                "hook_angle": draft["label"],
                "hook_status": HOOK_STATUS_QUALITY_PASSED,
                "hubspot_hook_property_name": HOOK_PROPERTY_NAME,
                "hubspot_sources_property_name": SOURCES_PROPERTY_NAME,
                "hubspot_outreach_writeback_status": (
                    writeback["hook_property"]["status"] if selected else WRITEBACK_STATUS_NOT_ATTEMPTED
                ),
                "hubspot_sources_writeback_status": (
                    writeback["sources_property"]["status"] if selected else WRITEBACK_STATUS_NOT_ATTEMPTED
                ),
                "hubspot_writeback_at": writeback.get("hubspot_writeback_at") if selected else None,
                "hubspot_writeback_error": writeback.get("hubspot_writeback_error") if selected else None,
                "used_by_bdr": None,
                "edited_hook_text": None,
                "outcome_status": None,
                "schema_version": HOOK_SCHEMA_VERSION,
                "created_at": now,
                "updated_at": now,
            }
        )
    return rows


def serialize_hook_json_fields(row: dict) -> dict:
    serialized = dict(row)
    for field_name in ("lead_brief_eval_json", "lint_result_json", "critic_result_json"):
        value = serialized.get(field_name)
        if value is not None and not isinstance(value, str):
            serialized[field_name] = json_field(value)
    return serialized


def insert_rows(*, client: Any, table_id: str, rows: list[dict], row_ids: list[str] | None = None) -> None:
    validate_bigquery_rows(table_id=table_id, rows=rows)
    kwargs: dict = {}
    if row_ids is not None:
        kwargs["row_ids"] = row_ids
    errors = client.insert_rows_json(table_id, rows, **kwargs)
    if errors:
        raise RuntimeError(f"BigQuery insert failed for {table_id}: {errors}")

def build_slack_delivery_idempotency_key(*, result: dict) -> str:
    return f"{_slack_delivery_idempotency_prefix(result=result)}:{result['lead_id']}:{result['output_id']}"


def build_slack_delivery_marker_row(*, result: dict) -> dict:
    idempotency_key = build_slack_delivery_idempotency_key(result=result)
    return {
        "output_id": idempotency_key,
        "run_id": result["run_id"],
        "stage": _slack_delivery_marker_stage(result=result),
        "lead_id": result["lead_id"],
        "contact_id": result.get("contact_id"),
        "company_id": result.get("company_id"),
        "resolved_company_domain": result.get("resolved_company_domain"),
        "output_type": SLACK_DELIVERY_OUTPUT_TYPE,
        "schema_version": SCHEMA_VERSION,
        "bigquery_table": bigquery_table_id(OUTPUTS_TABLE),
        "bigquery_row_id": result["output_id"],
        "gcs_uri": result["lead_brief_gcs_uri"],
        "created_at": result["completed_at"],
    }


def claim_slack_delivery_marker(
    *,
    result: dict,
    bigquery_client: Any | None = None,
) -> dict:
    """Claim a durable Slack delivery marker for this lead brief output.

    The marker is written before Slack is called so retries of the same output_id
    fail closed and do not duplicate the external side effect.
    """
    if bigquery_client is None:
        from google.cloud import bigquery

        bigquery_client = bigquery.Client(project=GCP_PROJECT_ID)

    idempotency_key = build_slack_delivery_idempotency_key(result=result)
    table_id = bigquery_table_id(OUTPUTS_TABLE)
    if _slack_delivery_marker_exists(
        bigquery_client=bigquery_client,
        table_id=table_id,
        idempotency_key=idempotency_key,
    ):
        return {
            "status": "duplicate",
            "idempotency_key": idempotency_key,
            "table": table_id,
            "row_id": idempotency_key,
        }

    marker_row = build_slack_delivery_marker_row(result=result)
    insert_rows(
        client=bigquery_client,
        table_id=table_id,
        rows=[marker_row],
        row_ids=[idempotency_key],
    )
    return {
        "status": "claimed",
        "idempotency_key": idempotency_key,
        "table": table_id,
        "row_id": idempotency_key,
    }


def _slack_delivery_marker_exists(
    *,
    bigquery_client: Any,
    table_id: str,
    idempotency_key: str,
) -> bool:
    query = f"""
select output_id
from `{table_id}`
where output_id = @output_id
  and output_type = '{SLACK_DELIVERY_OUTPUT_TYPE}'
limit 1
"""
    rows = list(
        bigquery_client.query(
            query,
            job_config=_build_output_id_query_config(output_id=idempotency_key),
        ).result(max_results=1)
    )
    return bool(rows)


def _build_output_id_query_config(*, output_id: str) -> Any | None:
    try:
        from google.cloud import bigquery
    except ModuleNotFoundError:
        return {"query_parameters": {"output_id": output_id}}
    return bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("output_id", "STRING", output_id)]
    )


def persist_lead_brief_result(
    *,
    result: dict,
    packet: dict,
    writeback: dict,
    bigquery_client: Any | None = None,
    gcs_client: Any | None = None,
) -> dict:
    from bdr_agent.stages.outreach_composer.artifacts import (
        write_rendered_lead_brief_html_to_gcs,
        write_text_to_gcs,
    )

    if bigquery_client is None:
        from google.cloud import bigquery

        bigquery_client = bigquery.Client(project=GCP_PROJECT_ID)
    result.setdefault("oz_metadata", runtime_oz_metadata().as_bigquery_fields())

    write_text_to_gcs(
        gcs_uri=result["lead_brief_gcs_uri"],
        content=packet["brief_markdown"],
        client=gcs_client,
    )
    write_rendered_lead_brief_html_to_gcs(
        gcs_uri=result["lead_brief_html_gcs_uri"],
        markdown=packet["brief_markdown"],
        client=gcs_client,
    )
    hook_rows = build_email_body_hook_rows(result=result, packet=packet, writeback=writeback)
    insert_rows(
        client=bigquery_client,
        table_id=bigquery_table_id(HOOKS_TABLE),
        rows=[serialize_hook_json_fields(row) for row in hook_rows],
        row_ids=[row["hook_id"] for row in hook_rows],
    )
    insert_rows(
        client=bigquery_client,
        table_id=bigquery_table_id(OUTPUTS_TABLE),
        rows=[build_output_index_row(result=result)],
        row_ids=[result["output_id"]],
    )
    insert_rows(
        client=bigquery_client,
        table_id=bigquery_table_id(RUNS_TABLE),
        rows=[build_run_metadata_row(result=result)],
        row_ids=[result["run_id"]],
    )
    return {
        "status": "persisted",
        "tables": [
            bigquery_table_id(HOOKS_TABLE),
            bigquery_table_id(OUTPUTS_TABLE),
            bigquery_table_id(RUNS_TABLE),
        ],
        "lead_brief_gcs_uri": result["lead_brief_gcs_uri"],
        "lead_brief_html_gcs_uri": result["lead_brief_html_gcs_uri"],
        "lead_brief_url": result["lead_brief_url"],
        "email_draft_ids": result["email_draft_ids"],
    }


def _result_stage(result: dict) -> str:
    return result.get("stage") or STAGE


def _slack_delivery_marker_stage(*, result: dict) -> str:
    stage = _result_stage(result)
    if stage == STAGE:
        return SLACK_DELIVERY_MARKER_STAGE
    return f"{stage}_slack_delivery"


def _slack_delivery_idempotency_prefix(*, result: dict) -> str:
    stage = _result_stage(result)
    if stage == STAGE:
        return SLACK_DELIVERY_IDEMPOTENCY_PREFIX
    return f"{stage}_slack"
