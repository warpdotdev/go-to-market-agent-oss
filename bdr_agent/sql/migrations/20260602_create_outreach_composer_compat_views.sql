-- Create additive Outreach Composer compatibility views over the live BDR tables.
-- Targets:
--   example-gcp-project.gtm_agents.bdr_agent_outreach_composer_runs
--   example-gcp-project.gtm_agents.bdr_agent_outreach_composer_outputs
--   example-gcp-project.gtm_agents.bdr_agent_outreach_composer_email_bodies
-- Safe to run more than once because each statement uses CREATE OR REPLACE VIEW.
-- Do not apply from local validation; run only during the approved production migration step.
--
-- These views expose canonical Outreach Composer aliases while preserving the
-- historical lead_brief rows, GCS paths, and bdr_agent_hooks columns in the
-- underlying tables.

CREATE OR REPLACE VIEW `example-gcp-project.gtm_agents.bdr_agent_outreach_composer_runs` AS
SELECT
  runs.*,
  'outreach_composer' AS canonical_stage,
  runs.stage AS legacy_stage
FROM `example-gcp-project.gtm_agents.bdr_agent_runs` AS runs
WHERE runs.stage = 'lead_brief';

CREATE OR REPLACE VIEW `example-gcp-project.gtm_agents.bdr_agent_outreach_composer_outputs` AS
SELECT
  outputs.*,
  CASE
    WHEN outputs.stage = 'lead_brief' THEN 'outreach_composer'
    WHEN outputs.stage = 'lead_brief_slack_delivery' THEN 'outreach_composer_slack_delivery'
    ELSE outputs.stage
  END AS canonical_stage,
  outputs.stage AS legacy_stage,
  CASE
    WHEN outputs.output_type = 'lead_brief_markdown' THEN 'outreach_composer_markdown'
    WHEN outputs.output_type = 'slack_review_notification' THEN 'outreach_composer_slack_review_notification'
    ELSE outputs.output_type
  END AS canonical_output_type,
  outputs.output_type AS legacy_output_type,
  outputs.gcs_uri AS outreach_composer_gcs_uri
FROM `example-gcp-project.gtm_agents.bdr_agent_outputs` AS outputs
WHERE outputs.stage IN ('lead_brief', 'lead_brief_slack_delivery')
   OR outputs.output_type IN ('lead_brief_markdown', 'slack_review_notification');

CREATE OR REPLACE VIEW `example-gcp-project.gtm_agents.bdr_agent_outreach_composer_email_bodies` AS
SELECT
  hooks.*,
  'outreach_composer' AS canonical_stage,
  hooks.lead_brief_output_id AS outreach_composer_output_id,
  hooks.lead_brief_gcs_uri AS outreach_composer_gcs_uri,
  hooks.lead_brief_eval_json AS outreach_composer_eval_json,
  COALESCE(hooks.final_hook_text, hooks.hook_text, hooks.candidate_hook_text) AS email_body_text
FROM `example-gcp-project.gtm_agents.bdr_agent_hooks` AS hooks
WHERE hooks.content_kind = 'email_body'
   OR hooks.lead_brief_output_id IS NOT NULL
   OR hooks.lead_brief_gcs_uri IS NOT NULL
   OR hooks.email_rank IS NOT NULL;
