-- pql_score.sql
-- Product Qualified Lead score per eligible domain.
-- Combines breadth, depth, velocity, and time-based urgency signals into a 0-100 composite.
--
-- REFERENCE QUERY — replace `your_project.your_dataset` with your warehouse
-- location and map the table/column names to your schema (see
-- reference/DATA_DEFINITIONS.md). Lines marked "EDIT:" are product-specific
-- assumptions to adjust. "Usage units" = your core consumption metric.
--
-- Scoring approach:
--   1. Compute raw metrics per domain
--   2. Percentile-rank each metric across the eligible population (0.0 to 1.0)
--   3. Apply category weights (urgency signals weighted heaviest)
--   4. Sum to composite score, scale to 0-100
--
-- Weight allocation (total = 100). Illustrative example weights — calibrate for your own funnel.
--   Breadth  (WAU)                          : 20
--   Depth    (usage units + per-user usage) : 20
--   Velocity (WoW growth)                   : 20
--   Urgency  (time-based signals)           : 40
--
-- Output column names (total_credits_30d, credits_per_user_30d, ...) are kept
-- stable for downstream consumers; read "credits" as "usage units".

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
    'yandex.ru', 'yandex.net', 'yandex.ua', 'yandex.kz', 'yandex.by',
    'yahoo.de', 'yahoo.fr', 'yahoo.es', 'yahoo.it', 'yahoo.co.jp', 'yahoo.com.au', 'yahoo.com.br',
    'live.com.au', 'live.co.uk', 'live.fr', 'live.de', 'live.ca',
    'hotmail.de', 'hotmail.fr', 'hotmail.it', 'hotmail.es',
    'passmail.net', 'simplelogin.com', 'anonaddy.com', 'duck.com',
    'gmx.de', 'foxmail.com', 'outlook.jp', 'protonmail.ch',
    'privaterelay.appleid.com', 'libero.it', 'ya.ru',
    'btinternet.com', 'wp.pl', 'passmail.com', '8alias.com',
    'hotmail.com.br', 'yahoo.com.mx',
    'universite-paris-saclay.fr', 'tuhh.de'
  ]) AS domain
),

eligible AS (
  SELECT wed.email_domain, kc.company_name, kc.industry, kc.company_size, kc.country, adu.active_users_last_30d
  FROM (
    SELECT DISTINCT uf.email_domain
    FROM `your_project.your_dataset.users` uf
    LEFT JOIN free_email_domains fed ON uf.email_domain = fed.domain
    WHERE uf.email_domain IS NOT NULL
      AND fed.domain IS NULL
      AND NOT ENDS_WITH(uf.email_domain, '.edu')
      AND NOT ENDS_WITH(uf.email_domain, '.edu.cn')
      AND NOT ENDS_WITH(uf.email_domain, '.ac.uk')
      AND uf.email_domain != 'your-company.com'  -- EDIT: exclude your own domain
  ) wed
  INNER JOIN (
    SELECT email_domain, COUNT(DISTINCT user_id) AS active_users_last_30d
    FROM `your_project.your_dataset.users`
    WHERE active_days_30d > 0 AND NOT is_excluded
    GROUP BY email_domain HAVING COUNT(DISTINCT user_id) >= 2
  ) adu USING (email_domain)
  INNER JOIN (
    -- EDIT: your paid self-serve plan names (both branches)
    SELECT DISTINCT admin_email_domain AS email_domain
    FROM `your_project.your_dataset.accounts`
    WHERE plan_name IN ('team', 'team_plus', 'business') AND subscription_status = 'active'
    UNION DISTINCT
    SELECT uf.email_domain
    FROM `your_project.your_dataset.account_members` tu
    INNER JOIN `your_project.your_dataset.accounts` tf ON tu.account_id = tf.account_id
    INNER JOIN `your_project.your_dataset.users` uf ON tu.user_id = uf.user_id
    WHERE tf.plan_name IN ('team', 'team_plus', 'business')
      AND tf.subscription_status = 'active' AND uf.email_domain IS NOT NULL
    GROUP BY uf.email_domain HAVING COUNT(DISTINCT uf.user_id) >= 2
  ) dwp USING (email_domain)
  LEFT JOIN (
    SELECT DISTINCT email_domain
    FROM `your_project.your_dataset.crm_deals`
    WHERE email_domain IS NOT NULL AND NOT is_closed_lost
  ) cex USING (email_domain)
  LEFT JOIN (
    SELECT DISTINCT email_domain, company_name, industry, company_size, country
    FROM `your_project.your_dataset.companies`
    WHERE email_domain IS NOT NULL
  ) kc USING (email_domain)
  WHERE cex.email_domain IS NULL
),

