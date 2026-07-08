# PLG → Sales Upsell Agent

Agent that identifies domains highly likely to convert to enterprise if reached out to this week, and the individuals within those domains most likely to champion the deal.

## Architecture

Three-layer system:

1. **Data layer (dbt + BigQuery)** — dbt models in the main analytics repo score every eligible domain daily and identify the best outreach contacts. Output lives in three BigQuery tables:
   - `plg_upsell_domain_scores` — one row per domain, composite 0–100 PQL score (latest snapshot)
   - `plg_upsell_domain_scores_daily` — append-only daily history of all scores (one row per domain per day)
   - `plg_upsell_domain_champions` — top 3 outreach contacts per domain, ranked by champion score

2. **Delivery layer (Oz cloud agent)** — a daily scheduled Oz cloud agent reads those tables, posts the day's top champions to Slack for the BDR team, and logs every recommendation to BigQuery.
   - `plg_upsell_recommendations_log` — append-only, one row per domain per day it was surfaced (scores at time of recommendation)

3. **Learning layer (dbt + Oz cloud agent)** — closes the feedback loop. A dbt model joins the recommendations log to HubSpot outcome data (contact created → email sent → reply → meeting booked → deal qualified). A weekly scheduled agent analyzes performance across all past recommendations and posts a digest to Slack so the team can evaluate hit rates and tune scoring weights over time.
   - `plg_upsell_recommendation_outcomes` — one row per recommendation enriched with HubSpot outcome columns and days-to-outcome latency

### Scheduled agent flows

```
Weekly (Mondays, 04:00 PT / 11:00 UTC) — Sync + Delivery
  └── Oz cloud agent wakes up
        └── Reads skill: .warp/skills/hubspot-plg-sync/SKILL.md
              └── Installs Python deps
                    ├── Runs plg_upsell/scripts/scoring_sync.py
                    │     (BigQuery → HubSpot: updates pqa_* / pql_* properties,
                    │      applies dampening logic, tiers accounts, associates champions)
                    └── Runs plg_upsell/scripts/scoring_digest.py
                          ├── Queries BigQuery scores + HubSpot active list
                          ├── Builds "This week's plays" (action-oriented insights)
                          ├── Renders Newly Active, Top Movers, Tier 1 PQAs, Pipeline Wins
                          └── Posts Block Kit digest → Slack #your-plg-alerts-channel

Weekly (planned) — Learning
  └── Oz cloud agent wakes up
        └── Queries plg_upsell_recommendation_outcomes (dbt model)
              └── Computes action rate, response rate, qualification rate by score band
                    └── Posts performance digest → Slack
```

The agent uses a GCP service account (`GCP_SERVICE_ACCOUNT_JSON`) for BigQuery auth and a Slack bot token (`PLG_SLACK_BOT_TOKEN`) — both stored as secrets in the Oz environment.

## V1: Data Layer

Reference SQL queries over a small set of generic tables (`users`, `accounts`, `usage_events`, ...). See `plg_upsell/reference/DATA_DEFINITIONS.md` for the canonical definitions and design decisions behind each input.

**Warehouse mapping (adopting this pipeline).** The scoring is warehouse-agnostic:
- **dbt:** set the `plg_source_database` / `plg_source_schema` vars (see `dbt/models/plg_upsell/plg_upsell_domain_scores_daily.sql`) and, if your column names differ, adjust the FROM/SELECT clauses.
- **Standalone `sql/`:** replace the `your_project.your_dataset` placeholder and map the table/column names to your schema. Lines marked `EDIT:` flag product-specific assumptions (paid-plan names, limit/paywall event filters, seat caps).

Throughout, **"usage units"** means your core consumption metric — AI credits, API calls, seats, build minutes, messages, etc. Output column names that still say "credits" (e.g. `total_credits_30d`) are kept stable for downstream consumers; read them as usage units.

### Directory Structure

