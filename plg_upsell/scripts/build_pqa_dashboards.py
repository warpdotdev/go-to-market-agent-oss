#!/usr/bin/env python3
"""Build two Metabase dashboards for PLG PQA/PQL scoring.

1. "Top 100 PQAs — PLG Upsell" — ranked account list, focused on the metrics
   that build the PQA composite (illustrative example weights: Breadth 20,
   Depth 20, Velocity 20, Urgency 40).
   Each row links to the detail dashboard pre-filtered to that company's domain.
2. "PQA / PQL Deep Dive" — parameterized by `email_domain`. Shows the PQA
   components, historical trend, raw signals, top-3 PQL (champion) contacts,
   and the breakdown that drives each champion's PQL score.

Naming note: the dbt model column `pql_score` on `plg_upsell_domain_scores` is
the domain-level composite; in product/GTM terminology (see
`plg-pqa-ops-gameplan.md`) that composite is called **PQA**. The per-user
`champion_score` from `plg_upsell_domain_champions` is called **PQL**. The
dashboards use the PQA/PQL naming in titles and map to the dbt columns under
the hood.

Source tables (BigQuery, database_id=2):
  example-gcp-project.analytics.plg_upsell_domain_scores
  example-gcp-project.analytics.plg_upsell_domain_champions
  example-gcp-project.analytics.plg_upsell_domain_scores_daily

Usage:
  python3 plg_upsell/scripts/build_pqa_dashboards.py --dry-run
  python3 plg_upsell/scripts/build_pqa_dashboards.py --confirm

Requires:
  METABASE_API_KEY env var.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone


BASE = os.environ.get("METABASE_URL", "https://metabase.example.com") + "/api"
KEY = os.environ.get("METABASE_API_KEY", "")
DATABASE_ID = 2  # Metabase database ID for your BigQuery connection
DEFAULT_COLLECTION_ID = 1057  # Sales
TOP_DASHBOARD_NAME = "Top 100 PQAs — PLG Upsell"
DETAIL_DASHBOARD_NAME = "PQA / PQL Deep Dive"
HUBSPOT_PORTAL_ID = os.environ.get("HUBSPOT_PORTAL_ID", "000000000")

HEADERS = {"X-API-Key": KEY, "Content-Type": "application/json"}

# Fully-qualified BigQuery tables (safer inside native SQL)
GCP_PROJECT = os.environ.get("GCP_PROJECT", "example-gcp-project")
BQ_DATASET = os.environ.get("BQ_DATASET", "analytics")
T_SCORES = f"`{GCP_PROJECT}.{BQ_DATASET}.plg_upsell_domain_scores`"
T_CHAMPIONS = f"`{GCP_PROJECT}.{BQ_DATASET}.plg_upsell_domain_champions`"
T_DAILY = f"`{GCP_PROJECT}.{BQ_DATASET}.plg_upsell_domain_scores_daily`"


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _request(method: str, path: str, data=None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(BASE + path, data=body, headers=HEADERS, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            txt = r.read().decode()
            return json.loads(txt) if txt else {}
    except urllib.error.HTTPError as e:
        sys.stderr.write(f"HTTP {e.code} on {method} {path}\n")
        sys.stderr.write(e.read().decode() + "\n")
        raise


def whoami():
    me = _request("GET", "/user/current")
    return me.get("common_name", "unknown"), me.get("email", "n/a")


def find_dashboard(name: str, collection_id: int):
    """Return dashboard dict or None. Uses search, filters by collection + name."""
    q = urllib.parse.quote(name, safe="")
    results = _request("GET", f"/search?q={q}&models=dashboard")
    for item in results.get("data", []):
        if item.get("name") == name and item.get("collection_id") == collection_id:
            return _request("GET", f"/dashboard/{item['id']}")
    return None


# ---------------------------------------------------------------------------
# SQL definitions
# ---------------------------------------------------------------------------

# --- Top 100 dashboard ---------------------------------------------------

SQL_TOP100_TABLE = f"""
-- Top 100 PQAs with all the raw signals that build the score.
-- email_domain is configured as a click-through column to the detail dashboard.
select
    row_number() over (order by pql_score desc) as rank,
    company_name,
    email_domain,
    pql_score as pqa_score,
    breadth_score,
    depth_score,
    velocity_score,
    urgency_score,
    round(avg_wau, 1) as avg_wau_4w,
    total_credits_30d,
    credits_per_user_30d,
    wow_growth_pct,
    users_hitting_limits as users_hitting_limits_14d,
    reload_dollars as reload_dollars_14d,
    users_upgraded as users_upgraded_30d,
    new_domain_members as new_teammates_14d,
    active_users_last_30d,
    industry,
    company_size,
    country
