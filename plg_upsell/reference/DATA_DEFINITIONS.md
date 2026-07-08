# PLG → Sales Agent: Data Definitions & Design Decisions

## Overview

This document defines every data input to the PLG upsell agent's domain scoring system — what it measures, where it comes from, and why.

**Warehouse-agnostic.** The pipeline reads a small set of generic tables (`users`, `accounts`, `usage_events`, `limit_events`, `overage_purchases`, ...). Point it at your warehouse via the dbt vars `plg_source_database` / `plg_source_schema`, and for the standalone `sql/` queries replace the `your_project.your_dataset` placeholder. Map any differing column names in the FROM/SELECT clauses; lines marked `EDIT:` flag product-specific assumptions.

**Usage units.** The framework operates on a generic **usage unit** — your core consumption metric. Examples throughout use AI credits, but this can be API calls, seats, build minutes, messages, compute seconds, etc. Some output column names still say "credits" (e.g. `total_credits_30d`, `credits_used_t30d`); these are kept stable for downstream consumers and should be read as usage units.

The unit of analysis is the **email domain** (e.g. `acme.com`). A domain represents a company. Users are mapped to domains via `users.email_domain`. Accounts are mapped via `accounts.admin_email_domain`.

---

## 1. Domain Eligibility Filters

> SQL: `sql/filters/eligible_domains.sql`

A domain must pass **all three inclusion criteria** and **none of the exclusion criteria** to enter the scoring pipeline.

Note: Company metadata (name, industry, size, country) is enriched via LEFT JOIN to `companies` when available, but is not required for eligibility.

### Inclusion Criteria

#### 1a. Work Email Domain
- **What:** Domain is a work email domain — not a free/consumer email provider (gmail, outlook, etc.), not a `.edu` domain, and not your own company domain (`your-company.com`).
- **Table:** `users` (source of `email_domain`)
- **Exclusion list:** Common free email providers (gmail.com, yahoo.com, hotmail.com, outlook.com, etc.) are excluded via an explicit blocklist. `.edu` domains and your own company domain are also excluded.
- **Why:** We want to surface all domains with real company usage. This broadens the eligible pool while still filtering out consumer and non-commercial domains.
- **Enrichment:** Company metadata (`company_name`, `industry`, `company_size`, `country`) is enriched via LEFT JOIN to `companies` when available — not all domains will have this data.

#### 1b. 2+ Active Users in Last 30 Days
- **What:** At least 2 non-excluded users with `active_days_30d > 0`.
- **Table:** `users`
- **Filters:** `NOT is_excluded`
- **Why:** A single user isn't a team motion. We need evidence of multi-user adoption at the domain before it's worth a sales touch. The exclusion flag prevents bot/abuse/internal accounts from inflating counts.

#### 1c. Has 1+ Account on a Paid Self-Serve Plan
- **What:** Domain has at least one account with an active subscription on a paid self-serve plan.
- **Table:** `accounts`
- **Filters:** `plan_name IN (...)` AND `subscription_status = 'active'` — `team`, `team_plus`, `business` are illustrative example plan names; **EDIT** to match your paid self-serve plans.
- **Domain resolution:** We identify an account's domain by the **admin's** email domain (`admin_email_domain`), since the admin is the billing owner.
- **Why:** These are the self-serve paid plans that sit just below enterprise. Domains already paying at this tier are the natural upsell pool.

### Exclusion Criteria

#### 1d. Already in the Sales Pipeline (CRM)
- **What:** Domain has any non-lost deal in your CRM (closed-won, open in any pipeline, or active POC).
- **Table:** `crm_deals` (CRM deals with `email_domain`; in HubSpot this is pre-joined from `crm_accounts`)
- **Filter:** `NOT is_closed_lost` — this captures:
  - **Closed-won deals** — already an enterprise customer
  - **Open deals** across all pipelines (new sales, expansion, renewals, POC)
  - **Active POCs** — mid-evaluation, sales is engaged

##### Decision: Why NOT exclude closed-lost deals?
Closed-lost means a prior sales attempt didn't convert. But if that domain now has strong organic PLG usage on a paid self-serve plan, that's a meaningful new signal. The product-led motion may succeed where the top-down sales motion didn't. These are potentially high-value re-engagement targets.

##### CRM Pipelines Reference
| Pipeline ID | Label | What it tracks |
|---|---|---|
| `YOUR_NEW_SALES_PIPELINE_ID` | new sales pipeline | New enterprise deal flow |
| `YOUR_EXPANSION_PIPELINE_ID` | expansion pipeline | Existing customer expansion |
| `YOUR_RENEWALS_PIPELINE_ID` | renewals pipeline | Contract renewals |
| `YOUR_POC_PIPELINE_ID` | poc pipeline | Proof-of-concept evaluations |
| `default` | sales pipeline | Legacy pipeline |

