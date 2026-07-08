-- recent_reload_credits.sql
-- Time-based signal: domains where users purchased add-on/overage units in the last 14 days.
--
-- REFERENCE QUERY — replace `your_project.your_dataset` with your warehouse
-- location and map the table/column names to your schema (see
-- reference/DATA_DEFINITIONS.md).
-- NOTE: `amount` is divided by 100 assuming the source stores cents. If your
-- source is already in dollars, remove the /100.

SELECT
  ac.email_domain,
  COUNT(*) AS num_purchases,
  COUNT(DISTINCT ac.account_id) AS accounts_purchasing,
  SUM(ac.amount) / 100.0 AS total_dollars_spent,
  SUM(ac.quantity) AS total_units_bought,
  ARRAY_AGG(DISTINCT ac.purchase_reason IGNORE NULLS) AS purchase_types,
  MAX(ac.purchased_at) AS latest_purchase
FROM `your_project.your_dataset.overage_purchases` ac
WHERE ac.purchased_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 14 DAY)
  AND ac.status = 'paid'
GROUP BY ac.email_domain
ORDER BY total_dollars_spent DESC;