-- ============================================================
-- BREADTH: Average WAU (last 4 complete weeks)
-- ============================================================
breadth AS (
  SELECT
    email_domain,
    AVG(weekly_wau) AS avg_wau
  FROM (
    SELECT uf.email_domain, w.week_start, COUNT(DISTINCT w.user_id) AS weekly_wau
    FROM `your_project.your_dataset.weekly_active_users` w
    INNER JOIN `your_project.your_dataset.users` uf ON w.user_id = uf.user_id
    WHERE w.week_start >= DATE_SUB(CURRENT_DATE(), INTERVAL 28 DAY)
      AND w.week_start < DATE_TRUNC(CURRENT_DATE(), WEEK(SUNDAY))
      AND NOT uf.is_excluded
      AND uf.email_domain IN (SELECT email_domain FROM eligible)
    GROUP BY uf.email_domain, w.week_start
  )
  GROUP BY email_domain
),

-- ============================================================
-- DEPTH: Total usage units + per-user usage units/week
-- ============================================================
depth_credits AS (
  SELECT
    uf.email_domain,
    CAST(SUM(ue.usage_units) AS FLOAT64) AS total_credits_30d,
    CAST(SUM(ue.usage_units) AS FLOAT64) / NULLIF(COUNT(DISTINCT ue.user_id), 0) AS credits_per_user_30d
  FROM `your_project.your_dataset.usage_events` ue
  INNER JOIN `your_project.your_dataset.users` uf ON ue.user_id = uf.user_id
  WHERE ue.event_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
    AND NOT uf.is_excluded
    AND uf.email_domain IN (SELECT email_domain FROM eligible)
  GROUP BY uf.email_domain
),

-- ============================================================
-- VELOCITY: WoW usage growth (avg over last 4 deltas)
-- ============================================================
weekly_credits AS (
  SELECT
    uf.email_domain,
    DATE_TRUNC(ue.event_date, WEEK(SUNDAY)) AS _week,
    CAST(SUM(ue.usage_units) AS FLOAT64) AS week_usage
  FROM `your_project.your_dataset.usage_events` ue
  INNER JOIN `your_project.your_dataset.users` uf ON ue.user_id = uf.user_id
  WHERE ue.event_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 35 DAY)
    AND ue.event_date < DATE_TRUNC(CURRENT_DATE(), WEEK(SUNDAY))
    AND NOT uf.is_excluded
    AND uf.email_domain IN (SELECT email_domain FROM eligible)
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

-- ============================================================
-- SPEND SIGNALS: subscription MRR + overage purchases (last 30d)
-- ============================================================
latest_revenue AS (
  SELECT billing_customer_id, mrr AS mrr
  FROM `your_project.your_dataset.account_revenue`
  WHERE is_valid = TRUE AND revenue_date <= CURRENT_DATE()
  QUALIFY ROW_NUMBER() OVER (PARTITION BY billing_customer_id ORDER BY revenue_date DESC) = 1
),

domain_spend AS (
  SELECT
    tf.admin_email_domain AS email_domain,
    SUM(COALESCE(rev.mrr, 0)) AS subscription_mrr
  FROM `your_project.your_dataset.accounts` tf
  LEFT JOIN latest_revenue rev ON tf.billing_customer_id = rev.billing_customer_id
  -- EDIT: your paid self-serve plan names
  WHERE tf.plan_name IN ('team', 'team_plus', 'business')
    AND tf.subscription_status = 'active'
    AND tf.admin_email_domain IN (SELECT email_domain FROM eligible)
  GROUP BY tf.admin_email_domain
),