---

## 2. Scoring Metrics

All metrics are computed per eligible domain. They fall into three categories: breadth, depth, and velocity.

### 2a. Breadth: Weekly Active Users (WAU)

> SQL: `sql/metrics/breadth_wau_by_domain.sql`

- **What:** Average number of distinct weekly active users per domain, averaged over the last 4 complete weeks.
- **Table:** `weekly_active_users`
- **Domain resolution:** Join to `users` on `user_id` to get `email_domain`.
- **Week definition:** Weeks start on Sunday. Only complete weeks are included (current partial week excluded).
- **Deduplication:** Users appearing in multiple rows within a week are deduplicated via `COUNT(DISTINCT user_id)`.
- **Exclusion filter:** Applied via `users.is_excluded`.
- **Output columns:** `avg_wau_last_4_weeks`, `min_wau_last_4_weeks`, `max_wau_last_4_weeks`
- **Why WAU over DAU:** WAU smooths out daily noise and better reflects sustained engagement patterns.

### 2b. Depth (Domain Level): Total Usage Units

> SQL: `sql/metrics/depth_total_credits_by_domain.sql`

- **What:** Total usage units (example: AI credits) consumed across all users in a domain over the last 30 days.
- **Table:** `usage_events`
- **Metric:** `SUM(usage_units)` cast to FLOAT64
- **Domain resolution:** Join to `users` on `user_id`.
- **Why:** Raw consumption is the strongest signal of product value. Domains burning through usage are getting real work done with the product.

### 2c. Depth (Domain Level): Overage / Add-on Revenue

> SQL: `sql/metrics/depth_reload_revenue_by_domain.sql`

- **What:** Total dollars spent on add-on/overage purchases per domain in the last 30 days.
- **Table:** `overage_purchases`
- **Domain resolution:** `overage_purchases.email_domain` (already present on the table).
- **Filters:** `status = 'paid'`, last 30 days by `purchased_at`.
- **Grouping:** By `email_domain` and `purchase_reason` (distinguishes auto-reload vs manual vs at-signup purchases).
- **Revenue:** `amount` is assumed to be in cents; divided by 100 for dollars. **EDIT** if your source is already in dollars.
- **Why:** Willingness to spend beyond the base plan is a direct signal of demand exceeding allocation — exactly the pain point enterprise solves.

### 2d. Depth (User Average): Usage Units per User per Week

> SQL: `sql/metrics/depth_avg_credits_per_user_week.sql`

- **What:** Average usage units consumed per user per week, by domain. Averaged over the last 4 complete weeks.
- **Tables:** `usage_events` joined to `users`
- **Metric:** `SUM(usage_units) / COUNT(DISTINCT user_id)` per week, then averaged across weeks.
- **Why:** Normalizes for domain size. A 5-person domain where every user burns 500 units/week is a hotter lead than a 500-person domain where 3 people use the product casually.

### 2e. Depth (User Average): Days Active per User per Week

> SQL: `sql/metrics/depth_avg_days_active_per_user_week.sql`

- **What:** Average number of days each user is active per week, by domain.
- **Tables:** `daily_active_users` for daily activity, scoped to last 4 complete weeks.
- **Domain resolution:** Join to `users` on `user_id`.
- **Metric:** Count distinct active dates per user per week → average across users → average across weeks.
- **Why:** Frequency of use. A domain where users are active 5 days/week is deeply embedded vs. 1 day/week casual usage. Complements usage depth with a behavioral stickiness signal.

### 2f. Velocity: Week-over-Week Growth

> SQL: `sql/metrics/velocity_wow_growth.sql`

- **What:** WoW percentage change in WAU, usage units, and active user count. Computed over the last 4 complete weeks (requires 5 weeks of data for 4 deltas).
- **Tables:** `usage_events` + `weekly_active_users`, both joined to `users`.
- **Metrics per domain:**
  - `avg_usage_wow_growth` — average WoW % change in usage consumption
  - `avg_wau_wow_growth` — average WoW % change in WAU
  - `avg_active_users_wow_growth` — average WoW % change in active users
  - `weeks_with_usage_growth` / `weeks_with_wau_growth` — count of weeks with positive growth
  - `latest_week_*` — absolute values for the most recent week
- **Why:** Growth trajectory matters as much as absolute levels. A domain that doubled usage in the last month is a better "reach out THIS WEEK" candidate than one with high but flat usage.

---

## 3. Time-Based Signals (Flags)

Binary/count-based signals that indicate a domain is at a decision point RIGHT NOW. These are not continuous metrics — they flag recent events.

### 3a. Hit Usage Limits

