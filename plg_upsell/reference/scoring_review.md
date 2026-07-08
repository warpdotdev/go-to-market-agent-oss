# PLG Upsell Scoring Review

This document explains how the PLG upsell scoring model surfaces domains and contacts for outreach.

> **Note:** All company names, contacts, and numeric figures in this document are **synthetic examples** for illustration only. They do not represent real customers, users, or data.

## What this model does

Every day, we score all eligible domains (companies using the product on paid self-serve plans — illustrative examples: `team`, `team_plus`, `business`) on a 0–100 scale. The score answers: **"Which domains should we reach out to this week?"** We then identify the best individual to contact at each domain.

## Part 1: Example companies (illustrative)

The examples below use fictional company names and made-up figures purely to show how the ranking reads. The key question for the GTM team on real data is: **Would you reach out to these? In this order?**

### Top domains by PQL score (synthetic)

| Rank | Company | Size | Industry | Active Users | PQL Score |
|------|-----------|------|---------------|-------------|-----------|
| 1 | Company A | 50 | IT & Services | 50 | **80** |
| 2 | Company B | 6 | Computer HW | 40 | **78** |
| 3 | Company C | 1,400 | IT & Services | 55 | **77** |
| 4 | Company D | 220 | IT & Services | 12 | **73** |
| 5 | Company E | 85 | IT & Services | 19 | **72** |

**Why these rank high:** heavy AI credit usage, strong week-over-week growth, and multiple users hitting credit limits. These are domains where the product is deeply embedded in daily workflows and users are pushing against plan boundaries.

**Top champion for #1 (Company A):** `first.last@example.com` — fullstack engineer, high credit usage, active most days, hit limits recently. Champion score ~70.

### Interesting profiles further down the list

- **A large enterprise** — high user breadth but low per-user AI usage; broad-but-shallow adoption. Is a very large, established org a realistic bottom-up target?
- **A well-known consumer brand** — highest breadth in the dataset, but near-zero AI depth and no urgency signals; correctly ranks low.
- **A fast-growing startup** — small but heavy per-user usage and strong urgency (new members, upgrades); early in the adoption curve.

## Part 2: How the scoring works

### How we get to the eligible domain set

We start from all work-email domains (excluding free/consumer email providers, `.edu`, and our own domain) and apply successive filters:

| Step | Filter |
|------|--------|
| 0 | All work-email domains (exclude free providers, `.edu`, own domain) |
| 1 | Has 2+ active non-fraud users in the last 30 days |
| 2 | Has an active paid self-serve plan (illustrative examples: team, team_plus, business) |
| 3 | Not already in the enterprise pipeline (no open or closed-won deal) |

The largest reduction comes from the active-user threshold (step 1); the plan filter (step 2) removes most remaining free-plan domains; pipeline exclusion removes a small remainder. Company metadata (name, industry, size, country) is enriched from a known-companies table when available.

### Scoring components

The PQL score (0–100) is the weighted sum of four categories. The weights below are illustrative examples — calibrate for your own funnel:

```
PQL Score = Breadth (20) + Depth (20) + Velocity (20) + Urgency (40)
```

**Breadth (20 pts)** — "How many people use the product?" Avg WAU over the last 4 weeks, percentile-ranked across eligible domains.

**Depth (20 pts)** — "How heavily are they using AI features?" Total AI credits (60%) + credits per user (40%), each percentile-ranked.

**Velocity (20 pts)** — "Is usage accelerating?" Average week-over-week credit growth, percentile-ranked.

**Urgency (40 pts)** — "Are there signals they need more than their current plan right now?" Four sub-signals, each percentile-ranked:
- **Credit limit hits (10 pts):** distinct users hitting AI credit limits in the last 14d.
- **Reload credit purchases (10 pts):** dollar amount of reload credits purchased in the last 14d.
- **Self-upgrades (10 pts):** users converting from free to paid in the last 30d.
- **Domain growth (10 pts):** new members joining domain teams in the last 14d.

### Champion scoring

For each domain, we score individual users (0–100) to find the best outreach contact. The weights below are illustrative examples — calibrate for your own funnel:

```
Champion = Credit Usage (20) + Activity (20) + Team Admin (20) + Limit Hits (20) + Role (20)
```

- Credit usage and activity are min-max normalized *within the domain* (ranked against peers, not globally).
- Team admin is binary: yes = 20 pts (billing authority).
- Limit hits are recency-weighted (last 7d counts 2x), normalized within domain.
- High-leverage roles (eng manager, devops/SRE, fullstack, backend) = 20 pts.

## Part 3: Questions for the GTM team

1. **Top-N gut check:** Looking at the top domains, would you actually reach out to all of them? Are any surprising?
2. **Company size signal:** We don't currently use company size as a scoring input. Should we?
3. **Urgency weighting:** Urgency is the heaviest category. Does that feel right, or should depth/velocity matter more?
4. **Big-but-shallow companies:** Large user counts with low per-user AI usage score low on depth and urgency. Is that correct, or is broad adoption at a name-brand company itself a signal?
5. **Industry diversity:** Most top-scoring domains are "IT & Services." Should we weight industry diversity, or is that just the reality of the user base?
6. **Champion selection:** Do the roles and profiles match who you'd want to reach out to? Is "team admin" overweighted vs. the heaviest individual user?
