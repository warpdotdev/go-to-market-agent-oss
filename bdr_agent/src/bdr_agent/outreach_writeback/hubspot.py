"""BDR-local HubSpot writeback helper for hook properties."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
import os
from typing import Any
import urllib.error
import urllib.request

from bdr_agent.outreach_writeback.config import (
    CREATED_AT_PROPERTY_NAME,
    HUBSPOT_BASE_URL,
    HUBSPOT_CONTACT_OBJECT_TYPE,
    HUBSPOT_LEAD_OBJECT_TYPE,
    HUBSPOT_TOKEN_ENV_CANDIDATES,
    HOOK_PROPERTY_NAME,
    SOURCES_PROPERTY_NAME,
    WRITEBACK_STATUS_FAILED,
    WRITEBACK_STATUS_NOT_ATTEMPTED,
    WRITEBACK_STATUS_SKIPPED_DRY_RUN,
    WRITEBACK_STATUS_SUCCEEDED,
)
from bdr_agent.outreach_writeback.schemas import utc_now_iso


@dataclass(frozen=True)
class PropertyWritebackResult:
    property_name: str
    status: str
    attempted: bool
    updated_at: str | None = None
    error: str | None = None


def is_hubspot_token_configured() -> bool:
    return any(bool(os.getenv(env_var)) for env_var in HUBSPOT_TOKEN_ENV_CANDIDATES)


def update_hook_properties(
    *,
    object_type: str,
    object_id: str | None,
    hook_text: str,
    sources_url: str | None,
    allow_write: bool = False,
    client: Any | None = None,
    api_token: str | None = None,
    timeout_seconds: float = 20.0,
) -> dict:
    """Update HubSpot hook/source properties, guarded by explicit allow_write."""
    if not allow_write:
        return _summary(
            hook_result=PropertyWritebackResult(
                property_name=HOOK_PROPERTY_NAME,
                status=WRITEBACK_STATUS_SKIPPED_DRY_RUN,
                attempted=False,
            ),
            sources_result=PropertyWritebackResult(
                property_name=SOURCES_PROPERTY_NAME,
                status=WRITEBACK_STATUS_SKIPPED_DRY_RUN,
                attempted=False,
            ),
            created_at_result=PropertyWritebackResult(
                property_name=CREATED_AT_PROPERTY_NAME,
                status=WRITEBACK_STATUS_SKIPPED_DRY_RUN,
                attempted=False,
            ),
        )

    normalized_object_type = normalize_hubspot_object_type(object_type)
    if not object_id:
        error = "HubSpot object id is required for live writeback."
        return _summary(
            hook_result=PropertyWritebackResult(
                property_name=HOOK_PROPERTY_NAME,
                status=WRITEBACK_STATUS_FAILED,
                attempted=False,
                error=error,
            ),
            sources_result=PropertyWritebackResult(
                property_name=SOURCES_PROPERTY_NAME,
                status=WRITEBACK_STATUS_NOT_ATTEMPTED,
                attempted=False,
                error=error,
            ),
            created_at_result=PropertyWritebackResult(
                property_name=CREATED_AT_PROPERTY_NAME,
                status=WRITEBACK_STATUS_NOT_ATTEMPTED,
                attempted=False,
                error=error,
            ),
        )

    hook_result = _update_single_property(
        object_type=normalized_object_type,
        object_id=object_id,
        property_name=HOOK_PROPERTY_NAME,
        property_value=hook_text,
        client=client,
        api_token=api_token,
        timeout_seconds=timeout_seconds,
    )
    sources_result = _update_single_property(
        object_type=normalized_object_type,
        object_id=object_id,
        property_name=SOURCES_PROPERTY_NAME,
        property_value=sources_url or "",
        client=client,
        api_token=api_token,
        timeout_seconds=timeout_seconds,
    )
    created_at_result = _update_single_property(
        object_type=normalized_object_type,
        object_id=object_id,
        property_name=CREATED_AT_PROPERTY_NAME,
        property_value=_hubspot_timestamp_ms(),
        client=client,
        api_token=api_token,
        timeout_seconds=timeout_seconds,
    )
    return _summary(
        hook_result=hook_result,
        sources_result=sources_result,
        created_at_result=created_at_result,
    )


def normalize_hubspot_object_type(object_type: str) -> str:
    normalized = (object_type or "").strip().lower()
    if normalized in {"contact", "contacts", "0-1"}:
        return HUBSPOT_CONTACT_OBJECT_TYPE
    if normalized in {"lead", "leads", "0-136"}:
        return HUBSPOT_LEAD_OBJECT_TYPE
    return object_type


def _update_single_property(
    *,
    object_type: str,
    object_id: str,
    property_name: str,
    property_value: str,
    client: Any | None,
    api_token: str | None,
    timeout_seconds: float,
) -> PropertyWritebackResult:
    try:
        _send_property_update(
            object_type=object_type,
            object_id=object_id,
            property_name=property_name,
            property_value=property_value,
            client=client,
            api_token=api_token,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:
        return PropertyWritebackResult(
            property_name=property_name,
            status=WRITEBACK_STATUS_FAILED,
            attempted=True,
            error=sanitize_error(exc, explicit_secret=api_token),
        )
    return PropertyWritebackResult(
        property_name=property_name,
        status=WRITEBACK_STATUS_SUCCEEDED,
        attempted=True,
        updated_at=utc_now_iso(),
    )


def _send_property_update(
    *,
    object_type: str,
    object_id: str,
    property_name: str,
    property_value: str,
    client: Any | None,
    api_token: str | None,
    timeout_seconds: float,
) -> None:
    properties = {property_name: property_value}
    if client is not None:
        if hasattr(client, "update_property"):
            client.update_property(object_type, object_id, property_name, property_value)
            return
        if hasattr(client, "update_properties"):
            client.update_properties(object_type, object_id, properties)
            return
        if hasattr(client, "patch"):
            client.patch(
                f"/crm/v3/objects/{object_type}/{object_id}",
                json={"properties": properties},
                timeout=timeout_seconds,
            )
            return
        raise TypeError("Injected HubSpot client must expose update_property, update_properties, or patch.")

    token = api_token or _load_hubspot_token()
    url = f"{HUBSPOT_BASE_URL}/crm/v3/objects/{object_type}/{object_id}"
    body = json.dumps({"properties": properties}).encode()
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="PATCH",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        response.read()


def _load_hubspot_token() -> str:
    for env_var in HUBSPOT_TOKEN_ENV_CANDIDATES:
        value = os.getenv(env_var)
        if value:
            return value
    raise RuntimeError("HubSpot API token is not configured.")


def _hubspot_timestamp_ms() -> str:
    return str(int(datetime.now(UTC).timestamp() * 1000))


def sanitize_error(exc: Exception, explicit_secret: str | None = None) -> str:
    message = str(exc)
    secrets = [explicit_secret] if explicit_secret else []
    for env_var in HUBSPOT_TOKEN_ENV_CANDIDATES:
        env_value = os.getenv(env_var)
        if env_value:
            secrets.append(env_value)
    for secret in secrets:
        if secret:
            message = message.replace(secret, "[redacted]")
    if isinstance(exc, urllib.error.HTTPError):
        message = f"HubSpot HTTP {exc.code}"
    return message


def _summary(
    *,
    hook_result: PropertyWritebackResult,
    sources_result: PropertyWritebackResult,
    created_at_result: PropertyWritebackResult,
) -> dict:
    errors = [
        result.error
        for result in (hook_result, sources_result, created_at_result)
        if result.error and result.status == WRITEBACK_STATUS_FAILED
    ]
    attempted_at = (
        datetime.now(UTC).isoformat()
        if hook_result.attempted or sources_result.attempted or created_at_result.attempted
        else None
    )
    return {
        "hook_property": asdict(hook_result),
        "sources_property": asdict(sources_result),
        "created_at_property": asdict(created_at_result),
        "hubspot_writeback_at": attempted_at,
        "hubspot_writeback_error": "; ".join(errors) if errors else None,
    }

