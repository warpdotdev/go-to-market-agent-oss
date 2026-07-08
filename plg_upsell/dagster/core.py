"""Pure helpers for the Dagster PLG HubSpot lead sync.

This module intentionally has no Dagster dependency so payload construction can
be unit-tested without a Dagster runtime or live HubSpot credentials.
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

TIER1_MIN = 70
TIER2_MIN = 65

TIER1_SEQUENCE_ID = os.environ.get("PLG_TIER1_SEQUENCE_ID", "")
TIER2_SEQUENCE_ID = os.environ.get("PLG_TIER2_SEQUENCE_ID", "")
PRODUCT_QUALIFIED_SEQUENCE_ID = os.environ.get(
    "PLG_PRODUCT_QUALIFIED_SEQUENCE_ID",
    os.environ.get("PLG_SEQUENCE_ID", TIER1_SEQUENCE_ID),
)
PRODUCT_QUALIFIED_SEQUENCE_NAME = os.environ.get(
    "PLG_PRODUCT_QUALIFIED_SEQUENCE_NAME",
    "",
)

ROUTING_MANUAL_BDR = "manual_bdr"
ROUTING_AUTOMATED_BDR = "automated_bdr"
ROUTING_MARKETING_TOUCH = "marketing_touch"
ROUTING_HELD = "held"

LEAD_SOURCE_DETAILED = "Product Qualified Lead (PQL)"
LEAD_SOURCE_SIMPLIFIED = "Product Qualified"

LEAD_OBJECT_TYPE = os.environ.get("PLG_HUBSPOT_LEAD_OBJECT_TYPE", "leads")

ACCOUNT_ROUTING_WORKFLOW_ID = os.environ.get("PLG_ACCOUNT_ROUTING_WORKFLOW_ID", "")
CONTACT_ROUTING_WORKFLOW_ID = os.environ.get("PLG_CONTACT_ROUTING_WORKFLOW_ID", "")

# Example HubSpot owner IDs (placeholders). Configure real IDs via env vars.
BDR_OWNER_A = os.environ.get("PLG_BDR_OWNER_A", "100000000010")
BDR_OWNER_B = os.environ.get("PLG_BDR_OWNER_B", "100000000011")
AE_OWNER_1 = os.environ.get("PLG_AE_OWNER_1", "100000000020")
AE_OWNER_2 = os.environ.get("PLG_AE_OWNER_2", "100000000021")
AE_OWNER_3 = os.environ.get("PLG_AE_OWNER_3", "100000000022")
AE_OWNER_4 = os.environ.get("PLG_AE_OWNER_4", "100000000023")
AE_TO_BDR_OWNER_ID = {
    AE_OWNER_1: BDR_OWNER_A,  # AE 1 -> BDR A
    AE_OWNER_2: BDR_OWNER_A,  # AE 2 -> BDR A
    AE_OWNER_3: BDR_OWNER_B,  # AE 3 -> BDR B
    AE_OWNER_4: BDR_OWNER_B,  # AE 4 -> BDR B
}
BDR_OWNER_IDS = {BDR_OWNER_A, BDR_OWNER_B}
AE_POOL_5001_PLUS = [AE_OWNER_3]                      # 5k+ engineers
AE_POOL_501_5000 = [AE_OWNER_4, AE_OWNER_2]           # 501-5000 engineers
AE_POOL_1_500 = [AE_OWNER_4, AE_OWNER_1, AE_OWNER_2]  # 1-500 engineers



def pql_owner_for_crm_owner(owner_id: Any) -> str | None:
    """Return the BDR owner that should own a PLG/PQL Lead.

    If *owner_id* is an AE, map it to the paired BDR. If it is already a BDR
    or another non-empty owner, preserve it. Returns None for blank values.
    """
    if owner_id in (None, ""):
        return None
    owner = str(owner_id).strip()
    if not owner:
        return None
    return AE_TO_BDR_OWNER_ID.get(owner, owner)


def ae_owner_for_company_routing(company_props: dict[str, Any]) -> str:
    """Return the AE owner ID for an unowned PLG company.

    Mirrors the PQA unassigned-account router tiering by engineering headcount.
    The Dagster sync is stateless, so it picks the first owner in the tier pool
    instead of advancing a round-robin cursor.
    """
    bucket = str(company_props.get("eng_count_bucket") or "").strip()
    raw_engineers = company_props.get("number_of_engineers_clay")
    try:
        engineer_count = float(raw_engineers) if raw_engineers not in (None, "") else None
    except (TypeError, ValueError):
        engineer_count = None

    if bucket == "5k+" or (engineer_count is not None and engineer_count >= 5001):
        return AE_POOL_5001_PLUS[0]
    if bucket == "500-5k" or (
        engineer_count is not None and 501 <= engineer_count <= 5000
    ):
        return AE_POOL_501_5000[0]
    return AE_POOL_1_500[0]


def to_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def to_int(value: Any) -> int:
    return int(to_float(value))


def tier_for_score(score: float) -> str:
    if score >= TIER1_MIN:
        return "tier_1"
    if score >= TIER2_MIN:
        return "tier_2"
    return "tier_3"


def routing_motion_for_tier(tier: str) -> str:
    if tier == "tier_1":
        return ROUTING_MANUAL_BDR
    if tier == "tier_2":
        return ROUTING_HELD
    return ROUTING_MARKETING_TOUCH


def sequence_intent_for_tier(tier: str) -> dict[str, str | bool | None]:
    """Return sequence intent for the lead's tier.
    Tier 1 is the only active BDR outreach motion. Tier 2 and Tier 3 are held
    out of lead creation and sequence enrollment for now.
    Tier 3 is marketing-touch only and should not enter a BDR sequence.
    """
    if tier == "tier_1":
        return {
            "should_enroll": True,
            "sequence_id": PRODUCT_QUALIFIED_SEQUENCE_ID,
            "sequence_name": PRODUCT_QUALIFIED_SEQUENCE_NAME,
            "enrollment_mode": "manual",
        }
    return {
        "should_enroll": False,
        "sequence_id": None,
        "sequence_name": None,
        "enrollment_mode": None,
    }


def build_company_properties(account: dict[str, Any], now_ms: int) -> dict[str, str]:
    score = to_float(account.get("pqa_score"))
    tier = account.get("pqa_tier") or tier_for_score(score)
    return {
        "pqa_score": str(round(score, 2)),
        "pqa_avg_wau": str(round(to_float(account.get("pqa_avg_wau")), 2)),
        "pqa_ai_credits_30d": str(round(to_float(account.get("pqa_ai_credits_30d")), 2)),
        "pqa_wow_growth": str(round(to_float(account.get("pqa_wow_growth")), 2)),
        "pqa_users_hitting_limits_14d": str(to_int(account.get("pqa_users_hitting_limits_14d"))),
        "pqa_reload_spend_14d": str(round(to_float(account.get("pqa_reload_spend_14d")), 2)),
        "pqa_free_to_paid_30d": str(to_int(account.get("pqa_free_to_paid_30d"))),
        "pqa_new_members_14d": str(to_int(account.get("pqa_new_members_14d"))),
        "pqa_tier": tier,
        "pqa_status": account.get("pqa_status") or ("active" if tier == "tier_1" else "nurture"),
        "pqa_last_scored_at": str(now_ms),
        "pqa_last_dagster_sync_at": str(now_ms),
        "pqa_routing_motion": routing_motion_for_tier(tier),
    }


def should_trigger_bdr_workflow(tier: str) -> bool:
    return tier == "tier_1"


def _truthy_hubspot_value(value: Any) -> bool:
    return str(value or "").strip().lower() == "true"


def company_enrichment_ready(company_props: dict[str, Any]) -> bool:
    """True when company data is good enough to route from current headcount.

    Clay may expose completion as a status, a boolean, or already-populated
    engineer count. Any of these means routing can use current enough data.
    """
    status = str(company_props.get("clay_enrichment_status") or "").strip().upper()
    if status in {"SUCCESS", "PARTIAL SUCCESS"}:
        return True
    if _truthy_hubspot_value(company_props.get("enriched_by_clay")):
        return True
    engineer_count = company_props.get("number_of_engineers_clay")
    return engineer_count not in (None, "")


def company_enrichment_queued(company_props: dict[str, Any]) -> bool:
    return _truthy_hubspot_value(company_props.get("company_clay_enrichment_queue"))


def should_request_company_enrichment(tier: str, company_props: dict[str, Any]) -> bool:
    return (
        tier == "tier_1"
        and not company_enrichment_ready(company_props)
        and not company_enrichment_queued(company_props)
    )


def build_company_enrichment_properties(now_ms: int) -> dict[str, str]:
    return {
        "company_clay_enrichment_queue": "true",
        "ready_for_enrichment": "true",
        "pqa_enriched_at": str(now_ms),
    }


def _count_from_apollo_department_value(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, dict):
        for key in ("count", "num_employees", "head_count", "headcount", "value"):
            if key in value:
                parsed = _count_from_apollo_department_value(value.get(key))
                if parsed is not None:
                    return parsed
    return None


def extract_apollo_engineering_headcount(org: dict[str, Any]) -> int | None:
    """Extract engineering headcount from Apollo org enrichment response."""
    departments = org.get("departmental_head_count") or {}
    if not isinstance(departments, dict):
        return None
    engineering_keys = {
        "engineering",
        "engineer",
        "information_technology",
        "information technology",
        "it",
    }
    total = 0
    found = False
    for key, value in departments.items():
        normalized_key = str(key or "").strip().lower()
        if normalized_key in engineering_keys or "engineer" in normalized_key:
            count = _count_from_apollo_department_value(value)
            if count is not None:
                total += count
                found = True
    return total if found else None


def build_company_apollo_enrichment_properties(
    org: dict[str, Any],
    now_ms: int,
) -> dict[str, str]:
    """Map Apollo org enrichment to writable HubSpot company properties."""
    props: dict[str, str] = {
        "pqa_enriched_at": str(now_ms),
    }
    engineer_count = extract_apollo_engineering_headcount(org)
    if engineer_count is not None:
        props["number_of_engineers_clay"] = str(engineer_count)
    return props


def build_contact_properties(
    champion: dict[str, Any],
    now_ms: int,
    *,
    request_enrichment: bool = False,
    sequence_intent: dict[str, str | bool | None] | None = None,
    run_id: str | None = None,
) -> dict[str, str]:
    props = {
        "pql_score": str(round(to_float(champion.get("pql_score")), 2)),
        "pql_champion_rank": str(to_int(champion.get("pql_champion_rank"))),
        "pql_is_team_admin": "true" if champion.get("pql_is_team_admin") else "false",
        "pql_ai_credit_usage_30d": str(round(to_float(champion.get("pql_ai_credit_usage_30d")), 2)),
        "pql_activity_frequency": str(to_int(champion.get("pql_activity_frequency"))),
        "pql_hit_credit_limit_14d": "true" if champion.get("pql_hit_credit_limit_14d") else "false",
        "pql_last_scored_at": str(now_ms),
        "pql_last_dagster_sync_at": str(now_ms),
    }
    if request_enrichment:
        props["clay_enrichment_queue"] = "true"
        props["available_for_enrichment"] = "true"
    if sequence_intent and sequence_intent.get("should_enroll"):
        props["plg_sequence_enrollment_requested"] = "true"
        props["plg_sequence_id"] = str(sequence_intent.get("sequence_id") or "")
        props["plg_sequence_name"] = str(sequence_intent.get("sequence_name") or "")
        props["plg_sequence_enrollment_mode"] = str(sequence_intent.get("enrollment_mode") or "")
        props["plg_sequence_requested_at"] = str(now_ms)
        if run_id:
            props["plg_sequence_sync_run_id"] = run_id
    return props


def lead_role_for_champion(champion: dict[str, Any], primary: dict[str, Any] | None = None) -> str:
    """Return this champion's role in the account-level lead set."""
    primary_email = (primary or {}).get("user_email")
    champion_email = champion.get("user_email")
    is_primary = bool(primary_email and champion_email and primary_email == champion_email)
    is_admin = bool(champion.get("pql_is_team_admin"))
    if is_primary and is_admin:
        return "primary_admin_champion"
    if is_primary:
        return "primary_champion"
    if is_admin:
        return "admin_champion"
    return "champion"