from {T_SCORES}
order by pql_score desc
limit 100
""".strip()


SQL_SCALAR_ELIGIBLE = f"select count(*) as eligible_domains from {T_SCORES}"
SQL_SCALAR_MEDIAN = (
    f"select round(approx_quantiles(pql_score, 2)[offset(1)], 1) as median_pqa from {T_SCORES}"
)
SQL_SCALAR_MEAN = f"select round(avg(pql_score), 1) as mean_pqa from {T_SCORES}"
SQL_SCALAR_TIER1 = f"select countif(pql_score >= 75) as tier1_count from {T_SCORES}"
SQL_SCALAR_TIER2 = (
    f"select countif(pql_score >= 50 and pql_score < 75) as tier2_count from {T_SCORES}"
)
SQL_SCALAR_TIER3 = (
    f"select countif(pql_score >= 25 and pql_score < 50) as tier3_count from {T_SCORES}"
)


SQL_DISTRIBUTION = f"""
-- PQA score distribution across all eligible domains, bucketed by 5.
select
    cast(floor(pql_score / 5) * 5 as int64) as pqa_bucket,
    count(*) as domains
from {T_SCORES}
group by pqa_bucket
order by pqa_bucket
""".strip()


SQL_COMPOSITION_TOP20 = f"""
-- Top 20 PQAs with their composite broken into the 4 weighted axes.
-- Each contribution is on the same 0-100 PQA scale (max contribution = weight).
-- Illustrative example weights — calibrate for your own funnel.
select
    company_name,
    round(breadth_score * 0.20, 1) as breadth_contrib_max20,
    round(depth_score * 0.20, 1) as depth_contrib_max20,
    round(velocity_score * 0.20, 1) as velocity_contrib_max20,
    round(urgency_score * 0.40, 1) as urgency_contrib_max40,
    pql_score as total_pqa
from {T_SCORES}
order by pql_score desc
limit 20
""".strip()


SQL_URGENCY_BREAKDOWN = f"""
-- Raw urgency-signal inputs for the top 20 PQAs.
-- These four signals combine (illustrative example weights 10 / 10 / 10 / 10) into urgency_score.
select
    company_name,
    email_domain,
    users_hitting_limits as users_hitting_limits_14d,
    reload_dollars as reload_dollars_14d,
    users_upgraded as users_upgraded_30d,
    new_domain_members as new_teammates_14d,
    urgency_score
from {T_SCORES}
order by pql_score desc
limit 20
""".strip()


# --- Detail dashboard (parameterized by {{email_domain}}) ------------------

SQL_DETAIL_HEADER = f"""
-- Company overview row. Parameter: {{{{email_domain}}}}
select
    company_name,
    email_domain,
    pql_score as pqa_score,
    case
        when pql_score >= 75 then 'Tier 1'
        when pql_score >= 50 then 'Tier 2'
        when pql_score >= 25 then 'Tier 3'
        else 'Below threshold'
    end as tier,
    active_users_last_30d,
    industry,
    company_size,
    country
from {T_SCORES}
where email_domain = {{{{email_domain}}}}
""".strip()


SQL_DETAIL_PQA_SCALAR = f"""
select pql_score as pqa_score
from {T_SCORES}
where email_domain = {{{{email_domain}}}}
""".strip()


SQL_DETAIL_TIER_SCALAR = f"""
select
    case
        when pql_score >= 75 then 'Tier 1'
        when pql_score >= 50 then 'Tier 2'
        when pql_score >= 25 then 'Tier 3'
        else 'Below threshold'
    end as tier
from {T_SCORES}
where email_domain = {{{{email_domain}}}}
""".strip()


SQL_DETAIL_RANK_SCALAR = f"""
with ranked as (
    select email_domain, row_number() over (order by pql_score desc) as rank
    from {T_SCORES}
)
select rank from ranked where email_domain = {{{{email_domain}}}}
""".strip()


SQL_DETAIL_DELTA_SCALAR = f"""
-- PQA score today minus PQA score ~7 days ago from the daily snapshot.
with today_score as (
    select pql_score as score_today
    from {T_SCORES}
    where email_domain = {{{{email_domain}}}}
),
prior_score as (
    select pql_score as score_prior
    from {T_DAILY}
    where email_domain = {{{{email_domain}}}}
        and scored_date <= date_sub(current_date(), interval 7 day)
    order by scored_date desc
    limit 1
)
select round(coalesce((select score_today from today_score) - (select score_prior from prior_score), 0), 1) as pqa_delta_7d
""".strip()


SQL_DETAIL_ACTIVE_USERS_SCALAR = f"""
select active_users_last_30d
from {T_SCORES}
where email_domain = {{{{email_domain}}}}
""".strip()


SQL_DETAIL_COMPOSITION = f"""
-- PQA component contributions to this company's composite score.
-- Max possible contribution per axis is shown in the column label so reps
-- can see at a glance which axis is driving the score.
-- Illustrative example weights — calibrate for your own funnel.
select 'Breadth (max 20)'  as component, round(breadth_score * 0.20, 1) as contribution
from {T_SCORES} where email_domain = {{{{email_domain}}}}
union all
select 'Depth (max 20)',    round(depth_score * 0.20, 1)
from {T_SCORES} where email_domain = {{{{email_domain}}}}
union all
select 'Velocity (max 20)', round(velocity_score * 0.20, 1)
from {T_SCORES} where email_domain = {{{{email_domain}}}}
union all
select 'Urgency (max 40)',  round(urgency_score * 0.40, 1)
from {T_SCORES} where email_domain = {{{{email_domain}}}}
""".strip()


SQL_DETAIL_HISTORY = f"""
-- PQA score history for the past 60 days.
select
    scored_date,
    pql_score as pqa_score