```
sql/
├── filters/
│   └── eligible_domains.sql       # Base domain filter (work email + 2+ active users + team, team_plus, or business plan)
├── metrics/
│   ├── breadth_wau_by_domain.sql              # Avg WAU per domain (last 4 weeks)
│   ├── depth_total_credits_by_domain.sql      # Total usage units consumed (last 30d)
│   ├── depth_reload_revenue_by_domain.sql     # Overage/add-on $ spent (last 30d)
│   ├── depth_avg_credits_per_user_week.sql    # Avg usage units/user/week
│   ├── depth_avg_days_active_per_user_week.sql # Avg days active/user/week
│   └── velocity_wow_growth.sql                # WoW growth in WAU + usage
├── signals/
│   ├── hit_limits.sql             # Users hitting usage limits (last 14d)
│   ├── recent_upgrades.sql        # Users upgrading to paid (last 30d)
│   ├── recent_reload_credits.sql  # Overage/add-on purchases (last 14d)
│   └── recent_domain_growth.sql    # New domain members added (last 14d)
└── scoring/
    ├── pql_score.sql              # Composite 0-100 PQL score per eligible domain
    └── domain_champion.sql        # Top 3 individual champions per domain
dbt/models/plg_upsell/
├── plg_upsell_domain_scores_daily.sql  # Incremental daily score snapshots (append-only)
└── schema.yml                          # Column documentation
reference/
├── DATA_DEFINITIONS.md            # Detailed definitions and design decisions for all inputs
└── bdr_email_template.md          # Current BDR outreach email template
```

### Domain Eligibility Criteria

Inclusion (all required):
1. **Work email domain** — domain from `users`, excluding free/consumer email providers (gmail, yahoo, etc.), `.edu` domains, and your own company domain (`your-company.com`)
2. **2+ active users** — at least 2 non-excluded users with activity in last 30 days
3. **Paid self-serve plan** — domain has 1+ account on a paid self-serve plan with active subscription (`team`, `team_plus`, `business` are illustrative example plan names — edit to match yours)

Exclusion:
4. **Not already in the sales pipeline** — domain has no open or closed-won deal in your CRM (`crm_deals`, excluding closed-lost)

Company metadata (name, industry, size, country) is enriched via LEFT JOIN to `companies` when available.

### Key Tables Used

These are generic table names; point them at your warehouse (see Warehouse mapping above).

| Table | Purpose |
|-------|---------|
| `companies` | Company metadata enrichment (name, industry, size, country) — not a filter |
| `users` | User-level facts: email domain, exclusion flag, activity, usage units, persona |
| `accounts` | Account plan, subscription, admin domain, seats |
| `weekly_active_users` | Weekly active user signal |
| `usage_events` | Core usage-unit consumption per event (example: AI credits) |
| `overage_purchases` | Add-on / overage purchases beyond the base plan |
| `limit_events` | Usage-limit / paywall hit events |
| `upgrades` | Free-to-paid upgrade events |
| `membership_events` | Account join/invite events |
| `crm_deals` | CRM deals — used to exclude domains already in the sales pipeline |

### Scoring Models

#### PQL Score — `plg_upsell/sql/scoring/pql_score.sql`

Produces a **0–100 composite score per eligible domain** answering: *"Which domains should we reach out to this week?"*

**Approach:**
- All metrics are **percentile-ranked** across the eligible population (0.0 → 1.0), then multiplied by their weight
- Urgency score is a weighted average of its four component percentiles, scaled to 0–100
- Composite PQL = sum of all weighted percentiles (0–100)

**Weight allocation (total = 100). Illustrative example weights — calibrate for your own funnel:**

- Breadth — Avg WAU (last 4 weeks): **20** — sustained multi-user adoption
- Depth — Total usage units + units per user (30d): **20** — raw product value + per-user intensity (split 60/40 within depth)
- Velocity — WoW usage growth: **20** — domain is accelerating now
- Urgency — Distinct users hitting usage limits (14d): **10**
- Urgency — Overage/add-on purchases (14d): **10**
- Urgency — Recent free→paid upgrades (30d): **10**
- Urgency — New domain members added (14d): **10**

