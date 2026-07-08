"""Deterministic runtime for the skill-authored lead brief stage."""

from __future__ import annotations

from datetime import datetime
import os
from typing import Any
from uuid import uuid4

from bdr_agent.outreach_writeback.config import (
    CREATED_AT_PROPERTY_NAME,
    HOOK_PROPERTY_NAME,
    HUBSPOT_CONTACT_OBJECT_TYPE,
    SOURCES_PROPERTY_NAME,
)
from bdr_agent.outreach_writeback.hubspot import normalize_hubspot_object_type, update_hook_properties
from bdr_agent.common.oz_metadata import runtime_oz_metadata
from bdr_agent.stages.outreach_composer.artifacts import (
    build_authenticated_gcs_url,
    build_lead_brief_gcs_uri,
    build_lead_brief_html_gcs_uri,
)
from bdr_agent.stages.outreach_composer.company_research import load_company_research_output
from bdr_agent.stages.outreach_composer.config import (
    DEFAULT_ARTIFACT_BASE_URI,
    DEFAULT_TRIGGER_SOURCE,
    DELIVERY_MODE_BOTH,
    DELIVERY_MODE_DRY_RUN,
    DELIVERY_MODE_ENV_CANDIDATES,
    DELIVERY_MODE_HUBSPOT,
    DELIVERY_MODE_SLACK,
    DRY_RUN_WRITEBACK_STATUS,
    HUBSPOT_WRITEBACK_ENV_VAR,
    NOT_ATTEMPTED_WRITEBACK_STATUS,
    SCHEMA_VERSION,
    STAGE,
    VALID_DELIVERY_MODES,
    resolve_persisted_stage,
    resolve_persisted_stage_mode,
)
from bdr_agent.stages.outreach_composer.slack import (
    SLACK_STATUS_FAILED,
    SLACK_STATUS_SKIPPED,
    post_lead_brief_review_notification,
    validate_lead_brief_review_notification_config,
)
from bdr_agent.stages.outreach_composer.storage import (
    build_email_body_hook_rows,
    build_output_index_row,
    build_run_metadata_row,
    claim_slack_delivery_marker,
    persist_lead_brief_result,
)
from bdr_agent.stages.outreach_composer.validation import normalize_lead_brief_packet
from bdr_agent.common.schemas import require_non_empty, utc_now_iso


def new_run_id() -> str:
    return f"bdr_run_{uuid4().hex}"


def new_output_id() -> str:
    return f"bdr_output_{uuid4().hex}"


def new_email_draft_id() -> str:
    return f"bdr_hook_{uuid4().hex}"


