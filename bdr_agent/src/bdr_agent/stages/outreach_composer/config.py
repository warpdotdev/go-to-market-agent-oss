"""Central constants for the skill-authored BDR Outreach Composer stage."""

from __future__ import annotations

import os
from bdr_agent.stages.company_research.config import GCS_ARTIFACT_URI_PREFIX
from bdr_agent.outreach_writeback.config import (
    HOOK_PROPERTY_NAME,
    SOURCES_PROPERTY_NAME,
    WRITEBACK_STATUS_NOT_ATTEMPTED,
    WRITEBACK_STATUS_SKIPPED_DRY_RUN,
)

SCHEMA_VERSION = "bdr_agent_lead_brief.v1"
CANONICAL_STAGE = "outreach_composer"
LEGACY_STAGE = "lead_brief"
ACCEPTED_STAGE_CONTRACTS = (
    CANONICAL_STAGE,
    LEGACY_STAGE,
)
RUNTIME_STAGE = LEGACY_STAGE
STAGE = RUNTIME_STAGE
OUTPUT_TYPE = "lead_brief_markdown"
CONTENT_KIND_EMAIL_BODY = "email_body"
DEFAULT_ARTIFACT_BASE_URI = GCS_ARTIFACT_URI_PREFIX
BRIEF_FILE_EXTENSION = "md"
DEFAULT_TRIGGER_SOURCE = "company_research_completed"
PERSISTED_STAGE_MODE_ENV_VAR = "BDR_AGENT_OUTREACH_COMPOSER_PERSISTED_STAGE_MODE"
PERSISTED_STAGE_MODE_LEGACY = "legacy"
PERSISTED_STAGE_MODE_CANONICAL = "canonical"
DEFAULT_PERSISTED_STAGE_MODE = PERSISTED_STAGE_MODE_LEGACY
VALID_PERSISTED_STAGE_MODES = {
    PERSISTED_STAGE_MODE_LEGACY,
    PERSISTED_STAGE_MODE_CANONICAL,
}
HUBSPOT_WRITEBACK_ENV_VAR = "BDR_AGENT_ALLOW_HUBSPOT_WRITEBACK"
OUTREACH_COMPOSER_DELIVERY_MODE_ENV_VAR = "BDR_AGENT_OUTREACH_COMPOSER_DELIVERY_MODE"
LEAD_BRIEF_DELIVERY_MODE_ENV_VAR = "BDR_AGENT_LEAD_BRIEF_DELIVERY_MODE"
REVIEW_DELIVERY_MODE_ENV_VAR = "BDR_AGENT_REVIEW_DELIVERY_MODE"
DELIVERY_MODE_ENV_CANDIDATES = (
    OUTREACH_COMPOSER_DELIVERY_MODE_ENV_VAR,
    REVIEW_DELIVERY_MODE_ENV_VAR,
    LEAD_BRIEF_DELIVERY_MODE_ENV_VAR,
)
DELIVERY_MODE_DRY_RUN = "dry_run"
DELIVERY_MODE_SLACK = "slack"
DELIVERY_MODE_HUBSPOT = "hubspot"
DELIVERY_MODE_BOTH = "both"
DELIVERY_MODE_SLACK_AND_HUBSPOT = "slack-and-hubspot"
VALID_DELIVERY_MODES = {
    DELIVERY_MODE_DRY_RUN,
    DELIVERY_MODE_SLACK,
    DELIVERY_MODE_HUBSPOT,
    DELIVERY_MODE_BOTH,
}
SLACK_BOT_TOKEN_ENV_CANDIDATES = (
    "BDR_AGENT_SLACK_BOT_TOKEN",
    "SLACK_BOT_TOKEN",
)
OUTREACH_COMPOSER_SLACK_CHANNEL_ENV_VAR = "BDR_AGENT_OUTREACH_COMPOSER_SLACK_CHANNEL_ID"
SLACK_CHANNEL_ENV_VAR = "BDR_AGENT_LEAD_BRIEF_SLACK_CHANNEL_ID"
REVIEW_SLACK_CHANNEL_ENV_VAR = "BDR_AGENT_REVIEW_SLACK_CHANNEL_ID"
SLACK_CHANNEL_ENV_CANDIDATES = (
    OUTREACH_COMPOSER_SLACK_CHANNEL_ENV_VAR,
    REVIEW_SLACK_CHANNEL_ENV_VAR,
    SLACK_CHANNEL_ENV_VAR,
)
HUBSPOT_PORTAL_ID_ENV_VAR = "BDR_AGENT_HUBSPOT_PORTAL_ID"
DRY_RUN_WRITEBACK_STATUS = WRITEBACK_STATUS_SKIPPED_DRY_RUN
NOT_ATTEMPTED_WRITEBACK_STATUS = WRITEBACK_STATUS_NOT_ATTEMPTED

