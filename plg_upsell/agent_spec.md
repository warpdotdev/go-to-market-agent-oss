# PLG → Sales Upsell Agent — Project Spec

## Problem
The PLG base contains high-value accounts that are likely to convert to Enterprise, but the sales team has no systematic way to identify them or know who to contact within the domain.

## Goal
Build an agent system that:
1. Scores every eligible PLG domain daily on likelihood to convert to Enterprise
2. Identifies the best individual champion to contact at each domain
3. Delivers scored accounts into the sales workflow at a cadence the team can act on
4. Learns from outcomes to improve scoring over time

---

## Phase 1: Scoring + Champion Identification (Complete as of 3/23)

Output: [Domain scores]()

### Build Domain Qualified Account Scoring — PQA Score (0–100)
Composite score per eligible domain answering: "Which domains should we reach out to this week?"

**Eligibility criteria:**
- Work email domain (excludes free/consumer email providers, `.edu`, and your own company domain `your-company.com`)
- 2+ active non-fraud users in last 30 days
- At least one active paid self-serve plan (illustrative example plans: `team`, `team_plus`, `business`)
- Not already in the enterprise pipeline (no open/closed-won HubSpot deal)

**Scoring approach:**
- All metrics percentile-ranked across the eligible population (0.0–1.0)
- Weighted sum produces a 0–100 composite score

**Illustrative example weights — calibrate for your own funnel:**

| Signal | Weight | Rationale |
|--------|--------|-----------|
| Breadth: Avg WAU (4 wks) | 20% | Sustained multi-user adoption |
| Depth: AI credits total + per user (30d) | 20% | Raw product value consumed (split 60/40 within depth) |
| Velocity: WoW credit growth | 20% | Domain is accelerating now |
| Urgency: Users hitting credit limits (14d) | 10% | Immediate demand signal |
| Urgency: Reload credit $ (14d) | 10% | Willingness to pay beyond plan |
| Urgency: Free→paid upgrades (30d) | 10% | Individual conversion momentum |
| Urgency: New domain members (14d) | 10% | Product spreading organically |

### Build Champion Scoring (0–100)
For each scored domain, identify the top 1-3 product users in that domain that are most likely to respond and connect to a decision-maker.

**Illustrative example weights — calibrate for your own funnel:**
- AI credit usage: 20 — heaviest user = most invested
- Activity frequency: 20 — regular engagement vs one-time spike
- Is team admin: 20 — billing authority and org influence
- Hit credit limits (14d): 20 — personally felt the pain point
- High-leverage role: 20 — engineering manager, devops/sre, fullstack, backend

### Delivery (current)
- Python script queries BigQuery scoring tables, posts top N champions to Slack
- Slack channel: configured via `SLACK_CHANNEL` environment variable

See `README.md` for full data source inventory, SQL directory structure, and local run instructions.

---

## Phase 2: Validate Scoring + Explore Enrichment
*Phase 2 (before CRM integration)*

**Owner: [GTM Lead]**

### 2a. Validate scoring quality
**Goal:** Confirm the scoring model surfaces the right accounts before handing off to the CRM owner for integration.

- Share ranked domains with the GTM team for manual review
- Capture feedback: are the right domains surfacing? Are the champions plausible contacts?
- Iterate on scoring logic based on feedback
- Set up daily dbt model run to recalculate scores (append so we have a score history)

### 2b. Evaluate enrichment sources
**Goal:** Identify what additional external data we should bring in to make recommendations actionable / supplement scoring, so the team knows what's feasible and can integrate pipelines.

- **Company research** — TBD could include key leaders by role, company size/stage, recent news, public AI/automation statements (basically more detailed research than our KCs)
- **Champion enrichment** — e.g. LinkedIn profile link, title/seniority level, past roles and companies
- **Draft outreach messaging** — personalized based on usage signals + company context + BDR email template


---

## Phase 3: CRM Integration + Sales Process
*Phase 3 (after scoring is validated)*

**Owner: [CRM Owner] (design) + [GTM Lead] (implementation)**

**Goal:** Get scored accounts and enrichment data into HubSpot (or another interface) so BDRs and AEs can act on them in their normal workflow.

Components:
- **Assignment** — scored accounts assigned to AEs/BDRs via round-robin or territory rules
- **Cadence design** — how many net new accounts per rep per week, refresh frequency
- **Pipeline tracking** — recommendations flow through HubSpot stages (e.g. new → attempting → connected → meeting → qualified → deal)
- **Feedback capture** — BDR marks whether the account/champion was good or bad and why
- **Account research + enrichment** — productionize whichever enrichment sources proved viable in Phase 2 (company research, champion enrichment, draft messaging) so BDRs get a complete package per account

---

## Phase 4: Self-Improvement Loop
*To do: Only necessary after Phase 3 is shipped*

**Owner: [GTM Lead]**

**Goal:** Close the feedback loop so the model learns which recommendations convert.

Components (already designed, not yet built):
- **Recommendations log** — every surfaced recommendation written to BigQuery (`plg_upsell_recommendations_log`)
- **Outcome tracking** — dbt model joining recommendations to HubSpot pipeline data (`plg_upsell_recommendation_outcomes`)
- **Agent learns** — use BDR feedback (from Phase 3) and pipeline outcomes to improve scoring
- **Weekly performance digest** — scheduled agent posts hit rates by score band, signal contribution analysis
- **Weight tuning** — manual at first (team reviews digest, adjusts weights); automated once sample size supports it

---

## Open Questions
- What's the right number of net new accounts per rep per week?
- Should scored accounts go directly into HubSpot as contacts/deals, or should there be a staging/approval step?
- Which enrichment source gives us the best seniority + org chart data?
- How do we handle accounts that score high repeatedly but haven't been actioned — do they escalate, age out, or stay in queue?
- What's the minimum PQL score threshold to surface (vs. just top-N)?


