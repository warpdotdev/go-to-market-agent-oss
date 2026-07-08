-- Canonical BDR Agent storage tables.
-- Project: example-gcp-project
-- Dataset: gtm_agents
-- Company research artifact prefix: gs://example-artifacts-bucket/bdr-agent/company_research/...

CREATE TABLE IF NOT EXISTS `example-gcp-project.gtm_agents.bdr_agent_runs` (
  run_id STRING NOT NULL,
  stage STRING,
  trigger_source STRING,
  lead_id STRING,
  contact_id STRING,
  company_id STRING,
  resolved_company_domain STRING,
  started_at TIMESTAMP,
  completed_at TIMESTAMP,
  duration_seconds FLOAT64,
  status STRING,
  failure_reason STRING,
  oz_run_id STRING,
  oz_run_link STRING,
  oz_session_link STRING,
  oz_credits_used FLOAT64,
  external_service_costs JSON,
  created_at TIMESTAMP
)
PARTITION BY DATE(created_at)
CLUSTER BY stage, lead_id, resolved_company_domain;

CREATE TABLE IF NOT EXISTS `example-gcp-project.gtm_agents.bdr_agent_outputs` (
  output_id STRING NOT NULL,
  run_id STRING,
  stage STRING,
  lead_id STRING,
  contact_id STRING,
  company_id STRING,
  resolved_company_domain STRING,
  output_type STRING,
  schema_version STRING,
  bigquery_table STRING,
  bigquery_row_id STRING,
  gcs_uri STRING,
  created_at TIMESTAMP
)
PARTITION BY DATE(created_at)
CLUSTER BY stage, lead_id, resolved_company_domain;

CREATE TABLE IF NOT EXISTS `example-gcp-project.gtm_agents.bdr_agent_company_research_outputs` (
  output_id STRING NOT NULL,
  run_id STRING,
  lead_id STRING,
  contact_id STRING,
  company_id STRING,
  resolved_company_domain STRING,
  trigger_source STRING,
  hydration_status STRING,
  company_context_json JSON,
  tier_1_internal_metrics_json JSON,
  tier_2_public_research_json JSON,
  tier_3_external_research_json JSON,
  reuse_json JSON,
  research_status STRING,
  schema_version STRING,
  gcs_uri STRING,
  created_at TIMESTAMP
)
PARTITION BY DATE(created_at)
CLUSTER BY resolved_company_domain, hydration_status, lead_id;

CREATE TABLE IF NOT EXISTS `example-gcp-project.gtm_agents.bdr_agent_hooks` (
  hook_id STRING NOT NULL,
  output_id STRING,
  run_id STRING,
  lead_id STRING,
  contact_id STRING,
  company_id STRING,
  resolved_company_domain STRING,
  company_research_output_id STRING,
  synthesis_output_id STRING,
  synthesis_gcs_uri STRING,
  lead_brief_output_id STRING,
  lead_brief_gcs_uri STRING,
  content_kind STRING,
  email_rank INTEGER,
  email_label STRING,
  why_this_may_work STRING,
  selected_for_hubspot BOOL,
  lead_brief_eval_json JSON,
  ai_hook_sources_url STRING,
  style_profile_id STRING,
  style_profile_version STRING,
  style_profile_fallback_reason STRING,
  positioning_snapshot_version STRING,
  positioning_pillar STRING,
  positioning_value_prop STRING,
  writer_mode STRING,
  candidate_hook_text STRING,
  final_hook_text STRING,
  generation_status STRING,
  rewrite_attempted BOOL,
  rewrite_reason STRING,
  lint_result_json JSON,
  critic_result_json JSON,
  candidate_generation_idempotency_key STRING,
  hook_text STRING,
  hook_angle STRING,
  hook_status STRING,
  hubspot_hook_property_name STRING,
  hubspot_sources_property_name STRING,
  hubspot_outreach_writeback_status STRING,
  hubspot_sources_writeback_status STRING,
  hubspot_writeback_at TIMESTAMP,
  hubspot_writeback_error STRING,
  used_by_bdr BOOL,
  edited_hook_text STRING,
  outcome_status STRING,
  schema_version STRING,
  created_at TIMESTAMP,
  updated_at TIMESTAMP
)
PARTITION BY DATE(created_at)
CLUSTER BY resolved_company_domain, lead_id, hook_status;