def run_lead_brief(
    *,
    lead_id: str,
    lead_brief_packet: dict,
    contact_id: str | None = None,
    company_id: str | None = None,
    resolved_company_domain: str | None = None,
    company_research_run_id: str | None = None,
    company_research_output_id: str | None = None,
    company_research_output: dict | None = None,
    company_research_bigquery_table: str | None = None,
    trigger_source: str = DEFAULT_TRIGGER_SOURCE,
    artifact_base_uri: str = DEFAULT_ARTIFACT_BASE_URI,
    persist_bigquery: bool = False,
    delivery_mode: str | None = None,
    allow_hubspot_writeback: bool = False,
    hubspot_object_type: str = HUBSPOT_CONTACT_OBJECT_TYPE,
    hubspot_object_id: str | None = None,
    hubspot_client: Any | None = None,
    hubspot_api_token: str | None = None,
    slack_client: Any | None = None,
    slack_token: str | None = None,
    slack_channel_id: str | None = None,
    hubspot_portal_id: str | None = None,
    bigquery_client: Any | None = None,
    gcs_client: Any | None = None,
    run_id: str | None = None,
    output_id: str | None = None,
    email_draft_ids: list[str] | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
    persisted_stage_mode: str | None = None,
) -> dict:
    started_at = started_at or utc_now_iso()
    completed_at = completed_at or utc_now_iso()
    run_id = run_id or new_run_id()
    output_id = output_id or new_output_id()
    email_draft_ids = email_draft_ids or [new_email_draft_id() for _ in range(3)]
    if len(email_draft_ids) != 3:
        raise ValueError("email_draft_ids must contain exactly three ids")
    lead_id = require_non_empty(lead_id, "lead_id")
    packet = normalize_lead_brief_packet(lead_brief_packet)
    effective_persisted_stage_mode = resolve_persisted_stage_mode(persisted_stage_mode)
    effective_stage = resolve_persisted_stage(effective_persisted_stage_mode)
    loaded_output = load_company_research_output(
        lead_id=lead_id,
        company_research_output_id=company_research_output_id,
        company_research_output=company_research_output,
        company_research_bigquery_table=company_research_bigquery_table,
        bigquery_client=bigquery_client,
    )
    contact = loaded_output.get("contact") or {}
    company = loaded_output.get("company") or {}
    hydration = loaded_output.get("hydration") or {}
    contact_id = contact_id or contact.get("contact_id")
    company_id = company_id or company.get("company_id")
    resolved_company_domain = resolved_company_domain or hydration.get("resolved_company_domain")
    company_research_run_id = company_research_run_id or loaded_output.get("run_id")
    company_research_output_id = company_research_output_id or loaded_output.get("output_id")
    lead_brief_gcs_uri = build_lead_brief_gcs_uri(
        run_id=run_id,
        output_id=output_id,
        artifact_base_uri=artifact_base_uri,
        stage=effective_stage,
    )
    lead_brief_html_gcs_uri = build_lead_brief_html_gcs_uri(
        run_id=run_id,
        output_id=output_id,
        artifact_base_uri=artifact_base_uri,
        stage=effective_stage,
    )
    lead_brief_url = build_authenticated_gcs_url(gcs_uri=lead_brief_html_gcs_uri)
    target_object_type = normalize_hubspot_object_type(hubspot_object_type)
    target_object_id = hubspot_object_id or _default_hubspot_object_id(
        hubspot_object_type=target_object_type,
        lead_id=lead_id,
        contact_id=contact_id,
    )
    normalized_delivery_mode = normalize_delivery_mode(delivery_mode or _env_delivery_mode())
    hubspot_writeback_requested = allow_hubspot_writeback or _env_allows_hubspot_writeback()
    effective_allow_hubspot_writeback = (
        normalized_delivery_mode in {DELIVERY_MODE_HUBSPOT, DELIVERY_MODE_BOTH}
        and hubspot_writeback_requested
    )
    selected_body = _selected_email_body(packet)
    writeback = _writeback_result(
        allow_write=effective_allow_hubspot_writeback,
        target_object_type=target_object_type,
        target_object_id=target_object_id,
        hook_text=selected_body,
        sources_url=lead_brief_url,
        hubspot_client=hubspot_client,
        hubspot_api_token=hubspot_api_token,
    )
    result = {
        "status": "completed",
        "stage": effective_stage,
        "runtime_stage": STAGE,
        "persisted_stage": effective_stage,
        "persisted_stage_mode": effective_persisted_stage_mode,
        "schema_version": SCHEMA_VERSION,
        "trigger_source": trigger_source,
        "lead_id": lead_id,
        "contact_id": contact_id,
        "company_id": company_id,
        "resolved_company_domain": resolved_company_domain,
        "company_research_run_id": company_research_run_id,
        "company_research_output_id": company_research_output_id,
        "dry_run": not persist_bigquery,
        "delivery_mode": normalized_delivery_mode,
        "hubspot_writeback_requested": hubspot_writeback_requested,
        "allow_hubspot_writeback": effective_allow_hubspot_writeback,
        "hubspot_object_type": target_object_type,
        "hubspot_object_id": target_object_id,
        "run_id": run_id,
        "output_id": output_id,
        "email_draft_ids": email_draft_ids,
        "selected_email_draft_id": email_draft_ids[0],
        "started_at": started_at,
        "completed_at": completed_at,
        "duration_seconds": _duration_seconds(started_at, completed_at),
        "lead_brief_gcs_uri": lead_brief_gcs_uri,
        "lead_brief_html_gcs_uri": lead_brief_html_gcs_uri,
        "lead_brief_url": lead_brief_url,
        "brief_markdown": packet["brief_markdown"],
        "email_body_drafts": packet["email_body_drafts"],
        "evaluation": packet["evaluation"],
        "rewrite": packet["rewrite"],
        "selected_email_body": selected_body,
        "hubspot_writeback": writeback,
        "slack_notification": _skipped_slack_notification(),
        "slack_delivery_marker": {"status": "not_requested"},
        "failure_reason": writeback.get("hubspot_writeback_error"),
        "oz_metadata": runtime_oz_metadata().as_bigquery_fields(),
        "bigquery_persistence": {"status": "not_requested"},
        "artifact_persistence": {"status": "not_requested"},
    }
    result["run_metadata_row"] = build_run_metadata_row(result=result)
    result["output_index_row"] = build_output_index_row(result=result)
    result["email_body_hook_rows"] = build_email_body_hook_rows(
        result=result,
        packet=packet,
        writeback=writeback,
    )
    if persist_bigquery:
        result["bigquery_persistence"] = persist_lead_brief_result(
            result=result,
            packet=packet,
            writeback=writeback,
            bigquery_client=bigquery_client,
            gcs_client=gcs_client,
        )
        result["artifact_persistence"] = {
            "status": "persisted",
            "lead_brief_gcs_uri": lead_brief_gcs_uri,
            "lead_brief_html_gcs_uri": lead_brief_html_gcs_uri,
            "lead_brief_url": lead_brief_url,
        }
    if normalized_delivery_mode in {DELIVERY_MODE_SLACK, DELIVERY_MODE_BOTH}:
        result["slack_notification"] = _deliver_slack_review_notification(
            result=result,
            company_research_output=loaded_output,
            persist_bigquery=persist_bigquery,
            bigquery_client=bigquery_client,
            slack_client=slack_client,
            slack_token=slack_token,
            slack_channel_id=slack_channel_id,
            hubspot_portal_id=hubspot_portal_id,
        )
    return result


