"""Deterministic BDR style-profile resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bdr_agent.outreach_writeback.config import (
    DEFAULT_STYLE_PROFILE_FALLBACK_REASON,
    DEFAULT_STYLE_PROFILE_ID,
    DEFAULT_STYLE_PROFILE_VERSION,
)

# Map HubSpot owner IDs to deterministic style-profile IDs.
# Placeholders for open source — configure real owner IDs for your workspace.
OWNER_STYLE_PROFILE_IDS = {
    "100000000010": "bdr_profile_a",
    "100000000011": "bdr_profile_b",
}

UNKNOWN_OWNER_FALLBACK_REASON = "hubspot_owner_id_unmapped"


@dataclass(frozen=True)
class StyleProfileResolution:
    style_profile_id: str
    style_profile_version: str
    fallback_reason: str | None
    hubspot_owner_id: str | None

    def as_metadata(self) -> dict[str, str | None]:
        return {
            "style_profile_id": self.style_profile_id,
            "style_profile_version": self.style_profile_version,
            "style_profile_fallback_reason": self.fallback_reason,
            "hubspot_owner_id": self.hubspot_owner_id,
        }


def resolve_style_profile(
    *,
    hubspot_owner_id: Any | None = None,
    company_research: dict | None = None,
) -> StyleProfileResolution:
    """Resolve the deterministic BDR style profile from lead owner metadata."""
    normalized_owner_id = _normalize_owner_id(
        hubspot_owner_id
        if hubspot_owner_id is not None
        else extract_hubspot_owner_id(company_research)
    )
    if normalized_owner_id in OWNER_STYLE_PROFILE_IDS:
        return StyleProfileResolution(
            style_profile_id=OWNER_STYLE_PROFILE_IDS[normalized_owner_id],
            style_profile_version=DEFAULT_STYLE_PROFILE_VERSION,
            fallback_reason=None,
            hubspot_owner_id=normalized_owner_id,
        )
    return StyleProfileResolution(
        style_profile_id=DEFAULT_STYLE_PROFILE_ID,
        style_profile_version=DEFAULT_STYLE_PROFILE_VERSION,
        fallback_reason=(
            UNKNOWN_OWNER_FALLBACK_REASON
            if normalized_owner_id
            else DEFAULT_STYLE_PROFILE_FALLBACK_REASON
        ),
        hubspot_owner_id=normalized_owner_id,
    )


def extract_hubspot_owner_id(company_research: dict | None) -> str | None:
    if not company_research:
        return None
    lead = company_research.get("lead") or {}
    hydration = company_research.get("hydration") or {}
    metadata = company_research.get("metadata") or {}
    for candidate in (
        lead.get("hubspot_owner_id"),
        hydration.get("hubspot_owner_id"),
        metadata.get("hubspot_owner_id"),
        company_research.get("hubspot_owner_id"),
    ):
        normalized = _normalize_owner_id(candidate)
        if normalized:
            return normalized
    return None


def _normalize_owner_id(value: Any | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
