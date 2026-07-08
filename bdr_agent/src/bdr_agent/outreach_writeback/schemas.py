"""Schema assembly and validation for BDR hook rows."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from bdr_agent.outreach_writeback.config import (
    DEFAULT_POSITIONING_SNAPSHOT_VERSION,
    DEFAULT_STYLE_PROFILE_FALLBACK_REASON,
    DEFAULT_STYLE_PROFILE_ID,
    DEFAULT_STYLE_PROFILE_VERSION,
    GENERATION_STATUS_CANDIDATE_GENERATED,
    GENERATION_STATUS_TRANSITIONS,
    GENERATION_STATUS_WRITEBACK_FAILED,
    GENERATION_STATUS_WRITEBACK_SUCCEEDED,
    HOOK_PROPERTY_NAME,
    HOOK_STATUS_CANDIDATE_GENERATED,
    SCHEMA_VERSION,
    SOURCES_PROPERTY_NAME,
    STAGE,
    VALID_GENERATION_STATUSES,
    VALID_WRITER_MODES,
    WRITEBACK_STATUS_NOT_ATTEMPTED,
    WRITER_MODE_CANDIDATE_GENERATION,
)
from bdr_agent.outreach_writeback.contracts import build_candidate_generation_idempotency_key


def new_run_id() -> str:
    return f"bdr_run_{uuid4().hex}"


def new_output_id() -> str:
    return f"bdr_output_{uuid4().hex}"


def new_hook_id() -> str:
    return f"bdr_hook_{uuid4().hex}"


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def build_hook_row(
    *,
    selected_hook: dict,
    lead_id: str,
    contact_id: str | None,
    company_id: str | None,
    resolved_company_domain: str | None,
    company_research_output_id: str | None,
    synthesis_run_id: str | None,
    synthesis_output_id: str,
    synthesis_gcs_uri: str,
    ai_hook_sources_url: str | None = None,
    run_id: str | None = None,
    output_id: str | None = None,
    hook_id: str | None = None,
    created_at: str | None = None,
    hook_status: str = HOOK_STATUS_CANDIDATE_GENERATED,
    hubspot_outreach_writeback_status: str = WRITEBACK_STATUS_NOT_ATTEMPTED,
    hubspot_sources_writeback_status: str = WRITEBACK_STATUS_NOT_ATTEMPTED,
    hubspot_writeback_at: str | None = None,
    hubspot_writeback_error: str | None = None,
    style_profile_id: str = DEFAULT_STYLE_PROFILE_ID,
    style_profile_version: str = DEFAULT_STYLE_PROFILE_VERSION,
    style_profile_fallback_reason: str | None = DEFAULT_STYLE_PROFILE_FALLBACK_REASON,
    positioning_snapshot_version: str = DEFAULT_POSITIONING_SNAPSHOT_VERSION,
    positioning_pillar: str | None = None,
    positioning_value_prop: str | None = None,
    writer_mode: str = WRITER_MODE_CANDIDATE_GENERATION,
    final_hook_text: str | None = None,
    generation_status: str = GENERATION_STATUS_CANDIDATE_GENERATED,
    rewrite_attempted: bool = False,
    rewrite_reason: str | None = None,
    lint_result_json: dict | None = None,
    critic_result_json: dict | None = None,
) -> dict:
    now = created_at or utc_now_iso()
    candidate_hook_text = selected_hook["hook_text"]
    row = {
        "hook_id": hook_id or new_hook_id(),
        "output_id": output_id or new_output_id(),
        "run_id": run_id or new_run_id(),
        "lead_id": lead_id,
        "contact_id": contact_id,
        "company_id": company_id,
        "resolved_company_domain": resolved_company_domain,
        "company_research_output_id": company_research_output_id,
        "synthesis_output_id": synthesis_output_id,
        "synthesis_gcs_uri": synthesis_gcs_uri,
        "lead_brief_output_id": None,
        "lead_brief_gcs_uri": None,
        "content_kind": None,
        "email_rank": None,
        "email_label": None,
        "why_this_may_work": None,
        "selected_for_hubspot": None,
        "lead_brief_eval_json": None,
        "ai_hook_sources_url": ai_hook_sources_url or synthesis_gcs_uri,
        "style_profile_id": style_profile_id,
        "style_profile_version": style_profile_version,
        "style_profile_fallback_reason": style_profile_fallback_reason,
        "positioning_snapshot_version": positioning_snapshot_version,
        "positioning_pillar": positioning_pillar or selected_hook.get("positioning_pillar"),
        "positioning_value_prop": positioning_value_prop or selected_hook.get("positioning_value_prop"),
        "writer_mode": writer_mode,
        "candidate_hook_text": candidate_hook_text,
        "final_hook_text": final_hook_text,
        "generation_status": generation_status,
        "rewrite_attempted": rewrite_attempted,
        "rewrite_reason": rewrite_reason,
        "lint_result_json": lint_result_json,
        "critic_result_json": critic_result_json,
        "candidate_generation_idempotency_key": build_candidate_generation_idempotency_key(
            lead_id=lead_id,
            synthesis_output_id=synthesis_output_id,
            style_profile_version=style_profile_version,
            positioning_snapshot_version=positioning_snapshot_version,
            writer_mode=writer_mode,
        ),
        "hook_text": candidate_hook_text,
        "hook_angle": selected_hook["hook_angle"],
        "hook_status": hook_status,
        "hubspot_hook_property_name": HOOK_PROPERTY_NAME,
        "hubspot_sources_property_name": SOURCES_PROPERTY_NAME,
        "hubspot_outreach_writeback_status": hubspot_outreach_writeback_status,
        "hubspot_sources_writeback_status": hubspot_sources_writeback_status,
        "hubspot_writeback_at": hubspot_writeback_at,
        "hubspot_writeback_error": hubspot_writeback_error,
        "used_by_bdr": None,
        "edited_hook_text": None,
        "outcome_status": None,
        "schema_version": SCHEMA_VERSION,
        "created_at": now,
        "updated_at": now,
    }
    validate_hook_row(row)
    return row


def validate_hook_row(row: dict) -> None:
    required_fields = {
        "hook_id",
        "output_id",
        "run_id",
        "lead_id",
        "contact_id",
        "company_id",
        "resolved_company_domain",
        "company_research_output_id",
        "synthesis_output_id",
        "synthesis_gcs_uri",
        "lead_brief_output_id",
        "lead_brief_gcs_uri",
        "content_kind",
        "email_rank",
        "email_label",
        "why_this_may_work",
        "selected_for_hubspot",
        "lead_brief_eval_json",
        "ai_hook_sources_url",
        "style_profile_id",
        "style_profile_version",
        "style_profile_fallback_reason",
        "positioning_snapshot_version",
        "positioning_pillar",
        "positioning_value_prop",
        "writer_mode",
        "candidate_hook_text",
        "final_hook_text",
        "generation_status",
        "rewrite_attempted",
        "rewrite_reason",
        "lint_result_json",
        "critic_result_json",
        "candidate_generation_idempotency_key",
        "hook_text",
        "hook_angle",
        "hook_status",
        "hubspot_hook_property_name",
        "hubspot_sources_property_name",
        "hubspot_outreach_writeback_status",
        "hubspot_sources_writeback_status",
        "hubspot_writeback_at",
        "hubspot_writeback_error",
        "used_by_bdr",
        "edited_hook_text",
        "outcome_status",
        "schema_version",
        "created_at",
        "updated_at",
    }
    missing = required_fields - row.keys()
    if missing:
        raise ValueError(f"Missing hook row fields: {sorted(missing)}")
    if row["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"Unexpected schema_version: {row['schema_version']}")
    if not row["hook_text"]:
        raise ValueError("hook_text is required")
    if not row["candidate_hook_text"]:
        raise ValueError("candidate_hook_text is required")
    if not row["hook_angle"]:
        raise ValueError("hook_angle is required")
    if not row["lead_id"]:
        raise ValueError("lead_id is required")
    if not row["synthesis_output_id"]:
        raise ValueError("synthesis_output_id is required")
    if not row["style_profile_id"]:
        raise ValueError("style_profile_id is required")
    if not row["style_profile_version"]:
        raise ValueError("style_profile_version is required")
    if not row["positioning_snapshot_version"]:
        raise ValueError("positioning_snapshot_version is required")
    if row["writer_mode"] not in VALID_WRITER_MODES:
        raise ValueError(f"Invalid writer_mode: {row['writer_mode']}")
    if row["generation_status"] not in VALID_GENERATION_STATUSES:
        raise ValueError(f"Invalid generation_status: {row['generation_status']}")
    if not isinstance(row["rewrite_attempted"], bool):
        raise ValueError("rewrite_attempted must be a bool")


def validate_generation_status_transition(*, from_status: str, to_status: str) -> None:
    if from_status not in GENERATION_STATUS_TRANSITIONS:
        raise ValueError(f"Invalid generation_status: {from_status}")
    if to_status not in GENERATION_STATUS_TRANSITIONS:
        raise ValueError(f"Invalid generation_status: {to_status}")
    if to_status not in GENERATION_STATUS_TRANSITIONS[from_status]:
        raise ValueError(f"Invalid generation_status transition: {from_status} -> {to_status}")


def apply_final_writeback_status(*, row: dict, succeeded: bool) -> dict:
    row["generation_status"] = (
        GENERATION_STATUS_WRITEBACK_SUCCEEDED if succeeded else GENERATION_STATUS_WRITEBACK_FAILED
    )
    validate_hook_row(row)
    return row


def build_hook_output(*, row: dict, selected_hook: dict) -> dict:
    validate_hook_row(row)
    return {
        "schema_version": SCHEMA_VERSION,
        "stage": STAGE,
        "run_id": row["run_id"],
        "output_id": row["output_id"],
        "hook_id": row["hook_id"],
        "selected_hook": selected_hook,
        "hook_row": row,
    }

