-- domain_champion.sql
-- For each eligible domain, identifies the best individual to reach out to.
--
-- REFERENCE QUERY — replace `your_project.your_dataset` with your warehouse
-- location and map the table/column names to your schema (see
-- reference/DATA_DEFINITIONS.md). Lines marked "EDIT:" are product-specific
-- assumptions to adjust. "Usage units" = your core consumption metric.
--
-- Champion scoring per user (within their domain). Illustrative example
-- weights (20 each, total 100) — calibrate for your own funnel:
--   1. Usage units (usage_units_30d)      : 20  — proves product value
--   2. Activity frequency (active_days_30d): 20  — regular user, not a one-time spike
--   3. Is account admin                    : 20  — has billing authority / org influence
--   4. Has hit usage limits                : 20  — personally felt the pain point
--   5. Has a high-leverage persona/role    : 20  — we can tailor the outreach
--
-- Output column names (credits_used_t30d, days_active_in_last_30,
-- grouped_survey_role, ...) are kept stable for downstream consumers.
-- Returns the top 3 candidates per domain, ranked by champion_score.

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
  SELECT wed.email_domain
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
    SELECT email_domain
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
  WHERE cex.email_domain IS NULL
),

-- Users who hit usage limits in the last 14 days
user_limit_hits AS (
  SELECT
    pw.user_id,
    COUNT(*) AS limit_hit_count
  FROM `your_project.your_dataset.limit_events` pw
  WHERE pw.event_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY)
    -- EDIT: narrow to the usage-limit / paywall events that signal upsell demand
    AND pw.feature = 'ai_feature'
    AND pw.entrypoint = 'agent'
  GROUP BY pw.user_id
),

-- Account admins (one user can admin multiple accounts; we just need the flag)
account_admins AS (
  SELECT DISTINCT admin_user_id AS user_id
  FROM `your_project.your_dataset.accounts`
  -- EDIT: your paid self-serve plan names
  WHERE plan_name IN ('team', 'team_plus')
    AND subscription_status = 'active'
    AND admin_email_domain IN (SELECT email_domain FROM eligible)
),

-- All active users in eligible domains with their attributes (canonical columns)
candidates AS (
  SELECT
    uf.user_id,
    uf.user_email,
    uf.email_domain,
    uf.usage_units_30d,
    uf.active_days_30d,
    uf.persona_role,
    uf.is_on_paid_account,
    uf.first_paid_plan,
    CASE WHEN ta.user_id IS NOT NULL THEN TRUE ELSE FALSE END AS is_team_admin,
    COALESCE(ulh.limit_hit_count, 0) AS limit_hit_count
  FROM `your_project.your_dataset.users` uf
  LEFT JOIN account_admins ta ON uf.user_id = ta.user_id
  LEFT JOIN user_limit_hits ulh ON uf.user_id = ulh.user_id
  WHERE uf.email_domain IN (SELECT email_domain FROM eligible)
    AND uf.active_days_30d > 0
    AND NOT uf.is_excluded
),

-- Min-max normalize usage and activity WITHIN each domain
scored AS (
  SELECT
    *,
    SAFE_DIVIDE(
      usage_units_30d - MIN(usage_units_30d) OVER (PARTITION BY email_domain),
      NULLIF(MAX(usage_units_30d) OVER (PARTITION BY email_domain) - MIN(usage_units_30d) OVER (PARTITION BY email_domain), 0)
    ) AS norm_usage,
    SAFE_DIVIDE(
      active_days_30d - MIN(active_days_30d) OVER (PARTITION BY email_domain),
      NULLIF(MAX(active_days_30d) OVER (PARTITION BY email_domain) - MIN(active_days_30d) OVER (PARTITION BY email_domain), 0)
    ) AS norm_activity
  FROM candidates
),

champion_scored AS (
  SELECT
    *,
    -- Weighted champion score (0-100). Illustrative example weights (20 each).
    ROUND(
      COALESCE(norm_usage, 0) * 20            -- power usage
      + COALESCE(norm_activity, 0) * 20       -- consistent engagement
      + (CASE WHEN is_team_admin THEN 1 ELSE 0 END) * 20   -- org authority
      + (CASE WHEN limit_hit_count > 0 THEN 1 ELSE 0 END) * 20  -- felt the pain
      -- EDIT: your high-leverage persona/role values
      + (CASE WHEN persona_role IN ('engineering_manager', 'devops/sre', 'fullstack', 'backend') THEN 1 ELSE 0 END) * 20
    , 1) AS champion_score
  FROM scored
),

ranked AS (
  SELECT
    *,
    ROW_NUMBER() OVER (PARTITION BY email_domain ORDER BY champion_score DESC, usage_units_30d DESC) AS rank_in_domain
  FROM champion_scored
)

SELECT
  email_domain,
  user_email,
  champion_score,
  rank_in_domain,
  -- Output aliases kept stable for downstream consumers.
  ROUND(CAST(usage_units_30d AS FLOAT64), 0) AS credits_used_t30d,
  active_days_30d AS days_active_in_last_30,
  is_team_admin,
  limit_hit_count,
  persona_role AS grouped_survey_role,
  is_on_paid_account AS is_on_team_with_active_subscription,
  first_paid_plan AS first_sub_plan_type
FROM ranked
WHERE rank_in_domain <= 3
ORDER BY email_domain, rank_in_domain;
