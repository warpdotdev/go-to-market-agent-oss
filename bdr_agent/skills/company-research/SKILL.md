---
name: bdr-company-research
description: Run BDR Agent company research for a HubSpot lead when agent_orchestrator starts the company_research stage. Canonical skill for the company_research runtime.
---
# BDR Company Research
Use this skill when `agent_orchestrator` starts `company_research` for a HubSpot lead. Invoke the deterministic runner; it owns webhook payload validation, company research, persistence, and stage completion.
## Prompt fields
Use these prompt fields as CLI inputs:
- `BDR_AGENT_STAGE`: `company_research`
- `BDR_AGENT_TRIGGER`
- `SOURCE_SYSTEM`
- `LEAD_ID`
- `HUBSPOT_WORKFLOW_ID`

Recommended optional prompt fields:
- `CONTACT_ID`
- `COMPANY_ID`
- At least one of `COMPANY_DOMAIN`, `COMPANY_WEBSITE`, or `COMPANY_ALTERNATIVE_DOMAIN`
- `LEAD_CREATED_AT`
- `LEAD_OWNER_ID`
- `LEAD_SOURCE_DETAILED`
- `CONTACT_EMAIL`
- `CONTACT_FIRST_NAME`
- `CONTACT_LAST_NAME`
- `CONTACT_JOB_TITLE`
- `COMPANY_NAME`
- `COMPANY_INDUSTRY`
- `COMPANY_NUM_EMPLOYEES`
- `COMPANY_ICP_TIER`

Only `LEAD_ID` is required to start the runner. The HubSpot workflow should send the recommended fields whenever they are available because the runner uses them before consulting BigQuery. If optional fields are blank or absent, the runner falls back to BigQuery hydration for those blanks.
## Setup
Run the deterministic bootstrap before invoking the CLI. It works whether the agent starts in the `gtm-agents` repo root or in `bdr_agent/`, avoids probing a dependency-free system Python first, and exports the active BDR source path explicitly:

```bash
if [ -d "bdr_agent/src/bdr_agent" ]; then
  export GTM_AGENTS_ROOT="$PWD"
  export BDR_AGENT_ROOT="$PWD/bdr_agent"
elif [ -d "src/bdr_agent" ]; then
  export BDR_AGENT_ROOT="$PWD"
  export GTM_AGENTS_ROOT="$(dirname "$PWD")"
else
  echo "Run from the gtm-agents repo root or from gtm-agents/bdr_agent." >&2
  exit 1
fi

export BDR_AGENT_VENV="/tmp/gtm-agents-bdr-company-research-venv"
python3 -m venv "$BDR_AGENT_VENV"
"$BDR_AGENT_VENV/bin/python" -m pip install --upgrade pip
"$BDR_AGENT_VENV/bin/python" -m pip install -r "$GTM_AGENTS_ROOT/requirements.txt"

export PYTHON="$BDR_AGENT_VENV/bin/python"
export PYTHONPATH="$BDR_AGENT_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

"$PYTHON" - <<'PY'
import importlib
for module in ("bdr_agent", "google.cloud.bigquery", "google.cloud.storage", "httpx"):
    importlib.import_module(module)
print("BDR Company Research imports OK")
PY
```
## Run
Export prompt field values before invoking the runner, or pass literal values directly. Do not combine variable assignment and command invocation in one shell command.

```bash
$PYTHON -m bdr_agent.stages.company_research.cli \
  --lead-id "$LEAD_ID" \
  --trigger-source "$BDR_AGENT_TRIGGER" \
  --source-system "$SOURCE_SYSTEM" \
  --hubspot-workflow-id "$HUBSPOT_WORKFLOW_ID" \
  --lead-created-at "${LEAD_CREATED_AT:-}" \
  --lead-owner-id "${LEAD_OWNER_ID:-}" \
  --lead-source-detailed "${LEAD_SOURCE_DETAILED:-}" \
  --contact-id "${CONTACT_ID:-}" \
  --contact-email "${CONTACT_EMAIL:-}" \
  --contact-first-name "${CONTACT_FIRST_NAME:-}" \
  --contact-last-name "${CONTACT_LAST_NAME:-}" \
  --contact-job-title "${CONTACT_JOB_TITLE:-}" \
  --company-id "${COMPANY_ID:-}" \
  --company-name "${COMPANY_NAME:-}" \
  --company-domain "${COMPANY_DOMAIN:-}" \
  --company-website "${COMPANY_WEBSITE:-}" \
  --company-alternative-domain "${COMPANY_ALTERNATIVE_DOMAIN:-}" \
  --company-industry "${COMPANY_INDUSTRY:-}" \
  --company-num-employees "${COMPANY_NUM_EMPLOYEES:-}" \
  --company-icp-tier "${COMPANY_ICP_TIER:-}" \
  --persist
```
## Failure handling
If the CLI fails, exits with a non-zero code, or returns an error in the structured output, **stop and report the failure**. Include the full error output, `failure_reason`, and the exact command that was run. Do not attempt to diagnose or fix source code. Do not create branches, push commits, or open pull requests. Source code fixes must be handled in a separate implementation task.
## Result handling
The command prints structured JSON. Report `run_id`, `output_id`, `status`, `storage`, `stage_completion`, and `failure_reason` when present.

Successful persistence writes the company research artifact under `gs://example-artifacts-bucket/bdr-agent/company_research/`, writes canonical BigQuery rows, and lets the runner send the stage-completion handoff when the webhook environment is configured.
## BigQuery usage
The runner treats the webhook payload as the primary source of truth for fresh Lead/Contact/Company fields and domain resolution. BigQuery hydration is retained only as a fallback for fields that are blank or absent from the webhook payload; non-blank webhook values always win over BigQuery values. BigQuery is also still used after hydration for Tier 1 internal product-usage metrics and Tier 2 reuse lookup by `resolved_company_domain`.
## Tier 1 internal metrics interpretation
`tier_1_internal_metrics` is product-usage evidence for hook writing, not PLG upsell qualification. It should answer whether the resolved company domain has any product adoption, recent activity, AI usage, team adoption, paid signal, or only historical/no usage.

Do not treat `has_product_usage`, `has_recent_product_usage`, or `has_paid_signal` as eligibility gates. Paid and team usage are stronger signals, but free usage and historical usage should still be reported because synthesis owns deciding whether the evidence is useful for a hook.

The Tier 1 SQL is stored in `sql/queries/tier_1_metrics_query.sql`. It intentionally uses PLG upsell SQL only as source-table guidance and does not apply PLG filters such as minimum active users, active Build plan requirement, work-email-only eligibility, or Enterprise pipeline exclusion.
## Reference files
- `sql/queries/tier_1_metrics_query.sql`
- `sql/queries/hydration_query.sql`
- `sql/tables/storage_tables.sql`
- `references/company_research_taxonomy.md`
- `tests/fixtures/company_research_example_outputs/not_ready.json`
