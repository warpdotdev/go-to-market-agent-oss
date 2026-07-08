"""Slack review notification helper for lead brief test delivery."""

from __future__ import annotations

import json
import os
import re
from typing import Any
import urllib.error
import urllib.request

from bdr_agent.outreach_writeback.config import HUBSPOT_CONTACT_OBJECT_TYPE
from bdr_agent.stages.outreach_composer.artifacts import build_authenticated_gcs_url
from bdr_agent.stages.outreach_composer.config import (
    HUBSPOT_PORTAL_ID_ENV_VAR,
    SLACK_BOT_TOKEN_ENV_CANDIDATES,
    SLACK_CHANNEL_ENV_CANDIDATES,
)

SLACK_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"
SLACK_STATUS_SKIPPED = "skipped"
SLACK_STATUS_NOT_CONFIGURED = "not_configured"
SLACK_STATUS_SUCCEEDED = "succeeded"
SLACK_STATUS_FAILED = "failed"
DEFAULT_SIGNOFF = "Best,"
SLACK_REVIEW_HEADER = "Lead draft email ready for feedback"
DISPLAY_INITIALISMS = {"ai", "api", "bdr", "cto", "gcp", "it", "mcp", "vp"}

def validate_lead_brief_review_notification_config(
    *,
    slack_client: Any | None = None,
    slack_token: str | None = None,
    slack_channel_id: str | None = None,
) -> dict:
    """Return whether Slack delivery has enough configuration to attempt a post."""
    channel_id = slack_channel_id or _load_slack_channel_id()
    token = slack_token or _load_slack_token()
    if not channel_id:
        return {
            "configured": False,
            "channel_id": channel_id,
            "error": f"One of {', '.join(SLACK_CHANNEL_ENV_CANDIDATES)} is not configured.",
        }
    if not token and slack_client is None:
        return {
            "configured": False,
            "channel_id": channel_id,
            "error": "Slack bot token is not configured.",
        }
    return {
        "configured": True,
        "channel_id": channel_id,
        "error": None,
    }


def post_lead_brief_review_notification(
    *,
    result: dict,
    company_research_output: dict,
    slack_client: Any | None = None,
    slack_token: str | None = None,
    slack_channel_id: str | None = None,
    hubspot_portal_id: str | None = None,
    timeout_seconds: float = 20.0,
) -> dict:
    """Post a one-way lead brief review notification to Slack."""
    channel_id = slack_channel_id or _load_slack_channel_id()
    token = slack_token or _load_slack_token()
    rendered_email_body = render_top_email_body_for_review(
        email_body=result["selected_email_body"],
        contact=company_research_output.get("contact") or {},
    )
    hubspot_record_url = build_hubspot_record_url(
        portal_id=hubspot_portal_id or os.getenv(HUBSPOT_PORTAL_ID_ENV_VAR),
        object_type=result.get("hubspot_object_type"),
        object_id=result.get("hubspot_object_id"),
    )
    base_summary = {
        "status": SLACK_STATUS_SKIPPED,
        "attempted": False,
        "channel_id": channel_id,
        "message_ts": None,
        "error": None,
        "hubspot_record_url": hubspot_record_url,
        "rendered_top_email_body": rendered_email_body,
    }
    if not channel_id:
        return {
            **base_summary,
            "status": SLACK_STATUS_NOT_CONFIGURED,
            "error": f"One of {', '.join(SLACK_CHANNEL_ENV_CANDIDATES)} is not configured.",
        }
    if not token and slack_client is None:
        return {
            **base_summary,
            "status": SLACK_STATUS_NOT_CONFIGURED,
            "error": "Slack bot token is not configured.",
        }

    payload = build_lead_brief_slack_payload(
        channel_id=channel_id,
        result=result,
        company_research_output=company_research_output,
        rendered_email_body=rendered_email_body,
        hubspot_record_url=hubspot_record_url,
    )
    try:
        response = _send_slack_post_message(
            payload=payload,
            token=token,
            slack_client=slack_client,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:
        return {
            **base_summary,
            "status": SLACK_STATUS_FAILED,
            "attempted": True,
            "error": sanitize_slack_error(exc, explicit_secret=token),
        }
    return {
        **base_summary,
        "status": SLACK_STATUS_SUCCEEDED,
        "attempted": True,
        "message_ts": response.get("ts"),
    }


def build_lead_brief_slack_payload(
    *,
    channel_id: str,
    result: dict,
    company_research_output: dict,
    rendered_email_body: str,
    hubspot_record_url: str | None,
) -> dict:
    contact = company_research_output.get("contact") or {}
    company = company_research_output.get("company") or {}
    lead_name = _full_name(contact) or result["lead_id"]
    company_name = _safe_title_case_text(
        company.get("company_name") or result.get("resolved_company_domain") or "Unknown company",
        preserve_initialisms=True,
    )
    title = _safe_title_case_text(contact.get("job_title"), preserve_initialisms=True)
    lead_identity = (
        f"<{hubspot_record_url}|{_escape_slack_link_label(lead_name)}>"
        if hubspot_record_url
        else _escape_slack_text(lead_name)
    )
    identity_parts = [lead_identity]
    if title:
        identity_parts.append(_escape_slack_text(title))
    if company_name:
        identity_parts.append(_escape_slack_text(company_name))
    identity_line = " | ".join(identity_parts)
    email_block = _truncate_for_slack_code_block(rendered_email_body)
    return {
        "channel": channel_id,
        "text": f"{SLACK_REVIEW_HEADER}: {identity_line}",
        "unfurl_links": False,
        "unfurl_media": False,
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{SLACK_REVIEW_HEADER}*\n{identity_line}",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Research brief:* {_research_brief_link(result)}",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Top email body preview:*\n```{email_block}```",
                },
            },
        ],
    }


