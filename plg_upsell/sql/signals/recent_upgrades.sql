-- recent_upgrades.sql
-- Time-based signal: domains where users upgraded to a paid plan in the last 30 days.
--
-- REFERENCE QUERY — replace `your_project.your_dataset` with your warehouse
-- location and map the table/column names to your schema (see
-- reference/DATA_DEFINITIONS.md).

SELECT
  uf.email_domain,
  COUNT(DISTINCT pc.user_id) AS users_upgraded,
  ARRAY_AGG(DISTINCT pc.upgrade_type IGNORE NULLS) AS upgrade_types,
  MIN(pc.upgraded_at) AS earliest_upgrade,
  MAX(pc.upgraded_at) AS latest_upgrade
FROM `your_project.your_dataset.upgrades` pc
INNER JOIN `your_project.your_dataset.users` uf
  ON pc.user_id = uf.user_id
WHERE pc.did_upgrade = TRUE
  AND pc.upgraded_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
  AND NOT uf.is_excluded
GROUP BY uf.email_domain
ORDER BY users_upgraded DESC;
