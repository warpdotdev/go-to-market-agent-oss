#!/usr/bin/env python3
"""
Weekly PQA/PQL score sync: BigQuery → HubSpot.

Pulls domain scores and champion contacts from BigQuery, resolves them to
HubSpot companies/contacts, applies dampening logic, updates all scoring
properties, and prints a summary delta (promotions / demotions / updates /
skips) that feeds the weekly Slack digest.

Column mapping (BQ → HubSpot):
  Domain scores (plg_upsell_domain_scores):
    pql_score           → pqa_score
    avg_wau             → pqa_avg_wau
    total_credits_30d   → pqa_ai_credits_30d
    wow_growth_pct      → pqa_wow_growth
    users_hitting_limits→ pqa_users_hitting_limits_14d
    reload_dollars      → pqa_reload_spend_14d
    users_upgraded      → pqa_free_to_paid_30d
    new_domain_members  → pqa_new_members_14d

  Champions (plg_upsell_domain_champions):
    champion_score      → pql_score
    rank_in_domain      → pql_champion_rank
    is_team_admin       → pql_is_team_admin
    credits_used_t30d   → pql_ai_credit_usage_30d
    days_active_in_last_30 → pql_activity_frequency
    limit_hit_count > 0 → pql_hit_credit_limit_14d

Lead creation and routing note:
  - Legacy direct HubSpot workflow routing is disabled here. Dagster owns PLG
    Lead creation/update, routing motion, and Tier 1/Tier 2 sequence intent.

Usage:
    python plg_upsell/scripts/scoring_sync.py                  # full run
    python plg_upsell/scripts/scoring_sync.py --dry-run        # no writes to HubSpot
    python plg_upsell/scripts/scoring_sync.py --top-n 20       # limit to top 20 domains
    python plg_upsell/scripts/scoring_sync.py --domain acme.com  # single domain test
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from google.cloud import bigquery
from google.oauth2 import service_account

from hubspot_agent.client import hubspot_request

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BQ_PROJECT = os.environ.get("GCP_PROJECT", "example-gcp-project")
BQ_DATASET = "analytics"
SCORES_TABLE = "plg_upsell_domain_scores"
DAILY_SCORES_TABLE = "plg_upsell_domain_scores_daily"
CHAMPIONS_TABLE = "plg_upsell_domain_champions"

PORTAL_ID = os.environ.get("HUBSPOT_PORTAL_ID", "000000000")
# Illustrative example thresholds — calibrate for your own funnel.
MIN_SCORE = 25
MAX_CHAMPION_RANK = 3

# Tier thresholds (pqa_score).
# Illustrative example cutoffs — calibrate for your own funnel.
TIER1_MIN = 70
TIER2_MIN = 65  # Tier 2: 65-70, Tier 3: 25-64 (no auto-routing)

# Dampening.
# Illustrative example constants — calibrate for your own funnel.
PROMOTE_STREAK = 2    # consecutive weeks Hot/Warm before Active
DEMOTE_STREAK = 2     # consecutive weeks below threshold before De-prioritized
HARD_FLOOR = 15       # immediate de-prioritize below this
HARD_CEILING = 85     # immediate promote above this

# HubSpot batch size limits
BATCH_SIZE = 100

# Domain → company ID cache file (persists across runs)
DOMAIN_CACHE_FILE = os.path.join(os.path.dirname(__file__), "domain_to_company_id.json")

# Target Accounts list name prefix — lists to exclude from PLG
TARGET_ACCOUNT_LIST_PREFIX = "[Target Accounts]"

# Legacy enrichment/routing policy. Lead distribution has moved to Dagster; the
# constants are retained only for old helper functions that will be deleted once
# the Dagster sync is fully live.
ENRICHMENT_TIERS = {"tier_1", "tier_2"}

# Legacy BDR routing — replaced by the Dagster PLG HubSpot lead sync.
# Set these to your HubSpot workflow IDs (find them in HubSpot > Automation > Workflows,
# then open each workflow and copy the ID from the URL).
ACCOUNT_ROUTING_WORKFLOW_ID = os.environ.get("PLG_ACCOUNT_ROUTING_WORKFLOW_ID", "")   # Account routing (company-based)
CONTACT_ROUTING_WORKFLOW_ID = os.environ.get("PLG_CONTACT_ROUTING_WORKFLOW_ID", "")   # Contact owner routing for BDR outreach

# Sequence IDs — fill in from HubSpot CRM > Sequences (Settings > CRM > Sequences,
# then open each sequence and copy the ID from the URL).
# Contacts are enrolled in the sequence matching their company's tier.
TIER1_SEQUENCE_ID = os.environ.get("PLG_TIER1_SEQUENCE_ID", "")   # Tier 1 sequence
TIER2_SEQUENCE_ID = os.environ.get("PLG_TIER2_SEQUENCE_ID", "")   # Tier 2 sequence

# Contacts that received a sales email (outbound, owner-sent) within this window
# are skipped for contact routing workflow + sequence enrollment.
SALES_EMAIL_LOOKBACK_DAYS = 30

# Slack — digest posts to the channel; Tier 2 approval notifications go by DM.
PLG_SLACK_BOT_TOKEN = os.environ.get("PLG_SLACK_BOT_TOKEN", "")
PLG_SLACK_CHANNEL = os.environ.get("SLACK_CHANNEL", "")  # e.g. your #your-plg-alerts-channel channel ID
# Tier 2 approval DMs go directly to the BDR manager, not the channel.
# Set to a Slack user/DM ID; falls back to PLG_SLACK_CHANNEL if unset.
TIER2_APPROVAL_SLACK_DM = os.environ.get("TIER2_APPROVAL_SLACK_DM", "")  # Slack user/DM ID for Tier 2 approvals
_env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env")
if os.path.exists(_env_path) and not PLG_SLACK_BOT_TOKEN:
    with open(_env_path) as _ef:
        for _line in _ef:
            _line = _line.strip()
            if _line.startswith("PLG_SLACK_BOT_TOKEN="):
                PLG_SLACK_BOT_TOKEN = _line.split("=", 1)[1]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _now_ms() -> int:
    """Current UTC time as Unix milliseconds (HubSpot datetime format)."""
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _tier(score: float) -> str:
    if score >= TIER1_MIN:
        return "tier_1"
    if score >= TIER2_MIN:
        return "tier_2"
    return "tier_3"


def _is_hot_or_warm(score: float) -> bool:
    return score >= TIER2_MIN  # Tier 1 or Tier 2


# ---------------------------------------------------------------------------
# BigQuery
# ---------------------------------------------------------------------------

def _build_bq_client() -> bigquery.Client:
    sa_json = os.environ.get("GCP_SERVICE_ACCOUNT_JSON")
    if sa_json:
        creds = service_account.Credentials.from_service_account_info(json.loads(sa_json))
        return bigquery.Client(project=BQ_PROJECT, credentials=creds)
    return bigquery.Client(project=BQ_PROJECT)


def fetch_domain_scores(
    *,
    top_n: int | None = None,
    domain_filter: str | None = None,
    domains_filter: list[str] | None = None,
) -> list[dict]:
    """Pull latest domain-level scores from BQ.

    Uses the daily snapshot rather than the positive-score table so domains
    that recently became ineligible (closed-won/open pipeline/POC, no active
    paid plan, etc.) still flow through with `pqa_score=0` and can be cleared
    or de-prioritized in HubSpot. Otherwise stale PQA fields remain on accounts
    that have dropped out of `plg_upsell_domain_scores`.

    Returns list of dicts keyed by HubSpot property name, plus `is_eligible`
    and `ineligibility_reason` metadata.
    """
    client = _build_bq_client()
    scores_ref = f"`{BQ_PROJECT}.{BQ_DATASET}.{DAILY_SCORES_TABLE}`"

    where_clauses = [
        f"scored_date = (SELECT MAX(scored_date) FROM {scores_ref})",
        f"(pql_score >= {MIN_SCORE} OR is_eligible = FALSE)",
    ]
    params: list = []
    if domain_filter:
        where_clauses.append("email_domain = @domain_filter")
        params.append(bigquery.ScalarQueryParameter("domain_filter", "STRING", domain_filter))
    if domains_filter:
        where_clauses.append("email_domain IN UNNEST(@domains_filter)")
        params.append(bigquery.ArrayQueryParameter("domains_filter", "STRING", list(domains_filter)))

    where = " AND ".join(where_clauses)
    limit = ""
    if top_n:
        limit = "LIMIT @top_n"
        params.append(bigquery.ScalarQueryParameter("top_n", "INT64", int(top_n)))

    sql = f"""
    SELECT
        email_domain,
        company_name,
        pql_score             AS pqa_score,
        avg_wau               AS pqa_avg_wau,
        total_credits_30d     AS pqa_ai_credits_30d,
        wow_growth_pct        AS pqa_wow_growth,
        users_hitting_limits  AS pqa_users_hitting_limits_14d,
        reload_dollars        AS pqa_reload_spend_14d,
        users_upgraded        AS pqa_free_to_paid_30d,
        new_domain_members    AS pqa_new_members_14d,
        is_eligible,
        ineligibility_reason
    FROM {scores_ref}
    WHERE {where}
    ORDER BY is_eligible DESC, pql_score DESC, email_domain
    {limit}
    """

    job_config = bigquery.QueryJobConfig(query_parameters=params)
    rows = client.query(sql, job_config=job_config).result()
    return [dict(row) for row in rows]


def fetch_champions(
    *,
    domains: list[str] | None = None,
) -> dict[str, list[dict]]:
    """Pull champion contacts for the given domains (or all scored domains).

    Returns {domain: [champion_dict, ...]} keyed by email_domain.
    """
    client = _build_bq_client()
    champs_ref = f"`{BQ_PROJECT}.{BQ_DATASET}.{CHAMPIONS_TABLE}`"

    where_clauses = ["rank_in_domain <= @max_rank"]
    params: list = [bigquery.ScalarQueryParameter("max_rank", "INT64", int(MAX_CHAMPION_RANK))]
    if domains:
        where_clauses.append("email_domain IN UNNEST(@domains)")
        params.append(bigquery.ArrayQueryParameter("domains", "STRING", list(domains)))
    where = " AND ".join(where_clauses)

    sql = f"""
    SELECT
        email_domain,
        user_email,
        champion_score                              AS pql_score,
        rank_in_domain                              AS pql_champion_rank,
        is_team_admin                               AS pql_is_team_admin,
        credits_used_t30d                           AS pql_ai_credit_usage_30d,
        days_active_in_last_30                      AS pql_activity_frequency,
        CASE WHEN limit_hit_count > 0 THEN TRUE ELSE FALSE END AS pql_hit_credit_limit_14d
    FROM {champs_ref}
    WHERE {where}
    ORDER BY email_domain, rank_in_domain
    """

    job_config = bigquery.QueryJobConfig(query_parameters=params)
    result: dict[str, list[dict]] = {}
    for row in client.query(sql, job_config=job_config).result():
        d = dict(row)
        domain = d.pop("email_domain")
        result.setdefault(domain, []).append(d)
    return result


# ---------------------------------------------------------------------------
# Domain → Company ID cache
# ---------------------------------------------------------------------------

def load_domain_cache() -> dict[str, str]:
    if os.path.exists(DOMAIN_CACHE_FILE):
        with open(DOMAIN_CACHE_FILE) as f:
            return json.load(f)
    return {}


def save_domain_cache(cache: dict[str, str]) -> None:
    with open(DOMAIN_CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)


def resolve_company_id(
    domain: str,
    cache: dict[str, str],
    dry_run: bool = False,
    allow_create: bool = True,
) -> tuple[str | None, bool]:
    """Return (company_id, is_new) for a domain.

    Searches HubSpot by domain first — never modifies an existing company's
    core fields (domain, name). Only creates if not found and *allow_create*
    is true. Ineligible domains should not create net-new HubSpot companies;
    they are included only to clear/de-prioritize previously synced companies.
    """
    if domain in cache:
        return cache[domain], False

    # Search HubSpot by domain
    result = hubspot_request("POST", "/crm/v3/objects/companies/search", {
        "filterGroups": [{"filters": [
            {"propertyName": "domain", "operator": "EQ", "value": domain}
        ]}],
        "properties": ["domain", "name"],
        "limit": 1,
    })
    hits = result.get("results", [])
    if hits:
        company_id = hits[0]["id"]
        cache[domain] = company_id
        return company_id, False
    if not allow_create:
        print(f"    → Skipping ineligible domain with no existing HubSpot company: {domain}")
        return None, False

    # Not found — create new (domain only; never overwrite existing)
    if dry_run:
        print(f"    [dry-run] Would create company for domain: {domain}")
        return None, True

    created = hubspot_request("POST", "/crm/v3/objects/companies", {
        "properties": {"domain": domain}
    })
    company_id = created["id"]
    cache[domain] = company_id
    print(f"    + Created company for {domain} (ID: {company_id})")
    return company_id, True


# ---------------------------------------------------------------------------
# Target Account exclusion
# ---------------------------------------------------------------------------

def get_target_account_company_ids() -> set[str]:
    """Return set of HubSpot company IDs that are in any [Target Accounts] list."""
    # Find matching lists
    result = hubspot_request("POST", "/crm/v3/lists/search", {
        "query": TARGET_ACCOUNT_LIST_PREFIX,
        "objectTypeId": "0-2",
        "count": 50,
        "offset": 0,
    })
    target_lists = [
        lst for lst in result.get("lists", [])
        if TARGET_ACCOUNT_LIST_PREFIX in lst.get("name", "")
    ]

    if not target_lists:
        print("  ⚠  No [Target Accounts] lists found — no exclusions applied")
        return set()

    excluded: set[str] = set()
    for lst in target_lists:
        list_id = lst["listId"]
        name = lst["name"]
        after = None
        while True:
            path = f"/crm/v3/lists/{list_id}/memberships?limit=500"
            if after:
                path += f"&after={after}"
            page = hubspot_request("GET", path)
            for member in page.get("results", []):
                excluded.add(str(member.get("recordId") or member.get("id", "")))
            paging = page.get("paging", {}).get("next", {})
            after = paging.get("after")
            if not after:
                break
        print(f"  → Loaded {len(excluded)} total excluded companies (after {name})")

    return excluded


# ---------------------------------------------------------------------------
# Read current HubSpot company state (for dampening)
# ---------------------------------------------------------------------------

def batch_read_company_state(company_ids: list[str]) -> dict[str, dict]:
    """Fetch current PQA state properties for a list of company IDs.

    Returns {company_id: {property: value, ...}}.
    """
    props = ["pqa_score", "pqa_status", "pqa_weeks_above_threshold",
             "pqa_weeks_below_threshold", "hubspot_owner_id",
             "pqa_bdr_routed_at",   # guard against double-routing
             "pqa_enriched_at"]     # guard against double-enrichment
    state: dict[str, dict] = {}

    for i in range(0, len(company_ids), BATCH_SIZE):
        batch = company_ids[i : i + BATCH_SIZE]
        result = hubspot_request("POST", "/crm/v3/objects/companies/batch/read", {
            "inputs": [{"id": cid} for cid in batch],
            "properties": props,
        })
        for item in result.get("results", []):
            state[item["id"]] = item.get("properties", {})

    return state


# ---------------------------------------------------------------------------
# Dampening logic
# ---------------------------------------------------------------------------

def compute_new_status(
    new_score: float,
    current_status: str | None,
    weeks_above: int,
    weeks_below: int,
) -> tuple[str, int, int]:
    """Return (new_status, new_weeks_above, new_weeks_below).

    Bootstrap rule: if pqa_status has never been set (None), this is the first
    sync for this account. Promote/tier immediately based on current score —
    the score has been live in BQ for weeks already, no reason to wait.

    Ongoing dampening only applies to accounts that have been through at least
    one prior sync (pqa_status is not None).
    """
    is_bootstrap = current_status is None
    current_status = current_status or "nurture"

    # Hard floor: immediate de-prioritize regardless
    if new_score < HARD_FLOOR:
        return "deprioritized", 0, 0

    # Bootstrap: first sync — promote immediately if above threshold
    if is_bootstrap:
        if _is_hot_or_warm(new_score):
            return "active", 0, 0
        else:
            return "nurture", 0, 0

    # Hard ceiling: immediate promote for ongoing accounts
    if new_score >= HARD_CEILING and current_status != "active":
        return "active", 0, 0

    is_hot = _is_hot_or_warm(new_score)

    if current_status != "active":
        # Promotion path — requires streak
        if is_hot:
            new_weeks_above = weeks_above + 1
            if new_weeks_above >= PROMOTE_STREAK:
                return "active", 0, 0
            return current_status, new_weeks_above, 0
        else:
            return current_status, 0, 0

    else:
        # Already active — demotion requires streak
        if is_hot:
            return "active", 0, 0
        else:
            new_weeks_below = weeks_below + 1
            if new_weeks_below >= DEMOTE_STREAK:
                return "deprioritized", 0, 0
            return "active", 0, new_weeks_below


# ---------------------------------------------------------------------------
# Company batch update
# ---------------------------------------------------------------------------

def build_company_payload(
    company_id: str,
    bq_row: dict,
    current_state: dict,
    now_ms: int,
) -> dict:
    """Build the HubSpot update payload for a single company."""
    new_score = _to_float(bq_row.get("pqa_score"))
    prev_score = _to_float(current_state.get("pqa_score") or 0)
    score_delta = new_score - prev_score

    current_status = current_state.get("pqa_status")
    weeks_above = int(current_state.get("pqa_weeks_above_threshold") or 0)
    weeks_below = int(current_state.get("pqa_weeks_below_threshold") or 0)

    new_status, new_above, new_below = compute_new_status(
        new_score, current_status, weeks_above, weeks_below
    )

    tier = _tier(new_score)

    props: dict[str, Any] = {
        # Scores from BQ
        "pqa_score":                    round(new_score, 2),
        "pqa_avg_wau":                  round(_to_float(bq_row.get("pqa_avg_wau")), 2),
        "pqa_ai_credits_30d":           round(_to_float(bq_row.get("pqa_ai_credits_30d")), 2),
        "pqa_wow_growth":               round(_to_float(bq_row.get("pqa_wow_growth")), 2),
        "pqa_users_hitting_limits_14d": int(_to_float(bq_row.get("pqa_users_hitting_limits_14d"))),
        "pqa_reload_spend_14d":         round(_to_float(bq_row.get("pqa_reload_spend_14d")), 2),
        "pqa_free_to_paid_30d":         int(_to_float(bq_row.get("pqa_free_to_paid_30d"))),
        "pqa_new_members_14d":          int(_to_float(bq_row.get("pqa_new_members_14d"))),
        # Computed
        "pqa_score_prev":               round(prev_score, 2),
        "pqa_score_delta":              round(score_delta, 2),
        "pqa_tier":                     tier,
        "pqa_last_scored_at":           now_ms,
        # Lifecycle
        "pqa_status":                   new_status,
        "pqa_weeks_above_threshold":    new_above,
        "pqa_weeks_below_threshold":    new_below,
    }

    return {
        "id": company_id,
        "properties": {k: str(v) if not isinstance(v, (str, bool)) else v
                       for k, v in props.items()},
        "_meta": {
            "new_status": new_status,
            "prev_status": current_status,
            "new_score": new_score,
            "score_delta": score_delta,
        },
    }


def batch_update_companies(payloads: list[dict], dry_run: bool = False) -> dict[str, str]:
    """Submit company updates in batches of BATCH_SIZE.

    Returns {company_id: new_status} for all processed companies.
    """
    status_map: dict[str, str] = {}

    # Strip internal _meta before sending
    clean = [{"id": p["id"], "properties": p["properties"]} for p in payloads]
    for p in payloads:
        status_map[p["id"]] = p["_meta"]["new_status"]

    if dry_run:
        return status_map

    for i in range(0, len(clean), BATCH_SIZE):
        batch = clean[i : i + BATCH_SIZE]
        hubspot_request("POST", "/crm/v3/objects/companies/batch/update", {"inputs": batch})

    return status_map


# ---------------------------------------------------------------------------
# Contact resolution
# ---------------------------------------------------------------------------

# Latest Lead Source picklist values applied on contact creation. These flag
# T1/T2 contacts that enter HubSpot through the PLG scoring pipeline as PQLs,
# so downstream attribution reporting (paid/inbound/outbound breakdowns)
# correctly classifies them. Tier 3 contacts are intentionally NOT stamped —
# the PQL tag is reserved for accounts that meet the BDR-worth bar. Only
# written on the create call — contacts that already exist keep their prior
# lead source (we never clobber real inbound/outbound attribution from
# another channel).
#
# Property internal names and option values are authoritative — confirmed
# against the live HubSpot portal. Note the detailed value includes the
# "(PQL)" suffix while the simplified value is just "Product Qualified".
PQL_LEAD_SOURCE_DETAILED = "Product Qualified Lead (PQL)"
PQL_LEAD_SOURCE_SIMPLIFIED = "Product Qualified"
# Tiers that receive the PQL lead-source stamp on contact creation.
# Matches ENRICHMENT_TIERS by design — both gates share the same "accounts
# worth a BDR's time" criterion — but kept as a separate constant so the
# two policies can diverge in future without coupling.
PQL_LEAD_SOURCE_TIERS = {"tier_1", "tier_2"}


def resolve_contact_id(
    email: str,
    dry_run: bool = False,
    enable_enrichment: bool = False,
    tag_as_pql: bool = False,
) -> tuple[str | None, bool]:
    """Return (contact_id, is_new) for an email. Creates contact if not found.

    Legacy enrichment and lead-source flags are ignored. Dagster now owns lead
    creation, sequence intent, and routing state.
    """
    result = hubspot_request("POST", "/crm/v3/objects/contacts/search", {
        "filterGroups": [{"filters": [
            {"propertyName": "email", "operator": "EQ", "value": email}
        ]}],
        "properties": [
            "email",
            "latest_lead_source_detailed",
            "latest_lead_source_simplified",
        ],
        "limit": 1,
    })
    hits = result.get("results", [])
    if hits:
        contact_id = hits[0]["id"]
        return contact_id, False

    if dry_run:
        print(f"    [dry-run] Would create contact: {email}")
        return None, True

    create_props: dict[str, str] = {"email": email}

    created = hubspot_request("POST", "/crm/v3/objects/contacts", {
        "properties": create_props,
    })
    contact_id = created["id"]
    print(f"    + Created contact {email} (ID: {contact_id})")
    return contact_id, True


def associate_contacts_to_companies(
    pairs: list[tuple[str, str]],
    dry_run: bool = False,
) -> None:
    """Associate contacts to their primary company in HubSpot.

    pairs: list of (contact_id, company_id)
    Uses the v4 associations batch API. Safe to call on existing associations.
    """
    if not pairs or dry_run:
        return

    inputs = [
        {
            "from": {"id": contact_id},
            "to": {"id": company_id},
            "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 1}],
        }
        for contact_id, company_id in pairs
    ]

    for i in range(0, len(inputs), BATCH_SIZE):
        batch = inputs[i : i + BATCH_SIZE]
        hubspot_request(
            "POST",
            "/crm/v4/associations/contact/company/batch/create",
            {"inputs": batch},
        )


def batch_update_contacts(payloads: list[dict], dry_run: bool = False) -> None:
    """Submit contact updates in batches of BATCH_SIZE."""
    if dry_run:
        return

    # Deduplicate by contact ID — a single HubSpot contact can appear under
    # multiple domain champions (e.g. shared email aliases). Keep the last
    # occurrence so the most-recently-scored domain wins.
    seen: dict[str, dict] = {}
    for p in payloads:
        seen[p["id"]] = p
    deduped = list(seen.values())
    if len(deduped) < len(payloads):
        print(f"    ⚠  Deduplicated {len(payloads) - len(deduped)} duplicate contact IDs before batch update")

    for i in range(0, len(deduped), BATCH_SIZE):
        batch = deduped[i : i + BATCH_SIZE]
        hubspot_request("POST", "/crm/v3/objects/contacts/batch/update", {"inputs": batch})


# ---------------------------------------------------------------------------
# BDR workflow routing
# ---------------------------------------------------------------------------

def contact_received_sales_email_recently(
    contact_id: str,
    days: int = SALES_EMAIL_LOOKBACK_DAYS,
) -> bool:
    """Return True if the contact received an outbound sales-rep email recently.

    Searches CRM email engagements (hs_email_direction=EMAIL, hubspot_owner_id set)
    for the contact in the last *days* days.  This covers 1:1 rep emails and
    sequence-sent emails but excludes marketing email sends, which are tracked
    separately through HubSpot's marketing tools.

    Errs on the side of *inclusion*: if the API call fails the function returns
    False so the contact is still enrolled rather than silently dropped.
    """
    cutoff_ms = str(int(
        (datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000
    ))
    try:
        result = hubspot_request(
            "POST",
            "/crm/v3/objects/emails/search",
            data={
                "filterGroups": [{
                    "filters": [
                        {
                            "propertyName": "associations.contact",
                            "operator": "EQ",
                            "value": contact_id,
                        },
                        {
                            "propertyName": "hs_createdate",
                            "operator": "GTE",
                            "value": cutoff_ms,
                        },
                        {
                            # Outbound 1:1 rep/sequence emails — excludes inbound
                            # and marketing emails (MARKETING_EMAIL direction).
                            "propertyName": "hs_email_direction",
                            "operator": "EQ",
                            "value": "EMAIL",
                        },
                    ]
                }],
                "properties": ["hs_createdate", "hs_email_direction", "hubspot_owner_id"],
                "limit": 1,
            },
        )
        for email in result.get("results", []):
            # Confirm a rep owns the email — rules out automated system emails
            # that might also use direction=EMAIL.
            if email.get("properties", {}).get("hubspot_owner_id"):
                return True
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"    ⚠  Could not check email history for contact {contact_id}: {exc}")
        return False  # err on the side of enrolling


# ---------------------------------------------------------------------------
# Workflow + sequence enrollment — handled by HubSpot, not this script.
# ---------------------------------------------------------------------------
#
# Earlier versions of this module POSTed to /automation/v4/enrollments to
# add companies/contacts to flows, and to /crm/v3/objects/contacts/{id}/
# enrollments for sequences. Both endpoints return 404 in this portal —
# they're not part of HubSpot's public API. The calls failed silently for
# weeks before being noticed, causing accounts to be
# stamped pqa_bdr_routed_at without ever being routed.
#
# The replacement: the HubSpot Account Router and Contact Router workflows
# self-trigger on property changes. The script's job is to write the
# trigger properties (pqa_status, pql_champion_rank, etc.) correctly.
# Workflow enrollment criteria + re-enrollment triggers (configured in the
# HubSpot UI) handle the rest.
#
# Sequence enrollment is genuinely unavailable via API in this portal —
# see _post_tier2_sequence_instructions_to_slack for the manual fallback.
#
# Trigger properties:
#   - Account Router (PLG_ACCOUNT_ROUTING_WORKFLOW_ID) re-enrolls on:
#       company.pqa_status -> active
#       company.pqa_bdr_routed_at -> unknown
#   - Contact Router (PLG_CONTACT_ROUTING_WORKFLOW_ID) re-enrolls on:
#       company.pqa_status -> active
#       contact.pql_champion_rank -> known
#     plus enrollment criteria:
#       contact.firstname known AND contact.lastname known
#       (filters out unranked / unnamed contacts that flooded the queue)


def _resolve_slack_channel(channel: str) -> str:
    """Resolve a Slack user ID or DM channel ID to a postable channel ID.

    If *channel* looks like a DM (starts with 'D') or user ID (starts with 'U'),
    call conversations.open to ensure the bot has the conversation open and
    return the canonical channel ID. Falls back to the original value on error.
    """
    import urllib.request as _ur
    import urllib.error as _ue
    if not PLG_SLACK_BOT_TOKEN:
        return channel
    if not channel.startswith(("D", "U")):
        return channel
    try:
        payload = json.dumps({"users": channel}).encode()
        req = _ur.Request(
            "https://slack.com/api/conversations.open",
            data=payload,
            headers={"Authorization": f"Bearer {PLG_SLACK_BOT_TOKEN}",
                     "Content-Type": "application/json"},
            method="POST",
        )
        with _ur.urlopen(req) as resp:
            body = json.loads(resp.read().decode())
            if body.get("ok"):
                resolved = body["channel"]["id"]
                if resolved != channel:
                    print(f"  → Resolved DM channel: {channel} → {resolved}")
                return resolved
            else:
                print(f"  ⚠  conversations.open error: {body.get('error')} — falling back to {channel}")
    except _ue.HTTPError as exc:
        print(f"  ⚠  conversations.open HTTP error: {exc.code} — falling back to {channel}")
    return channel


def _post_to_slack(text: str, channel: str = PLG_SLACK_CHANNEL) -> None:
    """Post a plain-text message to Slack via the bot token."""
    import urllib.request as _ur
    import urllib.error as _ue
    if not PLG_SLACK_BOT_TOKEN:
        print(f"  ⚠  PLG_SLACK_BOT_TOKEN not set — printing Tier 2 approval request to stdout.")
        print(text)
        return
    channel = _resolve_slack_channel(channel)
    payload = json.dumps({"channel": channel, "text": text}).encode()
    req = _ur.Request(
        "https://slack.com/api/chat.postMessage",
        data=payload,
        headers={"Authorization": f"Bearer {PLG_SLACK_BOT_TOKEN}",
                 "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with _ur.urlopen(req) as resp:
            body = json.loads(resp.read().decode())
            if not body.get("ok"):
                print(f"  ⚠  Slack error posting approval request: {body.get('error')}")
    except _ue.HTTPError as exc:
        print(f"  ⚠  Slack HTTP error: {exc.code}")


def _post_tier2_approval_to_slack(
    tier2_pending: list[dict],
    channel: str = TIER2_APPROVAL_SLACK_DM,
    dry_run: bool = False,
) -> None:
    """Post a Tier 2 sequence approval request to a Slack DM.

    The Tier 2 sequence starts with an automated email, so
    contacts are held back from automatic enrollment and surfaced here for
    BDR review before triggering.
    """
    if not tier2_pending:
        return

    lines = [
        f":pause_button: *Tier 2 Sequence Approvals Needed ({len(tier2_pending)} account(s))*",
        "",
        "_The Tier 2 sequence begins with an automated email."
        " Review these champion contacts before enrolling:_",
        "",
    ]
    for p in tier2_pending:
        n = len(p["contact_ids"])
        contact_word = "contact" if n == 1 else "contacts"
        lines.append(
            f"• *<https://app.hubspot.com/contacts/{PORTAL_ID}/company/{p['company_id']}"
            f"|{p['domain']}>*  ·  score {p['score']:.1f}  ·  {n} {contact_word} enriching via Clay"
        )

    domain_list = ",".join(p["domain"] for p in tier2_pending)
    lines += [
        "",
        "To enroll after review:",
        f"  `.venv/bin/python plg_upsell/scripts/scoring_sync.py --approve-tier2-domains {domain_list}`",
        "Add `--dry-run` to preview without sending emails.",
    ]

    message = "\n".join(lines)
    if dry_run:
        print("\n[DRY RUN] Would post Tier 2 approval request to Slack:")
        print(message)
    else:
        _post_to_slack(message, channel)
        print(f"  ✓ Tier 2 approval request posted to Slack ({len(tier2_pending)} account(s))")


def _get_champion_contacts_for_company(company_id: str) -> list[str]:
    """Return HubSpot contact IDs that pass the BDR-routing eligibility filter.

    Mirrors the Contact Router (PLG_CONTACT_ROUTING_WORKFLOW_ID) workflow's enrollment criteria:
    contact must have `pql_champion_rank`, `firstname`, AND `lastname` known.
    Without this filter, today's UI-side filter is the only thing standing
    between us and another spam-lead incident; with it, the script and
    workflow agree on what counts as a routable champion.

    Returns up to MAX_CHAMPION_RANK contact IDs sorted by ascending rank.
    """
    try:
        assoc = hubspot_request(
            "GET",
            f"/crm/v4/objects/companies/{company_id}/associations/contacts?limit=100",
        )
        contact_ids = [str(r["toObjectId"]) for r in assoc.get("results", [])]
        if not contact_ids:
            return []
        # Batch-read up to 100 contacts to find the ranked + named champions.
        # The previous [:20] slice silently dropped any ranked champion past
        # the 20th associated contact.
        result = hubspot_request("POST", "/crm/v3/objects/contacts/batch/read", {
            "inputs": [{"id": cid} for cid in contact_ids[:100]],
            "properties": ["pql_champion_rank", "firstname", "lastname", "email"],
        })
        eligible = []
        for c in result.get("results", []):
            props = c.get("properties", {})
            rank_val = props.get("pql_champion_rank")
            if not rank_val:
                continue
            if not props.get("firstname") or not props.get("lastname"):
                # Workflow's enrollment criteria would reject these too;
                # excluding here keeps the script's view consistent.
                continue
            eligible.append((int(rank_val), c["id"]))
        eligible.sort()
        return [cid for _, cid in eligible[:MAX_CHAMPION_RANK]]
    except Exception as exc:  # noqa: BLE001
        print(f"    ⚠  Could not fetch champion contacts for company {company_id}: {exc}")
        return []


def route_pending_accounts(
    dry_run: bool = False,
    tier_filter: str | None = None,
) -> int:
    """Route active PLG accounts that haven't been routed yet due to pending enrichment.

    Queries HubSpot for companies with pqa_status=active and pqa_bdr_routed_at
    not set. For each, fetches champion contacts, checks `_is_routable`, and
    routes enrolled contacts into the BDR workflows and sequences.

    When *tier_filter* is set (e.g. "tier_1"), only companies at that tier
    are considered — handy for staged rollouts ("route T1 tonight, T2 next
    week"). `None` processes all pending accounts.

    Run this 1–2 hours after scoring_sync.py to give Clay time to enrich:
      .venv/bin/python plg_upsell/scripts/scoring_sync.py --route-newly-active

    Returns total number of companies successfully routed.
    """
    if tier_filter:
        print(f"Querying HubSpot for active PLG accounts pending routing (tier={tier_filter})…")
    else:
        print("Querying HubSpot for active PLG accounts pending routing…")
    after = None
    pending: list[dict] = []
    while True:
        filters = [
            {"propertyName": "pqa_status", "operator": "EQ", "value": "active"},
            {"propertyName": "pqa_bdr_routed_at", "operator": "NOT_HAS_PROPERTY"},
        ]
        if tier_filter:
            filters.append(
                {"propertyName": "pqa_tier", "operator": "EQ", "value": tier_filter}
            )
        body: dict = {
            "filterGroups": [{"filters": filters}],
            "properties": ["name", "domain", "pqa_score", "pqa_tier"],
            "sorts": [{"propertyName": "pqa_score", "direction": "DESCENDING"}],
            "limit": 100,
        }
        if after:
            body["after"] = after
        result = hubspot_request("POST", "/crm/v3/objects/companies/search", body)
        for item in result.get("results", []):
            p = item.get("properties", {})
            pending.append({
                "id": item["id"],
                "domain": p.get("domain", ""),
                "name": p.get("name") or p.get("domain", "?"),
                "score": _to_float(p.get("pqa_score")),
                "tier": p.get("pqa_tier", ""),
            })
        after = result.get("paging", {}).get("next", {}).get("after")
        if not after:
            break

    print(f"  {len(pending)} account(s) pending routing")
    if not pending:
        return 0

    now_ms = _now_ms()
    tier2_pending: list[dict] = []
    routed = 0

    for company in pending:
        company_id = company["id"]
        domain = company["domain"]
        tier = company["tier"]
        print(f"  → {domain} ({tier}, score {company['score']:.1f})…")

        contact_ids = _get_champion_contacts_for_company(company_id)
        if not contact_ids:
            print(f"    ⚠  No champion contacts found — skipping")
            continue

        enrichment = _is_enriched_by_clay(contact_ids, dry_run=dry_run, tier=tier)
        enriched_ids = [cid for cid in contact_ids if enrichment.get(cid)]
        deferred_ids = [cid for cid in contact_ids if not enrichment.get(cid)]

        if not enriched_ids:
            print(f"    ⏳ No contacts enriched yet — skipping (retry later)")
            continue

        if deferred_ids:
            print(f"    ⏳ {len(deferred_ids)} contact(s) still pending enrichment")

        # Workflows self-trigger on the property writes already performed by
        # the main sync (pqa_status, pql_champion_rank). Nothing to enroll
        # from here — we just gate the stamp + the Tier 2 Slack approval.
        eligible: list[str] = []
        for contact_id in enriched_ids:
            if not dry_run and contact_received_sales_email_recently(contact_id):
                print(f"    → Skipping {contact_id} — recent sales email")
                continue
            eligible.append(contact_id)

        if tier == "tier_2" and eligible:
            tier2_pending.append({
                "domain": domain,
                "company_id": company_id,
                "score": company["score"],
                "contact_ids": eligible,
            })
            print(f"    ⏸  {len(eligible)} contact(s) held for Tier 2 sequence approval")

        # Stamp only when ALL contacts are done (none deferred)
        if not deferred_ids:
            if not dry_run:
                try:
                    hubspot_request(
                        "PATCH",
                        f"/crm/v3/objects/companies/{company_id}",
                        data={"properties": {"pqa_bdr_routed_at": str(now_ms)}},
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"    ⚠  Could not stamp pqa_bdr_routed_at: {exc}")
            else:
                print(f"    [dry-run] Would stamp pqa_bdr_routed_at on {domain}")

        routed += 1

    if tier2_pending:
        print(f"\nPosting Tier 2 approval request for {len(tier2_pending)} account(s)…")
        _post_tier2_approval_to_slack(tier2_pending, dry_run=dry_run)

    return routed


def approve_tier2_sequence_enrollment(
    domains: list[str],
    dry_run: bool = False,
) -> int:
    """Build a Slack DM with manual sequence-enrollment instructions per Tier 2 domain.

    Sequence enrollment is unavailable via API in this portal (POST
    /crm/v3/objects/contacts/{id}/enrollments returns 404). Until that
    changes, this command consolidates the BDR's TODO into a single Slack
    message: per-contact deep links to the HubSpot record, with a note to
    enroll each in TIER2_SEQUENCE_ID via the contact's "Enroll in sequence"
    button. Recent-email contacts are filtered out so the BDR doesn't
    re-engage someone already in active outreach.

    Returns the number of contacts surfaced for manual enrollment.
    """
    cache = load_domain_cache()
    surfaced = 0
    sections: list[str] = []

    for domain in domains:
        company_id = cache.get(domain)
        if not company_id:
            result = hubspot_request("POST", "/crm/v3/objects/companies/search", {
                "filterGroups": [{"filters": [
                    {"propertyName": "domain", "operator": "EQ", "value": domain}
                ]}],
                "properties": ["domain"],
                "limit": 1,
            })
            hits = result.get("results", [])
            if not hits:
                print(f"  ⚠  No HubSpot company found for domain: {domain}")
                continue
            company_id = hits[0]["id"]

        print(f"  → Building Tier 2 sequence-enrollment instructions for {domain} (company {company_id})…")
        contact_ids = _get_champion_contacts_for_company(company_id)
        if not contact_ids:
            print(f"    ⚠  No eligible champion contacts found for {domain}")
            continue

        eligible: list[str] = []
        for contact_id in contact_ids:
            if not dry_run and contact_received_sales_email_recently(contact_id):
                print(f"    → Skipping contact {contact_id} — sales email in last {SALES_EMAIL_LOOKBACK_DAYS}d")
                continue
            eligible.append(contact_id)
        if not eligible:
            continue

        bullets = "\n".join(
            f"   • <https://app.hubspot.com/contacts/{PORTAL_ID}/record/0-1/{cid}|contact {cid}>"
            for cid in eligible
        )
        sections.append(f"*<https://app.hubspot.com/contacts/{PORTAL_ID}/company/{company_id}|{domain}>*\n{bullets}")
        surfaced += len(eligible)

    if not sections:
        print("  No contacts to surface for manual enrollment.")
        return 0

    message = (
        f":megaphone: *Tier 2 Sequence Enrollment — {surfaced} contact(s)*\n\n"
        f"_Sequence API is unavailable in this portal. Open each contact and"
        f" click *Enroll in sequence → your Tier 2 sequence*"
        f" (sequence ID `{TIER2_SEQUENCE_ID}`)._\n\n"
        + "\n\n".join(sections)
    )
    if dry_run:
        print("\n[DRY RUN] Would post Tier 2 sequence-enrollment instructions to Slack:")
        print(message)
    else:
        _post_to_slack(message, TIER2_APPROVAL_SLACK_DM)
        print(f"  ✓ Tier 2 sequence-enrollment instructions posted to Slack ({surfaced} contact(s))")

    return surfaced


# Clay writes the outcome of its enrichment waterfall to the
# `clay_enrichment_status` picklist on the contact. Valid values observed in
# the live HubSpot portal: SUCCESS, PARTIAL SUCCESS, ERROR, BLANK.
#
# The legacy `enriched_by_clay` boolean is never populated by Clay in this
# portal. Empirically Clay's waterfall is strict — many contacts come back
# BLANK even when they have a good jobtitle + LinkedIn URL (the waterfall
# expects phone as well, which our Clay table doesn't currently hit). We
# route on Clay's own verdict when it says SUCCESS / PARTIAL SUCCESS, AND
# fall back for T1/T2 BLANK contacts that nonetheless have the minimum data
# a BDR needs to personalize outbound: a non-junk title and a LinkedIn URL.
CLAY_ENRICHED_STATUSES = {"SUCCESS", "PARTIAL SUCCESS"}

# Contact properties read to evaluate routability. Kept in one place so
# _is_routable, route_pending_accounts, and route_newly_active_accounts
# stay in sync on which fields they load.
ROUTABILITY_PROPERTIES = [
    "clay_enrichment_status",
    "jobtitle",
    "linkedinbio",
    "linkedin_url",
    "hs_linkedin_url",
]

# Heuristic junk-title detector — Apollo occasionally scrapes sentences or
# archived LinkedIn summaries into `jobtitle` (e.g. "The Operations Is Now
# Finished"). The checks below reject the obvious cases without filtering
# out short generic titles like "Developer" that still help a BDR
# personalize outreach.
_JUNK_TITLE_BLOCKLIST = (
    "is now finished",
    "no longer",
    "retired from",
    "former ",
)


def _is_junk_title(title: str | None) -> bool:
    """True when `title` looks like a scraped sentence rather than a real job title."""
    if not title:
        return True
    t = title.strip()
    if not t or t.lower() in {"null", "unknown", "n/a", "none"}:
        return True
    if len(t) > 80:
        return True
    # Sentence punctuation — real titles don't end a sentence mid-stream.
    if any(p in t for p in (". ", "! ", "? ")):
        return True
    # First-person / full-sentence openers.
    lower = t.lower()
    if lower.startswith(("the ", "i ", "my ", "we ")):
        return True
    if any(phrase in lower for phrase in _JUNK_TITLE_BLOCKLIST):
        return True
    # All-caps long strings are almost always scrape artefacts.
    if len(t) > 20 and t.isupper():
        return True
    return False


def _is_routable(props: dict, tier: str) -> bool:
    """Decide whether a single champion contact is workable by a BDR.

    Gate priority:
      1. If Clay declared SUCCESS / PARTIAL SUCCESS AND the title isn't junk,
         route. (Filters out the rare SUCCESS-with-garbage-title case.)
      2. For T1/T2 BLANK fallback: if the contact has a non-junk title AND
         any LinkedIn URL, route anyway. Empirically Clay marks many
         workable contacts BLANK because the phone provider failed.
      3. T3 never falls back. No secondary signal — rely on Clay's verdict.
    """
    status = props.get("clay_enrichment_status")
    title = props.get("jobtitle")
    title_ok = not _is_junk_title(title)
    has_linkedin = any(
        props.get(k) for k in ("linkedinbio", "linkedin_url", "hs_linkedin_url")
    )

    if status in CLAY_ENRICHED_STATUSES and title_ok:
        return True
    if tier in {"tier_1", "tier_2"} and title_ok and has_linkedin:
        return True
    return False


def _is_enriched_by_clay(
    contact_ids: list[str],
    dry_run: bool = False,
    tier: str = "tier_2",
) -> dict[str, bool]:
    """Return {contact_id: routable} for the given contacts.

    The name is historical — the gate now considers jobtitle + LinkedIn too,
    see `_is_routable`. *tier* determines whether BLANK-with-data fallback
    applies. In dry-run mode every contact is assumed routable so the
    preview shows what full routing would look like.
    """
    if dry_run:
        return {cid: True for cid in contact_ids}
    if not contact_ids:
        return {}
    try:
        result = hubspot_request("POST", "/crm/v3/objects/contacts/batch/read", {
            "inputs": [{"id": cid} for cid in contact_ids],
            "properties": ROUTABILITY_PROPERTIES,
        })
        return {
            c["id"]: _is_routable(c.get("properties", {}), tier)
            for c in result.get("results", [])
        }
    except Exception as exc:  # noqa: BLE001
        print(f"    ⚠  Could not read enrichment status: {exc} — proceeding anyway")
        return {cid: True for cid in contact_ids}  # err on the side of routing


def route_newly_active_accounts(
    promotions: list[dict],
    domain_to_contact_ids: dict[str, list[str]],
    domain_row_map: dict[str, dict],
    current_state: dict[str, dict],
    now_ms: int,
    dry_run: bool = False,
) -> tuple[int, int, list[dict]]:
    """Enroll newly promoted accounts into BDR routing workflows and sequences.

    Enrichment gate: only contacts whose `clay_enrichment_status` is in
    `CLAY_ENRICHED_STATUSES` (SUCCESS or PARTIAL SUCCESS) are routed. If
    ALL champion contacts for a domain are still unenriched (just queued
    this sync), routing is deferred entirely and pqa_bdr_routed_at is NOT
    stamped, so --route-newly-active can pick them up once Clay finishes.

    For each account in *promotions* that has not already been routed
    (pqa_bdr_routed_at is unset):
      1. Enroll the company in the Account routing workflow.
      2. For each enriched champion contact that has no recent sales email:
         - Enroll in the Contact owner routing workflow (both tiers).
         - Tier 1: enroll in TIER1_SEQUENCE_ID immediately (manual first email).
         - Tier 2: HOLD — collect into tier2_pending for BDR approval.
      3. Stamp pqa_bdr_routed_at only if at least one contact was processed.

    Returns (routed_count, skipped_count, tier2_pending).
    """
    routed = 0
    skipped = 0
    tier2_pending: list[dict] = []

    for p in promotions:
        domain = p["domain"]
        company_id = p["company_id"]
        tier = _tier(p["score"])

        # Guard: skip if already routed in a previous run
        if current_state.get(company_id, {}).get("pqa_bdr_routed_at"):
            skipped += 1
            print(f"    → Skipping {domain} — already routed")
            continue

        print(f"  → Routing {domain} ({tier}, score {p['score']:.1f})")

        # Workflows self-trigger on property writes the main sync just
        # performed (pqa_status -> active for the company; pql_champion_rank
        # set/changed for contacts). The script's job here is only to gate
        # the routed-at stamp + queue Tier 2 sequence-enrollment Slack.
        contact_ids = domain_to_contact_ids.get(domain, [])
        enrichment = _is_enriched_by_clay(contact_ids, dry_run=dry_run)
        enriched_ids = [cid for cid in contact_ids if enrichment.get(cid)]
        deferred_ids = [cid for cid in contact_ids if not enrichment.get(cid)]

        if deferred_ids and not dry_run:
            print(f"    ⏳ {len(deferred_ids)} contact(s) pending Clay enrichment — will route via --route-newly-active")

        if not enriched_ids and not dry_run:
            # No enriched contacts yet — skip stamp so --route-newly-active
            # retries once enrichment completes.
            print(f"    ⏳ All contacts pending enrichment — deferring {domain} routing")
            routed += 1  # company was processed (workflows fired), but stamp is held
            continue

        eligible_contact_ids: list[str] = []
        for contact_id in enriched_ids:
            if not dry_run and contact_received_sales_email_recently(contact_id):
                print(f"    → Skipping contact {contact_id} — sales email in last {SALES_EMAIL_LOOKBACK_DAYS}d")
                continue
            if dry_run:
                print(f"    [dry-run] Would check recent email history for contact {contact_id}")
            eligible_contact_ids.append(contact_id)

        if tier == "tier_2" and eligible_contact_ids:
            tier2_pending.append({
                "domain": domain,
                "company_id": company_id,
                "score": p["score"],
                "contact_ids": eligible_contact_ids,
            })
            print(f"    ⏸  {len(eligible_contact_ids)} contact(s) held for Tier 2 sequence approval")

        # 3. Stamp routing timestamp only when at least some contacts were processed.
        #    If deferred_ids remain, skip stamp so --route-newly-active retries them.
        if not deferred_ids:
            if not dry_run:
                try:
                    hubspot_request(
                        "PATCH",
                        f"/crm/v3/objects/companies/{company_id}",
                        data={"properties": {"pqa_bdr_routed_at": str(now_ms)}},
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"    ⚠  Could not stamp pqa_bdr_routed_at on {domain}: {exc}")
            else:
                print(f"    [dry-run] Would stamp pqa_bdr_routed_at on {domain} ({company_id})")
        else:
            print(f"    ⏳ Holding pqa_bdr_routed_at stamp on {domain} until enrichment completes")

        routed += 1

    return routed, skipped, tier2_pending


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Weekly PQA/PQL score sync: BQ → HubSpot.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview all changes without writing to HubSpot.")
    parser.add_argument("--top-n", type=int, default=None,
                        help="Only process the top N domains by score.")
    parser.add_argument("--domain", type=str, default=None,
                        help="Process a single domain (for testing).")
    parser.add_argument("--domains", type=str, default=None,
                        help="Comma-separated list of specific domains to process.")
    parser.add_argument(
        "--approve-tier2-domains",
        type=str,
        default=None,
        metavar="DOMAIN1,DOMAIN2",
        help="Deprecated. Lead routing and sequence enrollment intent now run via Dagster.",
    )
    parser.add_argument(
        "--route-newly-active",
        action="store_true",
        help="Deprecated. Lead routing and sequence enrollment intent now run via Dagster.",
    )
    parser.add_argument(
        "--tier",
        type=str,
        default=None,
        choices=["tier_1", "tier_2", "tier_3"],
        help="Restrict --route-newly-active to a single tier (e.g. --tier tier_1 "
             "to route only Tier 1 accounts and leave Tier 2/3 for a later run).",
    )
    args = parser.parse_args()

    dry_run: bool = args.dry_run
    now_ms = _now_ms()

    if dry_run:
        print("=== DRY RUN — no changes will be written to HubSpot ===\n")

    if args.route_newly_active:
        parser.error("--route-newly-active is disabled. Use the Dagster plg_hubspot_sync job.")
    if args.approve_tier2_domains:
        parser.error("--approve-tier2-domains is disabled. Use the Dagster plg_hubspot_sync job.")

    domains_list = [d.strip() for d in args.domains.split(",")] if args.domains else None

    # ── Step 1: Pull scores from BQ ─────────────────────────────────────────
    print("Step 1 — Pulling latest domain scores from BigQuery…")
    domain_rows = fetch_domain_scores(
        top_n=args.top_n,
        domain_filter=args.domain,
        domains_filter=domains_list,
    )
    ineligible_rows = [r for r in domain_rows if r.get("is_eligible") is False]
    scored_rows = [r for r in domain_rows if r.get("is_eligible") is not False]
    print(f"  {len(scored_rows)} eligible domain(s) scored ≥ {MIN_SCORE}")
    print(f"  {len(ineligible_rows)} ineligible domain(s) included for HubSpot cleanup")

    if not domain_rows:
        print("  Nothing to sync.")
        return

    domains = [r["email_domain"] for r in domain_rows]
    domain_row_map = {r["email_domain"]: r for r in domain_rows}

    # ── Step 2: Resolve domains → HubSpot company IDs ───────────────────────
    print("\nStep 2 — Resolving domains → HubSpot companies…")
    cache = load_domain_cache()
    company_id_map: dict[str, str] = {}  # domain → company_id

    new_companies = 0
    for domain in domains:
        bq_row = domain_row_map[domain]
        allow_create = bq_row.get("is_eligible") is not False
        cid, is_new = resolve_company_id(
            domain,
            cache,
            dry_run=dry_run,
            allow_create=allow_create,
        )
        if cid:
            company_id_map[domain] = cid
        if is_new:
            new_companies += 1

    if not dry_run:
        save_domain_cache(cache)

    print(f"  Resolved {len(company_id_map)} / {len(domains)} domains ({new_companies} new companies created)")

    # ── Step 3: Load Target Account exclusions ───────────────────────────────
    print("\nStep 3 — Loading Target Account exclusions…")
    excluded_ids = get_target_account_company_ids() if not dry_run else set()
    excluded_domains = {d for d, cid in company_id_map.items() if cid in excluded_ids}
    active_domain_map = {d: cid for d, cid in company_id_map.items()
                         if cid not in excluded_ids}
    print(f"  {len(excluded_domains)} domains excluded (Target Accounts)")
    print(f"  {len(active_domain_map)} domains to process")

    # ── Step 4: Read current HubSpot company state (for dampening) ──────────
    print("\nStep 4 — Reading current company state from HubSpot…")
    company_ids_to_process = list(active_domain_map.values())
    current_state = batch_read_company_state(company_ids_to_process) if not dry_run else {}
    print(f"  Fetched state for {len(current_state)} companies")

    # ── Step 5: Build company update payloads + dampening ───────────────────
    print("\nStep 5 — Computing new scores, tiers, and status transitions…")
    company_payloads: list[dict] = []
    promotions: list[dict] = []
    demotions: list[dict] = []

    for domain, company_id in active_domain_map.items():
        bq_row = domain_row_map[domain]
        state = current_state.get(company_id, {})
        payload = build_company_payload(company_id, bq_row, state, now_ms)
        company_payloads.append(payload)

        meta = payload["_meta"]
        prev = meta["prev_status"] or "nurture"
        new = meta["new_status"]

        if prev != "active" and new == "active":
            promotions.append({"domain": domain, "score": meta["new_score"],
                                "delta": meta["score_delta"], "company_id": company_id})
        elif prev == "active" and new != "active":
            demotions.append({"domain": domain, "score": meta["new_score"],
                              "company_id": company_id})

    # ── Step 6: Batch-update companies ──────────────────────────────────────
    print(f"\nStep 6 — Updating {len(company_payloads)} companies in HubSpot…")
    batch_update_companies(company_payloads, dry_run=dry_run)
    if not dry_run:
        print(f"  ✓ {len(company_payloads)} companies updated "
              f"({math_ceil(len(company_payloads) / BATCH_SIZE)} API calls)")

    # ── Step 7: Pull champion contacts ──────────────────────────────────────
    print("\nStep 7 — Pulling champion contacts from BigQuery…")
    champions_by_domain = fetch_champions(domains=list(active_domain_map.keys()))
    total_champs = sum(len(v) for v in champions_by_domain.values())
    print(f"  {total_champs} champion contacts across {len(champions_by_domain)} domains")

    # ── Step 7.5: Legacy enrichment disabled ────────────────────────────────
    print("\nStep 7.5 — Legacy enrichment disabled (handled by Dagster lead sync)…")
    enrichment_company_ids: set[str] = set()
    print("  No contacts will be queued for Clay or lead routing by scoring_sync.py")

    # ── Step 8: Resolve contacts + build update payloads ────────────────────
    print("\nStep 8 — Resolving contacts in HubSpot…")
    contact_payloads: list[dict] = []

    association_pairs: list[tuple[str, str]] = []  # (contact_id, company_id)
    domain_to_contact_ids: dict[str, list[str]] = {}  # used for BDR routing in Step 11
    new_contacts = 0
    enriched_contact_count = 0

    for domain, champions in champions_by_domain.items():
        company_id = active_domain_map.get(domain)
        for champ in champions:
            email = champ.get("user_email")
            if not email:
                continue

            contact_id, is_new_contact = resolve_contact_id(
                email,
                dry_run=dry_run,
            )
            if not contact_id:
                continue

            if is_new_contact:
                new_contacts += 1

            # Track for company association and BDR routing
            domain_to_contact_ids.setdefault(domain, []).append(contact_id)
            if company_id:
                association_pairs.append((contact_id, company_id))

            props: dict[str, str] = {
                "pql_score":              str(round(_to_float(champ.get("pql_score")), 2)),
                "pql_champion_rank":       str(int(_to_float(champ.get("pql_champion_rank")))),
                "pql_is_team_admin":       "true" if champ.get("pql_is_team_admin") else "false",
                "pql_ai_credit_usage_30d": str(round(_to_float(champ.get("pql_ai_credit_usage_30d")), 2)),
                "pql_activity_frequency":  str(int(_to_float(champ.get("pql_activity_frequency")))),
                "pql_hit_credit_limit_14d":"true" if champ.get("pql_hit_credit_limit_14d") else "false",
                "pql_last_scored_at":       str(now_ms),
            }
            contact_payloads.append({
                "id": contact_id,
                "properties": props,
            })

    # ── Step 9: Batch-update contacts ───────────────────────────────────────
    print(f"\nStep 9 — Updating {len(contact_payloads)} contacts in HubSpot…")
    batch_update_contacts(contact_payloads, dry_run=dry_run)
    if not dry_run:
        print(f"  ✓ {len(contact_payloads)} contacts updated ({new_contacts} new)")

    # ── Step 10: Associate contacts → companies ──────────────────────────────
    print(f"\nStep 10 — Associating {len(association_pairs)} contacts to their companies…")
    associate_contacts_to_companies(association_pairs, dry_run=dry_run)
    if not dry_run:
        print(f"  ✓ {len(association_pairs)} contact-company associations written")

    # ── Step 10.5: Legacy enrichment/routing disabled ───────────────────────
    stamped_enriched = 0
    routed = 0
    skipped_routing = 0
    tier2_pending: list[dict] = []
    print("\nStep 10.5 — Legacy pqa_enriched_at stamp skipped")
    print("Step 11 — Legacy BDR routing disabled (handled by Dagster lead sync)")

    # ── Summary ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("SYNC SUMMARY")
    print("=" * 60)
    print(f"  Domains processed  : {len(active_domain_map)}")
    print(f"  Ineligible cleanup : {len([d for d in active_domain_map if domain_row_map[d].get('is_eligible') is False])}")
    print(f"  Domains excluded   : {len(excluded_domains)} (Target Accounts)")
    print(f"  Companies updated  : {len(company_payloads)}")
    print(f"  Contacts updated   : {len(contact_payloads)} ({new_contacts} new)")
    print(f"  Associations       : {len(association_pairs)} contact→company links")
    print(f"  Promotions → Active: {len(promotions)}")
    print("  BDR routed         : disabled (Dagster lead sync owns routing)")
    print("  T2 pending approval: disabled (Dagster lead sync owns sequence intent)")
    print(f"  Demotions          : {len(demotions)}")
    print(f"  Enrichment flagged : {enriched_contact_count} contact(s) across "
          f"{len(enrichment_company_ids)} first-time T1/T2 PQA(s)")
    print(f"  Enrichment stamped : {stamped_enriched} company(ies) with pqa_enriched_at")

    if promotions:
        print("\n  NEW ACTIVE ACCOUNTS:")
        for p in sorted(promotions, key=lambda x: -x["score"])[:10]:
            delta_str = f" (+{p['delta']:.1f})" if p["delta"] > 0 else f" ({p['delta']:.1f})"
            print(f"    {p['domain']:<40} score {p['score']:.1f}{delta_str}")

    if demotions:
        print("\n  DE-PRIORITIZED:")
        for d in demotions[:10]:
            print(f"    {d['domain']:<40} score {d['score']:.1f}")

    if dry_run:
        print("\n  [DRY RUN — no changes were written]")


# ---------------------------------------------------------------------------
# Import needed for batch size calc in summary (avoid import at top for speed)
# ---------------------------------------------------------------------------
from math import ceil as math_ceil  # noqa: E402


if __name__ == "__main__":
    main()