Note: these weights are illustrative starting points. Tune the category and urgency-signal balance against your own funnel data.

**Output columns:** `pql_score`, `urgency_score`, `breadth_score`, `depth_score`, `velocity_score`, plus raw metrics (`avg_wau`, `paid_seats`, `total_credits_30d`, `credits_per_user_30d`, `wow_growth_pct`, `weeks_growing`, `limit_hits`, `users_hitting_limits`, `reload_dollars`, `users_upgraded`, `new_domain_members`), and firmographics (`company_name`, `industry`, `company_size`, `country`, `active_users_last_30d`)

#### Champion Score — `plg_upsell/sql/scoring/domain_champion.sql`

For each eligible domain, identifies the **top 3 individuals** most likely to respond to outreach and connect to a decision-maker. In the dbt model, eligible domains come from `plg_upsell_domain_scores` (i.e. champions are only computed for domains that already have a PQL score).

**Approach:**
1. Compute per-user signals within the domain
2. Min-max normalize usage units and activity **within each domain** (0 → 1) so users are ranked against their domain peers, not the global population
3. Apply weights to produce a 0–100 champion score
4. Rank users within each domain; return top 3 (`rank_in_domain <= 3`)

**Weight allocation (total = 100). Illustrative example weights — calibrate for your own funnel:**

- Usage-unit usage (min-max normalized within domain): **20** — heaviest user = most invested
- Activity frequency (min-max normalized within domain): **20** — regular usage vs one-time spike
- Is account admin: **20** — has billing authority and org influence
- Hit usage limits (last 14d, raw count): **20** — personally felt the pain point
- High-leverage persona/role (illustrative: engineering_manager, devops/sre, fullstack, backend): **20** — more likely to influence purchasing

**Output columns:** `user_email`, `champion_score`, `rank_in_domain`, `credits_used_t30d`, `days_active_in_last_30`, `is_team_admin`, `limit_hit_count`, `grouped_survey_role`, `is_on_team_with_active_subscription`, `first_sub_plan_type`

The digest (`plg_upsell/scripts/scoring_digest.py`) surfaces `rank_in_domain = 1` — the single top champion per Tier 1 domain — on each PQA card.

#### Daily Score History — `plg_upsell/dbt/models/plg_upsell/plg_upsell_domain_scores_daily.sql`

Append-only incremental dbt model that snapshots every domain's PQL score daily. Key design:

- **Ever-eligible universe** — once a domain qualifies, it gets a row every day forever, even if it later drops eligibility
- **Ineligible domains scored as 0** — if a domain loses its paid plan, falls below 2 active users, or enters the sales pipeline, `is_eligible = FALSE`, `pql_score = 0`, and `ineligibility_reason` explains which filter failed (e.g. `no_active_paid_plan`, `below_active_user_threshold`, `entered_enterprise_pipeline`)
- **Raw metrics always computed** — `avg_wau`, `total_credits_30d`, etc. are populated for all domains regardless of eligibility, so usage trends remain visible even for domains that dropped out
- **Score history** — `first_eligible_date` and `days_in_pool` track how long each domain has been in the scored universe

Partitioned by `scored_date`, clustered by `email_domain`. Unique key: `(email_domain, scored_date)`.

## V2: Weekly HubSpot Sync + Slack Digest

The delivery flow is a two-step Monday-morning pipeline: sync BigQuery scores to HubSpot, then post an action-oriented digest to Slack for the BDR team.

### Scripts

- `plg_upsell/scripts/scoring_sync.py` — legacy BigQuery → HubSpot score sync. Updates `pqa_*` / `pql_*` properties on companies and contacts, applies dampening logic, computes tiers, excludes Target Accounts, and associates champion contacts. Direct routing/enrichment side effects are disabled; Dagster owns Lead creation/routing orchestration and triggers the existing enrichment/routing workflows where applicable.
- `plg_upsell/dagster/` — new PQA/PQL → HubSpot sync path. Reads scoring tables, upserts companies/contacts, and creates or updates Leads with routing motion and Tier 1/Tier 2 sequence intent.
- `plg_upsell/scripts/scoring_digest.py` — Builds the weekly digest from BigQuery (authoritative for scores/signals) + HubSpot (state: active list, score deltas, URLs) and posts a Block Kit message to `#your-plg-alerts-channel` (`C0EXAMPLE000`).

