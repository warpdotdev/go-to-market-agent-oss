"""Stage-completion handoff helpers for company research."""

from __future__ import annotations

import os
from typing import Any

from bdr_agent.stages.company_research.config import (
    STAGE,
    STAGE_COMPLETION_HEADER_NAME,
    STAGE_COMPLETION_WEBHOOK_SECRET_ENV_VAR,
    STAGE_COMPLETION_WEBHOOK_URL_ENV_VAR,
)

WORKFLOW = "bdr_agent"
CANONICAL_NEXT_STAGE = "outreach_composer"
LEGACY_NEXT_STAGE = "lead_brief"
NEXT_STAGE = LEGACY_NEXT_STAGE
ACCEPTED_NEXT_STAGE_ALIASES = (
    CANONICAL_NEXT_STAGE,
    LEGACY_NEXT_STAGE,
)
COMPLETED_STATUS = "completed"


def normalize_next_stage_contract(value: str | None) -> str:
    normalized = (value or "").strip().lower().replace("-", "_")
    if normalized in {"", LEGACY_NEXT_STAGE}:
        return LEGACY_NEXT_STAGE
    if normalized == CANONICAL_NEXT_STAGE:
        return CANONICAL_NEXT_STAGE
    raise ValueError(
        "next_stage must be one of "
        f"{sorted(ACCEPTED_NEXT_STAGE_ALIASES)}; got {value!r}"
    )


def build_stage_completion_payload(*, result: dict, next_stage: str | None = None) -> dict:
    output = result["output"]
    storage = output.get("storage") or {}
    effective_next_stage = normalize_next_stage_contract(next_stage or NEXT_STAGE)
    payload = {
        "workflow": WORKFLOW,
        "source_stage": STAGE,
        "next_stage": effective_next_stage,
        "lead_id": result["lead_id"],
        "run_id": result["run_id"],
        "output_id": result["output_id"],
        "status": COMPLETED_STATUS,
        "idempotency_key": (
            f"{STAGE}:{result['run_id']}:{result['output_id']}:{effective_next_stage}"
        ),
    }
    optional_values = {
        "contact_id": output.get("contact", {}).get("contact_id"),
        "company_id": output.get("company", {}).get("company_id"),
        "resolved_company_domain": output.get("hydration", {}).get("resolved_company_domain"),
        "company_research_run_id": result["run_id"],
        "company_research_output_id": result["output_id"],
        "company_research_gcs_uri": storage.get("gcs_uri"),
        "gcs_uri": storage.get("gcs_uri"),
        "bigquery_table": storage.get("bigquery_table"),
        "bigquery_row_id": storage.get("bigquery_row_id"),
    }
    payload.update({key: value for key, value in optional_values.items() if value is not None})
    return payload


def skipped_stage_completion(reason: str) -> dict:
    return {
        "status": "skipped",
        "reason": reason,
    }


def send_stage_completion(
    *,
    result: dict,
    webhook_url: str | None = None,
    webhook_secret: str | None = None,
    client: Any | None = None,
    timeout_seconds: float = 90.0,
) -> dict:
    effective_url = webhook_url if webhook_url is not None else os.getenv(STAGE_COMPLETION_WEBHOOK_URL_ENV_VAR)
    if not effective_url:
        return skipped_stage_completion("webhook_url_not_configured")

    effective_secret = (
        webhook_secret
        if webhook_secret is not None
        else os.getenv(STAGE_COMPLETION_WEBHOOK_SECRET_ENV_VAR)
    )
    if not effective_secret:
        return skipped_stage_completion("webhook_secret_not_configured")

    payload = build_stage_completion_payload(result=result)
    headers = {
        STAGE_COMPLETION_HEADER_NAME: effective_secret,
        "Content-Type": "application/json",
    }
    try:
        response = _post_json(
            url=effective_url,
            payload=payload,
            headers=headers,
            client=client,
            timeout_seconds=timeout_seconds,
        )
        status_code = getattr(response, "status_code", None)
        return {
            "status": "sent",
            "webhook_url": effective_url,
            "http_status": status_code,
            "idempotency_key": payload["idempotency_key"],
        }
    except Exception as exc:
        return {
            "status": "failed",
            "webhook_url": effective_url,
            "error": str(exc),
            "idempotency_key": payload["idempotency_key"],
        }


def _post_json(
    *,
    url: str,
    payload: dict,
    headers: dict,
    client: Any | None,
    timeout_seconds: float,
) -> Any:
    if client is not None:
        response = client.post(url, headers=headers, json=payload, timeout=timeout_seconds)
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        return response

    try:
        import httpx
    except ModuleNotFoundError as exc:
        raise RuntimeError("httpx is required for live stage-completion handoff.") from exc

    with httpx.Client(timeout=timeout_seconds) as httpx_client:
        response = httpx_client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return response
