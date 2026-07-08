"""Hook candidate generation with a legacy explicit writeback path."""

from __future__ import annotations

from typing import Any

from bdr_agent.stages.company_research.config import GCP_PROJECT_ID
from bdr_agent.outreach_writeback.artifacts import (
    build_candidate_hook_artifact_uri,
    build_evaluate_input_artifact_uri,
    write_json_to_gcs,
)
from bdr_agent.outreach_writeback.config import (
    CREATED_AT_PROPERTY_NAME,
    GENERATION_STATUS_WRITEBACK_FAILED,
    GENERATION_STATUS_WRITEBACK_SUCCEEDED,
    HOOK_PROPERTY_NAME,
    HOOK_STATUS_CANDIDATE_GENERATED,
    HOOK_STATUS_GENERATED,
    HOOK_STATUS_WRITEBACK_FAILED,
    HOOK_STATUS_WRITEBACK_PARTIAL,
    HOOK_STATUS_WRITEBACK_SUCCEEDED,
    HUBSPOT_CONTACT_OBJECT_TYPE,
    SOURCES_PROPERTY_NAME,
    WRITEBACK_STATUS_FAILED,
    WRITEBACK_STATUS_NOT_ATTEMPTED,
    WRITEBACK_STATUS_SUCCEEDED,
)
from bdr_agent.outreach_writeback.contracts import (
    build_candidate_hook_artifact,
    build_evaluate_hook_input_artifact,
)
from bdr_agent.outreach_writeback.hubspot import (
    is_hubspot_token_configured,
    normalize_hubspot_object_type,
    update_hook_properties,
)
from bdr_agent.outreach_writeback.schemas import build_hook_output, build_hook_row, validate_hook_row
from bdr_agent.outreach_writeback.selector import select_hook
from bdr_agent.outreach_writeback.stage_completion import (
    send_stage_completion,
    skipped_stage_completion,
)
from bdr_agent.outreach_writeback.storage import persist_hook_result
from bdr_agent.outreach_writeback.style_profiles import resolve_style_profile