Digest sections:

1. **📊 This week's plays** — up to 5 action-oriented bullets, each naming a specific account, the concrete signal (with time window), and a recommended next step. Play types: just-promoted Tier 1, credit surge, limit-hits, land-and-expand, self-upgrades, Tier 1 drift, admin-champion emerged.
2. **🆕 Newly Active Accounts** — accounts that just crossed into the Active pipeline this sync. Stats labeled with explicit windows (4w avg WAU, 30d credits, 14d limit hits, WoW deltas).
3. **📈 Top Movers** — biggest positive WoW score deltas.
4. **🏆 Tier 1 PQAs** — multi-line card per Tier 1 company with score + WoW, usage stats, urgency signals (hidden when empty), and the top champion with an optional `✍️ Draft outreach` link (powered by the `draft-champion-outreach` skill).
5. **⚠️ Watch List** — accounts trending toward de-prioritization.
6. **💰 Pipeline Wins** — deals from PLG accounts that hit SAO / SQO / Closed Won in the last 7 days.

### One-time Slack app setup

1. Create a Slack app in your Slack workspace
2. Add **Bot Token Scopes**: `chat:write` (required), plus `channels:read` + `channels:join` if you want the script to self-verify channel membership
3. Install/reinstall the app to the workspace
4. Invite the bot to `#your-plg-alerts-channel`
5. Copy the bot token (`xoxb-…`) and store it as `PLG_SLACK_BOT_TOKEN` — locally in `.env`, and as an Oz team secret for cloud runs

### Required secrets / env vars

- `GENERAL_HUBSPOT_APP_TOKEN` (or `HUBSPOT_PRIVATE_APP_TOKEN`) — HubSpot private app token
- `PLG_SLACK_BOT_TOKEN` — Slack bot token for `chat.postMessage`
- `GCP_SERVICE_ACCOUNT_JSON` — GCP service account with BigQuery read access (or ADC via `GOOGLE_APPLICATION_CREDENTIALS`)
- `HUBSPOT_SLACK_WEBHOOK` — optional fallback when no bot token is set
- `PLG_OUTREACH_PROMPT_URL` — optional Warp Drive Prompt share URL; when set, each Tier 1 champion card renders a `✍️ Draft outreach` link that invokes the `draft-champion-outreach` skill
- `PLG_PRODUCT_QUALIFIED_SEQUENCE_ID` — HubSpot sequence ID for the Product Qualified Leads sequence. Dagster stamps this on Tier 1 champion contacts for the HubSpot workflow that performs actual sequence enrollment. Find this in HubSpot CRM > Sequences (the numeric ID appears in the URL when you open a sequence).
- `PLG_PRODUCT_QUALIFIED_SEQUENCE_NAME` — optional display name for the sequence trigger fields (e.g. `Tier 1 PQL`).

### Digest flags

- `--dry-run` — print the message to stdout without posting
- `--channel C0EXAMPLE000` — override the Slack channel
- `--delay-minutes 60` — wait N minutes before posting (useful for giving lead routing time after the sync)
- `--dataset analytics` / `--project example-gcp-project` — BigQuery overrides
- `--top-n 10` — number of top champions to surface per Tier 1 domain

### Local run

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt

# Sync scores BQ → HubSpot
.venv/bin/python plg_upsell/scripts/scoring_sync.py --dry-run
.venv/bin/python plg_upsell/scripts/scoring_sync.py