> SQL: `sql/signals/hit_limits.sql`

- **What:** Users in the domain hit a usage limit / paywall in the last 14 days.
- **Table:** `limit_events`
- **Filters:** `feature = 'ai_feature'` AND `entrypoint = 'agent'` are the illustrative filters for the limit we care about — **EDIT** to match how your product records limit/paywall hits (or remove to count all).
- **Domain resolution:** Join to `users` on `user_id`.
- **Output:** `total_limit_hits`, `users_hitting_limits`, date range.
- **Why:** Hitting a usage limit is the strongest immediate demand signal. Users literally tried to use more than their plan allows.

### 3b. Recent Upgrades

> SQL: `sql/signals/recent_upgrades.sql`

- **What:** Users in the domain upgraded from free to a paid plan in the last 30 days.
- **Table:** `upgrades`
- **Filters:** `did_upgrade = TRUE`, `upgraded_at` in last 30 days.
- **Output:** `users_upgraded`, `upgrade_types` (array of distinct types).
- **Why:** Recent upgrades signal momentum — the domain is actively investing. If individuals are upgrading on their own, there may be appetite for a centralized enterprise agreement.

### 3c. Recent Overage / Add-on Purchases

> SQL: `sql/signals/recent_reload_credits.sql`

- **What:** Add-on / overage purchases in the last 14 days.
- **Table:** `overage_purchases`
- **Filters:** `status = 'paid'`, last 14 days.
- **Output:** `num_purchases`, `accounts_purchasing`, `total_dollars_spent`, `total_units_bought`, `purchase_types`.
- **Why:** Buying overage means usage exceeded the plan allocation and users valued the product enough to pay more. Shorter lookback (14d vs 30d) because this is a recency signal.

### 3d. Recent Domain Growth

> SQL: `sql/signals/recent_domain_growth.sql`

- **What:** New members joining accounts at the domain in the last 14 days.
- **Table:** `membership_events`
- **Domain resolution:** Join to `accounts` on `account_id` → `admin_email_domain`.
- **Event types:** illustrative `'join team'`, `'join team via team discovery'`, `'invited teammates'`, `'send team invite email'` — **EDIT** to your event taxonomy.
- **Output:** `total_join_events`, `distinct_new_members`, `accounts_with_new_members`, date range.
- **Why:** Organic growth means the product is spreading within the company. More seats = stronger case for enterprise pricing, and the growth itself signals internal champions.

---

## 4. PQL Scoring Model

> SQL: `sql/scoring/pql_score.sql`

The Product Qualified Lead score is a composite 0–100 score per eligible domain, designed to answer: **"Which domains should we reach out to THIS WEEK?"**

### Approach
1. Compute raw metrics per domain (reusing all queries from sections 2 and 3)
2. Percentile-rank each metric across all eligible domains (0.0 to 1.0)
3. Apply category weights with urgency signals weighted heaviest
4. Sum weighted percentiles → composite score 0–100

### Weight Allocation

> Illustrative example weights — calibrate for your own funnel.

| Category | Weight | Rationale |
|---|---|---|
| **Breadth** (WAU) | 20 | Multi-user adoption across the domain |
| **Depth** (usage units + per-user intensity) | 20 | Proves product value. Split 60/40 between total usage and per-user usage to balance big domains vs intense small ones |
| **Velocity** (WoW usage growth) | 20 | Growth trajectory indicates the domain is accelerating now |
| **Urgency: Hit limits** | 10 | Immediate demand signal — users tried to use more than their plan allows |
| **Urgency: Overage purchases** | 10 | Willingness to pay beyond plan allocation — demand exceeds supply |
| **Urgency: Recent upgrades** | 10 | Individual users self-converting signals enterprise appetite |
| **Urgency: Domain growth** | 10 | Product spreading organically within the company |

Urgency signals total **40%** of the score in this example split. These weights are illustrative — tune the balance to your own data.

### Output Columns
- `pql_score` (0–100) — the composite rank
- `breadth_score`, `depth_score`, `velocity_score`, `urgency_score` — component scores for diagnostics
- All raw metric values for context (`avg_wau`, `total_credits_30d`, `limit_hits`, etc.)

### Score Distribution
- Median is ~50 by construction, since scores are built from percentile ranks across the eligible population
- Domains with several active urgency signals cluster toward the top of the range
- Actual counts and top-end scores depend entirely on your own funnel

### Daily Score History

> dbt model: `dbt/models/plg_upsell/plg_upsell_domain_scores_daily.sql`

