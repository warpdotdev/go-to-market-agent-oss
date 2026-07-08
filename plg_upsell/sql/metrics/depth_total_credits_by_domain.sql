-- depth_total_credits_by_domain.sql
-- Depth metric (domain level): total usage units consumed per domain in the last 30 days.
-- "Usage units" = your core consumption metric (AI credits, API calls, seats, ...).
--
-- REFERENCE QUERY — replace `your_project.your_dataset` with your warehouse
-- location and map the table/column names to your schema (see
-- reference/DATA_DEFINITIONS.md).

SELECT
  uf.email_domain,
  CAST(SUM(ue.usage_units) AS FLOAT64) AS total_usage_units_last_30d,
  COUNT(DISTINCT ue.user_id) AS users_with_usage
FROM `your_project.your_dataset.usage_events` ue
INNER JOIN `your_project.your_dataset.users` uf
  ON ue.user_id = uf.user_id
WHERE ue.event_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
  AND NOT uf.is_excluded
GROUP BY uf.email_domain
ORDER BY total_usage_units_last_30d DESC;
