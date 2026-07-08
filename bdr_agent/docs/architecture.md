# BDR Agent Architecture

## Purpose
The BDR Agent turns an eligible HubSpot lead into a source-backed outbound email body. The current happy path has two executable stages:

1. Company Research gathers lead/company context, product-usage evidence, public company-owned evidence, and reusable storage metadata.
2. Outreach Composer reads the completed company research output, drafts a brief plus three ranked email bodies, posts the review to Slack when configured, and writes the selected body to HubSpot when the live gate is enabled.

The human-facing second stage and active Python package are Outreach Composer. The persisted runtime identifiers still use `lead_brief` for BigQuery stage values, output types, GCS paths, and HubSpot integration compatibility.

## Repository layout
Active files live in this standalone BDR layout:

```text
bdr_agent/
  README.md
  docs/
    architecture.md
    oz_dev_smoke.md
  skills/
    company-research/SKILL.md
    outreach-composer/SKILL.md
    immediate-rewrite/SKILL.md
    self-improvement/SKILL.md
  src/
    bdr_agent/
      common/
      feedback_loop/
      hubspot_workflow_validation.py
      outreach_writeback/
      stages/
        company_research/
        outreach_composer/
  references/
    outreach_positioning_guide.md
    outreach_style_guide.md
    company_research_taxonomy.md
  sql/
    queries/
    tables/
    migrations/
  scripts/
  tests/
  legacy/
```

New BDR work should use these canonical paths. Historical skills and stage experiments belong under `legacy/`, not in active stage paths.

## End-to-end flow
```text
HubSpot workflow
  -> agent_orchestrator kickoff webhook
  -> Oz run using skills/company-research/SKILL.md
  -> bdr_agent.stages.company_research runtime
  -> BigQuery/GCS company_research output
  -> agent_orchestrator stage-completion webhook
  -> Oz run using skills/outreach-composer/SKILL.md
  -> bdr_agent.stages.outreach_composer runtime
  -> BigQuery/GCS/Slack/HubSpot outputs
```

`agent_orchestrator` should pass compact identifier-based prompt fields. It should not put full research JSON, generated copy, or large reference content into Oz prompts.

## Stage 1: Company Research
Skill:

- `skills/company-research/SKILL.md`

Runtime package:

- `src/bdr_agent/stages/company_research/`

Primary command shape:

```bash
PYTHONPATH=src python -m bdr_agent.stages.company_research.cli   --lead-id "$LEAD_ID"   --trigger-source "$BDR_AGENT_TRIGGER"   --source-system "$SOURCE_SYSTEM"   --hubspot-workflow-id "$HUBSPOT_WORKFLOW_ID"   --contact-id "${CONTACT_ID:-}"   --company-id "${COMPANY_ID:-}"   --company-domain "${COMPANY_DOMAIN:-}"   --company-website "${COMPANY_WEBSITE:-}"   --company-alternative-domain "${COMPANY_ALTERNATIVE_DOMAIN:-}"   --persist
```

Responsibilities:

- Build lead/contact/company context from the HubSpot webhook payload first.
- Use BigQuery hydration only as a fallback for blank or absent fields.
- Resolve company domain from company-backed fields only.
- Query Tier 1 internal product-usage metrics from `sql/queries/tier_1_metrics_query.sql`.
- Reuse recent Tier 2 public company research when eligible.
- Run fresh Exa-backed public company-owned research when reuse is not available.
- Skip Tier 3 by default with reason `tier_3_disabled_for_mvp`.
- Persist run metadata, output index rows, structured company research rows, and GCS JSON artifacts.
- Send a stage-completion webhook to trigger Outreach Composer when persistence succeeds.

Primary output tables/artifacts:

- `example-gcp-project.gtm_agents.bdr_agent_runs`
- `example-gcp-project.gtm_agents.bdr_agent_outputs`
- `example-gcp-project.gtm_agents.bdr_agent_company_research_outputs`
- `gs://example-artifacts-bucket/bdr-agent/company_research/<run_id>/<output_id>.json`

## Stage 2: Outreach Composer
Skill:

- `skills/outreach-composer/SKILL.md`

Runtime package:

- `src/bdr_agent/stages/outreach_composer/`

Primary command shape:

