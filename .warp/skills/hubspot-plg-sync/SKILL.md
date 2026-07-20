---
name: hubspot-plg-sync
description: Run the weekly PLG scoring sync and Slack digest. Pulls PQA/PQL scores from BigQuery, syncs them to HubSpot (companies + contacts), then posts the Monday morning digest to Slack. Use this skill when an Oz cloud agent needs to run the weekly BDR pipeline update.
---

# Weekly PLG HubSpot Sync + Slack Digest

## What this does
1. Pulls domain scores and champion contacts from BigQuery
2. Resolves and updates HubSpot companies + contacts with PQA/PQL properties
3. Applies dampening logic (bootstrap promotes immediately on first sync)
4. Excludes Target Account companies from PLG pipeline
5. Associates champion contacts to their companies
6. Legacy Clay enrichment and HubSpot workflow routing are disabled in `scoring_sync.py`. Dagster now owns Lead creation/update and writes the existing enrichment/routing workflow trigger fields plus Tier 1 Product Qualified Leads sequence intent. Tier 2 is held for now: no Lead creation, no enrichment trigger, and no sequence enrollment.
7. Posts the weekly Slack digest: `📊 This week at a glance` (action plays), `🆕 Newly Active Accounts`, `📈 Top Movers`, `🏆 Tier 1 PQAs`, `⚠️ Watch List`, `💰 Pipeline Wins`.
## Enrichment policy
Clay enrichment is expensive, so the Dagster sync restricts it to accounts BDRs will actually work:
- **Only enrich Tier 1** Product Qualified contacts for now. Tier 1 contacts receive `available_for_enrichment` and `clay_enrichment_queue` plus Product Qualified Leads sequence trigger fields.
- **Hold Tier 2** for now. Tier 2 contacts still get PQL property refreshes for reporting, but no Lead is created, no enrichment trigger is written, and no sequence enrollment is requested.
- **Never enrich Tier 3**. Tier 3 champions still get PQL property refreshes for reporting, but no Lead is created and no outreach trigger fields are written.

## Plays engine (📊 This week at a glance)
`plg_upsell/scripts/scoring_digest.py` generates up to five account-specific bullets from a typed candidate pool. Each candidate is ranked by priority, and at most one play per account surfaces so the top of the message isn't dominated by a single domain.

> All numeric thresholds below (cohort size, employee counts, credit growth, credit totals, member counts) are illustrative examples — calibrate them for your own funnel.

- **Tier 1 promotion** (priority 5) — any active account whose `pqa_tier` just flipped to `tier_1` this sync (`weeks_above == 0` + `tier == tier_1`). Shown as: `🏆 <Company> crossed Tier 1 this sync (score X, ±Y WoW)`.
- **Enterprise cohort** (priority 5) — fires when ≥ `ENTERPRISE_COHORT_MIN` (default 3) newly-active accounts have `company_size >= ENTERPRISE_SIZE_MIN` (default 2000). One themed bullet replaces the per-company enterprise bullets for those same domains, so BDRs see it as a segment-level signal rather than N near-identical one-offs. Shown as: `🏢 Enterprise wave: N large orgs (≥2,000 employees) newly active — <Company A>, <Company B>, … (combined ~E,EEE employees, U active product users)`. Cohort members still remain eligible for other plays (credit surge, land-and-expand, etc.) through their own domain.
- **Enterprise-scale account** (priority 4) — fires when an active account has `company_size >= ENTERPRISE_SIZE_MIN` (default 2000) AND is not already in the enterprise cohort above. Bullet names the company, employee count, active product users in the domain (30d), and current tier + score. Shown as: `🏢 <Company> is enterprise-scale (N,NNN employees, M active product users) — Tier X at S.S`. Engineer-specific count isn't in the dataset today; active product users (`active_users_last_30d`) is the closest proxy.
- **Credit surge** (priority 4) — WoW credit growth ≥ 30% and 30d credits ≥ 10,000.
- **Credit limits hit** (priority 4) — ≥ 1 user hit limits in the last 14d; reload $ included when non-zero.
- **Land-and-expand** (priority 3) — ≥ 2 new domain members joined in the last 14d.
- **Self-upgrades** (priority 3) — ≥ 1 user self-upgraded in the last 30d.
- **Tier 1 drift** (priority 3) — Tier 1 account with a negative WoW score delta.
- **Admin champion emerged** (priority 2) — Tier 1 account whose rank-1 champion is a team admin.

All bullets are pure signal framing (account + what changed + time window) — no prescriptive actions. BDRs already own the outreach motion for Tier 1/2 accounts, so the digest surfaces the what and lets reps decide the how. Time windows are explicit on every stat: `(14d)`, `(30d)`, `(4w avg)`, `WoW`.

