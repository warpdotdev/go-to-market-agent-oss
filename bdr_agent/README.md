# BDR Agent
The BDR Agent researches inbound PDF-download leads and writes a reviewed, source-backed outbound email body back to HubSpot. This README is the human-facing map of the standalone BDR layout: what triggers it, which skills and Python modules run, where outputs are stored, and which files are active.

## Current production flow
The observed live flow is:

```text
HubSpot workflow -> agent_orchestrator -> company_research Oz run -> agent_orchestrator stage completion -> lead_brief Oz run -> BigQuery/GCS/Slack/HubSpot outputs
```

The current production-intended runtime chain is `company_research -> lead_brief`.

Important naming note: the human-facing second stage and active Python package are Outreach Composer, but the persisted stage name, BigQuery stage value, output types, and GCS path still use `lead_brief`.

## Reference E2E run
Use this completed Example Corp run as the concrete example for inspecting the system.

### HubSpot request body
The lead was selected because it had never appeared in any BDR tables before the test. These are the request-body fields sent from the HubSpot workflow test:

- `lead_id`: `100000000001`
- `contact_id`: `100000000002`
- `company_id`: `100000000003`
- `company_domain`: `example.com`
- `company_website`: `example.com`
- `company_alternative_domain`: `example.com`
- `company_name`: `example corp`
- `lead_owner_id`: `100000000004`
- `lead_created_at`: `2026-05-22T08:46:03.32+00:00`
- `lead_source_detailed`: `inbound - oz campaign - pdf download`
- `contact_first_name`: `first`
- `contact_last_name`: `last`
- `contact_job_title`: `application consultant`
- `contact_email`: `first.last@example.com`

### Stage 1: Company Research
Observed Oz run:

- Oz run: https://oz.example.com/runs/EXAMPLE_RUN_ID
- Session: https://app.example.com/conversation/EXAMPLE_CONVERSATION_ID
- Skill path: `skills/company-research/SKILL.md`
- Runtime CLI: `python -m bdr_agent.stages.company_research.cli ... --persist`
- Python package: `src/bdr_agent/stages/company_research/`
- Deterministic run ID: `bdr_run_95ac28929ea74441bb90f62685e2281c`
- Output ID: `bdr_output_74eac4ca177a456784fe86474324e36e`
- Status: `research_complete`
- Resolved company domain: `example.com`
- Stage completion: sent to agent_orchestrator with HTTP 200 and `next_stage=lead_brief`

Company Research writes:

- `example-gcp-project.gtm_agents.bdr_agent_runs`
  - one row with `stage=company_research` and `status=research_complete`
- `example-gcp-project.gtm_agents.bdr_agent_outputs`
  - one row with `stage=company_research` and `output_type=company_research_json`
- `example-gcp-project.gtm_agents.bdr_agent_company_research_outputs`
  - one structured JSON row with hydration, Tier 1, Tier 2, Tier 3, reuse, and storage data
- GCS JSON artifact:
  - `gs://example-artifacts-bucket/bdr-agent/company_research/bdr_run_95ac28929ea74441bb90f62685e2281c/bdr_output_74eac4ca177a456784fe86474324e36e.json`

For the Example Corp example, Tier 1 found product usage and Tier 2 created fresh Exa-backed findings about Example Corp MCP/agentic banking content. The company-research run reported Exa cost of `$0.014`.

### Stage 2: Lead Brief / Outreach Composer
Observed Oz run:

- Oz run: https://oz.example.com/runs/EXAMPLE_RUN_ID
- Session: https://app.example.com/conversation/EXAMPLE_CONVERSATION_ID
- Skill path: `skills/outreach-composer/SKILL.md`
- Runtime CLI: `python -m bdr_agent.stages.outreach_composer.cli ... --persist-bigquery --allow-hubspot-writeback`
- Python package: `src/bdr_agent/stages/outreach_composer/`
- Deterministic run ID: `bdr_run_2008cbed632046028f8d36c739c7b80c`
- Output ID: `bdr_output_a2572c2689754810a55b54535ca3989c`
- Status: `completed`