# Post the digest
.venv/bin/python plg_upsell/scripts/scoring_digest.py --dry-run --channel C0EXAMPLE000
.venv/bin/python plg_upsell/scripts/scoring_digest.py --channel C0EXAMPLE000
```

### Oz cloud scheduling

The skill for scheduled runs lives in `.warp/skills/hubspot-plg-sync/SKILL.md`. The pipeline is split across three Monday-morning schedules so Clay enrichment has time to land between the sync and the routing pass:

- `plg-hubspot-weekly-sync` (`0 11 * * 1`, Mon 04:00 PT) — legacy score/property sync only; direct routing and enrichment side effects are disabled.
- `plg-hubspot-dagster-sync` (new) — create/update HubSpot companies, contacts, and Leads from the PQA/PQL tables. Tier 1 Leads carry manual BDR outreach + Product Qualified Leads sequence intent. Tier 2 is held for now: no Lead creation, no enrichment trigger, and no sequence enrollment. Tier 3 is marketing touch. Tier 1 contacts receive the current enrichment/routing workflow trigger fields from Dagster, plus contact-level sequence trigger fields for the Product Qualified Leads workflow.
- `plg-weekly-digest` (`0 17 * * 1`, Mon 10:00 PT) — post the digest to `#your-plg-alerts-channel`.

To recreate one from scratch (replace `<ENV_ID>`):

```bash
oz-dev schedule create \
  --team \
  --name "plg-hubspot-weekly-sync" \
  --cron "0 11 * * 1" \
  --environment <ENV_ID> \
  --skill "<your-org>/<your-repo>:hubspot-plg-sync" \
  --prompt "Run this week's PLG scoring sync and post the digest to #your-plg-alerts-channel."
```

### Learning Loop (planned)

The delivery layer currently posts recommendations but never learns whether they were good. The learning loop closes this gap with three components:

#### 1. Recommendations log

Every time the daily agent posts to Slack, it also appends rows to `plg_upsell_recommendations_log` in BigQuery:

- `surfaced_date` — date the recommendation was posted
- `email_domain`, `company_name` — the recommended domain
- `champion_email` — the surfaced champion contact
- `pql_score`, `breadth_score`, `depth_score`, `velocity_score`, `urgency_score` — scores at time of recommendation
- `champion_score` — champion score at time of recommendation

This is an append-only table — one row per domain per day it was surfaced.

#### 2. Outcome tracking (dbt model)

A dbt model (`plg_upsell_recommendation_outcomes`) joins the recommendations log to HubSpot data to track the downstream funnel per recommendation:

- Was a HubSpot contact created for this champion? (BDR acted on it)
- Was an email/sequence sent? (BDR engaged)
- Did the contact reply or book a meeting?
- Was a deal created? Was it marked as qualified (SQL)?
- Did it close-won?

Each row is one recommendation enriched with outcome columns and days-to-outcome latency.

#### 3. Weekly performance digest (Oz scheduled agent)

A weekly scheduled agent queries the outcome model and posts a summary to Slack:

- **Action rate** — % of surfaced recommendations the BDR pursued
- **Response rate** — of those pursued, % that got a reply
- **Qualification rate** — of responses, % that became SQLs
- **Conversion by score band** — are PQL 90+ domains actually converting better than 70–80?
- **Signal contribution** — which component scores correlate most with successful outcomes?

This gives the team data to manually tune weights when patterns emerge, and can eventually graduate to automated adjustments once sample sizes support it.

## Next Steps

- **Deploy dbt models to production** — once the dbt models are deployed, update the `--dataset` flag in the skill file and README from your development dataset to your production dataset (e.g. `analytics`)
- **Calibrate scoring weights** — share ranked domains with the GTM team and incorporate their weight adjustments into the dbt model once consensus is reached
- **Implement recommendations log** — add a BigQuery write step to `plg_upsell/scripts/scoring_digest.py` so every surfaced recommendation is logged to `plg_upsell_recommendations_log`
- **Build outcome tracking model** — create dbt model joining recommendations log to HubSpot data to track BDR actions and deal outcomes per recommendation
- **Set up weekly performance digest** — create a second Oz scheduled agent (weekly) that queries the outcome model and posts a performance summary to Slack
