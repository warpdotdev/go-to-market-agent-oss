-- depth_avg_credits_per_user_week.sql
-- Depth metric (user average): average usage units per user per week, by domain.
-- Aggregates usage_events at weekly grain, then averages across weeks.
--
-- REFERENCE QUERY — replace `your_project.your_dataset` with your warehouse
-- location and map the table/column names to your schema (see
-- reference/DATA_DEFINITIONS.md).

WITH weekly_domain_usage AS (
  SELECT
    uf.email_domain,
    DATE_TRUNC(ue.event_date, WEEK(SUNDAY)) AS _week,
    CAST(SUM(ue.usage_units) AS FLOAT64) AS total_usage,
    COUNT(DISTINCT ue.user_id) AS distinct_users
  FROM `your_project.your_dataset.usage_events` ue
  INNER JOIN `your_project.your_dataset.users` uf
    ON ue.user_id = uf.user_id
  WHERE ue.event_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 28 DAY)
    AND ue.event_date < DATE_TRUNC(CURRENT_DATE(), WEEK(SUNDAY))  -- only complete weeks
    AND NOT uf.is_excluded
  GROUP BY uf.email_domain, DATE_TRUNC(ue.event_date, WEEK(SUNDAY))
)

SELECT
  email_domain,
  AVG(total_usage / NULLIF(distinct_users, 0)) AS avg_usage_units_per_user_per_week,
  AVG(distinct_users) AS avg_active_users_per_week
FROM weekly_domain_usage
GROUP BY email_domain
ORDER BY avg_usage_units_per_user_per_week DESC;