def build_lead_properties(
    account: dict[str, Any],
    champion: dict[str, Any],
    *,
    company_id: str | None,
    contact_id: str | None,
    now_ms: int,
    run_id: str,
    lead_role: str | None = None,
    hubspot_owner_id: str | None = None,
) -> dict[str, str]:
    score = to_float(account.get("pqa_score"))
    tier = account.get("pqa_tier") or tier_for_score(score)
    sequence = sequence_intent_for_tier(tier)
    company_name = account.get("company_name") or account.get("email_domain") or "PLG account"
    champion_email = champion.get("user_email") or ""
    title = f"{company_name} — {tier.replace('_', ' ').title()} PLG"

    props = {
        "hs_lead_name": title,
        "plg_source": "pqa",
        "plg_email_domain": str(account.get("email_domain") or ""),
        "plg_company_id": str(company_id or ""),
        "plg_contact_id": str(contact_id or ""),
        "plg_champion_email": str(champion_email),
        "plg_lead_role": lead_role or lead_role_for_champion(champion),
        "pqa_score": str(round(score, 2)),
        "pqa_tier": tier,
        "pqa_routing_motion": routing_motion_for_tier(tier),
        "pql_score": str(round(to_float(champion.get("pql_score")), 2)),
        "pql_champion_rank": str(to_int(champion.get("pql_champion_rank"))),
        "plg_sequence_enrollment_requested": "true" if sequence["should_enroll"] else "false",
        "plg_sequence_id": str(sequence["sequence_id"] or ""),
        "plg_sequence_name": str(sequence["sequence_name"] or ""),
        "plg_sequence_enrollment_mode": str(sequence["enrollment_mode"] or ""),
        "plg_enrichment_requested": "true" if should_trigger_bdr_workflow(tier) else "false",
        "plg_route_via_workflow": "true" if should_trigger_bdr_workflow(tier) else "false",
        "plg_account_routing_workflow_id": ACCOUNT_ROUTING_WORKFLOW_ID if should_trigger_bdr_workflow(tier) else "",
        "plg_contact_routing_workflow_id": CONTACT_ROUTING_WORKFLOW_ID if should_trigger_bdr_workflow(tier) else "",
        "plg_sync_run_id": run_id,
        "plg_last_synced_at": str(now_ms),
        "lead_source_detailed": LEAD_SOURCE_DETAILED,
        "lead_source_simplified": LEAD_SOURCE_SIMPLIFIED,
    }
    if hubspot_owner_id:
        props["hubspot_owner_id"] = str(hubspot_owner_id)
    return props


