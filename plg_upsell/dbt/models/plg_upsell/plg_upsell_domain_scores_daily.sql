{{
  config(
    materialized='incremental',
    unique_key=['email_domain', 'scored_date'],
    partition_by={
      "field": "scored_date",
      "data_type": "date",
      "granularity": "day"
    },
    cluster_by=['email_domain'],
    on_schema_change='append_new_columns'
  )
}}

-- plg_upsell_domain_scores_daily
-- Append-only daily snapshot of PQL scores.
--
-- ADOPTING THIS MODEL
--   1. Point it at your warehouse by setting these dbt vars (or edit the
--      placeholder defaults below):
--        plg_source_database  (default: your_project)
--        plg_source_schema    (default: your_dataset)
--   2. The queries below assume a small set of generic tables/columns. Map your
--      own schema by renaming in the FROM/SELECT clauses, or expose views with
--      these names. Lines marked "EDIT:" call out product-specific assumptions.
--
-- USAGE UNIT
--   The framework (breadth / depth / velocity / urgency) is product-neutral. It
--   operates on a generic "usage unit" — your core consumption metric (AI
--   credits, API calls, build minutes, seats, messages, ...). Output columns
--   keep legacy names like total_credits_30d for downstream compatibility; read
--   them as "usage units".
--
-- Key behavior:
--   - Once a domain qualifies, it gets a row EVERY day, even if it later drops
--     eligibility. Ineligible domains get pql_score = 0 and an
--     ineligibility_reason explaining which filter they now fail.
--   - Percentile ranks (and component scores) are computed ONLY among today's
--     eligible domains. Raw metrics are computed for ALL domains in the universe.
--
-- Grain: one row per (email_domain, scored_date)

{%- set plg = var('plg_source_database', 'your_project') ~ '.' ~ var('plg_source_schema', 'your_dataset') -%}

-- ============================================================
-- FREE EMAIL BLOCKLIST
-- ============================================================
WITH free_email_domains AS (
  SELECT domain FROM UNNEST([
    'gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com', 'aol.com',
    'icloud.com', 'mail.com', 'protonmail.com', 'proton.me', 'pm.me',
    'zoho.com', 'yandex.com', 'live.com', 'msn.com', 'me.com', 'mac.com',
    'gmx.com', 'gmx.net', 'fastmail.com', 'hey.com', 'tutanota.com',
    'qq.com', '163.com', '126.com', 'yahoo.co.uk', 'yahoo.co.in',
    'hotmail.co.uk', 'outlook.fr', 'mail.ru', 'web.de', 'comcast.net',
    'att.net', 'verizon.net', 'sbcglobal.net', 'cox.net', 'earthlink.net',
    'rocketmail.com', 'ymail.com', 'googlemail.com',
    -- Yandex regional
    'yandex.ru', 'yandex.net', 'yandex.ua', 'yandex.kz', 'yandex.by',
    -- Yahoo regional
    'yahoo.de', 'yahoo.fr', 'yahoo.es', 'yahoo.it', 'yahoo.co.jp', 'yahoo.com.au', 'yahoo.com.br',
    -- Live / Hotmail regional
    'live.com.au', 'live.co.uk', 'live.fr', 'live.de', 'live.ca',
    'hotmail.de', 'hotmail.fr', 'hotmail.it', 'hotmail.es',
    -- Privacy / relay mail services
    'passmail.net', 'simplelogin.com', 'anonaddy.com',
    -- Email forwarding services (not company domains)
    'duck.com',
    -- Additional consumer/webmail domains
    'gmx.de', 'foxmail.com', 'outlook.jp', 'protonmail.ch',
    'privaterelay.appleid.com', 'libero.it', 'ya.ru',
    -- ISP / portal email services
    'btinternet.com', 'wp.pl',
    -- Email alias / forwarding services
    'passmail.com', '8alias.com',
    -- Regional Yahoo / Hotmail variants
    'hotmail.com.br', 'yahoo.com.mx',
    -- Known academic institutions (not caught by suffix rules)
    'universite-paris-saclay.fr', 'tuhh.de'
  ]) AS domain
),

