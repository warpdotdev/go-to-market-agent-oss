"""Company research orchestration."""

from __future__ import annotations

from datetime import datetime

from bdr_agent.stages.company_research.config import HYDRATION_HYDRATED
from bdr_agent.stages.company_research.hydration import (
    build_hydration_result,
    build_hydration_result_from_webhook_payload,
    fetch_hydration_row,
    merge_hydration_results,
)
from bdr_agent.stages.company_research.internal_metrics import (
    apply_tier_1_internal_metrics,
    fetch_tier_1_internal_metrics,
)
from bdr_agent.stages.company_research.public_research import (
    apply_tier_2_public_research,
    run_fresh_tier_2_public_research,
)
from bdr_agent.stages.company_research.reuse import apply_tier_2_reuse_lookup, find_reusable_tier_2_output
from bdr_agent.stages.company_research.schemas import build_minimal_company_research_output, utc_now_iso
from bdr_agent.stages.company_research.stage_completion import (
    send_stage_completion,
    skipped_stage_completion,
)
from bdr_agent.stages.company_research.storage import (
    mark_dry_run_storage,
    mark_not_persisted_storage,
    persist_company_research_result,
)

_UNSET = object()

HYDRATION_COMPLETE_STATUS = "hydration_complete"
RESEARCH_COMPLETE_STATUS = "research_complete"
TIER_1_ERROR_STATUS = "tier_1_error"
TIER_2_ERROR_STATUS = "tier_2_error"


def run_company_research(
    *,
    lead_id: str,
    trigger_source: str,
    source_system: str,
    hubspot_workflow_id: str,
    dry_run: bool = False,
    skip_bigquery: bool = False,
    webhook_payload: dict | None = None,
    hydration_row: object = _UNSET,
    tier_1_metrics_client: object | None = None,
    skip_tier_1_internal_metrics: bool = False,
    tier_2_reuse_client: object | None = None,
    skip_tier_2_reuse_lookup: bool = False,
    tier_2_public_research_client: object | None = None,
    skip_tier_2_public_research: bool = False,
    persist: bool = False,
    persistence_bigquery_client: object | None = None,
    persistence_storage_client: object | None = None,
    stage_completion_webhook_url: str | None = None,
    stage_completion_webhook_secret: str | None = None,
    stage_completion_client: object | None = None,
    send_stage_completion_on_success: bool = True,
) -> dict:
    started_at = utc_now_iso()
    if dry_run and persist:
        raise ValueError("dry_run and persist cannot both be true")

    try:
        if hydration_row is not _UNSET:
            hydration_result = build_hydration_result(hydration_row)
            context_source = "injected_hydration_row"
        else:
            webhook_hydration_result = build_hydration_result_from_webhook_payload(
                webhook_payload,
                fallback_lead_id=lead_id,
            )
            if webhook_hydration_result.hydration_status == HYDRATION_HYDRATED:
                hydration_result = webhook_hydration_result
                context_source = "webhook_payload"
            elif skip_bigquery:
                hydration_result = webhook_hydration_result
                context_source = "webhook_payload"
            else:
                fallback_row = fetch_hydration_row(lead_id=lead_id)
                fallback_hydration_result = build_hydration_result(fallback_row)
                hydration_result = merge_hydration_results(
                    primary=webhook_hydration_result,
                    fallback=fallback_hydration_result,
                )
                context_source = (
                    "webhook_payload_with_bigquery_fallback"
                    if fallback_row is not None
                    else "webhook_payload_bigquery_fallback_missing"
                )
    except Exception as exc:
        output = build_minimal_company_research_output(
            lead_id=lead_id,
            trigger_source=trigger_source,
            hydration_status="not_ready",
            missing_fields=["webhook_payload_error"],
        )
        _mark_storage_for_mode(output, dry_run=dry_run)
        return _complete_result_timing({
            "status": "error",
            "stage": output["stage"],
            "lead_id": lead_id,
            "source_system": source_system,
            "hubspot_workflow_id": hubspot_workflow_id,
            "dry_run": dry_run,
            "skip_bigquery": skip_bigquery,
            "run_id": output["run_id"],
            "output_id": output["output_id"],
            "output": output,
            "stage_completion": skipped_stage_completion("dry_run" if dry_run else "not_persisted"),
            "failure_reason": str(exc),
        }, started_at=started_at)
    output = build_minimal_company_research_output(
        lead_id=lead_id,
        trigger_source=trigger_source,
        hydration_status=hydration_result.hydration_status,
        missing_fields=hydration_result.missing_fields,
        resolved_company_domain=hydration_result.resolved_company_domain,
        resolved_company_domain_source=hydration_result.resolved_company_domain_source,
        lead=hydration_result.lead,
        contact=hydration_result.contact,
        company=hydration_result.company,
    )
    status = (
        HYDRATION_COMPLETE_STATUS
        if hydration_result.hydration_status == HYDRATION_HYDRATED
        else hydration_result.hydration_status
    )
    if status == HYDRATION_COMPLETE_STATUS and not skip_tier_1_internal_metrics:
        try:
            tier_1_result = fetch_tier_1_internal_metrics(
                resolved_company_domain=hydration_result.resolved_company_domain,
                client=tier_1_metrics_client,
            )
            apply_tier_1_internal_metrics(output, tier_1_result)
        except Exception as exc:
            output["tier_1_internal_metrics"].update(
                {
                    "status": "error",
                    "error": str(exc),
                }
            )
    if status == HYDRATION_COMPLETE_STATUS and not skip_tier_2_reuse_lookup:
        try:
            lookup = find_reusable_tier_2_output(
                current_domain=hydration_result.resolved_company_domain,
                client=tier_2_reuse_client,
            )
            apply_tier_2_reuse_lookup(output, lookup)
        except Exception as exc:
            output["reuse"]["non_reuse_reason"] = "tier_2_reuse_lookup_error"
            output["reuse"]["lookup_error"] = str(exc)
    if (
        status == HYDRATION_COMPLETE_STATUS
        and not skip_tier_2_public_research
        and output["reuse"]["reuse_status"] != "partial_reuse"
    ):
        tier_2_result = run_fresh_tier_2_public_research(
            resolved_company_domain=hydration_result.resolved_company_domain,
            company_name=hydration_result.company.get("company_name") if hydration_result.company else None,
            client=tier_2_public_research_client,
        )
        apply_tier_2_public_research(output, tier_2_result)
    if status == HYDRATION_COMPLETE_STATUS:
        status = _final_research_status(output)
    result = {
        "status": status,
        "stage": output["stage"],
        "lead_id": lead_id,
        "source_system": source_system,
        "hubspot_workflow_id": hubspot_workflow_id,
        "dry_run": dry_run,
        "skip_bigquery": skip_bigquery,
        "context_source": context_source,
        "run_id": output["run_id"],
        "output_id": output["output_id"],
        "output": output,
        "failure_reason": _failure_reason_for_status(
            status=status,
            hydration_status=hydration_result.hydration_status,
            output=output,
        ),
    }
    _complete_result_timing(result, started_at=started_at)
    if dry_run:
        mark_dry_run_storage(output)
        result["stage_completion"] = skipped_stage_completion("dry_run")
    elif persist:
        persist_company_research_result(
            result=result,
            client=persistence_bigquery_client,
            storage_client=persistence_storage_client,
        )
        if send_stage_completion_on_success and _should_send_stage_completion(status):
            result["stage_completion"] = send_stage_completion(
                result=result,
                webhook_url=stage_completion_webhook_url,
                webhook_secret=stage_completion_webhook_secret,
                client=stage_completion_client,
            )
        elif send_stage_completion_on_success:
            result["stage_completion"] = skipped_stage_completion(f"status_{status}")
        else:
            result["stage_completion"] = skipped_stage_completion("disabled")
    else:
        mark_not_persisted_storage(output)
        result["stage_completion"] = skipped_stage_completion("not_persisted")
    return result


