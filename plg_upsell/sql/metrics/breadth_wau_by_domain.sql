-- breadth_wau_by_domain.sql
-- Breadth metric: average weekly active users per domain over the last 4 complete weeks.
--
-- REFERENCE QUERY — replace `your_project.your_dataset` with your warehouse
-- location and map the table/column names to your schema (see
-- reference/DATA_DEFINITIONS.md).

WITH last_4_weeks AS (
  SELECT DISTINCT week_start
  FROM `your_project.your_dataset.weekly_active_users`
  WHERE week_start >= DATE_SUB(CURRENT_DATE(), INTERVAL 28 DAY)
    AND week_start < DATE_TRUNC(CURRENT_DATE(), WEEK(SUNDAY))  -- only complete weeks
  ORDER BY week_start DESC
),

weekly_domain_wau AS (
  SELECT
    uf.email_domain,
    w.week_start,
    COUNT(DISTINCT w.user_id) AS wau
  FROM `your_project.your_dataset.weekly_active_users` w
  INNER JOIN `your_project.your_dataset.users` uf
    ON w.user_id = uf.user_id
  INNER JOIN last_4_weeks l4w
    ON w.week_start = l4w.week_start
  WHERE NOT uf.is_excluded
  GROUP BY uf.email_domain, w.week_start
)

SELECT
  email_domain,
  AVG(wau) AS avg_wau_last_4_weeks,
  MIN(wau) AS min_wau_last_4_weeks,
  MAX(wau) AS max_wau_last_4_weeks
FROM weekly_domain_wau
GROUP BY email_domain
ORDER BY avg_wau_last_4_weeks DESC;