from {T_DAILY}
where email_domain = {{{{email_domain}}}}
    and scored_date >= date_sub(current_date(), interval 60 day)
order by scored_date
""".strip()


SQL_DETAIL_SIGNALS = f"""
-- Raw signal detail for this domain, sorted to match the PQA axes.
select
    'Breadth'  as axis, 'Avg WAU (4w)'                     as signal, cast(round(avg_wau, 2) as string) as value from {T_SCORES} where email_domain = {{{{email_domain}}}} union all
select 'Breadth',         'Active users (30d)',             cast(active_users_last_30d as string) from {T_SCORES} where email_domain = {{{{email_domain}}}} union all
select 'Depth',           'Total AI credits (30d)',         cast(total_credits_30d as string)     from {T_SCORES} where email_domain = {{{{email_domain}}}} union all
select 'Depth',           'Credits per user (30d)',         cast(credits_per_user_30d as string)  from {T_SCORES} where email_domain = {{{{email_domain}}}} union all
select 'Velocity',        'WoW credit growth %',            cast(wow_growth_pct as string)        from {T_SCORES} where email_domain = {{{{email_domain}}}} union all
select 'Velocity',        'Weeks growing',                  cast(weeks_growing as string)         from {T_SCORES} where email_domain = {{{{email_domain}}}} union all
select 'Urgency',         'Users hitting limits (14d)',     cast(users_hitting_limits as string)  from {T_SCORES} where email_domain = {{{{email_domain}}}} union all
select 'Urgency',         'Limit hit events (14d)',         cast(limit_hits as string)            from {T_SCORES} where email_domain = {{{{email_domain}}}} union all
select 'Urgency',         'Reload $ (14d)',                 cast(reload_dollars as string)        from {T_SCORES} where email_domain = {{{{email_domain}}}} union all
select 'Urgency',         'Users upgraded (30d)',           cast(users_upgraded as string)        from {T_SCORES} where email_domain = {{{{email_domain}}}} union all
select 'Urgency',         'New teammates (14d)',            cast(new_domain_members as string)    from {T_SCORES} where email_domain = {{{{email_domain}}}}
""".strip()


SQL_DETAIL_CHAMPIONS = f"""
-- Top 3 PQL (champion) contacts for this domain.
select
    rank_in_domain,
    user_email,
    champion_score as pql_score,
    credits_used_t30d,
    days_active_in_last_30,
    is_team_admin,
    limit_hit_count,
    limit_hits_weighted,
    grouped_survey_role,
    is_on_team_with_active_subscription,
    first_sub_plan_type
from {T_CHAMPIONS}
where email_domain = {{{{email_domain}}}}
order by rank_in_domain
""".strip()


# Per-champion PQL composition. We can't recompute the normalized components
# from the static table, so we show the raw-input contributions that drive the
# PQL weights. Illustrative example weights (20 each) — calibrate for your own funnel.
SQL_DETAIL_CHAMPION_COMPOSITION = f"""
with c as (
    select
        user_email,
        rank_in_domain,
        credits_used_t30d,
        days_active_in_last_30,
        limit_hits_weighted,
        is_team_admin,
        grouped_survey_role,
        champion_score
    from {T_CHAMPIONS}
    where email_domain = {{{{email_domain}}}}
)
select
    user_email,
    round(safe_divide(credits_used_t30d,
        nullif(max(credits_used_t30d) over (), 0)) * 20, 1) as credits_contrib_max20,
    round(safe_divide(days_active_in_last_30,
        nullif(max(days_active_in_last_30) over (), 0)) * 20, 1) as activity_contrib_max20,
    case when is_team_admin then 20 else 0 end as admin_contrib_max20,
    round(safe_divide(limit_hits_weighted,
        nullif(max(limit_hits_weighted) over (), 0)) * 20, 1) as limit_hits_contrib_max20,
    case when grouped_survey_role in ('engineering_manager','devops/sre','fullstack','backend') then 20 else 0 end as role_contrib_max20,
    champion_score as total_pql