-- ============================================================
-- ELIGIBILITY FILTER COMPONENTS (checked independently)
-- ============================================================
work_email_domains AS (
  SELECT DISTINCT uf.email_domain
  FROM `{{ plg }}.users` uf
  LEFT JOIN free_email_domains fed ON uf.email_domain = fed.domain
  WHERE uf.email_domain IS NOT NULL
    AND fed.domain IS NULL
    AND NOT ENDS_WITH(uf.email_domain, '.edu')
    AND NOT ENDS_WITH(uf.email_domain, '.edu.cn')
    AND NOT ENDS_WITH(uf.email_domain, '.ac.uk')
    AND uf.email_domain != 'your-company.com'  -- EDIT: exclude your own domain
),

active_domain_users AS (
  SELECT
    uf.email_domain,
    COUNT(DISTINCT uf.user_id) AS active_users_last_30d
  FROM `{{ plg }}.users` uf
  WHERE uf.active_days_30d > 0
    AND NOT uf.is_excluded
  GROUP BY uf.email_domain
  HAVING COUNT(DISTINCT uf.user_id) >= 2
),

domains_with_build_teams AS (
  -- Path 1: account admin's email domain (primary)
  SELECT DISTINCT admin_email_domain AS email_domain
  FROM `{{ plg }}.accounts`
  -- EDIT: your paid self-serve plan names
  WHERE plan_name IN ('team', 'team_plus', 'business')
    AND subscription_status = 'active'
  UNION DISTINCT
  -- Path 2: 2+ account members share this email domain (admin mismatch fix)
  SELECT uf.email_domain
  FROM `{{ plg }}.account_members` tu
  INNER JOIN `{{ plg }}.accounts` tf ON tu.account_id = tf.account_id
  INNER JOIN `{{ plg }}.users` uf ON tu.user_id = uf.user_id
  -- EDIT: your paid self-serve plan names
  WHERE tf.plan_name IN ('team', 'team_plus', 'business')
    AND tf.subscription_status = 'active'
    AND uf.email_domain IS NOT NULL
  GROUP BY uf.email_domain
  HAVING COUNT(DISTINCT uf.user_id) >= 2
),

crm_exclude_domains AS (
  SELECT DISTINCT email_domain
  FROM `{{ plg }}.crm_deals`
  WHERE email_domain IS NOT NULL
    AND NOT is_closed_lost
),

-- ============================================================
-- TODAY'S ELIGIBLE DOMAINS (passes all filters)
-- ============================================================
today_eligible AS (
  SELECT wed.email_domain
  FROM work_email_domains wed
  INNER JOIN active_domain_users adu USING (email_domain)
  INNER JOIN domains_with_build_teams dwt USING (email_domain)
  LEFT JOIN crm_exclude_domains cex USING (email_domain)
  WHERE cex.email_domain IS NULL
),

-- ============================================================
-- "EVER ELIGIBLE" UNIVERSE
-- Domains that qualify today + all previously seen domains
-- ============================================================
ever_eligible AS (
  SELECT DISTINCT email_domain FROM today_eligible
  {% if is_incremental() %}
  UNION DISTINCT
  SELECT DISTINCT email_domain FROM {{ this }}
  {% endif %}
),

-- ============================================================
-- DOMAIN STATUS + INELIGIBILITY REASON
-- ============================================================
domain_status AS (
  SELECT
    ee.email_domain,
    te.email_domain IS NOT NULL AS is_eligible,
    CASE
      WHEN te.email_domain IS NOT NULL THEN CAST(NULL AS STRING)
      WHEN wed.email_domain IS NULL THEN 'excluded_domain'
      WHEN adu.email_domain IS NULL THEN 'below_active_user_threshold'
      WHEN dwt.email_domain IS NULL THEN 'no_active_paid_plan'
      WHEN cex.email_domain IS NOT NULL THEN 'entered_enterprise_pipeline'
      ELSE 'unknown'
    END AS ineligibility_reason
  FROM ever_eligible ee
  LEFT JOIN today_eligible te ON ee.email_domain = te.email_domain
  LEFT JOIN work_email_domains wed ON ee.email_domain = wed.email_domain
  LEFT JOIN (SELECT DISTINCT email_domain FROM active_domain_users) adu ON ee.email_domain = adu.email_domain
  LEFT JOIN domains_with_build_teams dwt ON ee.email_domain = dwt.email_domain
  LEFT JOIN crm_exclude_domains cex ON ee.email_domain = cex.email_domain
),

