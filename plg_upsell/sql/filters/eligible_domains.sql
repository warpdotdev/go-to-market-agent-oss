-- eligible_domains.sql
-- Base domain filter for the PLG upsell pipeline. A domain must satisfy ALL
-- inclusion criteria and NONE of the exclusion criteria:
--   1. Work email domain (not free/consumer, .edu, or your own company domain)
--   2. 2+ active users in last 30 days (not excluded)
--   3. Has 1+ account on a paid self-serve plan
--   AND NOT already a non-lost CRM deal (current customer or open pipeline).
-- Closed-lost deals are intentionally kept as re-engagement candidates.
--
-- REFERENCE QUERY — replace `your_project.your_dataset` with your warehouse
-- location and map the table/column names to your schema (see
-- reference/DATA_DEFINITIONS.md). Lines marked "EDIT:" are product-specific
-- assumptions to adjust.

WITH free_email_domains AS (
  -- Common free/consumer email providers to exclude
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

work_email_domains AS (
  -- Filter 1: work email domain
  SELECT DISTINCT uf.email_domain
  FROM `your_project.your_dataset.users` uf
  LEFT JOIN free_email_domains fed ON uf.email_domain = fed.domain
  WHERE uf.email_domain IS NOT NULL
    AND fed.domain IS NULL
    AND NOT ENDS_WITH(uf.email_domain, '.edu')
    AND NOT ENDS_WITH(uf.email_domain, '.edu.cn')
    AND NOT ENDS_WITH(uf.email_domain, '.ac.uk')
    AND uf.email_domain != 'your-company.com'  -- EDIT: exclude your own domain
),

active_domain_users AS (
  -- Filter 2: 2+ non-excluded users with at least 1 active day in last 30
  SELECT
    uf.email_domain,
    COUNT(DISTINCT uf.user_id) AS active_users_last_30d
  FROM `your_project.your_dataset.users` uf
  WHERE uf.active_days_30d > 0
    AND NOT uf.is_excluded
  GROUP BY uf.email_domain
  HAVING COUNT(DISTINCT uf.user_id) >= 2
),

domains_with_paid_accounts AS (
  -- Filter 3: domain has 1+ account on a paid self-serve plan
  -- Path 1 (primary): account admin's email domain matches the user domain
  SELECT DISTINCT admin_email_domain AS email_domain
  FROM `your_project.your_dataset.accounts`
  -- EDIT: your paid self-serve plan names
  WHERE plan_name IN ('team', 'team_plus', 'business')
    AND subscription_status = 'active'
  UNION DISTINCT
  -- Path 2 (mismatch fix): 2+ users at this domain are members of a paid account
  SELECT uf.email_domain
  FROM `your_project.your_dataset.account_members` tu
  INNER JOIN `your_project.your_dataset.accounts` tf ON tu.account_id = tf.account_id
  INNER JOIN `your_project.your_dataset.users` uf ON tu.user_id = uf.user_id
  -- EDIT: your paid self-serve plan names
  WHERE tf.plan_name IN ('team', 'team_plus', 'business')
    AND tf.subscription_status = 'active'
    AND uf.email_domain IS NOT NULL
  GROUP BY uf.email_domain
  HAVING COUNT(DISTINCT uf.user_id) >= 2
),

crm_exclude_domains AS (
  -- Exclusion: domains with any non-lost CRM deal (closed-won or open pipeline).
  SELECT DISTINCT email_domain
  FROM `your_project.your_dataset.crm_deals`
  WHERE email_domain IS NOT NULL
    AND NOT is_closed_lost
)

SELECT
  wed.email_domain,
  kc.company_name,
  kc.industry,
  kc.company_size,
  kc.country,
  adu.active_users_last_30d
FROM work_email_domains wed
INNER JOIN active_domain_users adu USING (email_domain)
INNER JOIN domains_with_paid_accounts dwp USING (email_domain)
LEFT JOIN crm_exclude_domains cex USING (email_domain)
LEFT JOIN (
  -- Enrich with company metadata when available (not a filter)
  SELECT DISTINCT email_domain, company_name, industry, company_size, country
  FROM `your_project.your_dataset.companies`
  WHERE email_domain IS NOT NULL
) kc USING (email_domain)
WHERE cex.email_domain IS NULL  -- exclude domains already in the sales pipeline
ORDER BY adu.active_users_last_30d DESC;