def run_outreach_writeback(
    *,
    lead_id: str,
    contact_id: str | None,
    company_id: str | None,
    resolved_company_domain: str | None,
    company_research_output_id: str | None,
    synthesis_run_id: str | None,
    synthesis_output_id: str,
    synthesis_gcs_uri: str,
    synthesis_brief: str | None = None,
    evidence_packet: dict | None = None,
    company_research: dict | None = None,
    person_research: dict | None = None,
    hubspot_owner_id: str | None = None,
    ai_hook_sources_url: str | None = None,
    trigger_source: str | None = None,
    allow_writes: bool = False,
    hubspot_object_type: str = HUBSPOT_CONTACT_OBJECT_TYPE,
    hubspot_object_id: str | None = None,
    hubspot_client: Any | None = None,
    hubspot_api_token: str | None = None,
    persist_bigquery: bool = False,
    bigquery_client: Any | None = None,
    gcs_client: Any | None = None,
    persist_candidate_artifacts: bool | None = None,
    stage_completion_webhook_url: str | None = None,
    stage_completion_webhook_secret: str | None = None,
    stage_completion_client: Any | None = None,
    send_stage_completion_on_success: bool = True,
) -> dict:
    selected_hook = select_hook(
        synthesis_brief=synthesis_brief,
        evidence_packet=evidence_packet,
        company_research=company_research,
        person_research=person_research,
        resolved_company_domain=resolved_company_domain,
    )
    lint_result = _candidate_prevalidation_result(selected_hook=selected_hook)
    style_profile = resolve_style_profile(
        hubspot_owner_id=hubspot_owner_id or _packet_hubspot_owner_id(evidence_packet),
        company_research=company_research,
    )
    row = build_hook_row(
        selected_hook=selected_hook,
        lead_id=lead_id,
        contact_id=contact_id,
        company_id=company_id,
        resolved_company_domain=resolved_company_domain,
        company_research_output_id=company_research_output_id,
        synthesis_run_id=synthesis_run_id,
        synthesis_output_id=synthesis_output_id,
        synthesis_gcs_uri=synthesis_gcs_uri,
        ai_hook_sources_url=ai_hook_sources_url,
        style_profile_id=style_profile.style_profile_id,
        style_profile_version=style_profile.style_profile_version,
        style_profile_fallback_reason=style_profile.fallback_reason,
        positioning_pillar=selected_hook["positioning_pillar"],
        positioning_value_prop=selected_hook["positioning_value_prop"],
        hook_status=HOOK_STATUS_GENERATED if allow_writes else HOOK_STATUS_CANDIDATE_GENERATED,
        hubspot_outreach_writeback_status=WRITEBACK_STATUS_NOT_ATTEMPTED,
        hubspot_sources_writeback_status=WRITEBACK_STATUS_NOT_ATTEMPTED,
        lint_result_json=lint_result,
    )

    target_object_type = normalize_hubspot_object_type(hubspot_object_type)
    target_object_id = hubspot_object_id or _default_hubspot_object_id(
        hubspot_object_type=target_object_type,
        lead_id=lead_id,
        contact_id=contact_id,
    )
    candidate_hook_artifact_ref = build_candidate_hook_artifact_uri(
        run_id=row["run_id"],
        output_id=row["output_id"],
    )
    candidate_hook_artifact = build_candidate_hook_artifact(hook_row=row)
    evaluate_and_writeback_input_artifact_ref = build_evaluate_input_artifact_uri(
        run_id=row["run_id"],
        output_id=row["output_id"],
    )
    evaluate_and_writeback_input_artifact = build_evaluate_hook_input_artifact(
        hook_row=row,
        candidate_hook_artifact_ref=candidate_hook_artifact_ref,
        target_hubspot_object_type=target_object_type,
        target_hubspot_object_id=target_object_id or "",
    )
    if allow_writes:
        writeback = update_hook_properties(
            object_type=target_object_type,
            object_id=target_object_id,
            hook_text=row["hook_text"],
            sources_url=row["ai_hook_sources_url"],
            allow_write=True,
            client=hubspot_client,
            api_token=hubspot_api_token,
        )
        _apply_writeback_to_row(row=row, writeback=writeback, allow_writes=allow_writes)
    else:
        writeback = _candidate_only_writeback()

    result = {
        "status": row["hook_status"],
        "stage": "outreach_writeback",
        "trigger_source": trigger_source,
        "lead_id": lead_id,
        "contact_id": contact_id,
        "company_id": company_id,
        "resolved_company_domain": resolved_company_domain,
        "company_research_output_id": company_research_output_id,
        "synthesis_run_id": synthesis_run_id,
        "synthesis_output_id": synthesis_output_id,
        "synthesis_gcs_uri": synthesis_gcs_uri,
        "run_id": row["run_id"],
        "output_id": row["output_id"],
        "hook_id": row["hook_id"],
        "candidate_only": not allow_writes,
        "dry_run": not allow_writes,
        "allow_writes": allow_writes,
        "evidence_packet_used": evidence_packet is not None,
        "hubspot_object_type": target_object_type,
        "hubspot_object_id": target_object_id,
        "hubspot_token_configured": bool(hubspot_api_token) or is_hubspot_token_configured(),
        "hubspot_writeback": writeback,
        "style_profile": style_profile.as_metadata(),
        "selected_hook": selected_hook,
        "candidate_hook_artifact_ref": candidate_hook_artifact_ref,
        "candidate_hook_artifact_uri": candidate_hook_artifact_ref,
        "candidate_hook_artifact": candidate_hook_artifact,
        "evaluate_and_writeback_input_artifact_ref": evaluate_and_writeback_input_artifact_ref,
        "evaluate_and_writeback_input_artifact_uri": evaluate_and_writeback_input_artifact_ref,
        "evaluate_and_writeback_input_artifact": evaluate_and_writeback_input_artifact,
        "stage_completion": {
            "status": "skipped",
            "reason": "candidate_not_persisted",
            "next_stage": "evaluate_and_writeback",
            "candidate_hook_artifact_ref": candidate_hook_artifact_ref,
            "candidate_generation_idempotency_key": row["candidate_generation_idempotency_key"],
        },
        "hook_row": row,
        "output": build_hook_output(row=row, selected_hook=selected_hook),
        "failure_reason": row.get("hubspot_writeback_error"),
        "artifact_persistence": {"status": "not_requested"},
        "bigquery_persistence": {"status": "not_requested"},
    }

    if persist_bigquery:
        if persist_candidate_artifacts is None:
            persist_candidate_artifacts = True
        if persist_candidate_artifacts:
            _persist_candidate_artifacts(result=result, client=gcs_client)
        else:
            result["artifact_persistence"] = skipped_stage_completion(
                "candidate_artifact_persistence_disabled"
            )
        if bigquery_client is None:
            from google.cloud import bigquery

            bigquery_client = bigquery.Client(project=GCP_PROJECT_ID)
        result["bigquery_persistence"] = persist_hook_result(
            result=result,
            client=bigquery_client,
            gcs_uri=candidate_hook_artifact_ref,
        )
        if not persist_candidate_artifacts:
            result["stage_completion"] = skipped_stage_completion("candidate_artifacts_not_persisted")
        elif send_stage_completion_on_success:
            result["stage_completion"] = send_stage_completion(
                result=result,
                webhook_url=stage_completion_webhook_url,
                webhook_secret=stage_completion_webhook_secret,
                client=stage_completion_client,
            )
        else:
            result["stage_completion"] = skipped_stage_completion("disabled")
    return result


def _default_hubspot_object_id(*, hubspot_object_type: str, lead_id: str, contact_id: str | None) -> str | None:
    if hubspot_object_type == HUBSPOT_CONTACT_OBJECT_TYPE:
        return contact_id
    return lead_id


