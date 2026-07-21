---
name: setup
description: Set up the GTM Agents repo — clone it if needed, then go from checkout to a working, validated install. Use this skill whenever a user says "run the setup skill", asks to set up, install, configure, or deploy this repo, or wants to get the PLG upsell / BDR agents running locally or as Oz cloud agents.
---

# GTM Agents Setup

Get the GTM Agents repo (cloning it if needed) to a validated, configured checkout, then point the user at the right deployment path. Everything ships with placeholder defaults, so install + tests work with **zero credentials** — real values are only needed for live runs.

## 1. Get the repo

Check whether the current directory is already a checkout of this repo (e.g. `README.md` titled "GTM Agents" and a `git remote` pointing at `go-to-market-agent-oss`). If so, skip cloning and work from here.

Otherwise, clone it and settle into the checkout:

```bash
git clone https://github.com/warpdotdev/go-to-market-agent-oss.git
cd go-to-market-agent-oss
```

If a clone already exists somewhere else on disk, prefer `cd`-ing into it over cloning a duplicate.

Work from the repo root for everything that follows. All paths and commands below are repo-root-relative.

## 2. Check prerequisites

- **Python 3.10+** is required (the `fastmcp` dependency needs it; CI runs 3.12). Check with `python3 --version`. On macOS, the Homebrew binary is typically `/opt/homebrew/bin/python3` if the system Python is too old.
- `git` (already satisfied by step 1).
- **Oz CLI** — only needed for Oz cloud runs and scheduling (step 7). Verify with `oz whoami`:
  - **Command not found** → if the [Warp app](https://docs.warp.dev/getting-started/installation-and-setup) is already installed, the CLI ships with it. Otherwise, prefer the standalone Oz CLI — there is no need to install the full Warp app just for the CLI. See [Installing the CLI](https://docs.warp.dev/reference/cli#installing-the-cli); on macOS: `brew tap warpdotdev/warp && brew install --cask oz`.
  - **Not authenticated** → run `oz login` (interactive), or for CI/headless environments export `WARP_API_KEY`.

If Python is too old, help the user install a newer one before continuing.

## 3. Install dependencies

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## 4. Validate the install (no credentials needed)

Run both test suites — they must pass on a fresh clone before any configuration:

```bash
# Shared hubspot_agent + PLG tests
python3 -m unittest discover -s tests -t .

# BDR agent tests (src layout)
PYTHONPATH="$PWD/bdr_agent/src" python3 -m unittest discover -s bdr_agent/tests -p 'test_*.py'
```

If anything fails here, stop and fix it (wrong Python version and missing deps are the usual causes) — do not proceed to configuration with a broken baseline.

## 5. Configure the environment

```bash
cp .env.example .env
```

`.env` is git-ignored. Ask the user which agent(s) they want to run and only walk through the variables that matter for those:

- **Everything that touches data (both agents)**: `GCP_PROJECT`, `BQ_DATASET`, `GCS_ARTIFACT_BUCKET`, plus Google Cloud auth — either `GCP_SERVICE_ACCOUNT_JSON` or Application Default Credentials (`gcloud auth application-default login`).
- **HubSpot sync / writeback (both agents)**: `HUBSPOT_PRIVATE_APP_TOKEN` (several aliases accepted — see `.env.example`), `HUBSPOT_PORTAL_ID`.
- **Slack digests / notifications**: `PLG_SLACK_BOT_TOKEN`, `SLACK_CHANNEL`. One-time Slack app setup (scopes, install, invite bot) is documented in `plg_upsell/README.md`.
- **PLG upsell only (optional)**: routing owner IDs, workflow/sequence IDs, `PLG_OUTREACH_PROMPT_URL` — see the commented blocks in `.env.example` and `plg_upsell/README.md`.
- **BDR agent only (optional)**: `BDR_AGENT_EXA_API_KEY` for public research. Writeback defaults are safe (`dry_run`, writeback disabled) — leave `BDR_AGENT_ALLOW_HUBSPOT_WRITEBACK` and the delivery mode alone until the user has reviewed dry-run output.

Handling secrets: never echo, cat, or print secret values, and never commit `.env`. Have the user paste tokens directly into `.env` themselves, or write them via commands that don't display the value.

The full variable reference lives in `.env.example` and `CONTRIBUTING.md`.

## 6. Optional live-readiness checks

Only when credentials are configured, offer these safe (read-only / dry-run) checks:

```bash
# PLG: preview the HubSpot sync and Slack digest without writing/posting
.venv/bin/python plg_upsell/scripts/scoring_sync.py --dry-run
.venv/bin/python plg_upsell/scripts/scoring_digest.py --dry-run

# BDR: local smoke check (skip flags avoid live dependencies)
PYTHONPATH="$PWD/bdr_agent/src" python3 bdr_agent/scripts/bdr_oz_dev_smoke.py \
  --skip-dependency-imports --skip-bigquery-read --skip-exa-key-check --skip-step2-dry-run
```

## 7. Point at deployment

Setup ends with a working local checkout. For production deployment, direct the user to the per-agent docs:

- **PLG upsell** (`plg_upsell/README.md`): warehouse mapping for the SQL/dbt models, one-time Slack app setup, and Oz cloud scheduling via the `hubspot-plg-sync` skill (`oz-dev schedule create ...` example included). One-time HubSpot property/sequence setup is in `.warp/skills/hubspot-plg-sync/SKILL.md`.
- **BDR agent** (`bdr_agent/README.md`): the `company_research -> lead_brief` runtime chain, HubSpot workflow trigger, storage tables (`bdr_agent/sql/tables/storage_tables.sql` + `sql/migrations/`), and per-stage skills under `.warp/skills/`.
- For Oz cloud runs, secrets go in the Oz environment (team secrets), not `.env`.

## 8. Report

Summarize for the user:
- Python version and install status
- Test results (both suites)
- Which `.env` variables were configured vs. left as placeholders, and what that disables
- Recommended next step for the agent(s) they care about