## Required secrets
- `GENERAL_HUBSPOT_APP_TOKEN` (or `HUBSPOT_PRIVATE_APP_TOKEN`) — HubSpot private app token
- `PLG_SLACK_BOT_TOKEN` — Slack bot token for digest posting (uses `chat.postMessage`)
- `GCP_SERVICE_ACCOUNT_JSON` — GCP service account with BigQuery read access
- `HUBSPOT_SLACK_WEBHOOK` — (optional fallback) Slack incoming webhook URL
- `PLG_OUTREACH_PROMPT_URL` — (optional) Warp Drive Prompt share URL for the `draft-champion-outreach` skill. When set, each Tier 1 champion card in the digest renders a `✍️ Draft outreach` link that opens the prompt with the champion's context pre-filled. Leave unset to suppress the link.
- `PLG_PRODUCT_QUALIFIED_SEQUENCE_ID` — HubSpot sequence ID for the Product Qualified Leads sequence. Dagster stamps this on Tier 1 champion contacts so the HubSpot workflow can perform actual sequence enrollment. Find this in HubSpot CRM > Sequences (the numeric ID appears in the URL when you open a sequence).
- `PLG_PRODUCT_QUALIFIED_SEQUENCE_NAME` — optional display name for sequence trigger fields (e.g. `Tier 1 PQL`).

## One-time HubSpot setup (required before first live run)
1. **Create `pqa_bdr_routed_at` property** on the Company object (HubSpot Settings → Properties → Companies → Create property). Type: `Date and time`. This timestamp prevents double-routing on re-runs.
2. **Create `pqa_enriched_at` property** on the Company object. Type: `Date and time`. This timestamp gates Clay enrichment so a given PQA is only enriched once in its lifetime (Step 7.5 + 10.5). Without it, the preflight will flag every first-time T1/T2 account as eligible on every run.
3. **Fill in sequence IDs** in `plg_upsell/scripts/scoring_sync.py` (`TIER1_SEQUENCE_ID` / `TIER2_SEQUENCE_ID`). Find the IDs in HubSpot CRM → Sequences — the numeric ID is in the URL when you open a sequence.
4. **Confirm workflow re-enrollment is disabled** on your Account Router and Contact Router workflows (`PLG_ACCOUNT_ROUTING_WORKFLOW_ID` and `PLG_CONTACT_ROUTING_WORKFLOW_ID`) so contacts that drift back out and re-promote don't get enrolled a second time through the workflow itself.

## Steps

1. Ensure we are on the correct branch and dependencies are installed:
   ```
   git checkout main && git pull
   python3 -m venv .venv
   .venv/bin/python -m pip install -r requirements.txt
   ```
   > Note: Use Python 3.10+ — the `fastmcp` dependency requires it. On macOS the Homebrew binary is typically `/opt/homebrew/bin/python3`.

2. Write HubSpot credentials to `.env` (fallback for scripts that read from file):
   ```
   echo "HUBSPOT_PRIVATE_APP_TOKEN=${GENERAL_HUBSPOT_APP_TOKEN:-${HUBSPOT_PRIVATE_APP_TOKEN}}" > .env
   ```
   > `PLG_SLACK_BOT_TOKEN` is read directly from the environment — no `.env` entry needed.

3. Run the **scoring sync** (BQ → HubSpot):
   ```
   .venv/bin/python plg_upsell/scripts/scoring_sync.py
   ```
   Expected output: domains processed, companies updated, contacts updated, promotions/demotions count.

4. Run the **Slack digest** (posts to `#your-plg-alerts-channel`):
   ```
   .venv/bin/python plg_upsell/scripts/scoring_digest.py
   ```
   Expected output: "✓ Digest posted to #your-plg-alerts-channel".
   The digest's `🏆 Tier 1 PQAs` section renders a multi-line card per Tier 1 company (score + WoW, usage stats with explicit windows, urgency signals when present, and the top champion). When `PLG_OUTREACH_PROMPT_URL` is configured, the champion line carries a `✍️ Draft outreach` link that opens the Warp Drive Prompt invoking the `draft-champion-outreach` skill with the champion's context; the skill drafts 3–4 tailored emails and replies in the digest thread.

5. Report success with:
   - Number of companies updated
   - Number of contacts updated
   - Number of promotions → Active
   - Number of accounts BDR-routed (and how many skipped due to recent sales email)
   - Number of contacts flagged for Clay enrichment + number of companies stamped with `pqa_enriched_at` (pulled from the `Enrichment flagged` / `Enrichment stamped` lines in the sync summary)
   - Slack digest channel confirmation

6. If either script fails, return the exact error and stop. Do not proceed to the digest if the sync fails.

7. **If `PLG_OUTREACH_PROMPT_URL` is not set**, warn that Tier 1 champion cards will render without `✍️ Draft outreach` links. The digest still posts correctly — the links are additive.

## Flags
- Add `--dry-run` to either script to preview without writing to HubSpot or posting to Slack
- Add `--top-n 20` to `scoring_sync.py` to limit to top 20 domains (for testing)
- Add `--domains "domain1.com,domain2.com"` to `scoring_sync.py` to target specific domains
- `--route-newly-active` is deprecated and disabled; use the Dagster `plg_hubspot_sync` job for Lead routing
- `--approve-tier2-domains` is deprecated and disabled; Dagster records Tier 1/Tier 2 sequence intent on Leads