-- ============================================================
-- FIRMOGRAPHICS (for all domains, from companies when available)
-- ============================================================
firmographics AS (
  SELECT
    ee.email_domain,
    COALESCE(adu.active_users_last_30d, 0) AS active_users_last_30d,
    kc.company_name,
    kc.industry,
    kc.company_size,
    kc.country
  FROM ever_eligible ee
  LEFT JOIN (
    SELECT email_domain, COUNT(DISTINCT user_id) AS active_users_last_30d
    FROM `{{ plg }}.users`
    WHERE active_days_30d > 0 AND NOT is_excluded
    GROUP BY email_domain
  ) adu ON ee.email_domain = adu.email_domain
  LEFT JOIN (
    SELECT DISTINCT email_domain, company_name, industry, company_size, country
    FROM `{{ plg }}.companies`
    WHERE email_domain IS NOT NULL
  ) kc ON ee.email_domain = kc.email_domain
),

-- ============================================================
-- METRICS (computed for ALL ever-eligible domains)
-- ============================================================
-- BREADTH: average weekly active users (last 4 complete weeks)
breadth AS (
  SELECT
    email_domain,
    AVG(weekly_wau) AS avg_wau
  FROM (
    SELECT uf.email_domain, w.week_start, COUNT(DISTINCT w.user_id) AS weekly_wau
    FROM `{{ plg }}.weekly_active_users` w
    INNER JOIN `{{ plg }}.users` uf ON w.user_id = uf.user_id
    WHERE w.week_start >= DATE_SUB(CURRENT_DATE(), INTERVAL 28 DAY)
      AND w.week_start < DATE_TRUNC(CURRENT_DATE(), WEEK(SUNDAY))
      AND NOT uf.is_excluded
      AND uf.email_domain IN (SELECT email_domain FROM ever_eligible)
    GROUP BY uf.email_domain, w.week_start
  )
  GROUP BY email_domain
),

-- DEPTH: total usage units + per-user usage units (last 30d).
-- Output aliases keep the legacy "credits" names for downstream compatibility.
depth_credits AS (
  SELECT
    uf.email_domain,
    CAST(SUM(ue.usage_units) AS FLOAT64) AS total_credits_30d,
    CAST(SUM(ue.usage_units) AS FLOAT64)
      / NULLIF(COUNT(DISTINCT ue.user_id), 0) AS credits_per_user_30d
  FROM `{{ plg }}.usage_events` ue
  INNER JOIN `{{ plg }}.users` uf ON ue.user_id = uf.user_id
  WHERE ue.event_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
    AND NOT uf.is_excluded
    AND uf.email_domain IN (SELECT email_domain FROM ever_eligible)
  GROUP BY uf.email_domain
),

-- VELOCITY: WoW usage growth (avg over last 4 deltas)
weekly_credits AS (
  SELECT
    uf.email_domain,
    DATE_TRUNC(ue.event_date, WEEK(SUNDAY)) AS _week,
    CAST(SUM(ue.usage_units) AS FLOAT64) AS week_usage
  FROM `{{ plg }}.usage_events` ue
  INNER JOIN `{{ plg }}.users` uf ON ue.user_id = uf.user_id
  WHERE ue.event_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 35 DAY)
    AND ue.event_date < DATE_TRUNC(CURRENT_DATE(), WEEK(SUNDAY))
    AND NOT uf.is_excluded
    AND uf.email_domain IN (SELECT email_domain FROM ever_eligible)
  GROUP BY uf.email_domain, DATE_TRUNC(ue.event_date, WEEK(SUNDAY))
),

velocity AS (
  SELECT
    email_domain,
    AVG(SAFE_DIVIDE(week_usage - prev_usage, NULLIF(prev_usage, 0))) AS avg_wow_growth,
    COUNTIF(week_usage > prev_usage) AS weeks_growing
  FROM (
    SELECT *, LAG(week_usage) OVER (PARTITION BY email_domain ORDER BY _week) AS prev_usage
    FROM weekly_credits
  )
  WHERE prev_usage IS NOT NULL
  GROUP BY email_domain
),

sig_limits AS (
  SELECT uf.email_domain, COUNT(*) AS limit_hits, COUNT(DISTINCT pw.user_id) AS users_hitting_limits
  FROM `{{ plg }}.limit_events` pw
  INNER JOIN `{{ plg }}.users` uf ON pw.user_id = uf.user_id
  WHERE pw.event_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY)
    -- EDIT: narrow to the usage-limit / paywall events that signal upsell demand
    AND pw.feature = 'ai_feature' AND pw.entrypoint = 'agent'
    AND NOT uf.is_excluded
    AND uf.email_domain IN (SELECT email_domain FROM ever_eligible)
  GROUP BY uf.email_domain
),

