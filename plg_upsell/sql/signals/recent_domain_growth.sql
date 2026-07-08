-- recent_domain_growth.sql
-- Time-based signal: domains where new members joined accounts in the last 14 days.
--
-- REFERENCE QUERY — replace `your_project.your_dataset` with your warehouse
-- location and map the table/column names to your schema (see
-- reference/DATA_DEFINITIONS.md).

SELECT
  tf.admin_email_domain AS email_domain,
  COUNT(*) AS total_join_events,
  COUNT(DISTINCT me.user_id) AS distinct_new_members,
  COUNT(DISTINCT me.account_id) AS accounts_with_new_members,
  MIN(me.event_date) AS first_join_date,
  MAX(me.event_date) AS latest_join_date
FROM `your_project.your_dataset.membership_events` me
INNER JOIN `your_project.your_dataset.accounts` tf
  ON me.account_id = tf.account_id
WHERE me.event_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY)
  -- EDIT: your "new member joined / invited" event names
  AND me.event_name IN (
    'join team',
    'join team via team discovery',
    'invited teammates',
    'send team invite email'
  )
GROUP BY tf.admin_email_domain
ORDER BY distinct_new_members DESC;
