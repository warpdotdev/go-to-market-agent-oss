"""Stage-completion handoff helpers for candidate hook generation."""

from __future__ import annotations

from typing import Any

from bdr_agent.outreach_writeback.config import STAGE
from bdr_agent.stage_completion import send_stage_completion_payload, skipped_stage_completion

WORKFLOW = "bdr_agent"
NEXT_STAGE = "evaluate_and_writeback"
COMPLETED_STATUS = "completed"


def build_stage_completion_payload(*, result: dict) -> dict:
    row = result["hook_row"]
    persistence = result.get("bigquery_persistence") or {}
    candidate_hook_artifact_uri = result.get("candidate_hook_artifact_uri") or result.get(
        "candidate_hook_artifact_ref"
    )
    evaluate_input_artifact_uri = result.get("evaluate_and_writeback_input_artifact_uri")
    payload = {
        "workflow": WORKFLOW,
        "source_stage": STAGE,
        "next_stage": NEXT_STAGE,
        "lead_id": result["lead_id"],
        "run_id": result["run_id"],
        "output_id": result["output_id"],
        "status": COMPLETED_STATUS,
        "idempotency_key": f"{STAGE}:{result['run_id']}:{result['output_id']}:{NEXT_STAGE}",
    }
    optional_values = {
        "contact_id": result.get("contact_id"),
        "company_id": result.get("company_id"),
        "resolved_company_domain": result.get("resolved_company_domain"),
        "outreach_writeback_run_id": result["run_id"],
        "outreach_writeback_output_id": result["output_id"],
        "outreach_writeback_hook_id": result["hook_id"],
        "outreach_writeback_bigquery_table": persistence.get("hook_bigquery_table"),
        "outreach_writeback_bigquery_row_id": persistence.get("hook_bigquery_row_id"),
        "candidate_hook_id": row["hook_id"],
        "candidate_hook_output_id": row["output_id"],
        "candidate_hook_artifact_id": row["hook_id"],
        "candidate_hook_artifact_ref": candidate_hook_artifact_uri,
        "candidate_hook_artifact_uri": candidate_hook_artifact_uri,
        "candidate_generation_idempotency_key": row["candidate_generation_idempotency_key"],
        "evaluate_and_writeback_input_artifact_uri": evaluate_input_artifact_uri,
        "evaluate_and_writeback_input_artifact_ref": evaluate_input_artifact_uri,
        "synthesis_run_id": result.get("synthesis_run_id"),
        "synthesis_output_id": result.get("synthesis_output_id"),
        "synthesis_gcs_uri": result.get("synthesis_gcs_uri"),
        "company_research_output_id": result.get("company_research_output_id"),
        "gcs_uri": candidate_hook_artifact_uri,
        "bigquery_table": persistence.get("hook_bigquery_table"),
        "bigquery_row_id": persistence.get("hook_bigquery_row_id"),
    }
    payload.update({key: value for key, value in optional_values.items() if value is not None})
    return payload


def send_stage_completion(
    *,
    result: dict,
    webhook_url: str | None = None,
    webhook_secret: str | None = None,
    client: Any | None = None,
    timeout_seconds: float = 10.0,
) -> dict:
    return send_stage_completion_payload(
        payload=build_stage_completion_payload(result=result),
        webhook_url=webhook_url,
        webhook_secret=webhook_secret,
        client=client,
        timeout_seconds=timeout_seconds,
    )