sig_reloads AS (
  SELECT ac.email_domain, SUM(ac.amount) AS reload_dollars, COUNT(*) AS reload_count
  FROM `{{ plg }}.overage_purchases` ac
  WHERE ac.purchased_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 14 DAY)
    AND ac.status = 'paid'
    AND ac.email_domain IN (SELECT email_domain FROM ever_eligible)
  GROUP BY ac.email_domain
),

sig_upgrades AS (
  SELECT uf.email_domain, COUNT(DISTINCT pc.user_id) AS users_upgraded
  FROM `{{ plg }}.upgrades` pc
  INNER JOIN `{{ plg }}.users` uf ON pc.user_id = uf.user_id
  WHERE pc.did_upgrade = TRUE
    AND pc.upgraded_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
    AND NOT uf.is_excluded
    AND uf.email_domain IN (SELECT email_domain FROM ever_eligible)
  GROUP BY uf.email_domain
),

sig_domain_growth AS (
  SELECT tf.admin_email_domain AS email_domain, COUNT(DISTINCT me.user_id) AS new_domain_members
  FROM `{{ plg }}.membership_events` me
  INNER JOIN `{{ plg }}.accounts` tf ON me.account_id = tf.account_id
  WHERE me.event_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY)
    -- EDIT: your "new member joined / invited" event names
    AND me.event_name IN ('join team', 'join team via team discovery', 'invited teammates', 'send team invite email')
    AND tf.admin_email_domain IN (SELECT email_domain FROM ever_eligible)
  GROUP BY tf.admin_email_domain
),

-- ============================================================
-- SPEND SIGNALS: subscription MRR + overage purchases (last 30d)
-- Used for flat score bonuses, not percentile ranking.
-- QUALIFY avoids future-dated projected rows in the revenue table.
-- ============================================================
latest_revenue AS (
  SELECT billing_customer_id, mrr AS mrr
  FROM `{{ plg }}.account_revenue`
  WHERE is_valid = TRUE AND revenue_date <= CURRENT_DATE()
  QUALIFY ROW_NUMBER() OVER (PARTITION BY billing_customer_id ORDER BY revenue_date DESC) = 1
),

domain_spend AS (
  SELECT
    tf.admin_email_domain AS email_domain,
    SUM(COALESCE(rev.mrr, 0)) AS subscription_mrr
  FROM `{{ plg }}.accounts` tf
  LEFT JOIN latest_revenue rev ON tf.billing_customer_id = rev.billing_customer_id
  -- EDIT: your paid self-serve plan names
  WHERE tf.plan_name IN ('team', 'team_plus', 'business')
    AND tf.subscription_status = 'active'
    AND tf.admin_email_domain IN (SELECT email_domain FROM ever_eligible)
  GROUP BY tf.admin_email_domain
),

domain_reload_30d AS (
  SELECT ac.email_domain, SUM(ac.amount) AS reload_30d
  FROM `{{ plg }}.overage_purchases` ac
  WHERE ac.purchased_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
    AND ac.status = 'paid'
    AND ac.email_domain IN (SELECT email_domain FROM ever_eligible)
  GROUP BY ac.email_domain
),

-- ============================================================
-- SEAT CAP SIGNAL: account at or near its plan seat limit
-- EDIT: set the seat caps for your plans (example values shown), or drop this
-- signal entirely if your plans are not seat-capped.
-- At cap   (account_size >= cap)    : strong upgrade signal → +10pts
-- Near cap (account_size >= 80% cap): approaching limit    → +5pts
-- ============================================================
seat_cap_signal AS (
  SELECT
    tf.admin_email_domain AS email_domain,
    MAX(
      CASE
        WHEN CAST(tf.account_size AS INT64) >=
          CASE WHEN tf.plan_name IN ('team_plus','business') THEN 25 ELSE 10 END
        THEN 'at_cap'
        WHEN CAST(tf.account_size AS INT64) >=
          CASE WHEN tf.plan_name IN ('team_plus','business') THEN 20 ELSE 8 END
        THEN 'near_cap'
        ELSE 'below'
      END
    ) AS cap_status
  FROM `{{ plg }}.accounts` tf
  WHERE tf.plan_name IN ('team', 'team_plus', 'business')
    AND tf.subscription_status = 'active'
    AND tf.admin_email_domain IN (SELECT email_domain FROM ever_eligible)
  GROUP BY tf.admin_email_domain
),