domain_reload_30d AS (
  SELECT ac.email_domain, SUM(ac.amount) AS reload_30d
  FROM `your_project.your_dataset.overage_purchases` ac
  WHERE ac.purchased_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
    AND ac.status = 'paid'
    AND ac.email_domain IN (SELECT email_domain FROM eligible)
  GROUP BY ac.email_domain
),

-- ============================================================
-- SEAT CAP SIGNAL: account at or near plan seat limit
-- EDIT: set the seat caps for your plans (example values shown), or drop this
-- signal if your plans are not seat-capped.
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
  FROM `your_project.your_dataset.accounts` tf
  WHERE tf.plan_name IN ('team', 'team_plus', 'business')
    AND tf.subscription_status = 'active'
    AND tf.admin_email_domain IN (SELECT email_domain FROM eligible)
  GROUP BY tf.admin_email_domain
),

-- ============================================================
-- TARGET ACCOUNT: CRM target-account flag
-- ============================================================
target_accounts AS (
  SELECT DISTINCT email_domain, TRUE AS is_target_account
  FROM `your_project.your_dataset.crm_accounts`
  WHERE is_target_account = TRUE
    AND email_domain IS NOT NULL
    AND email_domain != ''
),

-- ============================================================
-- URGENCY SIGNALS
-- ============================================================
sig_limits AS (
  SELECT uf.email_domain, COUNT(*) AS limit_hits, COUNT(DISTINCT pw.user_id) AS users_hitting
  FROM `your_project.your_dataset.limit_events` pw
  INNER JOIN `your_project.your_dataset.users` uf ON pw.user_id = uf.user_id
  WHERE pw.event_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY)
    -- EDIT: narrow to the usage-limit / paywall events that signal upsell demand
    AND pw.feature = 'ai_feature' AND pw.entrypoint = 'agent'
    AND NOT uf.is_excluded
    AND uf.email_domain IN (SELECT email_domain FROM eligible)
  GROUP BY uf.email_domain
),

sig_reloads AS (
  SELECT ac.email_domain, SUM(ac.amount) AS reload_dollars, COUNT(*) AS reload_count
  FROM `your_project.your_dataset.overage_purchases` ac
  WHERE ac.purchased_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 14 DAY)
    AND ac.status = 'paid'
    AND ac.email_domain IN (SELECT email_domain FROM eligible)
  GROUP BY ac.email_domain
),

sig_upgrades AS (
  SELECT uf.email_domain, COUNT(DISTINCT pc.user_id) AS users_upgraded
  FROM `your_project.your_dataset.upgrades` pc
  INNER JOIN `your_project.your_dataset.users` uf ON pc.user_id = uf.user_id
  WHERE pc.did_upgrade = TRUE
    AND pc.upgraded_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
    AND NOT uf.is_excluded
    AND uf.email_domain IN (SELECT email_domain FROM eligible)
  GROUP BY uf.email_domain
),

sig_domain_growth AS (
  SELECT tf.admin_email_domain AS email_domain, COUNT(DISTINCT me.user_id) AS new_domain_members
  FROM `your_project.your_dataset.membership_events` me
  INNER JOIN `your_project.your_dataset.accounts` tf ON me.account_id = tf.account_id
  WHERE me.event_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY)
    -- EDIT: your "new member joined / invited" event names
    AND me.event_name IN ('join team', 'join team via team discovery', 'invited teammates', 'send team invite email')
    AND tf.admin_email_domain IN (SELECT email_domain FROM eligible)
  GROUP BY tf.admin_email_domain
),

