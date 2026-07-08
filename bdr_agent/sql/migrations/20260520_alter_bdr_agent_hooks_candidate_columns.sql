-- Add candidate/evaluate hook lifecycle columns to the live BDR hooks table.
-- Target: example-gcp-project.gtm_agents.bdr_agent_hooks
-- Safe to run more than once because each column uses IF NOT EXISTS.
-- Do not apply from local validation; run only during the approved production migration step.

ALTER TABLE `example-gcp-project.gtm_agents.bdr_agent_hooks`
ADD COLUMN IF NOT EXISTS style_profile_id STRING;

ALTER TABLE `example-gcp-project.gtm_agents.bdr_agent_hooks`
ADD COLUMN IF NOT EXISTS style_profile_version STRING;

ALTER TABLE `example-gcp-project.gtm_agents.bdr_agent_hooks`
ADD COLUMN IF NOT EXISTS style_profile_fallback_reason STRING;

ALTER TABLE `example-gcp-project.gtm_agents.bdr_agent_hooks`
ADD COLUMN IF NOT EXISTS positioning_snapshot_version STRING;

ALTER TABLE `example-gcp-project.gtm_agents.bdr_agent_hooks`
ADD COLUMN IF NOT EXISTS positioning_pillar STRING;

ALTER TABLE `example-gcp-project.gtm_agents.bdr_agent_hooks`
ADD COLUMN IF NOT EXISTS positioning_value_prop STRING;

ALTER TABLE `example-gcp-project.gtm_agents.bdr_agent_hooks`
ADD COLUMN IF NOT EXISTS writer_mode STRING;

ALTER TABLE `example-gcp-project.gtm_agents.bdr_agent_hooks`
ADD COLUMN IF NOT EXISTS candidate_hook_text STRING;

ALTER TABLE `example-gcp-project.gtm_agents.bdr_agent_hooks`
ADD COLUMN IF NOT EXISTS final_hook_text STRING;

ALTER TABLE `example-gcp-project.gtm_agents.bdr_agent_hooks`
ADD COLUMN IF NOT EXISTS generation_status STRING;

ALTER TABLE `example-gcp-project.gtm_agents.bdr_agent_hooks`
ADD COLUMN IF NOT EXISTS rewrite_attempted BOOL;

ALTER TABLE `example-gcp-project.gtm_agents.bdr_agent_hooks`
ADD COLUMN IF NOT EXISTS rewrite_reason STRING;

ALTER TABLE `example-gcp-project.gtm_agents.bdr_agent_hooks`
ADD COLUMN IF NOT EXISTS lint_result_json JSON;

ALTER TABLE `example-gcp-project.gtm_agents.bdr_agent_hooks`
ADD COLUMN IF NOT EXISTS critic_result_json JSON;

ALTER TABLE `example-gcp-project.gtm_agents.bdr_agent_hooks`
ADD COLUMN IF NOT EXISTS candidate_generation_idempotency_key STRING;