def _apply_writeback_to_row(*, row: dict, writeback: dict, allow_writes: bool) -> None:
    hook_status = writeback["hook_property"]["status"]
    sources_status = writeback["sources_property"]["status"]
    created_at_status = writeback.get("created_at_property", {}).get("status")
    row["hubspot_outreach_writeback_status"] = hook_status
    row["hubspot_sources_writeback_status"] = sources_status
    row["hubspot_writeback_at"] = writeback.get("hubspot_writeback_at")
    row["hubspot_writeback_error"] = writeback.get("hubspot_writeback_error")
    if allow_writes:
        if hook_status == sources_status == created_at_status == WRITEBACK_STATUS_SUCCEEDED:
            row["hook_status"] = HOOK_STATUS_WRITEBACK_SUCCEEDED
            row["generation_status"] = GENERATION_STATUS_WRITEBACK_SUCCEEDED
        elif (
            hook_status == WRITEBACK_STATUS_FAILED
            or sources_status == WRITEBACK_STATUS_FAILED
            or created_at_status == WRITEBACK_STATUS_FAILED
        ):
            row["hook_status"] = HOOK_STATUS_WRITEBACK_FAILED
            row["generation_status"] = GENERATION_STATUS_WRITEBACK_FAILED
        elif (
            hook_status == WRITEBACK_STATUS_SUCCEEDED
            or sources_status == WRITEBACK_STATUS_SUCCEEDED
            or created_at_status == WRITEBACK_STATUS_SUCCEEDED
        ):
            row["hook_status"] = HOOK_STATUS_WRITEBACK_PARTIAL
        else:
            row["hook_status"] = HOOK_STATUS_WRITEBACK_FAILED
            row["generation_status"] = GENERATION_STATUS_WRITEBACK_FAILED
    row["updated_at"] = row["hubspot_writeback_at"] or row["updated_at"]
    validate_hook_row(row)


def _candidate_only_writeback() -> dict:
    return {
        "hook_property": {
            "property_name": HOOK_PROPERTY_NAME,
            "status": WRITEBACK_STATUS_NOT_ATTEMPTED,
            "attempted": False,
            "updated_at": None,
            "error": None,
        },
        "sources_property": {
            "property_name": SOURCES_PROPERTY_NAME,
            "status": WRITEBACK_STATUS_NOT_ATTEMPTED,
            "attempted": False,
            "updated_at": None,
            "error": None,
        },
        "created_at_property": {
            "property_name": CREATED_AT_PROPERTY_NAME,
            "status": WRITEBACK_STATUS_NOT_ATTEMPTED,
            "attempted": False,
            "updated_at": None,
            "error": None,
        },
        "hubspot_writeback_at": None,
        "hubspot_writeback_error": None,
    }


def _candidate_prevalidation_result(*, selected_hook: dict) -> dict:
    hook_text = selected_hook.get("hook_text") or ""
    failures = []
    checks = {
        "exactly_one_candidate": True,
        "hook_text_present": bool(hook_text.strip()),
        "hook_text_within_500_chars": len(hook_text) <= 500,
        "single_line_hook_text": "\n" not in hook_text and "\r" not in hook_text,
        "source_labels_present": bool(selected_hook.get("source_labels")),
        "positioning_pillar_present": bool(selected_hook.get("positioning_pillar")),
        "positioning_value_prop_present": bool(selected_hook.get("positioning_value_prop")),
    }
    failures.extend(check for check, passed in checks.items() if not passed)
    result = {
        "status": "passed" if not failures else "failed",
        "checks": checks,
        "failures": failures,
    }
    if failures:
        raise ValueError(f"Candidate hook pre-validation failed: {failures}")
    return result


def _persist_candidate_artifacts(*, result: dict, client: Any | None = None) -> None:
    write_json_to_gcs(
        gcs_uri=result["candidate_hook_artifact_uri"],
        artifact=result["candidate_hook_artifact"],
        client=client,
    )
    write_json_to_gcs(
        gcs_uri=result["evaluate_and_writeback_input_artifact_uri"],
        artifact=result["evaluate_and_writeback_input_artifact"],
        client=client,
    )
    result["artifact_persistence"] = {
        "status": "persisted",
        "candidate_hook_artifact_uri": result["candidate_hook_artifact_uri"],
        "evaluate_and_writeback_input_artifact_uri": result["evaluate_and_writeback_input_artifact_uri"],
    }


def _packet_hubspot_owner_id(evidence_packet: dict | None) -> str | None:
    if not evidence_packet:
        return None
    hubspot_owner_id = evidence_packet.get("hubspot_owner_id")
    if hubspot_owner_id is None or str(hubspot_owner_id).strip().lower() == "unknown":
        return None
    return str(hubspot_owner_id).strip()