def render_top_email_body_for_review(*, email_body: str, contact: dict) -> str:
    first_name = _safe_title_case_text(contact.get("first_name"))
    greeting = f"Hi {first_name}," if first_name else "Hi,"
    return f"{greeting}\n\n{email_body.strip()}\n\n{DEFAULT_SIGNOFF}"


def build_hubspot_record_url(
    *,
    portal_id: str | None,
    object_type: str | None,
    object_id: str | None,
) -> str | None:
    if not portal_id or not object_id:
        return None
    object_path = "0-1" if object_type == HUBSPOT_CONTACT_OBJECT_TYPE else object_type
    if not object_path:
        return None
    return f"https://app.hubspot.com/contacts/{portal_id}/record/{object_path}/{object_id}"


def sanitize_slack_error(exc: Exception, explicit_secret: str | None = None) -> str:
    message = str(exc)
    secrets = [explicit_secret] if explicit_secret else []
    for env_var in SLACK_BOT_TOKEN_ENV_CANDIDATES:
        env_value = os.getenv(env_var)
        if env_value:
            secrets.append(env_value)
    for secret in secrets:
        if secret:
            message = message.replace(secret, "[redacted]")
    if isinstance(exc, urllib.error.HTTPError):
        message = f"Slack HTTP {exc.code}"
    return message


def _send_slack_post_message(
    *,
    payload: dict,
    token: str | None,
    slack_client: Any | None,
    timeout_seconds: float,
) -> dict:
    if slack_client is not None:
        if hasattr(slack_client, "post_message"):
            return slack_client.post_message(payload)
        if hasattr(slack_client, "chat_postMessage"):
            return slack_client.chat_postMessage(**payload)
        if hasattr(slack_client, "post"):
            return slack_client.post(SLACK_POST_MESSAGE_URL, json=payload, timeout=timeout_seconds)
        raise TypeError("Injected Slack client must expose post_message, chat_postMessage, or post.")
    body = json.dumps(payload).encode()
    request = urllib.request.Request(
        SLACK_POST_MESSAGE_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        raw_response = response.read().decode()
    parsed = json.loads(raw_response)
    if not parsed.get("ok"):
        raise RuntimeError(f"Slack chat.postMessage failed: {parsed.get('error') or 'unknown_error'}")
    return parsed


def _load_slack_token() -> str | None:
    for env_var in SLACK_BOT_TOKEN_ENV_CANDIDATES:
        value = os.getenv(env_var)
        if value:
            return value
    return None


def _load_slack_channel_id() -> str | None:
    for env_var in SLACK_CHANNEL_ENV_CANDIDATES:
        value = os.getenv(env_var)
        if value:
            return value
    return None


def _full_name(contact: dict) -> str | None:
    name = " ".join(
        value
        for value in (
            _safe_title_case_text(contact.get("first_name")),
            _safe_title_case_text(contact.get("last_name")),
        )
        if value
    )
    return name or None

def _safe_title_case_text(value: str | None, *, preserve_initialisms: bool = False) -> str | None:
    text = " ".join(str(value or "").strip().split())
    if not text:
        return None
    if text != text.lower():
        return text
    if not any(character.isalpha() for character in text) or "." in text:
        return text
    titled = text.title()
    if preserve_initialisms:
        for initialism in DISPLAY_INITIALISMS:
            titled = re.sub(rf"\b{initialism.title()}\b", initialism.upper(), titled)
    return titled


def _escape_slack_text(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _escape_slack_link_label(value: str) -> str:
    return _escape_slack_text(value).replace("|", "¦")


def _research_brief_link(result: dict) -> str:
    url = result.get("lead_brief_url")
    if not url and result.get("lead_brief_html_gcs_uri"):
        url = build_authenticated_gcs_url(gcs_uri=result["lead_brief_html_gcs_uri"])
    if url:
        return f"<{url}|Open research brief>"
    return "_not available_"


def _truncate_for_slack_code_block(text: str, max_length: int = 2800) -> str:
    clean_text = text.replace("```", "'''")
    if len(clean_text) <= max_length:
        return clean_text
    return f"{clean_text[: max_length - 16].rstrip()}\n...[truncated]"