-- ============================================================
-- TARGET ACCOUNT: CRM target-account flag
-- ============================================================
target_accounts AS (
  SELECT DISTINCT email_domain, TRUE AS is_target_account
  FROM `{{ plg }}.crm_accounts`
  WHERE is_target_account = TRUE
    AND email_domain IS NOT NULL
    AND email_domain != ''
),

-- ============================================================
-- ASSEMBLE METRICS FOR ALL DOMAINS
-- ============================================================
all_metrics AS (
  SELECT
    ds.email_domain,
    ds.is_eligible,
    ds.ineligibility_reason,
    fg.active_users_last_30d,
    fg.company_name,
    fg.industry,
    fg.company_size,
    fg.country,
    COALESCE(b.avg_wau, 0) AS avg_wau,
    COALESCE(dc.total_credits_30d, 0) AS total_credits_30d,
    COALESCE(dc.credits_per_user_30d, 0) AS credits_per_user_30d,
    COALESCE(v.avg_wow_growth, 0) AS avg_wow_growth,
    COALESCE(v.weeks_growing, 0) AS weeks_growing,
    COALESCE(sl.limit_hits, 0) AS limit_hits,
    COALESCE(sl.users_hitting_limits, 0) AS users_hitting_limits,
    COALESCE(sr.reload_dollars, 0) AS reload_dollars,
    COALESCE(sr.reload_count, 0) AS reload_count,
    COALESCE(su.users_upgraded, 0) AS users_upgraded,
    COALESCE(stg.new_domain_members, 0) AS new_domain_members,
    -- Spend signals
    COALESCE(dsp.subscription_mrr, 0) AS subscription_mrr,
    COALESCE(dr.reload_30d, 0) AS reload_30d,
    COALESCE(dsp.subscription_mrr, 0) + COALESCE(dr.reload_30d, 0) AS total_monthly_spend,
    -- Target account flag
    COALESCE(ta.is_target_account, FALSE) AS is_target_account,
    -- Seat cap status
    COALESCE(sc.cap_status, 'below') AS cap_status
  FROM domain_status ds
  LEFT JOIN firmographics fg USING (email_domain)
  LEFT JOIN breadth b USING (email_domain)
  LEFT JOIN depth_credits dc USING (email_domain)
  LEFT JOIN velocity v USING (email_domain)
  LEFT JOIN sig_limits sl USING (email_domain)
  LEFT JOIN sig_reloads sr USING (email_domain)
  LEFT JOIN sig_upgrades su USING (email_domain)
  LEFT JOIN sig_domain_growth stg USING (email_domain)
  LEFT JOIN domain_spend dsp USING (email_domain)
  LEFT JOIN domain_reload_30d dr USING (email_domain)
  LEFT JOIN target_accounts ta USING (email_domain)
  LEFT JOIN seat_cap_signal sc USING (email_domain)
),

