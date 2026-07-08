-- hit_limits.sql
-- Time-based signal: domains where users hit usage limits in the last 14 days.
--
-- REFERENCE QUERY — replace `your_project.your_dataset` with your warehouse
-- location and map the table/column names to your schema (see
-- reference/DATA_DEFINITIONS.md).

SELECT
  uf.email_domain,
  COUNT(*) AS total_limit_hits,
  COUNT(DISTINCT pw.user_id) AS users_hitting_limits,
  MIN(pw.event_date) AS first_hit_date,
  MAX(pw.event_date) AS latest_hit_date
FROM `your_project.your_dataset.limit_events` pw
INNER JOIN `your_project.your_dataset.users` uf
  ON pw.user_id = uf.user_id
WHERE pw.event_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY)
  -- EDIT: narrow to the usage-limit / paywall events that signal upsell demand
  AND pw.feature = 'ai_feature'
  AND pw.entrypoint = 'agent'
  AND NOT uf.is_excluded
GROUP BY uf.email_domain
ORDER BY total_limit_hits DESC;