The stage prompt stayed compact and identifier-based. It included `LEAD_ID`, `PREVIOUS_RUN_ID`, `PREVIOUS_OUTPUT_ID`, `COMPANY_RESEARCH_OUTPUT_ID`, `COMPANY_RESEARCH_GCS_URI`, and `BDR_AGENT_ALLOW_HUBSPOT_WRITEBACK=true`; it did not include full research JSON or generated copy.

Lead Brief reads:

- Company research output from BigQuery, using `COMPANY_RESEARCH_OUTPUT_ID` or `PREVIOUS_OUTPUT_ID`
- `references/outreach_positioning_guide.md`
- `references/outreach_style_guide.md`

Lead Brief writes:

- `example-gcp-project.gtm_agents.bdr_agent_runs`
  - one row with `stage=lead_brief` and `status=completed`
- `example-gcp-project.gtm_agents.bdr_agent_outputs`
  - one `lead_brief_markdown` row for the durable brief
  - one `lead_brief_slack_delivery` row as a Slack idempotency marker
- `example-gcp-project.gtm_agents.bdr_agent_hooks`
  - three rows with `content_kind=email_body`, `email_rank=1..3`, and `generation_status=quality_passed`
  - rank 1 has `selected_for_hubspot=true`
- GCS Markdown artifact:
  - `gs://example-artifacts-bucket/bdr-agent/lead_brief/bdr_run_2008cbed632046028f8d36c739c7b80c/bdr_output_a2572c2689754810a55b54535ca3989c.md`
- GCS rendered HTML artifact:
  - `https://storage.cloud.google.com/example-artifacts-bucket/bdr-agent/lead_brief/bdr_run_2008cbed632046028f8d36c739c7b80c/bdr_output_a2572c2689754810a55b54535ca3989c.html?authuser=0`

The Example Corp run generated three ranked email body drafts:

1. `MCP launch + cloud agent operations`, selected for HubSpot
2. `Agentic banking publishing + cloud triggers`
3. `Doc export + terminal-native + quiet product traction`

### Slack output
Slack delivery is performed by `bdr_agent.stages.outreach_composer.slack.post_lead_brief_review_notification` when the effective delivery mode includes Slack.

The durable idempotency marker is written before Slack posting to prevent duplicate external side effects on retry:

- `output_id`: `lead_brief_slack:100000000001:bdr_output_a2572c2689754810a55b54535ca3989c`
- `stage`: `lead_brief_slack_delivery`
- `output_type`: `slack_review_notification`
- marker table: `example-gcp-project.gtm_agents.bdr_agent_outputs`

The Slack message includes the HubSpot record link when `BDR_AGENT_HUBSPOT_PORTAL_ID` is configured, the rendered brief link, and a rendered preview of the rank-1 email body with greeting/signoff added for human review.

### HubSpot writeback
HubSpot writeback is performed by `bdr_agent.outreach_writeback.hubspot.update_hook_properties` through the `lead_brief` runtime. For the Example Corp example, the Oz run reported successful writeback to contact `100000000002`.

Fields written:

- `ai_hook_intro`: rank-1 full email body, without greeting/signoff/sender
- `ai_hook_sources`: rendered lead brief HTML URL
- `ai_personalized_at`: writeback timestamp

Only rank 1 is written to HubSpot. Rank 2 and rank 3 remain stored in BigQuery as alternatives with writeback status `not_attempted`.

## Runtime code map
### Company Research
Active skill/config entrypoint:

- `skills/company-research/SKILL.md`

Active Python files:

- `src/bdr_agent/stages/company_research/cli.py`: parses prompt fields and calls the runner
- `src/bdr_agent/stages/company_research/run.py`: orchestrates hydration, Tier 1, Tier 2, persistence, and stage completion
- `src/bdr_agent/stages/company_research/hydration.py`: merges webhook payload with BigQuery fallback
- `src/bdr_agent/stages/company_research/internal_metrics.py`: loads product-usage metrics
- `src/bdr_agent/stages/company_research/public_research.py`: runs Exa-backed public company research
- `src/bdr_agent/stages/company_research/reuse.py`: checks reusable Tier 2 outputs
- `src/bdr_agent/stages/company_research/schemas.py`: builds the `bdr_agent_company_research.v1` JSON output
- `src/bdr_agent/stages/company_research/storage.py`: writes GCS artifacts and BigQuery rows
- `src/bdr_agent/stages/company_research/stage_completion.py`: sends `company_research -> lead_brief` handoff to agent_orchestrator

Active references:

- `sql/queries/hydration_query.sql`
- `sql/queries/tier_1_metrics_query.sql`
- `sql/tables/storage_tables.sql`
- `sql/migrations/`
- `references/company_research_taxonomy.md`
- `tests/fixtures/company_research_example_outputs/`

### Lead Brief / Outreach Composer
Active skill/config entrypoint:

- `skills/outreach-composer/SKILL.md`

Active Python files:

- `src/bdr_agent/stages/outreach_composer/cli.py`: validates arguments and calls the runtime
- `src/bdr_agent/stages/outreach_composer/run.py`: validates the packet, loads company research, creates IDs, handles delivery mode, HubSpot writeback, Slack delivery, and persistence
- `src/bdr_agent/stages/outreach_composer/company_research.py`: loads prior company research from BigQuery or supplied JSON
- `src/bdr_agent/stages/outreach_composer/validation.py`: enforces packet shape and email-body constraints
- `src/bdr_agent/stages/outreach_composer/storage.py`: writes run/output/hook rows and Slack idempotency marker
- `src/bdr_agent/stages/outreach_composer/artifacts.py`: builds Markdown/HTML GCS URIs and authenticated URLs
- `src/bdr_agent/stages/outreach_composer/slack.py`: posts review notification to Slack
- `src/bdr_agent/stages/outreach_composer/local_preview.py`: local-only preview and regression helper; not part of the production run

Active writing references:

- `references/outreach_positioning_guide.md`
- `references/outreach_style_guide.md`

## Output tables
### `bdr_agent_runs`
One row per executable stage run. Current active stages:

- `company_research`
- `lead_brief`

Fields include `run_id`, `stage`, `trigger_source`, lead/contact/company IDs, resolved domain, timestamps, status, failure reason, Oz links when supplied, and external service costs.

### `bdr_agent_outputs`
One row per durable output or delivery marker. Current active output types include:

- `company_research_json`
- `lead_brief_markdown`
- `slack_review_notification`

### `bdr_agent_company_research_outputs`
One row per Company Research output. This is the structured company research system of record.

### `bdr_agent_hooks`
Despite the historical table name, the active Outreach Composer path stores full email bodies here:

- one row per ranked draft
- `content_kind=email_body`
- `email_rank=1..3`
- `selected_for_hubspot=true` only for rank 1
- `hook_text`, `candidate_hook_text`, and `final_hook_text` contain the email body for the current table schema

## Inspecting a run
Use the lead ID to inspect all active outputs:

```bash
bq --project_id=example-gcp-project query --use_legacy_sql=false '
DECLARE target_lead_id STRING DEFAULT "100000000001";
SELECT "runs" AS source, TO_JSON_STRING(t) AS row_json
FROM `example-gcp-project.gtm_agents.bdr_agent_runs` t
WHERE CAST(lead_id AS STRING) = target_lead_id
UNION ALL
SELECT "outputs", TO_JSON_STRING(t)
FROM `example-gcp-project.gtm_agents.bdr_agent_outputs` t
WHERE CAST(lead_id AS STRING) = target_lead_id
UNION ALL
SELECT "company_research_outputs", TO_JSON_STRING(t)
FROM `example-gcp-project.gtm_agents.bdr_agent_company_research_outputs` t
WHERE CAST(lead_id AS STRING) = target_lead_id
UNION ALL
SELECT "hooks", TO_JSON_STRING(t)
FROM `example-gcp-project.gtm_agents.bdr_agent_hooks` t
WHERE CAST(lead_id AS STRING) = target_lead_id;
'
```