from c
order by rank_in_domain
""".strip()


# ---------------------------------------------------------------------------
# Card + dashboard builders
# ---------------------------------------------------------------------------

def make_dataset_query(sql: str, parameterize_email_domain: bool = False) -> dict:
    native = {"query": sql}
    if parameterize_email_domain:
        native["template-tags"] = {
            "email_domain": {
                "id": "email-domain-tag",
                "name": "email_domain",
                "display-name": "Email domain",
                "type": "text",
                "required": True,
            }
        }
    return {"database": DATABASE_ID, "type": "native", "native": native}


def make_card(
    *,
    name: str,
    display: str,
    sql: str,
    collection_id: int,
    viz: dict | None = None,
    parameterize_email_domain: bool = False,
    description: str | None = None,
) -> dict:
    payload = {
        "name": name,
        "display": display,
        "database_id": DATABASE_ID,
        "collection_id": collection_id,
        "visualization_settings": viz or {},
        "dataset_query": make_dataset_query(sql, parameterize_email_domain),
    }
    if description:
        payload["description"] = description
    if parameterize_email_domain:
        payload["parameters"] = [
            {
                "id": "email-domain-param",
                "type": "category",
                "target": ["variable", ["template-tag", "email_domain"]],
                "name": "Email domain",
                "slug": "email_domain",
            }
        ]
    return payload


def attribution_tile(author_email: str, on_date: str) -> dict:
    text = (
        f"Built by {author_email} on {on_date}  ·  "
        "source: dbt models `plg_upsell_domain_scores`, `plg_upsell_domain_champions`, "
        "`plg_upsell_domain_scores_daily`  ·  illustrative example score weights: Breadth 20 · Depth 20 · Velocity 20 · Urgency 40"
    )
    return {
        "id": -1000,
        "card_id": None,
        "row": 0,
        "col": 0,
        "size_x": 24,
        "size_y": 1,
        "visualization_settings": {
            "virtual_card": {
                "name": None,
                "display": "text",
                "archived": False,
                "dataset_query": {},
                "visualization_settings": {},
            },
            "text": text,
        },
        "parameter_mappings": [],
    }


ELIGIBILITY_NOTE_MARKDOWN = (
    "### What makes a domain eligible?\n"
    "A domain has to clear every filter below to appear in the PQA pool. "
    "Source: `dbt/models/sales/plg_upsell_domain_scores.sql`.\n"
    "- **Known company** — present in `companies`, deduplicated by domain (largest `company_size` wins on ties).\n"
    "- **Active usage** — at least **2 distinct non-excluded users** with activity in the last 30 days (`users.days_active_in_last_30 > 0` and `not is_excluded`).\n"
    "- **Has a paid PLG team** — at least one team on a paid self-serve plan (illustrative examples: team, team_plus) with an **active** subscription (`accounts.plan_type in ('team','team_plus') and subscription_status = 'active'`).\n"
    "- **Not already in the enterprise pipeline** — no open (non-closed-lost) deal in `crm_deals`.\n"
    "- **Not an .edu domain** — `.edu` and `.edu.<cc>` are excluded.\n"
    "- **Not on the explicit exclusion list** — currently `your-company.com`.\n"
    "- **Free-email providers** (gmail / yahoo / outlook / …) are not considered domains and never appear here.\n"
    "Domains that lose eligibility later (e.g. enter the enterprise pipeline) keep a row in `plg_upsell_domain_scores_daily` with `is_eligible=false` and `ineligibility_reason` populated, but drop out of `plg_upsell_domain_scores`."
)


def eligibility_note_tile(row: int) -> dict:
    return {
        "id": -1001,
        "card_id": None,
        "row": row,
        "col": 0,
        "size_x": 24,
        "size_y": 6,
        "visualization_settings": {
            "virtual_card": {
                "name": None,
                "display": "text",
                "archived": False,
                "dataset_query": {},
                "visualization_settings": {},
            },
            "text": ELIGIBILITY_NOTE_MARKDOWN,
        },
        "parameter_mappings": [],
    }


# ---------------------------------------------------------------------------
# Viz settings helpers
# ---------------------------------------------------------------------------

def top100_viz(detail_dashboard_id: int | None) -> dict:
    """Table viz for the top-100 card, including a click-through on email_domain."""
    column_settings: dict = {
        '["name","pqa_score"]': {
            "number_style": "decimal",
            "column_title": "PQA score",
        },
        '["name","rank"]': {"column_title": "#"},
        '["name","company_name"]': {"column_title": "Company"},
        '["name","email_domain"]': {"column_title": "Domain"},
        '["name","breadth_score"]': {"column_title": "Breadth"},
        '["name","depth_score"]': {"column_title": "Depth"},
        '["name","velocity_score"]': {"column_title": "Velocity"},
        '["name","urgency_score"]': {"column_title": "Urgency"},
        '["name","avg_wau_4w"]': {"column_title": "Avg WAU (4w)"},
        '["name","total_credits_30d"]': {"column_title": "Credits 30d"},
        '["name","credits_per_user_30d"]': {"column_title": "Credits/user 30d"},
        '["name","wow_growth_pct"]': {"column_title": "WoW %"},
        '["name","users_hitting_limits_14d"]': {"column_title": "Users hitting limits (14d)"},
        '["name","reload_dollars_14d"]': {
            "column_title": "Reload $ (14d)",
            "number_style": "currency",
            "currency": "USD",
            "currency_style": "symbol",
        },
        '["name","users_upgraded_30d"]': {"column_title": "Users upgraded (30d)"},
        '["name","new_teammates_14d"]': {"column_title": "New teammates (14d)"},
        '["name","active_users_last_30d"]': {"column_title": "Active users 30d"},
        '["name","industry"]': {"column_title": "Industry"},
        '["name","company_size"]': {"column_title": "Company size"},
        '["name","country"]': {"column_title": "Country"},
    }
    if detail_dashboard_id is not None:
        # Make the email_domain column a click-through to the detail dashboard,
        # passing the clicked row's email_domain as the parameter value.
        column_settings['["name","email_domain"]']["click_behavior"] = {
            "type": "link",
            "linkType": "url",
            "linkTextTemplate": "{{email_domain}}  →",
            "linkTemplate": f"{os.environ.get('METABASE_URL','https://metabase.example.com')}/dashboard/{detail_dashboard_id}?email_domain={{{{email_domain}}}}",
        }

    return {
        "table.pivot": False,
        "table.columns": [
            {"name": c, "enabled": True}
            for c in [
                "rank",
                "company_name",
                "email_domain",
                "pqa_score",
                "breadth_score",
                "depth_score",
                "velocity_score",
                "urgency_score",
                "avg_wau_4w",
                "total_credits_30d",
                "credits_per_user_30d",
                "wow_growth_pct",
                "users_hitting_limits_14d",
                "reload_dollars_14d",
                "users_upgraded_30d",
                "new_teammates_14d",
                "active_users_last_30d",
                "industry",
                "company_size",
                "country",
            ]
        ],
        "column_settings": column_settings,
        "table.conditional_formatting": [
            {
                "columns": ["pqa_score"],
                "type": "range",
                "colors": ["#EBF7EB", "#84BB4C"],
                "min_type": "custom",
                "min_value": 0,
                "max_type": "custom",
                "max_value": 100,
            }
        ],
    }


def distribution_viz() -> dict:
    return {
        "graph.dimensions": ["pqa_bucket"],
        "graph.metrics": ["domains"],
        "graph.x_axis.title_text": "PQA bucket",
        "graph.y_axis.title_text": "# domains",
        "graph.show_values": False,
    }


def composition_stacked_viz() -> dict:
    return {
        "graph.dimensions": ["company_name"],
        "graph.metrics": [
            "breadth_contrib_max20",
            "depth_contrib_max20",
            "velocity_contrib_max20",
            "urgency_contrib_max40",
        ],
        "stackable.stack_type": "stacked",
        "graph.x_axis.title_text": "Company",
        "graph.y_axis.title_text": "PQA contribution",
    }


def detail_composition_viz() -> dict:
    return {
        "graph.dimensions": ["component"],
        "graph.metrics": ["contribution"],
        "graph.x_axis.title_text": "Component",
        "graph.y_axis.title_text": "Contribution (max = component weight)",
        "graph.show_values": True,
    }


def detail_history_viz() -> dict:
    return {
        "graph.dimensions": ["scored_date"],
        "graph.metrics": ["pqa_score"],
        "graph.x_axis.title_text": "Date",
        "graph.y_axis.title_text": "PQA score",
    }


def champions_table_viz() -> dict:
    """Top PQL champions table viz, with each user_email linked to HubSpot search."""
    link_template = (
        f"https://app.hubspot.com/contacts/{HUBSPOT_PORTAL_ID}/objects/0-1/views/all/list"
        "?query={{user_email}}"
    )
    return {
        "table.pivot": False,
        "table.columns": [
            {"name": n, "enabled": True}
            for n in [
                "rank_in_domain",
                "user_email",
                "pql_score",
                "credits_used_t30d",
                "days_active_in_last_30",
                "is_team_admin",
                "limit_hit_count",
                "limit_hits_weighted",
                "grouped_survey_role",
                "is_on_team_with_active_subscription",
                "first_sub_plan_type",
            ]
        ],
        "column_settings": {
            '["name","rank_in_domain"]': {"column_title": "#"},
            '["name","user_email"]': {
                "column_title": "Email (click → HubSpot)",
                "view_as": "link",
                "link_text": "{{user_email}}  ↗",
                "link_url": link_template,
                "click_behavior": {
                    "type": "link",
                    "linkType": "url",
                    "linkTextTemplate": "{{user_email}}  ↗",
                    "linkTemplate": link_template,
                },
            },
            '["name","pql_score"]': {"column_title": "PQL"},
            '["name","credits_used_t30d"]': {"column_title": "AI credits (30d)"},
            '["name","days_active_in_last_30"]': {"column_title": "Active days (30d)"},
            '["name","is_team_admin"]': {"column_title": "Team admin?"},
            '["name","limit_hit_count"]': {"column_title": "Limit hits (14d)"},
            '["name","limit_hits_weighted"]': {"column_title": "Limit hits weighted"},
            '["name","grouped_survey_role"]': {"column_title": "Role"},
            '["name","is_on_team_with_active_subscription"]': {"column_title": "On paid team?"},
            '["name","first_sub_plan_type"]': {"column_title": "First paid plan"},
        },
    }


def champion_composition_viz() -> dict:
    return {
        "graph.dimensions": ["user_email"],
        "graph.metrics": [
            "credits_contrib_max20",
            "activity_contrib_max20",
            "admin_contrib_max20",
            "limit_hits_contrib_max20",
            "role_contrib_max20",
        ],
        "stackable.stack_type": "stacked",
        "graph.y_axis.title_text": "PQL contribution",
    }


# ---------------------------------------------------------------------------
# Dashboard orchestration
# ---------------------------------------------------------------------------

def _param_mapping(card_id: int, dashboard_param_id: str) -> list:
    return [
        {
            "parameter_id": dashboard_param_id,
            "card_id": card_id,
            "target": ["variable", ["template-tag", "email_domain"]],
        }
    ]


def build(collection_id: int, *, dry_run: bool, confirm: bool):
    if not dry_run and not confirm:
        raise SystemExit("Refusing to write without --confirm. Re-run with --confirm or --dry-run.")

    author_name, author_email = whoami()
    on_date = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    print(f"Authenticated as: {author_name} ({author_email})")

    # ----- helpers ---------------------------------------------------------
    def post(path, data):
        if dry_run:
            print(f"[DRY-RUN] POST {path}")
            if "name" in data:
                print(f"         name={data['name']!r}")
            return {"id": -999, "name": data.get("name")}
        return _request("POST", path, data)

    def put(path, data):
        if dry_run:
            print(f"[DRY-RUN] PUT  {path}  ({len(data.get('dashcards', []))} dashcards)")
            return {}
        return _request("PUT", path, data)

    def create_or_get_dashboard(name: str, description: str, parameters: list) -> int:
        if not dry_run:
            existing = find_dashboard(name, collection_id)
            if existing:
                print(f"Updating existing dashboard {name!r} (id={existing['id']})")
                _request(
                    "PUT",
                    f"/dashboard/{existing['id']}",
                    {"description": description, "parameters": parameters},
                )
                return existing["id"]
        new = post(
            "/dashboard",
            {
                "name": name,
                "collection_id": collection_id,
                "description": description,
                "parameters": parameters,
            },
        )
        print(f"Created dashboard {name!r} -> id={new.get('id')}")
        return new["id"]

    # ----- 1) create detail dashboard first (we need its id for linking) ---
    # Pick the current #1 PQA as the default filter value so the dashboard renders
    # something useful on first open. Click-throughs from the top-100 table will
    # override it via the URL parameter.
    default_domain = _request(
        "POST",
        "/dataset",
        {
            "database": DATABASE_ID,
            "type": "native",
            "native": {
                "query": f"select email_domain from {T_SCORES} order by pql_score desc limit 1"
            },
        },
    )["data"]["rows"][0][0] if not dry_run else "example.com"
    detail_param = {
        "id": "email-domain-dash",
        "type": "category",
        "slug": "email_domain",
        "name": "Email domain",
        "sectionId": "string",
        "required": False,
        "default": default_domain,
    }
    detail_dash_id = create_or_get_dashboard(
        DETAIL_DASHBOARD_NAME,
        "PQA/PQL deep dive for a single email domain. Filter the dashboard by the "
        "company's email domain to see the PQA composite components, historical trend, "
        "raw signals, and top 3 PQL (champion) contacts with their score breakdown.",
        [detail_param],
    )

    # ----- 2) create top-100 dashboard -------------------------------------
    top_dash_id = create_or_get_dashboard(
        TOP_DASHBOARD_NAME,
        "Top 100 Product Qualified Accounts, ranked by the PQA composite score "
        "(Breadth 15, Depth 25, Velocity 20, Urgency 40). Click any company's domain "
        "to open the matching PQA / PQL Deep Dive dashboard for that account.",
        [],
    )

    # ----- 3) create cards -------------------------------------------------
    print("\n--- Creating Top 100 cards ---")
    top_cards = {}
    top_cards["eligible"] = post(
        "/card",
        make_card(
            name="Eligible domains",
            display="scalar",
            sql=SQL_SCALAR_ELIGIBLE,
            collection_id=collection_id,
            description="Total domains currently eligible for PQA scoring.",
        ),
    )
    top_cards["median"] = post(
        "/card",
        make_card(name="Median PQA", display="scalar", sql=SQL_SCALAR_MEDIAN, collection_id=collection_id),
    )
    top_cards["mean"] = post(
        "/card",
        make_card(name="Mean PQA", display="scalar", sql=SQL_SCALAR_MEAN, collection_id=collection_id),
    )
    top_cards["tier1"] = post(
        "/card",
        make_card(
            name="Tier 1 (PQA ≥ 75)", display="scalar", sql=SQL_SCALAR_TIER1, collection_id=collection_id
        ),
    )
    top_cards["tier2"] = post(
        "/card",
        make_card(
            name="Tier 2 (50–74)", display="scalar", sql=SQL_SCALAR_TIER2, collection_id=collection_id
        ),
    )
    top_cards["tier3"] = post(
        "/card",
        make_card(
            name="Tier 3 (25–49)", display="scalar", sql=SQL_SCALAR_TIER3, collection_id=collection_id
        ),
    )
    top_cards["distribution"] = post(
        "/card",
        make_card(
            name="PQA score distribution",
            display="bar",
            sql=SQL_DISTRIBUTION,
            collection_id=collection_id,
            viz=distribution_viz(),
            description="Histogram of PQA scores across all eligible domains (bucketed by 5).",
        ),
    )
    top_cards["top100"] = post(
        "/card",
        make_card(
            name="Top 100 PQAs (click domain to open deep dive)",
            display="table",
            sql=SQL_TOP100_TABLE,
            collection_id=collection_id,
            viz=top100_viz(detail_dash_id if not dry_run else 0),
            description=(
                "Top 100 accounts ranked by PQA composite. Click the domain column to "
                "drill into the PQA / PQL deep dive for that company."
            ),
        ),
    )
    top_cards["composition"] = post(
        "/card",
        make_card(
            name="PQA composition — top 20",
            display="bar",
            sql=SQL_COMPOSITION_TOP20,
            collection_id=collection_id,
            viz=composition_stacked_viz(),
            description=(
                "Stacked bar for the top 20 PQAs showing each axis' contribution to the "
                "composite. Max stack height = 100."
            ),
        ),
    )
    top_cards["urgency"] = post(
        "/card",
        make_card(
            name="Urgency signals — top 20",
            display="table",
            sql=SQL_URGENCY_BREAKDOWN,
            collection_id=collection_id,
            description="Raw urgency-axis signals for the top 20 PQAs.",
        ),
    )

    # ----- 4) create detail cards ------------------------------------------
    print("\n--- Creating Detail cards ---")
    detail_cards = {}
    detail_cards["header"] = post(
        "/card",
        make_card(
            name="Company overview",
            display="table",
            sql=SQL_DETAIL_HEADER,
            collection_id=collection_id,
            parameterize_email_domain=True,
        ),
    )
    detail_cards["pqa"] = post(
        "/card",
        make_card(
            name="PQA score",
            display="scalar",
            sql=SQL_DETAIL_PQA_SCALAR,
            collection_id=collection_id,
            parameterize_email_domain=True,
        ),
    )
    detail_cards["tier"] = post(
        "/card",
        make_card(
            name="Tier",
            display="scalar",
            sql=SQL_DETAIL_TIER_SCALAR,
            collection_id=collection_id,
            parameterize_email_domain=True,
        ),
    )
    detail_cards["rank"] = post(
        "/card",
        make_card(
            name="Rank among eligible",
            display="scalar",
            sql=SQL_DETAIL_RANK_SCALAR,
            collection_id=collection_id,
            parameterize_email_domain=True,
        ),
    )
    detail_cards["delta"] = post(
        "/card",
        make_card(
            name="PQA Δ vs 7d ago",
            display="scalar",
            sql=SQL_DETAIL_DELTA_SCALAR,
            collection_id=collection_id,
            parameterize_email_domain=True,
        ),
    )
    detail_cards["active_users"] = post(
        "/card",
        make_card(
            name="Active users (30d)",
            display="scalar",
            sql=SQL_DETAIL_ACTIVE_USERS_SCALAR,
            collection_id=collection_id,
            parameterize_email_domain=True,
        ),
    )
    detail_cards["composition"] = post(
        "/card",
        make_card(
            name="PQA composition",
            display="bar",
            sql=SQL_DETAIL_COMPOSITION,
            collection_id=collection_id,
            viz=detail_composition_viz(),
            parameterize_email_domain=True,
            description="Each axis' contribution to this company's composite PQA score.",
        ),
    )
    detail_cards["history"] = post(
        "/card",
        make_card(
            name="PQA score — last 60 days",
            display="line",
            sql=SQL_DETAIL_HISTORY,
            collection_id=collection_id,
            viz=detail_history_viz(),
            parameterize_email_domain=True,
        ),
    )
    detail_cards["signals"] = post(
        "/card",
        make_card(
            name="Signal detail",
            display="table",
            sql=SQL_DETAIL_SIGNALS,
            collection_id=collection_id,
            parameterize_email_domain=True,
            description="Raw signals behind the PQA axes for this domain.",
        ),
    )
    detail_cards["champions"] = post(
        "/card",
        make_card(
            name="Top PQL champions",
            display="table",
            sql=SQL_DETAIL_CHAMPIONS,
            collection_id=collection_id,
            viz=champions_table_viz(),
            parameterize_email_domain=True,
            description=(
                "Top 3 champion candidates (PQLs) for this domain, by champion_score. "
                "Email column links to the matching HubSpot contact search."
            ),
        ),
    )
    detail_cards["champion_composition"] = post(
        "/card",
        make_card(
            name="PQL composition per champion",
            display="bar",
            sql=SQL_DETAIL_CHAMPION_COMPOSITION,
            collection_id=collection_id,
            viz=champion_composition_viz(),
            parameterize_email_domain=True,
            description=(
                "Stacked contributions per champion. Credits 35 · Activity 20 · Admin 20 · "
                "Limit hits 15 · Role 10. Max stack height = 100."
            ),
        ),
    )

    # ----- 5) layout -------------------------------------------------------
    def dc(card_id, row, col, size_x, size_y, *, idx, param_mappings=None, viz=None):
        return {
            "id": -(idx + 1),
            "card_id": card_id,
            "row": row,
            "col": col,
            "size_x": size_x,
            "size_y": size_y,
            "series": [],
            "visualization_settings": viz or {},
            "parameter_mappings": param_mappings or [],
        }

    # --- Top 100 layout ---
    top_dashcards = [attribution_tile(author_email, on_date)]
    idx = 0
    # Row 1 (y=1): 6 KPI scalars, each 4 wide => 24 total
    for i, key in enumerate(["eligible", "median", "mean", "tier1", "tier2", "tier3"]):
        top_dashcards.append(dc(top_cards[key]["id"], 1, i * 4, 4, 3, idx=idx))
        idx += 1
    # Row 2 (y=4): distribution (full width 24 x 6)
    top_dashcards.append(dc(top_cards["distribution"]["id"], 4, 0, 24, 6, idx=idx)); idx += 1
    # Row 3 (y=10): top 100 (full width 24 x 14)
    top_dashcards.append(dc(top_cards["top100"]["id"], 10, 0, 24, 14, idx=idx)); idx += 1
    # Row 4 (y=24): composition 12 wide + urgency breakdown 12 wide
    top_dashcards.append(dc(top_cards["composition"]["id"], 24, 0, 12, 8, idx=idx)); idx += 1
    top_dashcards.append(dc(top_cards["urgency"]["id"],     24, 12, 12, 8, idx=idx)); idx += 1
    # Eligibility note (full width)
    top_dashcards.append(eligibility_note_tile(row=32))

    put(f"/dashboard/{top_dash_id}", {"dashcards": top_dashcards})
    print(f"Attached {len(top_dashcards)} dashcards to Top 100 dashboard (id={top_dash_id}).")

    # --- Detail layout ---
    pmap = _param_mapping  # shorthand
    detail_dashcards = [attribution_tile(author_email, on_date)]
    idx = 0
    # Row 1 (y=1): 6 KPI scalars (pqa / tier / rank / delta / active_users / header-summary). 24 wide total.
    for i, key in enumerate(["pqa", "tier", "rank", "delta", "active_users"]):
        detail_dashcards.append(
            dc(
                detail_cards[key]["id"], 1, i * 4, 4, 3, idx=idx,
                param_mappings=pmap(detail_cards[key]["id"], "email-domain-dash"),
            )
        )
        idx += 1
    # Header/company overview table (1 slot at col 20, width 4 x 3)
    detail_dashcards.append(
        dc(
            detail_cards["header"]["id"], 1, 20, 4, 3, idx=idx,
            param_mappings=pmap(detail_cards["header"]["id"], "email-domain-dash"),
        )
    )
    idx += 1
    # Row 2 (y=4): composition (12 x 7) + history (12 x 7)
    detail_dashcards.append(
        dc(
            detail_cards["composition"]["id"], 4, 0, 12, 7, idx=idx,
            param_mappings=pmap(detail_cards["composition"]["id"], "email-domain-dash"),
        )
    )
    idx += 1
    detail_dashcards.append(
        dc(
            detail_cards["history"]["id"], 4, 12, 12, 7, idx=idx,
            param_mappings=pmap(detail_cards["history"]["id"], "email-domain-dash"),
        )
    )
    idx += 1
    # Row 3 (y=11): signal table (full width 24 x 8)
    detail_dashcards.append(
        dc(
            detail_cards["signals"]["id"], 11, 0, 24, 8, idx=idx,
            param_mappings=pmap(detail_cards["signals"]["id"], "email-domain-dash"),
        )
    )
    idx += 1
    # Row 4 (y=19): champions table (14 x 8) + champion composition (10 x 8)
    detail_dashcards.append(
        dc(
            detail_cards["champions"]["id"], 19, 0, 14, 8, idx=idx,
            param_mappings=pmap(detail_cards["champions"]["id"], "email-domain-dash"),
        )
    )
    idx += 1
    detail_dashcards.append(
        dc(
            detail_cards["champion_composition"]["id"], 19, 14, 10, 8, idx=idx,
            param_mappings=pmap(detail_cards["champion_composition"]["id"], "email-domain-dash"),
        )
    )
    idx += 1
    # Eligibility note (full width, at the bottom)
    detail_dashcards.append(eligibility_note_tile(row=27))

    put(f"/dashboard/{detail_dash_id}", {"dashcards": detail_dashcards})
    print(f"Attached {len(detail_dashcards)} dashcards to Detail dashboard (id={detail_dash_id}).")

    if not dry_run:
        base = os.environ.get("METABASE_URL", "https://metabase.example.com")
        print("\n✓ Done.")
        print(f"  Top 100 dashboard: {base}/dashboard/{top_dash_id}")
        print(f"  Detail dashboard : {base}/dashboard/{detail_dash_id}")
        print(f"\n  Try the link:  {base}/dashboard/{detail_dash_id}?email_domain=example.com")


def main():
    if not KEY:
        sys.exit("METABASE_API_KEY env var is required.")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--collection-id", type=int, default=DEFAULT_COLLECTION_ID)
    ap.add_argument("--dry-run", action="store_true", help="Print intended calls, don't write.")
    ap.add_argument(
        "--confirm", action="store_true", help="Required to actually POST/PUT to Metabase."
    )
    args = ap.parse_args()
    build(args.collection_id, dry_run=args.dry_run, confirm=args.confirm)


if __name__ == "__main__":
    main()
