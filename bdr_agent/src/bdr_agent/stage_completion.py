"""Shared stage-completion webhook helpers for BDR agent stages."""

from __future__ import annotations

import os
from typing import Any

from bdr_agent.stages.company_research.config import (
    STAGE_COMPLETION_HEADER_NAME,
    STAGE_COMPLETION_WEBHOOK_SECRET_ENV_VAR,
    STAGE_COMPLETION_WEBHOOK_URL_ENV_VAR,
)


def skipped_stage_completion(reason: str) -> dict:
    return {
        "status": "skipped",
        "reason": reason,
    }


def send_stage_completion_payload(
    *,
    payload: dict,
    webhook_url: str | None = None,
    webhook_secret: str | None = None,
    client: Any | None = None,
    timeout_seconds: float = 10.0,
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