```bash
PYTHONPATH=src python -m bdr_agent.stages.outreach_composer.cli   --lead-id "$LEAD_ID"   --company-research-output-id "$COMPANY_RESEARCH_OUTPUT_ID"   --lead-brief-packet-json-file /tmp/lead_brief_packet.json   --persist-bigquery   --allow-hubspot-writeback
```

Responsibilities:

- Load the latest completed company research row by `COMPANY_RESEARCH_OUTPUT_ID`, `PREVIOUS_OUTPUT_ID`, or `LEAD_ID` fallback.
- Read the two active writing references: `references/outreach_positioning_guide.md` and `references/outreach_style_guide.md`.
- Write a concise lead brief and exactly three ranked full email body drafts.
- Validate body-only boundaries: no greeting, no sign-off, no sender name, 85 words or fewer, and at most one soft question.
- Persist the lead brief Markdown and rendered HTML artifacts to GCS.
- Insert one output row for the brief and three ranked rows in `bdr_agent_hooks`.
- Post a Slack review notification when the effective delivery mode includes Slack.
- Write rank 1 to HubSpot only when both delivery mode and writeback gate allow it.

Primary output tables/artifacts:

- `example-gcp-project.gtm_agents.bdr_agent_runs`
- `example-gcp-project.gtm_agents.bdr_agent_outputs`
- `example-gcp-project.gtm_agents.bdr_agent_hooks`
- `gs://example-artifacts-bucket/bdr-agent/lead_brief/<run_id>/<output_id>.md`
- `gs://example-artifacts-bucket/bdr-agent/lead_brief/<run_id>/<output_id>.html`

## Slack and HubSpot delivery
Slack delivery is idempotency-protected by a marker row in `bdr_agent_outputs` before posting. The Slack message includes the HubSpot record link when configured, a rendered brief link, and a review preview with greeting/signoff added for humans.

HubSpot writeback is intentionally narrow:

- `ai_hook_intro`: selected rank-1 email body only, without greeting/signoff/sender.
- `ai_hook_sources`: rendered lead brief/source URL.
- `ai_personalized_at`: writeback timestamp.

No other HubSpot fields should be written by the BDR runtime.

## Stage and naming contract
Keep these persisted names stable unless downstream systems are intentionally migrated:

- `company_research`
- `lead_brief`
- `lead_brief_slack_delivery`
- `bdr_agent_runs`
- `bdr_agent_outputs`
- `bdr_agent_company_research_outputs`
- `bdr_agent_hooks`
- `gs://example-artifacts-bucket/bdr-agent/...`
- `ai_hook_intro`
- `ai_hook_sources`
- `ai_personalized_at`

The `src/bdr_agent/stages/` namespace is the active runtime location for executable stages. Persistence contracts should remain stable until coordinated migration work is explicitly planned.

Additive Outreach Composer compatibility views may expose canonical aliases for analytics and deployment consumers without renaming physical rows or columns:

- `bdr_agent_outreach_composer_runs`: filters persisted `stage=lead_brief` rows and adds `canonical_stage=outreach_composer`.
- `bdr_agent_outreach_composer_outputs`: covers `lead_brief` and `lead_brief_slack_delivery` output rows and adds canonical stage/output-type aliases.
- `bdr_agent_outreach_composer_email_bodies`: covers ranked email body rows in `bdr_agent_hooks` and adds `outreach_composer_*` aliases plus `email_body_text`.

## Supporting flows
### Immediate Rewrite
`skills/immediate-rewrite/SKILL.md` handles Slack-thread rewrite requests. It should use only the Slack thread, linked lead brief/ranked drafts, the two active writing references, and the human feedback text. Safe HubSpot updates remain limited to `ai_hook_intro` and `ai_personalized_at`.

### Self-Improvement
`skills/self-improvement/SKILL.md` handles scheduled guide-learning. It may update only:

- `references/outreach_positioning_guide.md`
- `references/outreach_style_guide.md`

It should not create another durable pattern library, generated prospect-copy archive, HubSpot writeback script, or Slack event handler.

## Validation
Local validation should run from the BDR Agent repository root with `src` on `PYTHONPATH`:

```bash
PYTHONPATH=src python3 -m unittest discover tests
PYTHONPATH=src python3 scripts/bdr_oz_dev_smoke.py --skip-dependency-imports --skip-bigquery-read --skip-exa-key-check --skip-step2-dry-run
git --no-pager diff --check
```

Broader validation is required when changing persistence contracts, stage-completion payloads, HubSpot writeback behavior, or delivery modes.
