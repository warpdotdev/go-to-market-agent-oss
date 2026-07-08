-- depth_reload_revenue_by_domain.sql
-- Depth metric (domain level): total $ spent on add-on/overage purchases per
-- domain in the last 30 days.
--
-- REFERENCE QUERY — replace `your_project.your_dataset` with your warehouse
-- location and map the table/column names to your schema (see
-- reference/DATA_DEFINITIONS.md).
-- NOTE: `amount` is divided by 100 assuming the source stores cents. If your
-- source is already in dollars, remove the /100.

SELECT
  ac.email_domain,
  ac.purchase_reason,
  COUNT(*) AS num_purchases,
  SUM(ac.amount) AS total_overage_revenue_cents,
  SUM(ac.amount) / 100.0 AS total_overage_revenue_dollars,
  SUM(ac.quantity) AS total_units_purchased
FROM `your_project.your_dataset.overage_purchases` ac
WHERE ac.purchased_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
  AND ac.status = 'paid'
GROUP BY ac.email_domain, ac.purchase_reason
ORDER BY total_overage_revenue_cents DESC;