-- ============================================================
-- PERCENTILE RANKS (only among today's eligible domains)
-- ============================================================
eligible_ranked AS (
  SELECT
    email_domain,
    PERCENT_RANK() OVER (ORDER BY avg_wau) AS pctl_wau,
    PERCENT_RANK() OVER (ORDER BY total_credits_30d) AS pctl_total_credits,
    PERCENT_RANK() OVER (ORDER BY credits_per_user_30d) AS pctl_credits_per_user,
    PERCENT_RANK() OVER (ORDER BY avg_wow_growth) AS pctl_velocity,
    PERCENT_RANK() OVER (ORDER BY limit_hits) AS pctl_limits,
    PERCENT_RANK() OVER (ORDER BY reload_dollars) AS pctl_reloads,
    PERCENT_RANK() OVER (ORDER BY users_upgraded) AS pctl_upgrades,
    PERCENT_RANK() OVER (ORDER BY new_domain_members) AS pctl_domain_growth
  FROM all_metrics
  WHERE is_eligible = TRUE
),

-- ============================================================
-- COMPUTE SCORES (eligible get real scores, ineligible get 0)
-- ============================================================
eligible_scores AS (
  SELECT
    er.email_domain,
    ROUND(pctl_wau * 100, 1) AS breadth_score,
    ROUND((pctl_total_credits * 0.6 + pctl_credits_per_user * 0.4) * 100, 1) AS depth_score,
    ROUND(pctl_velocity * 100, 1) AS velocity_score,
    -- Illustrative example weights — calibrate for your own funnel.
    ROUND((pctl_limits * 10 + pctl_reloads * 10 + pctl_upgrades * 10 + pctl_domain_growth * 10) / 40 * 100, 1) AS urgency_score,
    -- Base 100-pt percentile score + flat spend/target account bonuses.
    -- Illustrative example weights — calibrate for your own funnel.
    -- Score can exceed 100 for high-spend or target accounts.
    ROUND(
      pctl_wau * 20
      + (pctl_total_credits * 0.6 + pctl_credits_per_user * 0.4) * 20
      + pctl_velocity * 20
      + pctl_limits * 10
      + pctl_reloads * 10
      + pctl_upgrades * 10
      + pctl_domain_growth * 10
      -- Spend tier bonus (tiered, no stacking). Illustrative example thresholds:
      --   >= $833/mo (~$10K ARR): +10pts
      --   >= $500/mo            : +5pts
      + CASE
          WHEN am.total_monthly_spend >= 833 THEN 10
          WHEN am.total_monthly_spend >= 500 THEN 5
          ELSE 0
        END
      -- Target account bonus
      + CASE WHEN am.is_target_account THEN 15 ELSE 0 END
      -- Seat cap bonus
      + CASE WHEN am.cap_status = 'at_cap'   THEN 10 ELSE 0 END
      + CASE WHEN am.cap_status = 'near_cap' THEN 5  ELSE 0 END
    , 1) AS pql_score
  FROM eligible_ranked er
  INNER JOIN all_metrics am USING (email_domain)
),

-- ============================================================
-- FIRST ELIGIBLE DATE (from history)
-- ============================================================
{% if is_incremental() %}
first_seen AS (
  SELECT
    email_domain,
    MIN(scored_date) AS first_eligible_date
  FROM {{ this }}
  WHERE is_eligible
  GROUP BY email_domain
),
{% endif %}

-- ============================================================
-- FINAL OUTPUT
-- ============================================================
final AS (
  SELECT
    CURRENT_DATE() AS scored_date,
    am.email_domain,
    am.is_eligible,
    am.ineligibility_reason,

    -- Scores (0 for ineligible)
    COALESCE(es.pql_score, 0) AS pql_score,
    COALESCE(es.breadth_score, 0) AS breadth_score,
    COALESCE(es.depth_score, 0) AS depth_score,
    COALESCE(es.velocity_score, 0) AS velocity_score,
    COALESCE(es.urgency_score, 0) AS urgency_score,

    -- Raw metrics (populated for ALL domains, regardless of eligibility)
    am.avg_wau,
    ROUND(am.total_credits_30d, 0) AS total_credits_30d,
    ROUND(am.credits_per_user_30d, 0) AS credits_per_user_30d,
    ROUND(am.avg_wow_growth * 100, 1) AS wow_growth_pct,
    am.weeks_growing,
    am.limit_hits,
    am.users_hitting_limits,
    am.reload_dollars,
    am.reload_count,
    am.users_upgraded,
    am.new_domain_members,
    -- Spend and target account signals
    ROUND(am.subscription_mrr, 0) AS subscription_mrr,
    ROUND(am.reload_30d, 0) AS reload_30d,
    ROUND(am.total_monthly_spend, 0) AS total_monthly_spend,
    am.is_target_account,
    am.cap_status,

    -- Firmographics
    am.company_name,
    am.industry,
    am.company_size,
    am.country,
    am.active_users_last_30d,

    -- Pool metadata
    {% if is_incremental() %}
    COALESCE(fs.first_eligible_date, CURRENT_DATE()) AS first_eligible_date,
    DATE_DIFF(CURRENT_DATE(), COALESCE(fs.first_eligible_date, CURRENT_DATE()), DAY) AS days_in_pool
    {% else %}
    CURRENT_DATE() AS first_eligible_date,
    0 AS days_in_pool
    {% endif %}

  FROM all_metrics am
  LEFT JOIN eligible_scores es ON am.email_domain = es.email_domain
  {% if is_incremental() %}
  LEFT JOIN first_seen fs ON am.email_domain = fs.email_domain
  {% endif %}
)

SELECT * FROM final