def normalize_stage_contract(value: str | None) -> str:
    """Return a supported stage contract while accepting canonical and legacy names."""
    normalized = (value or "").strip().lower().replace("-", "_")
    if normalized in {"", PERSISTED_STAGE_MODE_LEGACY, LEGACY_STAGE}:
        return LEGACY_STAGE
    if normalized in {PERSISTED_STAGE_MODE_CANONICAL, CANONICAL_STAGE}:
        return CANONICAL_STAGE
    raise ValueError(
        "stage contract must be one of "
        f"{sorted(ACCEPTED_STAGE_CONTRACTS)}; got {value!r}"
    )


def normalize_persisted_stage_mode(value: str | None) -> str:
    """Normalize the explicit persisted-stage mode, defaulting to legacy storage."""
    normalized = (value or "").strip().lower().replace("-", "_")
    if normalized in {"", PERSISTED_STAGE_MODE_LEGACY, LEGACY_STAGE}:
        return PERSISTED_STAGE_MODE_LEGACY
    if normalized in {PERSISTED_STAGE_MODE_CANONICAL, CANONICAL_STAGE}:
        return PERSISTED_STAGE_MODE_CANONICAL
    raise ValueError(
        "persisted_stage_mode must be one of "
        f"{sorted(VALID_PERSISTED_STAGE_MODES)}; got {value!r}"
    )


def persisted_stage_for_mode(mode: str | None) -> str:
    normalized_mode = normalize_persisted_stage_mode(mode)
    if normalized_mode == PERSISTED_STAGE_MODE_CANONICAL:
        return CANONICAL_STAGE
    return LEGACY_STAGE


def resolve_persisted_stage_mode(value: str | None = None) -> str:
    """Resolve the persisted-stage feature flag without changing legacy defaults."""
    if value is not None:
        return normalize_persisted_stage_mode(value)
    return normalize_persisted_stage_mode(os.getenv(PERSISTED_STAGE_MODE_ENV_VAR))


def resolve_persisted_stage(value: str | None = None) -> str:
    return persisted_stage_for_mode(resolve_persisted_stage_mode(value))


__all__ = [
    "ACCEPTED_STAGE_CONTRACTS",
    "BRIEF_FILE_EXTENSION",
    "CANONICAL_STAGE",
    "CONTENT_KIND_EMAIL_BODY",
    "DEFAULT_ARTIFACT_BASE_URI",
    "DEFAULT_PERSISTED_STAGE_MODE",
    "DEFAULT_TRIGGER_SOURCE",
    "DELIVERY_MODE_BOTH",
    "DELIVERY_MODE_DRY_RUN",
    "DELIVERY_MODE_ENV_CANDIDATES",
    "DELIVERY_MODE_HUBSPOT",
    "DELIVERY_MODE_SLACK_AND_HUBSPOT",
    "DELIVERY_MODE_SLACK",
    "DRY_RUN_WRITEBACK_STATUS",
    "HOOK_PROPERTY_NAME",
    "HUBSPOT_PORTAL_ID_ENV_VAR",
    "LEAD_BRIEF_DELIVERY_MODE_ENV_VAR",
    "LEGACY_STAGE",
    "HUBSPOT_WRITEBACK_ENV_VAR",
    "NOT_ATTEMPTED_WRITEBACK_STATUS",
    "PERSISTED_STAGE_MODE_CANONICAL",
    "PERSISTED_STAGE_MODE_ENV_VAR",
    "PERSISTED_STAGE_MODE_LEGACY",
    "OUTREACH_COMPOSER_DELIVERY_MODE_ENV_VAR",
    "OUTREACH_COMPOSER_SLACK_CHANNEL_ENV_VAR",
    "OUTPUT_TYPE",
    "REVIEW_DELIVERY_MODE_ENV_VAR",
    "REVIEW_SLACK_CHANNEL_ENV_VAR",
    "RUNTIME_STAGE",
    "SCHEMA_VERSION",
    "SLACK_BOT_TOKEN_ENV_CANDIDATES",
    "SLACK_CHANNEL_ENV_CANDIDATES",
    "SLACK_CHANNEL_ENV_VAR",
    "SOURCES_PROPERTY_NAME",
    "STAGE",
    "VALID_PERSISTED_STAGE_MODES",
    "VALID_DELIVERY_MODES",
    "normalize_persisted_stage_mode",
    "normalize_stage_contract",
    "persisted_stage_for_mode",
    "resolve_persisted_stage",
    "resolve_persisted_stage_mode",
]