def _selected_email_body(packet: dict) -> str:
    return next(draft["body"] for draft in packet["email_body_drafts"] if draft["rank"] == 1)


def normalize_delivery_mode(value: str | None) -> str:
    normalized = (value or "").strip().lower().replace("-", "_")
    if not normalized or normalized in {"none", "off"}:
        return DELIVERY_MODE_DRY_RUN
    if normalized in {"slack_and_hubspot", "slack+hubspot"}:
        return DELIVERY_MODE_BOTH
    if normalized not in VALID_DELIVERY_MODES:
        raise ValueError(
            "delivery_mode must be one of "
            f"{sorted(VALID_DELIVERY_MODES | {'slack-and-hubspot'})}; got {value!r}"
        )
    return normalized


def _writeback_result(
    *,
    allow_write: bool,
    target_object_type: str,
    target_object_id: str | None,
    hook_text: str,
    sources_url: str,
    hubspot_client: Any | None,
    hubspot_api_token: str | None,
) -> dict:
    if allow_write:
        return update_hook_properties(
            object_type=target_object_type,
            object_id=target_object_id,
            hook_text=hook_text,
            sources_url=sources_url,
            allow_write=True,
            client=hubspot_client,
            api_token=hubspot_api_token,
        )
    return {
        "hook_property": {
            "property_name": HOOK_PROPERTY_NAME,
            "status": DRY_RUN_WRITEBACK_STATUS,
            "attempted": False,
            "updated_at": None,
            "error": None,
        },
        "sources_property": {
            "property_name": SOURCES_PROPERTY_NAME,
            "status": DRY_RUN_WRITEBACK_STATUS if target_object_id else NOT_ATTEMPTED_WRITEBACK_STATUS,
            "attempted": False,
            "updated_at": None,
            "error": None,
        },
        "created_at_property": {
            "property_name": CREATED_AT_PROPERTY_NAME,
            "status": DRY_RUN_WRITEBACK_STATUS if target_object_id else NOT_ATTEMPTED_WRITEBACK_STATUS,
            "attempted": False,
            "updated_at": None,
            "error": None,
        },
        "hubspot_writeback_at": None,
        "hubspot_writeback_error": None,
    }


def _default_hubspot_object_id(*, hubspot_object_type: str, lead_id: str, contact_id: str | None) -> str | None:
    if hubspot_object_type == HUBSPOT_CONTACT_OBJECT_TYPE:
        return contact_id
    return lead_id


def _env_allows_hubspot_writeback() -> bool:
    return os.getenv(HUBSPOT_WRITEBACK_ENV_VAR, "").strip().lower() in {"1", "true", "yes"}


def _env_delivery_mode() -> str | None:
    for env_var in DELIVERY_MODE_ENV_CANDIDATES:
        value = os.getenv(env_var)
        if value:
            return value
    return None


def _skipped_slack_notification() -> dict:
    return {
        "status": SLACK_STATUS_SKIPPED,
        "attempted": False,
        "channel_id": None,
        "message_ts": None,
        "error": None,
        "hubspot_record_url": None,
        "rendered_top_email_body": None,
    }


def _deliver_slack_review_notification(
    *,
    result: dict,
    company_research_output: dict,
    persist_bigquery: bool,
    bigquery_client: Any | None,
    slack_client: Any | None,
    slack_token: str | None,
    slack_channel_id: str | None,
    hubspot_portal_id: str | None,
) -> dict:
    config = validate_lead_brief_review_notification_config(
        slack_client=slack_client,
        slack_token=slack_token,
        slack_channel_id=slack_channel_id,
    )
    if not config["configured"]:
        return post_lead_brief_review_notification(
            result=result,
            company_research_output=company_research_output,
            slack_client=slack_client,
            slack_token=slack_token,
            slack_channel_id=slack_channel_id,
            hubspot_portal_id=hubspot_portal_id,
        )
    if not persist_bigquery:
        return {
            **_skipped_slack_notification(),
            "channel_id": config["channel_id"],
            "reason": "slack_delivery_requires_persistence",
        }

    if persist_bigquery:
        try:
            result["slack_delivery_marker"] = claim_slack_delivery_marker(
                result=result,
                bigquery_client=bigquery_client,
            )
        except Exception as exc:
            return {
                **_skipped_slack_notification(),
                "status": SLACK_STATUS_FAILED,
                "attempted": False,
                "channel_id": config["channel_id"],
                "error": f"Slack delivery marker claim failed: {exc}",
            }

        if result["slack_delivery_marker"]["status"] == "duplicate":
            return {
                **_skipped_slack_notification(),
                "channel_id": config["channel_id"],
                "reason": "duplicate_delivery_marker",
                "idempotency_key": result["slack_delivery_marker"]["idempotency_key"],
            }

    return post_lead_brief_review_notification(
        result=result,
        company_research_output=company_research_output,
        slack_client=slack_client,
        slack_token=slack_token,
        slack_channel_id=slack_channel_id,
        hubspot_portal_id=hubspot_portal_id,
    )


def _duration_seconds(started_at: str, completed_at: str) -> float | None:
    try:
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        completed = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    return round((completed - started).total_seconds(), 6)
