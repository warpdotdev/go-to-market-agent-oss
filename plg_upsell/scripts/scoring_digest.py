#!/usr/bin/env python3
"""
Weekly PLG Slack digest — posts Tuesday morning after the scoring sync.

Sections:
  1. NEW HOT ACCOUNTS — promoted to Active since last sync (pqa_weeks_above_threshold = 0)
  2. TOP MOVERS       — biggest pqa_score_delta (positive) among Active accounts
  3. WATCH LIST       — Active accounts with pqa_weeks_below_threshold >= 1
  4. TOP CHAMPIONS    — highest pql_score contacts across Active accounts
  5. PIPELINE WINS    — deals that hit SAO / SQO / Closed Won in trailing 7 days
                        from any PLG-active account

Usage:
    python plg_upsell/scripts/scoring_digest.py                       # post to Slack
    python plg_upsell/scripts/scoring_digest.py --dry-run             # print to stdout only
    python plg_upsell/scripts/scoring_digest.py --channel C123        # override Slack channel
    python plg_upsell/scripts/scoring_digest.py --delay-minutes 60    # wait N mins before posting

Scheduling (recommended):
    Run scoring_sync.py first, then scoring_digest.py with a delay to allow
    lead routing before owners are tagged in the message.
    Cron example (times are PDT = UTC-7; update to PST/UTC-8 in winter):
        0 14 * * 2  python plg_upsell/scripts/scoring_sync.py               # 7am PT
        0 16 * * 2  python plg_upsell/scripts/scoring_sync.py --route-newly-active  # 9am PT
        0 17 * * 2  python plg_upsell/scripts/scoring_digest.py --delay-minutes 0   # 10am PT

Env vars:
    PLG_SLACK_BOT_TOKEN     — Slack bot token (preferred; uses chat.postMessage)
    HUBSPOT_SLACK_WEBHOOK   — Slack incoming webhook URL (fallback)
    SLACK_CHANNEL           — default channel (overridden by --channel)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from google.cloud import bigquery
from google.oauth2 import service_account

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from hubspot_agent.client import hubspot_request

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PORTAL_ID = os.environ.get("HUBSPOT_PORTAL_ID", "000000000")

TOP_MOVERS_LIMIT = 5
WATCH_LIST_LIMIT = 5
TOP_CHAMPIONS_LIMIT = 10
PIPELINE_DAYS = 7

# Tier thresholds (computed from BQ pql_score, matching scoring_sync.py).
# Illustrative example cutoffs — calibrate for your own funnel.
TIER1_THRESHOLD = 70
TIER2_THRESHOLD = 50

# BigQuery
GCP_PROJECT = os.environ.get("GCP_PROJECT", "example-gcp-project")
BQ_DATASET = "analytics"
BQ_SCORES_TABLE = "plg_upsell_domain_scores"
BQ_CHAMPIONS_TABLE = "plg_upsell_domain_champions"

# HubSpot deal stage IDs that count as wins.
# TODO: confirm Stage 1 (SAO) and Stage 2 (SQO) internal IDs after pipeline update.
# Stage 7 (closedwon) is a HubSpot default and should be correct.
# Find all stage IDs at: Settings > CRM > Deals > Pipelines > (pipeline) > Edit
PIPELINE_WIN_STAGES = {
    "appointmentscheduled": "SAO",   # Stage 1 - Sales Accepted Opportunity
    "qualifiedtobuy": "SQO",         # Stage 2 - Sales Qualified Opportunity
    "closedwon": "Closed Won",       # Stage 7 - Closed Won
}

# Slack — prefer bot token (chat.postMessage), fall back to incoming webhook
SLACK_BOT_TOKEN = os.environ.get("PLG_SLACK_BOT_TOKEN", "")
SLACK_WEBHOOK = os.environ.get("HUBSPOT_SLACK_WEBHOOK", "")
DEFAULT_CHANNEL = os.environ.get("SLACK_CHANNEL", "")

# Warp Drive Prompt URL that kicks off the draft-champion-outreach skill.
# Expected shape:
#   https://app.warp.dev/drive/prompt/Draft-PLG-champion-outreach-<id>
# The digest appends champion + company context as query-string arguments.
PLG_OUTREACH_PROMPT_URL = os.environ.get("PLG_OUTREACH_PROMPT_URL", "")

# Load credentials from .env if not in environment
_env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env")
if os.path.exists(_env_path) and (
    (not SLACK_BOT_TOKEN and not SLACK_WEBHOOK) or not PLG_OUTREACH_PROMPT_URL
):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line.startswith("PLG_SLACK_BOT_TOKEN=") and not SLACK_BOT_TOKEN:
                SLACK_BOT_TOKEN = _line.split("=", 1)[1]
            elif _line.startswith("SLACK_BOT_TOKEN=") and not SLACK_BOT_TOKEN:
                SLACK_BOT_TOKEN = _line.split("=", 1)[1]
            elif _line.startswith("HUBSPOT_SLACK_WEBHOOK=") and not SLACK_WEBHOOK:
                SLACK_WEBHOOK = _line.split("=", 1)[1]
            elif _line.startswith("PLG_OUTREACH_PROMPT_URL=") and not PLG_OUTREACH_PROMPT_URL:
                PLG_OUTREACH_PROMPT_URL = _line.split("=", 1)[1]


# ---------------------------------------------------------------------------
# HubSpot data fetchers
# ---------------------------------------------------------------------------

def _float(val: Any) -> float:
    if val is None:
        return 0.0
    if isinstance(val, Decimal):
        return float(val)
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_identifier(value: str, label: str) -> str:
    if not IDENTIFIER_RE.match(value):
        raise ValueError(f"Invalid {label}: {value!r}")
    return value


def _build_bigquery_client(project: str) -> bigquery.Client:
    service_account_json = os.environ.get("GCP_SERVICE_ACCOUNT_JSON")
    if service_account_json:
        try:
            info = json.loads(service_account_json)
        except json.JSONDecodeError as exc:
            raise RuntimeError("GCP_SERVICE_ACCOUNT_JSON is not valid JSON") from exc
        credentials = service_account.Credentials.from_service_account_info(info)
        return bigquery.Client(project=project, credentials=credentials)
    return bigquery.Client(project=project)


def _hs_url(object_type: str, object_id: str) -> str:
    type_path = {"companies": "company", "contacts": "contact", "deals": "deal"}
    return f"https://app.hubspot.com/contacts/{PORTAL_ID}/{type_path.get(object_type, object_type)}/{object_id}"


def fetch_active_companies() -> list[dict]:
    """Return all companies with pqa_status = active, sorted by pqa_score desc."""
    props = [
        "name", "domain", "pqa_score", "pqa_score_delta", "pqa_score_prev",
        "pqa_tier", "pqa_avg_wau", "pqa_ai_credits_30d", "pqa_reload_spend_14d",
        "pqa_users_hitting_limits_14d", "pqa_weeks_above_threshold",
        "pqa_weeks_below_threshold", "pqa_last_scored_at", "hubspot_owner_id",
    ]
    companies: list[dict] = []
    after = None

    while True:
        body: dict = {
            "filterGroups": [{"filters": [
                {"propertyName": "pqa_status", "operator": "EQ", "value": "active"}
            ]}],
            "properties": props,
            "sorts": [{"propertyName": "pqa_score", "direction": "DESCENDING"}],
            "limit": 100,
        }
        if after:
            body["after"] = after

        result = hubspot_request("POST", "/crm/v3/objects/companies/search", body)
        for item in result.get("results", []):
            p = item.get("properties", {})
            companies.append({
                "id": item["id"],
                "name": p.get("name") or p.get("domain", "?"),
                "domain": p.get("domain", ""),
                "score": _float(p.get("pqa_score")),
                "delta": _float(p.get("pqa_score_delta")),
                "tier": p.get("pqa_tier", ""),
                "avg_wau": _float(p.get("pqa_avg_wau")),
                "credits_30d": _float(p.get("pqa_ai_credits_30d")),
                "reload_spend": _float(p.get("pqa_reload_spend_14d")),
                "limit_hits": int(_float(p.get("pqa_users_hitting_limits_14d"))),
                "weeks_above": int(_float(p.get("pqa_weeks_above_threshold"))),
                "weeks_below": int(_float(p.get("pqa_weeks_below_threshold"))),
                "owner_id": p.get("hubspot_owner_id", ""),
                "url": _hs_url("companies", item["id"]),
            })

        paging = result.get("paging", {}).get("next", {})
        after = paging.get("after")
        if not after:
            break

    return companies


def fetch_pipeline_wins(active_company_ids: set, days: int = PIPELINE_DAYS) -> list:
    """Return deals from PLG active accounts that entered a win stage in the last N days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff_ms = str(int(cutoff.timestamp() * 1000))

    result = hubspot_request("POST", "/crm/v3/objects/deals/search", {
        "filterGroups": [{"filters": [
            {"propertyName": "hs_lastmodifieddate", "operator": "GTE", "value": cutoff_ms},
            {"propertyName": "dealstage", "operator": "IN",
             "values": list(PIPELINE_WIN_STAGES.keys())},
        ]}],
        "properties": ["dealname", "dealstage", "amount", "closedate",
                       "hs_lastmodifieddate", "hubspot_owner_id"],
        "associations": ["companies"],
        "limit": 100,
    })

    wins = []
    for item in result.get("results", []):
        p = item.get("properties", {})
        # Check if associated company is a PLG account
        assoc_companies = (
            item.get("associations", {})
            .get("companies", {})
            .get("results", [])
        )
        company_ids = {a["id"] for a in assoc_companies}
        if not company_ids & active_company_ids:
            continue

        stage_label = PIPELINE_WIN_STAGES.get(p.get("dealstage", ""), p.get("dealstage", "?"))
        wins.append({
            "id": item["id"],
            "name": p.get("dealname", "Unnamed deal"),
            "stage": stage_label,
            "amount": _float(p.get("amount")),
            "modified": p.get("hs_lastmodifieddate", "")[:10],
            "url": _hs_url("deals", item["id"]),
        })

    return wins