Append-only incremental model that snapshots all scores daily.
- **Grain:** one row per `(email_domain, scored_date)`
- **Materialization:** `incremental` (BigQuery merge), partitioned by `scored_date`, clustered by `email_domain`
- **Ever-eligible universe:** Once a domain passes all eligibility filters, it stays in the table forever. If it later fails a filter, `is_eligible = FALSE` and `ineligibility_reason` is set to one of: `excluded_domain`, `below_active_user_threshold`, `no_active_paid_plan`, `entered_enterprise_pipeline`.
- **Scores vs metrics:** Component scores (`pql_score`, `breadth_score`, etc.) are 0 for ineligible domains because percentile ranking is only meaningful among eligible peers. Raw metrics (`avg_wau`, `total_credits_30d`, etc.) are computed for ALL domains so usage trends remain visible.
- **Pool metadata:** `first_eligible_date` (date domain first entered the pool) and `days_in_pool` (days since first eligibility) are tracked.

---

## 5. Domain Champion Identification

> SQL: `sql/scoring/domain_champion.sql`

For each eligible domain, identifies the top 3 individuals most likely to respond to outreach and connect us to a decision-maker.

### Champion Scoring (per user, within their domain)

> Illustrative example weights — calibrate for your own funnel. Source columns below are the generic inputs; the query aliases them to stable output names (`credits_used_t30d`, `days_active_in_last_30`, `grouped_survey_role`) for downstream consumers.

| Signal | Weight | Source | Rationale |
|---|---|---|---|
| Usage-unit usage (normalized) | 20 | `users.usage_units_30d` | Heaviest user = most invested in the product |
| Activity frequency (normalized) | 20 | `users.active_days_30d` | Regular usage vs one-time spike |
| Is account admin | 20 | `accounts.admin_user_id` match | Has billing authority and org influence |
| Hit usage limits | 20 | `limit_events` (last 14d) | Personally experienced the pain point enterprise solves |
| High-leverage persona | 20 | `users.persona_role` in an illustrative list (engineering_manager, devops/sre, fullstack, backend) — **EDIT** to your ICP | More likely to influence purchasing decisions |

### Normalization
Usage and activity are min-max normalized **within each domain** (0 to 1), so the scoring compares users against their domain peers, not the global population. This prevents large domains from always surfacing the same type of user.

### Output
- Top 3 users per domain, ranked by `champion_score`
- Includes (stable output names): `user_email`, `credits_used_t30d`, `days_active_in_last_30`, `is_team_admin`, `limit_hit_count`, `grouped_survey_role`, `first_sub_plan_type`

### Design Decision: Why top 3?
Single-champion risks are high — the #1 user might be unreachable, unresponsive, or not the right persona. Surfacing 3 gives sales optionality. The scoring naturally differentiates between a power-user champion (#1) vs a decision-maker champion (admin, #2 or #3).

---

## 6. Key Tables Reference

Generic table names — point them at your warehouse via the dbt vars / `sql/` placeholder.

| Table | Used For | Domain Key |
|---|---|---|
| `companies` | Company metadata enrichment (name, industry, size, country) | `email_domain` |
| `users` | User attributes, exclusion flag, domain mapping, usage, persona | `email_domain` |
| `accounts` | Account plan, subscription, admin domain, seats | `admin_email_domain` |
| `weekly_active_users` | Weekly active user signal | join via `user_id` → `users` |
| `daily_active_users` | Daily active signal | join via `user_id` → `users` |
| `usage_events` | Usage-unit consumption per event | join via `user_id` → `users` |
| `overage_purchases` | Add-on / overage purchases | `email_domain` (native) |
| `limit_events` | Usage-limit / paywall hits | join via `user_id` → `users` |
| `upgrades` | Free-to-paid upgrade events | join via `user_id` → `users` |
| `membership_events` | Account join/invite events | join via `account_id` → `accounts` |
| `crm_deals` | CRM deals | `email_domain` (native) |

---

## 7. Global Conventions

- **Exclusion filtering:** Always `WHERE NOT is_excluded` when touching `users` (fraud/abuse/internal accounts). Paying subscribers are never excluded by this flag.
- **Week boundaries:** Weeks start on Sunday (`WEEK(SUNDAY)`). Only complete weeks are used — the current partial week is excluded via `< DATE_TRUNC(CURRENT_DATE(), WEEK(SUNDAY))`.
- **Lookback windows:**
  - Metrics: 28 days (4 complete weeks) for averages, 35 days for velocity (needs 5 weeks for 4 WoW deltas).
  - Signals: 14 days for high-recency signals (limits, overage, growth), 30 days for upgrades (which are less frequent).
- **Domain resolution hierarchy:**
  - For user-level data: `users.email_domain`
  - For account-level data: `accounts.admin_email_domain`
  - For billing data: `overage_purchases.email_domain` (native)
  - For CRM data: `crm_deals.email_domain` (native)