def _mark_storage_for_mode(output: dict, *, dry_run: bool) -> None:
    if dry_run:
        mark_dry_run_storage(output)
    else:
        mark_not_persisted_storage(output)


def _final_research_status(output: dict) -> str:
    tier_1_status = output["tier_1_internal_metrics"].get("status")
    tier_2_status = output["tier_2_public_company_research"].get("status")
    tier_1_ran = tier_1_status != "not_run"
    tier_2_ran = tier_2_status != "not_run"
    if not tier_1_ran and not tier_2_ran:
        return HYDRATION_COMPLETE_STATUS
    if tier_1_status == "error":
        return TIER_1_ERROR_STATUS
    if tier_2_status == "error":
        return TIER_2_ERROR_STATUS
    return RESEARCH_COMPLETE_STATUS


def _should_send_stage_completion(status: str) -> bool:
    return status == RESEARCH_COMPLETE_STATUS


def _failure_reason_for_status(*, status: str, hydration_status: str, output: dict) -> str | None:
    if status in {HYDRATION_COMPLETE_STATUS, RESEARCH_COMPLETE_STATUS}:
        return None
    if status == TIER_1_ERROR_STATUS:
        return output["tier_1_internal_metrics"].get("error") or "Tier 1 internal metrics failed."
    if status == TIER_2_ERROR_STATUS:
        tier_2 = output["tier_2_public_company_research"]
        errors = tier_2.get("errors") or []
        if errors:
            return "; ".join(str(error) for error in errors)
        return "Tier 2 public company research failed."
    return f"Hydration ended with status {hydration_status}."


def _complete_result_timing(result: dict, *, started_at: str) -> dict:
    completed_at = utc_now_iso()
    result["started_at"] = started_at
    result["completed_at"] = completed_at
    result["duration_seconds"] = _duration_seconds(started_at, completed_at)
    return result


def _duration_seconds(started_at: str, completed_at: str) -> float | None:
    try:
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        completed = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    return round((completed - started).total_seconds(), 6)