def _tier_from_score(score: float) -> str:
    """Compute tier from a 0–100 score using the same thresholds as scoring_sync."""
    if score >= TIER1_THRESHOLD:
        return "tier_1"
    if score >= TIER2_THRESHOLD:
        return "tier_2"
    return "tier_3"


def fetch_domain_scores_bq(
    *,
    project: str = GCP_PROJECT,
    dataset: str = BQ_DATASET,
    scores_table: str = BQ_SCORES_TABLE,
    allowed_domains: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Fetch per-domain scoring data from BigQuery, keyed by email_domain.

    Returns ``{}`` if ``allowed_domains`` is an empty list (no query issued).
    ``None`` means no filter.
    """
    if allowed_domains is not None and len(allowed_domains) == 0:
        return {}

    dataset = _validate_identifier(dataset, "dataset")
    scores_table = _validate_identifier(scores_table, "scores_table")

    client = _build_bigquery_client(project=project)
    scores_ref = f"`{project}.{dataset}.{scores_table}`"

    domain_filter_sql = ""
    query_params: list[Any] = []
    if allowed_domains is not None:
        domain_filter_sql = "where email_domain in unnest(@allowed_domains)"
        query_params.append(
            bigquery.ArrayQueryParameter("allowed_domains", "STRING", list(allowed_domains))
        )

    sql = f"""
select
    email_domain, company_name, company_size, industry,
    pql_score, breadth_score, depth_score, velocity_score, urgency_score,
    active_users_last_30d, avg_wau, total_credits_30d, wow_growth_pct,
    limit_hits, users_hitting_limits, reload_dollars, users_upgraded, new_domain_members
from {scores_ref}
{domain_filter_sql}
"""
    job_config = bigquery.QueryJobConfig(query_parameters=query_params)
    rows = client.query(sql, job_config=job_config).result()
    return {
        row["email_domain"]: {key: row[key] for key in row.keys()}
        for row in rows
    }


def fetch_top_champions_bq(
    *,
    project: str = GCP_PROJECT,
    dataset: str = BQ_DATASET,
    scores_table: str = BQ_SCORES_TABLE,
    champions_table: str = BQ_CHAMPIONS_TABLE,
    top_n: int = TOP_CHAMPIONS_LIMIT,
    allowed_domains: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Fetch top champion contacts from BigQuery.

    If ``allowed_domains`` is provided, restrict results to champions whose
    ``email_domain`` is in the list. An empty list short-circuits and returns
    no rows (no query is issued). ``None`` means no filtering.
    """
    if allowed_domains is not None and len(allowed_domains) == 0:
        return []

    dataset = _validate_identifier(dataset, "dataset")
    scores_table = _validate_identifier(scores_table, "scores_table")
    champions_table = _validate_identifier(champions_table, "champions_table")

    client = _build_bigquery_client(project=project)
    scores_ref = f"`{project}.{dataset}.{scores_table}`"
    champions_ref = f"`{project}.{dataset}.{champions_table}`"

    domain_filter_sql = ""
    query_params: list[Any] = [bigquery.ScalarQueryParameter("top_n", "INT64", top_n)]
    if allowed_domains is not None:
        domain_filter_sql = "where email_domain in unnest(@allowed_domains)"
        query_params.append(
            bigquery.ArrayQueryParameter("allowed_domains", "STRING", list(allowed_domains))
        )

    sql = f"""
with top_domains as (
    select
        email_domain, company_name, company_size, industry,
        pql_score, breadth_score, depth_score, velocity_score, urgency_score,
        active_users_last_30d, avg_wau, total_credits_30d, wow_growth_pct,
        limit_hits, users_hitting_limits, reload_dollars, users_upgraded, new_domain_members
    from {scores_ref}
    {domain_filter_sql}
    order by pql_score desc
    limit @top_n
)
select
    td.*,
    dc.user_email, dc.champion_score, dc.rank_in_domain,
    dc.credits_used_t30d, dc.days_active_in_last_30, dc.is_team_admin,
    dc.limit_hit_count, dc.grouped_survey_role
from top_domains td
inner join {champions_ref} dc using (email_domain)
where dc.rank_in_domain = 1
order by td.pql_score desc
"""
    job_config = bigquery.QueryJobConfig(query_parameters=query_params)
    rows = client.query(sql, job_config=job_config).result()
    return [{key: row[key] for key in row.keys()} for row in rows]


# ---------------------------------------------------------------------------
# Draft-outreach URL builder
# ---------------------------------------------------------------------------

# Cap the total URL length so Slack renders it cleanly. Slack's hard limit for
# link URLs is ~3000 chars; leave headroom for the prompt base + safety.
_DRAFT_URL_MAX_LEN = 2800


def _urgency_signals_token(row: dict[str, Any]) -> str:
    """Build a compact comma-separated urgency token for the outreach prompt."""
    parts: list[str] = []
    limit_hits = int(_float(row.get("limit_hits")))
    if limit_hits:
        users_hitting = int(_float(row.get("users_hitting_limits")))
        parts.append(f"limit_hits_14d:{limit_hits}({users_hitting}users)")
    reload_dollars = _float(row.get("reload_dollars"))
    if reload_dollars > 0:
        parts.append(f"reload_spend:${int(reload_dollars)}")
    users_upgraded = int(_float(row.get("users_upgraded")))
    if users_upgraded:
        parts.append(f"upgrades:{users_upgraded}")
    new_members = int(_float(row.get("new_domain_members")))
    if new_members:
        parts.append(f"new_members:{new_members}")
    return ",".join(parts)


def _draft_outreach_url(row: dict[str, Any], company: dict[str, Any] | None) -> str | None:
    """Return the Warp Drive Prompt URL for drafting outreach to this champion.

    Returns ``None`` if ``PLG_OUTREACH_PROMPT_URL`` is not configured — the
    caller should render the champion card without the link in that case.
    """
    if not PLG_OUTREACH_PROMPT_URL:
        return None

    email = (row.get("user_email") or "").strip()
    if not email:
        return None

    company_name = (company or {}).get("name") or row.get("company_name") or row.get("email_domain", "")
    hubspot_company_url = (company or {}).get("url", "")

    champion_name = email.split("@", 1)[0] if "@" in email else email

    args = {
        "champion_email":       email,
        "champion_name":        champion_name,
        "domain":               row.get("email_domain", ""),
        "company_name":         company_name,
        "hubspot_company_url": hubspot_company_url,
        "hubspot_contact_url": "",  # populated in phase 2 once contact IDs are looked up
        "pql_score":            f"{_float(row.get('pql_score')):.1f}",
        "credits_30d":          str(int(_float(row.get("credits_used_t30d")))),
        "is_team_admin":        "true" if row.get("is_team_admin") else "false",
        "role":                 row.get("grouped_survey_role") or "unknown",
        "urgency_signals":      _urgency_signals_token(row),
        "tier":                 "tier_1",
    }

    # Drop empty values to keep the URL compact.
    args = {k: v for k, v in args.items() if v not in (None, "")}
    query = urllib.parse.urlencode(args, safe="")
    url = f"{PLG_OUTREACH_PROMPT_URL}?{query}"

    # If we're over budget, drop urgency_signals first (it's the largest variable)
    # and rebuild. We keep the core identification fields no matter what.
    if len(url) > _DRAFT_URL_MAX_LEN and "urgency_signals" in args:
        args.pop("urgency_signals")
        query = urllib.parse.urlencode(args, safe="")
        url = f"{PLG_OUTREACH_PROMPT_URL}?{query}"

    return url


# ---------------------------------------------------------------------------
# Message builder
# ---------------------------------------------------------------------------

def _tier_emoji(tier: str) -> str:
    return {"tier_1": "🥇", "tier_2": "🥈", "tier_3": "🥉"}.get(tier, "")


_MEDALS = ["🥇", "🥈", "🥉"]


def _urgency_fragments(row: dict[str, Any]) -> list[str]:
    """Return human-readable urgency fragments with explicit time windows.

    Fragments are omitted when the underlying signal is zero. Windows:
      - limit hits / users hitting limits / reload spend / new members: 14d
      - self-upgrades: 30d
    """
    fragments: list[str] = []
    limit_hits = int(_float(row.get("limit_hits")))
    users_hitting = int(_float(row.get("users_hitting_limits")))
    if limit_hits > 0:
        user_word = "user" if users_hitting == 1 else "users"
        fragments.append(f"{limit_hits} limit hits (14d, {users_hitting} {user_word})")
    reload_spend = _float(row.get("reload_dollars") or row.get("reload_spend"))
    if reload_spend > 0:
        fragments.append(f"${reload_spend:,.0f} reload (14d)")
    upgrades = int(_float(row.get("users_upgraded")))
    if upgrades > 0:
        fragments.append(f"{upgrades} self-upgraded (30d)")
    new_members = int(_float(row.get("new_domain_members") or row.get("new_members")))
    if new_members > 0:
        fragments.append(f"{new_members} new members (14d)")
    return fragments


# Play generation: each candidate is (priority, account_domain, bullet_text).
# Higher priority surfaces first. account_domain is used to dedupe so we don't
# spam the same company across multiple plays — at most one play per account.
_Play = tuple[int, str, str]

# Enterprise-cohort play thresholds.
# When ≥ENTERPRISE_COHORT_MIN newly-active accounts have
# company_size ≥ ENTERPRISE_SIZE_MIN, a single themed "enterprise wave" bullet
# is emitted instead of N per-company enterprise bullets.
# Illustrative example thresholds — calibrate for your own funnel.
ENTERPRISE_COHORT_MIN = 3
ENTERPRISE_SIZE_MIN = 2000


# Slack Block Kit limits
_SLACK_SECTION_MAX_CHARS = 3000


def _company_link(c: dict[str, Any]) -> str:
    url = c.get("url") or ""
    name = c.get("name") or c.get("domain", "?")
    return f"<{url}|{name}>" if url else f"*{name}*"


def _section_chunks(lines: list[str], sep: str = "\n") -> list[dict]:
    """Split a list of mrkdwn lines into section blocks, each ≤ 3000 chars."""
    blocks: list[dict] = []
    current: list[str] = []
    current_len = 0
    for line in lines:
        needed = len(line) + (len(sep) if current else 0)
        if current and current_len + needed > _SLACK_SECTION_MAX_CHARS:
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": sep.join(current)}})
            current = [line]
            current_len = len(line)
        else:
            current.append(line)
            current_len += needed
    if current:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": sep.join(current)}})
    return blocks


def build_insights_blocks(
    companies: list[dict[str, Any]],
    champions_by_domain: dict[str, dict[str, Any]] | None = None,
) -> list[dict]:
    """Build an action-oriented "This week's plays" section.

    Each bullet names a specific account + the concrete signal (with its time
    window) + a recommended BDR action. Candidates are generated per play
    type, ranked by priority, deduped one-per-account, and the top 5 are
    emitted. A baseline pipeline-shape line is always prepended for context.
    """
    if not companies:
        return [{
            "type": "section",
            "text": {"type": "mrkdwn", "text": "_No active accounts this week._"},
        }]

    champions_by_domain = champions_by_domain or {}

    total = len(companies)
    t1 = sum(1 for c in companies if c.get("tier") == "tier_1")
    t2 = sum(1 for c in companies if c.get("tier") == "tier_2")
    new_this_week = sum(1 for c in companies if c.get("weeks_above") == 0)

    baseline = (
        f"_Pipeline: *{total} active* · {t1} Tier 1 · {t2} Tier 2 · "
        f"{new_this_week} newly active this week._"
    )

    candidates: list[_Play] = []

    # ---- Play: just-promoted Tier 1 (priority 5) ----
    for c in companies:
        if c.get("tier") == "tier_1" and c.get("weeks_above") == 0 and c.get("domain"):
            score = _float(c.get("score"))
            delta = _float(c.get("delta"))
            delta_str = f"{'+' if delta >= 0 else ''}{delta:.1f} WoW"
            candidates.append((
                5,
                c["domain"],
                f":trophy: *{_company_link(c)}* crossed Tier 1 this sync (score {score:.1f}, {delta_str})",
            ))

    # ---- Play: enterprise cohort (priority 5) ----
    # When multiple newly-active accounts are enterprise-scale in the same
    # sync, surface them as a single themed "enterprise wave" bullet instead
    # of N individual per-company enterprise bullets. Reads as a segment-level
    # signal for BDRs ("something is happening in enterprise this week")
    # rather than N near-identical one-offs.
    cohort_companies = [
        c for c in companies
        if c.get("weeks_above") == 0
        and int(_float(c.get("company_size"))) >= ENTERPRISE_SIZE_MIN
        and c.get("domain")
    ]
    cohort_domains: set[str] = set()
    if len(cohort_companies) >= ENTERPRISE_COHORT_MIN:
        cohort_domains = {c["domain"] for c in cohort_companies}
        total_employees = sum(
            int(_float(c.get("company_size"))) for c in cohort_companies
        )
        total_active_users = sum(
            int(_float(c.get("active_users_last_30d"))) for c in cohort_companies
        )
        # Sort by score desc so the bullet reads top-down.
        ordered = sorted(cohort_companies, key=lambda x: -_float(x.get("score")))
        names_str = ", ".join(_company_link(c) for c in ordered)
        users_str = (
            f", {total_active_users:,} active product users"
            if total_active_users > 0
            else ""
        )
        candidates.append((
            5,
            # Sentinel domain — does not clash with real account dedupe,
            # and lets per-account plays (credit surge, etc.) still fire for
            # cohort members through their own domain.
            "__enterprise_cohort__",
            f":office: Enterprise wave: {len(cohort_companies)} large orgs "
            f"(≥{ENTERPRISE_SIZE_MIN:,} employees) newly active — {names_str} "
            f"(combined ~{total_employees:,} employees{users_str})",
        ))

    # ---- Play: enterprise-scale account (priority 4) ----
    # Surface when an active account is a large org worth noting. Uses
    # company_size (employee count from BQ) as the gate. Engineer count
    # specifically isn't available; active product users is the closest proxy.
    # When the enterprise-cohort play fires above, suppress the per-company
    # version for cohort members — they're already named in the cohort bullet.
    for c in companies:
        if c.get("domain") in cohort_domains:
            continue
        size = int(_float(c.get("company_size")))
        if size >= ENTERPRISE_SIZE_MIN and c.get("domain"):
            tier_label = {"tier_1": "Tier 1", "tier_2": "Tier 2", "tier_3": "Tier 3"}.get(
                c.get("tier", ""), "Active"
            )
            active_users = int(_float(c.get("active_users_last_30d")))
            users_str = f", {active_users} active product users" if active_users > 0 else ""
            candidates.append((
                4,
                c["domain"],
                f":office: *{_company_link(c)}* is enterprise-scale ({size:,} employees{users_str}) — {tier_label} at {_float(c.get('score')):.1f}",
            ))

    # ---- Play: credit surge (priority 4) ----
    # Illustrative example thresholds (>=30% WoW, >=10k credits) — calibrate for your own funnel.
    for c in companies:
        wow = _float(c.get("wow_growth"))
        credits = _float(c.get("credits_30d"))
        if wow >= 30 and credits >= 10_000 and c.get("domain"):
            candidates.append((
                4,
                c["domain"],
                f":chart_with_upwards_trend: *{_company_link(c)}* credits surged +{wow:.0f}% WoW ({int(credits):,} last 30d)",
            ))

    # ---- Play: users hitting limits (priority 4) ----
    for c in companies:
        users_hitting = int(_float(c.get("users_hitting_limits")))
        if users_hitting > 0 and c.get("domain"):
            limit_hits = int(_float(c.get("limit_hits")))
            reload_spend = _float(c.get("reload_spend"))
            reload_str = f", ${reload_spend:,.0f} reload" if reload_spend > 0 else ""
            user_word = "user" if users_hitting == 1 else "users"
            candidates.append((
                4,
                c["domain"],
                f":rotating_light: *{_company_link(c)}*: {users_hitting} {user_word} hit credit limits (14d, {limit_hits} hits{reload_str})",
            ))

    # ---- Play: land-and-expand (priority 3) ----
    for c in companies:
        new_members = int(_float(c.get("new_members")))
        if new_members >= 2 and c.get("domain"):
            candidates.append((
                3,
                c["domain"],
                f":busts_in_silhouette: *{_company_link(c)}*: {new_members} new domain members joined (14d)",
            ))

    # ---- Play: self-upgrades (priority 3) ----
    for c in companies:
        upgrades = int(_float(c.get("users_upgraded")))
        if upgrades > 0 and c.get("domain"):
            user_word = "user" if upgrades == 1 else "users"
            candidates.append((
                3,
                c["domain"],
                f":arrow_up: *{_company_link(c)}*: {upgrades} {user_word} self-upgraded (30d)",
            ))

    # ---- Play: Tier 1 drift (priority 3) ----
    for c in companies:
        if c.get("tier") == "tier_1" and _float(c.get("delta")) < 0 and c.get("domain"):
            delta = _float(c.get("delta"))
            score = _float(c.get("score"))
            candidates.append((
                3,
                c["domain"],
                f":warning: *{_company_link(c)}* Tier 1 drift: score dipped {delta:.1f} WoW (now {score:.1f})",
            ))

    # ---- Play: admin champion emerged on a Tier 1 (priority 2) ----
    for c in companies:
        if c.get("tier") != "tier_1" or not c.get("domain"):
            continue
        champ = champions_by_domain.get(c["domain"]) or {}
        if champ.get("is_team_admin"):
            email = champ.get("user_email", "")
            role = champ.get("grouped_survey_role") or "unknown"
            candidates.append((
                2,
                c["domain"],
                f":key: *{_company_link(c)}* admin champion emerged: <mailto:{email}|{email}> (`{role}`)",
            ))

    # Rank by priority, dedupe one-per-account, keep top 5.
    candidates.sort(key=lambda t: -t[0])
    seen_accounts: set[str] = set()
    chosen: list[str] = []
    for _, domain, text in candidates:
        if domain in seen_accounts:
            continue
        seen_accounts.add(domain)
        chosen.append(text)
        if len(chosen) >= 5:
            break

    if not chosen:
        chosen = ["_No actionable plays this week — no accounts crossed thresholds or surfaced signals._"]

    body = baseline + "\n" + "\n".join(chosen)
    return [{"type": "section", "text": {"type": "mrkdwn", "text": body}}]


def build_tier1_pqa_blocks(
    tier1_companies: list[dict[str, Any]],
    champions_by_domain: dict[str, dict[str, Any]] | None = None,
) -> list[dict]:
    """Build multi-line Block Kit cards for Tier 1 PQAs.

    Per company: score + WoW delta, WAU + credits + WoW growth %, optional
    urgency line (hidden when empty), and a top champion line with a
    Draft outreach link.
    """
    if not tier1_companies:
        return [{
            "type": "section",
            "text": {"type": "mrkdwn", "text": "_No Tier 1 accounts this week._"},
        }]

    champions_by_domain = champions_by_domain or {}
    blocks: list[dict] = []

    for idx, c in enumerate(tier1_companies):
        medal = _MEDALS[idx] if idx < len(_MEDALS) else "•"

        # Line 1 — company, score, WoW delta
        delta = _float(c.get("delta"))
        delta_str = f"+{delta:.1f}" if delta > 0 else f"{delta:.1f}"
        line1 = (
            f"{medal} *<{c['url']}|{c['name']}>*  ·  "
            f"Score *{_float(c.get('score')):.1f}* ({delta_str} WoW)"
        )

        # Line 2 — WAU, credits, WoW credit growth (explicit windows)
        wow_growth = _float(c.get("wow_growth"))
        growth_str = f"  ·  +{wow_growth:.0f}% credits WoW" if wow_growth > 0 else ""
        line2 = (
            f"   :chart_with_upwards_trend: {_float(c.get('avg_wau')):.1f} WAU (4w avg)  ·  "
            f"{int(_float(c.get('credits_30d'))):,} credits (30d){growth_str}"
        )

        # Line 3 — urgency signals, hidden when empty
        urgency = _urgency_fragments(c)
        urgency_line = f"\n   :rotating_light: {' · '.join(urgency)}" if urgency else ""

        # Line 4 — top champion + draft-outreach link
        champion_line = ""
        champion = champions_by_domain.get(c.get("domain", ""))
        if champion:
            email = champion.get("user_email", "")
            role = champion.get("grouped_survey_role") or "unknown"
            admin_flag = " :key:" if champion.get("is_team_admin") else ""
            champ_credits = int(_float(champion.get("credits_used_t30d")))
            active_days = int(_float(champion.get("days_active_in_last_30")))
            # Merge the domain context into the champion row so the URL
            # builder picks up domain-level urgency signals.
            champion_context = {**c, **champion, "email_domain": c.get("domain", "")}
            draft_url = _draft_outreach_url(champion_context, c)
            draft_link = f"  ·  <{draft_url}|✍️ Draft outreach>" if draft_url else ""
            champion_line = (
                f"\n   :trophy: Champion: <mailto:{email}|{email}>{admin_flag}  ·  `{role}`"
                f"  ·  {champ_credits:,} credits (30d)  ·  {active_days}d active (30d){draft_link}"
            )

        text = f"{line1}\n{line2}{urgency_line}{champion_line}"
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": text}})

    return blocks


def build_slack_message(
    new_hot: list[dict],
    top_movers: list[dict],
    watch_list: list[dict],
    tier1_pqa_blocks: list[dict],
    pipeline_wins: list[dict],
    total_active: int,
    insights_blocks: list[dict] | None = None,
) -> list[dict]:
    """Build Slack Block Kit message."""
    run_date = datetime.now(timezone.utc).strftime("%B %d, %Y")
    blocks: list[dict] = []

    def _header(text: str) -> dict:
        return {"type": "header", "text": {"type": "plain_text", "text": text}}

    def _section(text: str) -> dict:
        return {"type": "section", "text": {"type": "mrkdwn", "text": text}}

    def _divider() -> dict:
        return {"type": "divider"}

    # ── Header ───────────────────────────────────────────────────────────
    blocks.append(_header(f"📊 PLG Weekly Digest — {run_date}"))
    blocks.append(_divider())

    # ── 1. Insights — This week at a glance ────────────────────────────────────────
    blocks.append(_header("📊 This week at a glance"))
    if insights_blocks:
        blocks.extend(insights_blocks)
    else:
        blocks.append(_section(
            f"*{total_active} active accounts* in pipeline  ·  "
            f"{len(new_hot)} new this week  ·  "
            f"{len(watch_list)} on watch list"
        ))
    blocks.append(_divider())

    # ── 2. Newly Active Accounts ────────────────────────────────────────────────
    blocks.append(_header("🆕 Newly Active Accounts"))
    blocks.append(_section(
        "_Accounts that just crossed into the Active PLG pipeline this sync — "
        "2 consecutive weeks above the Tier 2 threshold (score ≥ 50), or an "
        "immediate promotion when score ≥ 85._"
    ))
    if new_hot:
        lines = []
        for c in new_hot:
            tier = _tier_emoji(c["tier"])
            delta_str = f"+{c['delta']:.1f}" if c["delta"] > 0 else f"{c['delta']:.1f}"
            extras: list[str] = []
            wow = _float(c.get("wow_growth"))
            if wow > 0:
                extras.append(f"+{wow:.0f}% credits WoW")
            limit_users = int(_float(c.get("users_hitting_limits")))
            if limit_users > 0:
                user_word = "user" if limit_users == 1 else "users"
                extras.append(f"{limit_users} {user_word} hit limits (14d)")
            extras_str = ("  ·  " + "  ·  ".join(extras)) if extras else ""
            lines.append(
                f"{tier} *<{c['url']}|{c['name']}>*  ·  score *{c['score']:.1f}* ({delta_str} WoW)  ·  "
                f"{c['avg_wau']:.1f} WAU (4w)  ·  {int(c['credits_30d']):,} credits (30d){extras_str}"
            )
        blocks.extend(_section_chunks(lines))
    else:
        blocks.append(_section("_No newly active accounts this week._"))

    blocks.append(_divider())

    # ── 3. Top Movers ──────────────────────────────────────────────────────
    blocks.append(_header("📈 Top Movers (score increase)"))
    movers = [c for c in top_movers if c["delta"] > 0]
    if movers:
        lines = []
        for c in movers[:TOP_MOVERS_LIMIT]:
            lines.append(
                f"*<{c['url']}|{c['name']}>*  ·  {c['score']:.1f}  *(+{c['delta']:.1f})*"
            )
        blocks.append(_section("\n".join(lines)))
    else:
        blocks.append(_section("_No significant upward movers this week._"))

    blocks.append(_divider())

    # ── 4. Tier 1 PQAs ────────────────────────────────────────────────────────
    blocks.append(_header("🏆 Tier 1 PQAs"))
    if tier1_pqa_blocks:
        blocks.extend(tier1_pqa_blocks)
    else:
        blocks.append(_section("_No Tier 1 accounts this week._"))

    blocks.append(_divider())

    # ── 4. Watch List ────────────────────────────────────────────────
    blocks.append(_header("⚠️ Watch List (trending toward de-prioritization)"))
    if watch_list:
        lines = []
        for c in watch_list[:WATCH_LIST_LIMIT]:
            streak = c["weeks_below"]
            lines.append(
                f"*<{c['url']}|{c['name']}>*  ·  score {c['score']:.1f}  ·  "
                f"week {streak}/{2} below threshold"
            )
        blocks.append(_section("\n".join(lines)))
    else:
        blocks.append(_section("_No accounts trending toward de-prioritization._"))

    blocks.append(_divider())

    # ── 5. Pipeline Wins (trailing 7d) ────────────────────────────────────────
    blocks.append(_header(f"💰 Pipeline Wins (last {PIPELINE_DAYS} days)"))
    if pipeline_wins:
        lines = []
        for deal in pipeline_wins:
            amount_str = f"  ·  ${deal['amount']:,.0f}" if deal["amount"] > 0 else ""
            lines.append(
                f"*<{deal['url']}|{deal['name']}>*  ·  *{deal['stage']}*{amount_str}  ·  {deal['modified']}"
            )
        blocks.append(_section("\n".join(lines)))
    else:
        blocks.append(_section(f"_No PLG accounts moved to SAO/SQO/Won in the last {PIPELINE_DAYS} days._"))

    return blocks


# ---------------------------------------------------------------------------
# Slack posting
# ---------------------------------------------------------------------------

def post_to_slack(blocks: list[dict], channel: str, dry_run: bool = False) -> None:
    text = "PLG Weekly Digest"  # fallback for notifications

    if dry_run:
        print("\n[DRY RUN — would post to Slack]")
        for block in blocks:
            if block["type"] == "header":
                print(f"\n=== {block['text']['text']} ===")
            elif block["type"] == "section":
                print(block["text"]["text"])
            elif block["type"] == "divider":
                print("---")
        return

    if SLACK_BOT_TOKEN:
        # Preferred: chat.postMessage via PLG_SLACK_BOT_TOKEN (bot user: plg-upsell-bot).
        payload = json.dumps({
            "channel": channel,
            "text": text,
            "blocks": blocks,
        }).encode()
        req = urllib.request.Request(
            "https://slack.com/api/chat.postMessage",
            data=payload,
            headers={
                "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
                "Content-Type": "application/json; charset=utf-8",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            if body.get("ok"):
                print(f"✓ Digest posted to {channel}")
            else:
                print(f"✗ Slack API error: {body.get('error', 'unknown_error')}")
        except urllib.error.HTTPError as e:
            print(f"✗ Slack error: HTTP {e.code} — {e.read().decode()}")
    elif SLACK_WEBHOOK:
        # Fallback: incoming webhook
        payload = json.dumps({
            "text": text,
            "channel": channel,
            "blocks": blocks,
        }).encode()
        req = urllib.request.Request(
            SLACK_WEBHOOK,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req) as resp:
                if resp.status == 200:
                    print(f"✓ Digest posted to {channel}")
        except urllib.error.HTTPError as e:
            print(f"✗ Slack error: HTTP {e.code} — {e.read().decode()}")
    else:
        print("⚠  Neither PLG_SLACK_BOT_TOKEN nor HUBSPOT_SLACK_WEBHOOK is set — printing to stdout instead.")
        post_to_slack(blocks, channel, dry_run=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Post weekly PLG digest to Slack.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print message to stdout without posting to Slack.")
    parser.add_argument("--channel", type=str, default=DEFAULT_CHANNEL,
                        help=f"Slack channel to post to (default: {DEFAULT_CHANNEL})")
    parser.add_argument("--delay-minutes", type=int, default=0,
                        help="Wait N minutes before posting (allows lead routing after sync).")
    parser.add_argument("--dataset", type=str, default=BQ_DATASET,
                        help=f"BigQuery dataset for champion scores (default: {BQ_DATASET})")
    parser.add_argument("--project", type=str, default=GCP_PROJECT,
                        help=f"GCP project ID (default: {GCP_PROJECT})")
    parser.add_argument("--top-n", type=int, default=TOP_CHAMPIONS_LIMIT,
                        help=f"Number of top champions to include (default: {TOP_CHAMPIONS_LIMIT})")
    args = parser.parse_args()

    if args.delay_minutes > 0 and not args.dry_run:
        print(f"Waiting {args.delay_minutes} minutes for lead routing before posting…")
        time.sleep(args.delay_minutes * 60)

    print("Fetching active PLG companies from HubSpot…")
    all_active = fetch_active_companies()
    total_active = len(all_active)
    active_ids = {c["id"] for c in all_active}
    print(f"  {total_active} active accounts")

    # Enrich the HubSpot-sourced active list with authoritative BQ signals.
    # BQ is the source of truth for scores + signals; HubSpot contributes
    # state-only fields (url, owner, weeks_above/below, score_delta).
    active_domains = [c["domain"] for c in all_active if c.get("domain")]
    print(
                f"Fetching domain scores for {len(active_domains)} active domain(s) from BigQuery ({args.dataset})…"
    )
    bq_by_domain = fetch_domain_scores_bq(
        project=args.project,
        dataset=args.dataset,
        allowed_domains=active_domains,
    )
    print(f"  {len(bq_by_domain)} domain(s) found in BQ")

    for c in all_active:
        bq = bq_by_domain.get(c.get("domain", ""), {})
        if bq:
            # Override HubSpot stats with BQ authoritative values.
            c["score"] = _float(bq.get("pql_score", c["score"]))
            c["avg_wau"] = _float(bq.get("avg_wau", c["avg_wau"]))
            c["credits_30d"] = _float(bq.get("total_credits_30d", c["credits_30d"]))
            c["reload_spend"] = _float(bq.get("reload_dollars", c.get("reload_spend", 0)))
            c["limit_hits"] = int(_float(bq.get("limit_hits", c.get("limit_hits", 0))))
            c["users_hitting_limits"] = int(_float(bq.get("users_hitting_limits")))
            c["users_upgraded"] = int(_float(bq.get("users_upgraded")))
            c["new_members"] = int(_float(bq.get("new_domain_members")))
            c["wow_growth"] = _float(bq.get("wow_growth_pct"))
            c["company_size"] = bq.get("company_size")
            c["industry"] = bq.get("industry")
            c["active_users_last_30d"] = bq.get("active_users_last_30d")
        # Recompute tier from BQ score (falls back to HubSpot score when BQ
        # missing — _tier_from_score handles 0).
        c["tier"] = _tier_from_score(_float(c.get("score")))

    # Derive sections from the enriched data.
    new_hot    = [c for c in all_active if c["weeks_above"] == 0]
    top_movers = sorted([c for c in all_active if c["delta"] > 0],
                        key=lambda x: -x["delta"])[:TOP_MOVERS_LIMIT]
    watch_list = sorted([c for c in all_active if c["weeks_below"] >= 1],
                        key=lambda x: -x["weeks_below"])[:WATCH_LIST_LIMIT]

    tier1_companies = sorted(
        [c for c in all_active if c["tier"] == "tier_1"],
        key=lambda x: -_float(x.get("score")),
    )
    tier1_domains = [c["domain"] for c in tier1_companies if c.get("domain")]

    if tier1_domains:
        print(
            f"Fetching top champions for {len(tier1_domains)} Tier 1 domain(s) from BigQuery…"
        )
        champion_rows = fetch_top_champions_bq(
            project=args.project,
            dataset=args.dataset,
            top_n=args.top_n,
            allowed_domains=tier1_domains,
        )
        print(f"  {len(champion_rows)} Tier 1 champion(s)")
    else:
        print("No Tier 1 accounts this week — skipping champions BigQuery fetch.")
        champion_rows = []

    champions_by_domain = {
        row["email_domain"]: row
        for row in champion_rows
        if row.get("email_domain")
    }

    if not PLG_OUTREACH_PROMPT_URL:
        print(
            "⚠  PLG_OUTREACH_PROMPT_URL is not configured — champion cards will "
            "render without 'Draft outreach' links. Set PLG_OUTREACH_PROMPT_URL "
            "to the Warp Drive Prompt share URL to enable them."
        )

    insights_blocks = build_insights_blocks(all_active, champions_by_domain)
    tier1_pqa_blocks = build_tier1_pqa_blocks(tier1_companies, champions_by_domain)

    print(f"Fetching pipeline wins (last {PIPELINE_DAYS} days)…")
    pipeline_wins = fetch_pipeline_wins(active_ids)
    print(f"  {len(pipeline_wins)} pipeline wins")

    print("Building message…")
    blocks = build_slack_message(
        new_hot=new_hot,
        top_movers=top_movers,
        watch_list=watch_list,
        tier1_pqa_blocks=tier1_pqa_blocks,
        pipeline_wins=pipeline_wins,
        total_active=total_active,
        insights_blocks=insights_blocks,
    )

    post_to_slack(blocks, channel=args.channel, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