-- ============================================================
-- ASSEMBLE + PERCENTILE RANK
-- ============================================================
assembled AS (
  SELECT
    e.*,
    COALESCE(b.avg_wau, 0) AS avg_wau,
    COALESCE(dc.total_credits_30d, 0) AS total_credits_30d,
    COALESCE(dc.credits_per_user_30d, 0) AS credits_per_user_30d,
    COALESCE(v.avg_wow_growth, 0) AS avg_wow_growth,
    COALESCE(v.weeks_growing, 0) AS weeks_growing,
    COALESCE(sl.limit_hits, 0) AS limit_hits,
    COALESCE(sl.users_hitting, 0) AS users_hitting_limits,
    COALESCE(sr.reload_dollars, 0) AS reload_dollars,
    COALESCE(sr.reload_count, 0) AS reload_count,
    COALESCE(su.users_upgraded, 0) AS users_upgraded,
    COALESCE(stg.new_domain_members, 0) AS new_domain_members,
    COALESCE(ds.subscription_mrr, 0) AS subscription_mrr,
    COALESCE(dr.reload_30d, 0) AS reload_30d,
    COALESCE(ds.subscription_mrr, 0) + COALESCE(dr.reload_30d, 0) AS total_monthly_spend,
    COALESCE(ta.is_target_account, FALSE) AS is_target_account,
    COALESCE(sc.cap_status, 'below') AS cap_status
  FROM eligible e
  LEFT JOIN breadth b USING (email_domain)
  LEFT JOIN depth_credits dc USING (email_domain)
  LEFT JOIN velocity v USING (email_domain)
  LEFT JOIN sig_limits sl USING (email_domain)
  LEFT JOIN sig_reloads sr USING (email_domain)
  LEFT JOIN sig_upgrades su USING (email_domain)
  LEFT JOIN sig_domain_growth stg USING (email_domain)
  LEFT JOIN domain_spend ds USING (email_domain)
  LEFT JOIN domain_reload_30d dr USING (email_domain)
  LEFT JOIN target_accounts ta USING (email_domain)
  LEFT JOIN seat_cap_signal sc USING (email_domain)
),

ranked AS (
  SELECT
    *,
    PERCENT_RANK() OVER (ORDER BY avg_wau) AS pctl_wau,
    PERCENT_RANK() OVER (ORDER BY total_credits_30d) AS pctl_total_credits,
    PERCENT_RANK() OVER (ORDER BY credits_per_user_30d) AS pctl_credits_per_user,
    PERCENT_RANK() OVER (ORDER BY avg_wow_growth) AS pctl_velocity,
    PERCENT_RANK() OVER (ORDER BY limit_hits) AS pctl_limits,
    PERCENT_RANK() OVER (ORDER BY reload_dollars) AS pctl_reloads,
    PERCENT_RANK() OVER (ORDER BY users_upgraded) AS pctl_upgrades,
    PERCENT_RANK() OVER (ORDER BY new_domain_members) AS pctl_domain_growth
  FROM assembled
)

SELECT
  email_domain,
  company_name,
  industry,
  company_size,
  country,
  active_users_last_30d,

  -- Component scores (each 0-100, weighted)
  ROUND(pctl_wau * 100, 1) AS breadth_score,
  ROUND((pctl_total_credits * 0.6 + pctl_credits_per_user * 0.4) * 100, 1) AS depth_score,
  ROUND(pctl_velocity * 100, 1) AS velocity_score,
  ROUND((pctl_limits * 10 + pctl_reloads * 10 + pctl_upgrades * 10 + pctl_domain_growth * 10) / 40 * 100, 1) AS urgency_score,

  -- Composite PQL score. Illustrative example weights — calibrate for your own funnel.
  -- Base: 100pts percentile-ranked across eligible population, plus flat bonuses.
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
        WHEN total_monthly_spend >= 833 THEN 10
        WHEN total_monthly_spend >= 500 THEN 5
        ELSE 0
      END
    + CASE WHEN is_target_account THEN 15 ELSE 0 END
    + CASE WHEN cap_status = 'at_cap'   THEN 10 ELSE 0 END
    + CASE WHEN cap_status = 'near_cap' THEN 5  ELSE 0 END
  , 1) AS pql_score,

  -- Raw signal values for context
  avg_wau,
  ROUND(total_credits_30d, 0) AS total_credits_30d,
  ROUND(credits_per_user_30d, 0) AS credits_per_user_30d,
  ROUND(avg_wow_growth * 100, 1) AS wow_growth_pct,
  weeks_growing,
  limit_hits,
  users_hitting_limits,
  reload_dollars,
  users_upgraded,
  new_domain_members,
  ROUND(subscription_mrr, 0) AS subscription_mrr,
  ROUND(reload_30d, 0) AS reload_30d,
  ROUND(total_monthly_spend, 0) AS total_monthly_spend,
  is_target_account,
  cap_status

FROM ranked
ORDER BY pql_score DESC;
