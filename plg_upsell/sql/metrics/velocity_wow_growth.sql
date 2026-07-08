-- velocity_wow_growth.sql
-- Velocity metrics: week-over-week growth rates per domain.
-- Computes WoW change in WAU and usage units over the last 4 complete weeks.
-- Flags domains with sustained growth (multiple consecutive growth weeks).
--
-- REFERENCE QUERY — replace `your_project.your_dataset` with your warehouse
-- location and map the table/column names to your schema (see
-- reference/DATA_DEFINITIONS.md).

WITH weekly_domain_stats AS (
  SELECT
    uf.email_domain,
    DATE_TRUNC(ue.event_date, WEEK(SUNDAY)) AS _week,
    CAST(SUM(ue.usage_units) AS FLOAT64) AS total_usage,
    COUNT(DISTINCT ue.user_id) AS active_users
  FROM `your_project.your_dataset.usage_events` ue
  INNER JOIN `your_project.your_dataset.users` uf
    ON ue.user_id = uf.user_id
  WHERE ue.event_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 35 DAY)  -- 5 weeks for 4 WoW deltas
    AND ue.event_date < DATE_TRUNC(CURRENT_DATE(), WEEK(SUNDAY))
    AND NOT uf.is_excluded
  GROUP BY uf.email_domain, DATE_TRUNC(ue.event_date, WEEK(SUNDAY))
),

weekly_wau AS (
  SELECT
    uf.email_domain,
    w.week_start AS _week,
    COUNT(DISTINCT w.user_id) AS wau
  FROM `your_project.your_dataset.weekly_active_users` w
  INNER JOIN `your_project.your_dataset.users` uf
    ON w.user_id = uf.user_id
  WHERE w.week_start >= DATE_SUB(CURRENT_DATE(), INTERVAL 35 DAY)
    AND w.week_start < DATE_TRUNC(CURRENT_DATE(), WEEK(SUNDAY))
    AND NOT uf.is_excluded
  GROUP BY uf.email_domain, w.week_start
),

combined AS (
  SELECT
    COALESCE(s.email_domain, w.email_domain) AS email_domain,
    COALESCE(s._week, w._week) AS _week,
    COALESCE(s.total_usage, 0) AS total_usage,
    COALESCE(s.active_users, 0) AS active_users,
    COALESCE(w.wau, 0) AS wau
  FROM weekly_domain_stats s
  FULL OUTER JOIN weekly_wau w
    ON s.email_domain = w.email_domain AND s._week = w._week
),

with_lag AS (
  SELECT
    *,
    LAG(total_usage) OVER (PARTITION BY email_domain ORDER BY _week) AS prev_usage,
    LAG(wau) OVER (PARTITION BY email_domain ORDER BY _week) AS prev_wau,
    LAG(active_users) OVER (PARTITION BY email_domain ORDER BY _week) AS prev_users
  FROM combined
),

wow_changes AS (
  SELECT
    email_domain,
    _week,
    -- Usage growth
    SAFE_DIVIDE(total_usage - prev_usage, NULLIF(prev_usage, 0)) AS usage_wow_pct,
    -- WAU growth
    SAFE_DIVIDE(wau - prev_wau, NULLIF(CAST(prev_wau AS FLOAT64), 0)) AS wau_wow_pct,
    -- Active user growth
    SAFE_DIVIDE(active_users - prev_users, NULLIF(CAST(prev_users AS FLOAT64), 0)) AS users_wow_pct,
    total_usage,
    wau,
    active_users
  FROM with_lag
  WHERE prev_usage IS NOT NULL  -- exclude first week (no prior)
)

SELECT
  email_domain,
  -- Average WoW growth rates
  AVG(usage_wow_pct) AS avg_usage_wow_growth,
  AVG(wau_wow_pct) AS avg_wau_wow_growth,
  AVG(users_wow_pct) AS avg_active_users_wow_growth,
  -- Consecutive growth weeks (sustained growth signal)
  COUNTIF(usage_wow_pct > 0) AS weeks_with_usage_growth,
  COUNTIF(wau_wow_pct > 0) AS weeks_with_wau_growth,
  -- Latest week absolute values
  MAX_BY(total_usage, _week) AS latest_week_usage,
  MAX_BY(wau, _week) AS latest_week_wau,
  MAX_BY(active_users, _week) AS latest_week_active_users
FROM wow_changes
GROUP BY email_domain
ORDER BY avg_usage_wow_growth DESC;
