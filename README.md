# GTM Agents

Home for Warp's GTM (go-to-market) agents — purpose-built systems that help reps move from raw signals to scored, actionable outreach.

## How to deploy
The fastest path: open this repo in [Warp](https://www.warp.dev) and paste this prompt into the agent input:
```text
Run the setup skill
```
The agent picks up [`.warp/skills/setup/SKILL.md`](.warp/skills/setup/SKILL.md), which installs dependencies, validates the install with the test suites, walks you through `.env` configuration for the agents you care about, and points you at per-agent deployment docs.

Or follow the manual steps below.
### Manual setup (reference)
**Prerequisites**
- **Python 3.10+** (the `fastmcp` dependency requires it; CI runs 3.12).
- For live runs only: a **Google Cloud project with BigQuery**, a **HubSpot private app token**, and (for Slack digests) a **Slack bot token**. The test suites run with no credentials.

**Install**
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

**Configure environment**
```bash
cp .env.example .env
```
`.env` is git-ignored. Every variable ships with a placeholder default (e.g. `example-gcp-project`) so the code imports and tests cleanly with no configuration; set real values to run against your own infrastructure. For Google Cloud auth, either set `GCP_SERVICE_ACCOUNT_JSON` or use Application Default Credentials (`gcloud auth application-default login`). See `.env.example` and `CONTRIBUTING.md` for the full variable reference.

**Run tests**
```bash
# Shared hubspot_agent + PLG tests
python3 -m unittest discover -s tests -t .

# BDR agent tests (src layout)
PYTHONPATH="$PWD/bdr_agent/src" python3 -m unittest discover -s bdr_agent/tests -p 'test_*.py'
```

Per-agent setup, configuration, and deployment instructions (warehouse mapping, HubSpot/Slack one-time setup, Oz cloud scheduling) live in each agent's README (`plg_upsell/README.md`, `bdr_agent/README.md`).

## Agents

| Agent | Status | Description |
| --- | --- | --- |
| [`plg_upsell/`](plg_upsell/) | Live | Identifies PLG domains likely to convert to enterprise, surfaces top champion contacts, syncs scores to HubSpot, and posts a weekly digest to `#your-plg-alerts-channel`. |
| [`bdr_agent/`](bdr_agent/) | In progress | V1 outbound personalization. Researches HubSpot leads/accounts, generates source-backed personalized hooks, and writes them back to HubSpot for rep review. Phase 1 (HubSpot ingest) scaffolded. |

Each agent owns its own scripts, SQL/dbt models, and reference material under its package directory. See the per-agent README for setup, configuration, and run instructions.

## Shared

- [`hubspot_agent/`](hubspot_agent/) — shared HubSpot CRM library used by every agent in this repo (workflows, lists, properties, duplicate detection, etc.).
- [`tests/`](tests/) — tests for the shared `hubspot_agent` library.
- [`.warp/skills/`](.warp/skills/) — Oz cloud agent skills. Each skill is namespaced by agent.
- `requirements.txt` — Python dependencies shared across agents.
- `.env.example` — template for all supported environment variables; copy to `.env` and fill in.

## Repo conventions

- Run commands and file paths in agent READMEs are **repo-root-relative** (e.g. `plg_upsell/scripts/scoring_sync.py`), so commands work from a single `cd` into the repo.
- Branch naming: `<your-name>/<short-description>` (e.g. `jordan/feat-outbound-hooks-skeleton`).
- New agents get their own top-level package (`agent_name/`) and a per-agent skill under `.warp/skills/`.