def choose_ranked_champions(champions_by_domain: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    chosen: dict[str, dict[str, Any]] = {}
    for domain, champions in champions_by_domain.items():
        ranked = sorted(champions, key=lambda c: to_int(c.get("pql_champion_rank") or 999))
        if ranked:
            chosen[domain] = ranked[0]
    return chosen


def choose_lead_champions(champions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return lead-worthy champions for one account.

    We create a lead for the top-ranked champion and, when different, a lead for
    the top-ranked team admin. This gives BDRs both the strongest usage champion
    and the account admin/billing contact without creating duplicate leads when
    the same person fills both roles.
    """
    ranked = sorted(champions, key=lambda c: to_int(c.get("pql_champion_rank") or 999))
    if not ranked:
        return []
    selected = [ranked[0]]
    selected_emails = {str(ranked[0].get("user_email") or "").lower()}
    for champion in ranked:
        email = str(champion.get("user_email") or "").lower()
        if champion.get("pql_is_team_admin") and email not in selected_emails:
            selected.append(champion)
            break
    return selected


def choose_lead_champions_by_domain(
    champions_by_domain: dict[str, list[dict[str, Any]]]
) -> dict[str, list[dict[str, Any]]]:
    return {
        domain: selected
        for domain, champions in champions_by_domain.items()
        if (selected := choose_lead_champions(champions))
    }
