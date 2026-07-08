-- depth_avg_days_active_per_user_week.sql
-- Depth metric (user average): average days active per user per week, by domain.
-- Uses weekly_active_users to scope weeks, and daily_active_users for per-day
-- activity within each week.
--
-- REFERENCE QUERY — replace `your_project.your_dataset` with your warehouse
-- location and map the table/column names to your schema (see
-- reference/DATA_DEFINITIONS.md).

WITH last_4_weeks AS (
  SELECT DISTINCT week_start
  FROM `your_project.your_dataset.weekly_active_users`
  WHERE week_start >= DATE_SUB(CURRENT_DATE(), INTERVAL 28 DAY)
    AND week_start < DATE_TRUNC(CURRENT_DATE(), WEEK(SUNDAY))
),

daily_activity AS (
  -- Count distinct active days per user per week
  SELECT
    uf.email_domain,
    dau.user_id,
    DATE_TRUNC(dau.activity_date, WEEK(SUNDAY)) AS week_start,
    COUNT(DISTINCT dau.activity_date) AS days_active_in_week
  FROM `your_project.your_dataset.daily_active_users` dau
  INNER JOIN `your_project.your_dataset.users` uf
    ON dau.user_id = uf.user_id
  WHERE dau.activity_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 28 DAY)
    AND dau.activity_date < DATE_TRUNC(CURRENT_DATE(), WEEK(SUNDAY))
    AND NOT uf.is_excluded
  GROUP BY uf.email_domain, dau.user_id, DATE_TRUNC(dau.activity_date, WEEK(SUNDAY))
),

weekly_domain_avg AS (
  SELECT
    email_domain,
    week_start,
    AVG(days_active_in_week) AS avg_days_active_per_user,
    COUNT(DISTINCT user_id) AS active_users
  FROM daily_activity
  INNER JOIN last_4_weeks USING (week_start)
  GROUP BY email_domain, week_start
)

SELECT
  email_domain,
  AVG(avg_days_active_per_user) AS avg_days_active_per_user_per_week,
  AVG(active_users) AS avg_active_users_per_week
FROM weekly_domain_avg
GROUP BY email_domain
ORDER BY avg_days_active_per_user_per_week DESC;
