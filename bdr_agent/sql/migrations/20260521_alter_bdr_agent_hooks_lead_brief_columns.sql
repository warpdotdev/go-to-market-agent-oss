-- Add lead_brief ranked email body columns to the live BDR hooks table.
-- Target: example-gcp-project.gtm_agents.bdr_agent_hooks
-- Safe to run more than once because each column uses IF NOT EXISTS.
-- Do not apply from local validation; run only during the approved production migration step.

ALTER TABLE `example-gcp-project.gtm_agents.bdr_agent_hooks`
ADD COLUMN IF NOT EXISTS company_research_output_id STRING;

ALTER TABLE `example-gcp-project.gtm_agents.bdr_agent_hooks`
ADD COLUMN IF NOT EXISTS lead_brief_output_id STRING;

ALTER TABLE `example-gcp-project.gtm_agents.bdr_agent_hooks`
ADD COLUMN IF NOT EXISTS lead_brief_gcs_uri STRING;

ALTER TABLE `example-gcp-project.gtm_agents.bdr_agent_hooks`
ADD COLUMN IF NOT EXISTS content_kind STRING;

ALTER TABLE `example-gcp-project.gtm_agents.bdr_agent_hooks`
ADD COLUMN IF NOT EXISTS email_rank INTEGER;

ALTER TABLE `example-gcp-project.gtm_agents.bdr_agent_hooks`
ADD COLUMN IF NOT EXISTS email_label STRING;

ALTER TABLE `example-gcp-project.gtm_agents.bdr_agent_hooks`
ADD COLUMN IF NOT EXISTS why_this_may_work STRING;

ALTER TABLE `example-gcp-project.gtm_agents.bdr_agent_hooks`
ADD COLUMN IF NOT EXISTS selected_for_hubspot BOOL;

ALTER TABLE `example-gcp-project.gtm_agents.bdr_agent_hooks`
ADD COLUMN IF NOT EXISTS lead_brief_eval_json JSON;