Use Oz run IDs to inspect transcripts:

```bash
oz-dev run get 00000000-0000-0000-0000-000000000000 --output-format json
oz-dev run get 00000000-0000-0000-0000-000000000000 --output-format json
```

## Directory status
### Active runtime
- `src/bdr_agent/stages/company_research/`
- `src/bdr_agent/stages/outreach_composer/`
- `src/bdr_agent/outreach_writeback/`
- `src/bdr_agent/stage_completion.py`

### Active skills
- `skills/company-research/SKILL.md`: Company Research
- `skills/outreach-composer/SKILL.md`: Outreach Composer
- `skills/immediate-rewrite/SKILL.md`: Slack-thread rewrite flow
- `skills/self-improvement/SKILL.md`: scheduled guide-learning flow

### Active references and SQL
- `references/`
- `sql/queries/`
- `sql/tables/`
- `sql/migrations/`

### Naming contracts still awaiting migration
- `lead_brief`: persisted second-stage contract for current BigQuery stage values, output types, GCS artifact paths, and Slack delivery markers.
- `bdr_agent_hooks`: historical table name that now stores ranked Outreach Composer email bodies.
- `ai_hook_intro` and `ai_hook_sources`: HubSpot property names still written by Outreach Composer.

### Additive Outreach Composer compatibility views
For later deployment PRs, `sql/migrations/20260602_create_outreach_composer_compat_views.sql` prepares view-only aliases over the existing tables:

- `bdr_agent_outreach_composer_runs`
- `bdr_agent_outreach_composer_outputs`
- `bdr_agent_outreach_composer_email_bodies`

These views expose `canonical_stage`, `canonical_output_type`, `outreach_composer_output_id`, `outreach_composer_gcs_uri`, `outreach_composer_eval_json`, and `email_body_text` where applicable. They intentionally preserve the underlying `lead_brief` rows, `lead_brief_*` columns, GCS paths, and HubSpot property names.

### Legacy reference material
- `legacy/skills/write-hook/`: legacy skill/reference path retained only as reference material.
- `legacy/skills/evaluate-and-writeback/`: historical quality-gate/writeback skill retained only as reference material.
- `legacy/stages/evaluate_and_writeback/`: historical quality-gate/writeback runtime retained only as reference material.
- `legacy/stages/synthesis/`: historical evidence-only synthesis helpers retained only as reference material.

### Archive
- `../bdr_agent_archive_2026-06-01/`: archived pre-preview implementation. Treat as reference-only.

### Test-only or local-only
- `tests/`
- `scripts/bdr_oz_dev_smoke.py`
- `src/bdr_agent/stages/outreach_composer/local_preview.py`
- `src/bdr_agent/feedback_loop/`

## Cleanup rules
Use this standalone layout as the source of truth for new BDR work:

1. New skill references should point to `skills/company-research/SKILL.md` or `skills/outreach-composer/SKILL.md`.
2. New runtime imports should come from `src/bdr_agent/`.
3. SQL belongs in `sql/`; writing/reference guidance belongs in `references/`; historical material belongs in `legacy/`.
4. Keep persisted stage values, BigQuery table names, GCS prefixes, and HubSpot property names stable unless the downstream systems are intentionally migrated.
5. After each cleanup pass, run targeted tests and at least one smoke BDR path before removing more.

## Validation commands
Useful local checks:

```bash
PYTHONPATH=/path/to/gtm-agents/bdr_agent/src python3 -m unittest discover /path/to/gtm-agents/bdr_agent/tests
cd /path/to/gtm-agents/bdr_agent && PYTHONPATH=/path/to/gtm-agents/bdr_agent/src python3 scripts/bdr_oz_dev_smoke.py --skip-dependency-imports --skip-bigquery-read --skip-exa-key-check --skip-step2-dry-run
git --no-pager diff --check
```

Use broader tests when changing imports, storage contracts, or stage paths.
