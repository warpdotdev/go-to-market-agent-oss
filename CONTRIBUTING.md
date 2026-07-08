# Contributing

Thanks for your interest in contributing! This repository contains GTM
automation agents (BDR research/outreach, PLG upsell scoring, HubSpot tooling).

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Running tests

```bash
# PLG / root tests
python3 -m unittest discover -s tests -t .

# BDR agent tests (src layout)
PYTHONPATH="$PWD/bdr_agent/src" python3 -m unittest discover -s bdr_agent/tests -p 'test_*.py'
```

## Configuration (environment variables)

This codebase ships with **placeholder defaults** so it imports and tests
cleanly, but you must set real values via environment variables to run against
your own infrastructure. No real secrets or IDs are committed.

Core infrastructure:

- `GCP_PROJECT` — Google Cloud / BigQuery project ID (default `example-gcp-project`).
- `BQ_DATASET` — BigQuery dataset (defaults vary per component, e.g. `gtm_agents`, `prod`).
- `GCS_ARTIFACT_BUCKET` — GCS bucket for artifacts (default `example-artifacts-bucket`).
- `GCP_SERVICE_ACCOUNT_JSON` — service-account JSON for BigQuery auth (optional; ADC used otherwise).

HubSpot:

- `HUBSPOT_API_KEY` / `HUBSPOT_PRIVATE_APP_TOKEN` — private app token.
- `HUBSPOT_PORTAL_ID` — HubSpot portal ID (default `000000000`).
- `PLG_BDR_OWNER_A`, `PLG_BDR_OWNER_B`, `PLG_AE_OWNER_1..4` — routing owner IDs.
- `PLG_ACCOUNT_ROUTING_WORKFLOW_ID`, `PLG_CONTACT_ROUTING_WORKFLOW_ID`, `PLG_TIER1_SEQUENCE_ID`, `PLG_TIER2_SEQUENCE_ID`.

Integrations:

- `BDR_AGENT_EXA_API_KEY` — Exa search API key.
- `APOLLO_API_ENRICHMENT_API_KEY` — Apollo enrichment key.
- `SLACK_CHANNEL`, `TIER2_APPROVAL_SLACK_DM`, `PLG_SLACK_BOT_TOKEN`, `HUBSPOT_SLACK_WEBHOOK` — Slack delivery.
- `METABASE_URL`, `METABASE_API_KEY` — Metabase dashboards.
- `BDR_AGENT_STAGE_COMPLETION_WEBHOOK_URL`, `BDR_AGENT_STAGE_COMPLETION_WEBHOOK_SECRET` — stage handoff.

Secrets are read from the environment or a git-ignored `.env` file at the repo
root. **Never commit real secrets, credentials, or customer data.**

## Coding notes

- Prefer parameterized BigQuery queries (`QueryJobConfig` + query parameters)
  over string interpolation of user-controlled values.
- Keep example/placeholder values obviously fake.

## Pull requests

Run the test suites and ensure no secrets or personal data are introduced
before opening a PR. CI runs secret scanning, static analysis, and a
dependency audit.
