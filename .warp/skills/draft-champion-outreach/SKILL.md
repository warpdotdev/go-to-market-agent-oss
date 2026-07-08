---
name: draft-champion-outreach
description: Draft personalized outbound emails for a PLG champion surfaced by the weekly digest. Use this skill when a BDR clicks the "Draft outreach" link on a Tier 1 champion card in #your-plg-alerts-channel and needs 3–4 ready-to-send email drafts tailored to whether the champion is an existing customer, warm prospect, or cold contact. The skill classifies the relationship, does a short research pass, produces drafts plus explicit reasoning, posts them back in the digest thread, and (phase 2) enrolls the contact in a HubSpot Sequence on send.
---

# Draft champion outreach

## What this does
Produces 3–4 ready-to-send outbound email drafts for a specific PLG champion, tailored to their relationship with your product. Output includes:

1. A short rationale the rep can audit (why this angle, what evidence was used, what was deliberately omitted).
2. 3–4 email drafts in send-ready form (subject + body + recommended send-day offset).
3. A `mailto:` link per draft with the subject and body pre-filled (phase 1).
4. A threaded Slack reply on the original digest message.

## When it runs
Triggered by the **Draft PLG champion outreach** Warp Drive Prompt that is linked from each Tier 1 champion card in the weekly digest. The prompt passes champion + company context as arguments.

## Input contract (prompt arguments)
The Warp Drive Prompt that launches this skill must pass all of:

- `champion_email` — e.g. `alex@acme.com`
- `champion_name` — best-effort name; fall back to local-part of email
- `domain` — company email domain, e.g. `acme.com`
- `company_name` — HubSpot company name
- `hubspot_company_url` — e.g. `https://app.hubspot.com/contacts/000000000/company/<id>`
- `hubspot_contact_url` — optional; link to the contact record if it exists
- `pql_score` — champion-level PQL score (0–100)
- `credits_30d` — credits consumed by this user in the last 30 days
- `is_team_admin` — `true`/`false`
- `role` — grouped survey role (`engineering_leader`, `ic_engineer`, `devops`, etc.) or `unknown`
- `urgency_signals` — comma-joined short tokens: `limit_hits_14d:N`, `reload_spend:$X`, `upgrades:N`, `new_members:N`
- `tier` — `tier_1` (only Tier 1 is surfaced today; included for future flexibility)

If any required argument is missing, stop and ask the rep to re-open the link from the digest.

## Required secrets
- `HUBSPOT_PRIVATE_APP_TOKEN` (or `GENERAL_HUBSPOT_APP_TOKEN` fallback) — CRM lookups
- `GCP_SERVICE_ACCOUNT_JSON` — BigQuery usage history
- `PLG_SLACK_BOT_TOKEN` — posting the drafts back in the digest thread
- `WARP_API_KEY` — only if you spawn sub-agents for deep research

## Steps

### 1. Classify the relationship
Query HubSpot for the contact and company, then assign exactly one class:

- **existing_customer** — company `lifecyclestage == customer` OR any deal in `closedwon` stage for this company.
- **warm_prospect** — company has an open deal, a logged call/meeting, or any inbound form submission in the last 90 days.
- **cold_contact** — none of the above.

Record the signals you used so you can cite them in step 4.

### 2. Research pass
For all classes:

- Pull the last 30-day usage summary from BigQuery (credits trend, limits hit, new seats joining) for `{domain}`.
- Pull the last 5 email/meeting engagements for `{champion_email}` from HubSpot Engagements API.
- For cold contacts only: find one verifiable public artifact for the champion — a GitHub profile, engineering blog post, conference talk, or Stack Overflow answer. No generic "I saw on LinkedIn"; anchor to something specific.

Cap the research step at 60 seconds. If you can't find a public artifact for a cold contact, mark it `no_hook_found` and draft a generic research-backed opener instead — do not fabricate.

### 3. Draft 3–4 emails
Playbook per class:

**existing_customer (expansion angle)**
- Email 1: reference the specific in-product signal (limit hits, WoW credit growth, new seats). Ask if it's time to discuss a team/enterprise plan.
- Email 2: quantify the seat or credit gap vs current plan. Propose a 20-min call with a specific time.
- Email 3: case study or relevant teammate rollout. One clear next step.
- Email 4 (optional): bump / break-up email, ≤40 words.

**warm_prospect (re-engage)**
- Email 1: re-engage on the specific signal ("saw your team hit limits 4× last week"). One question, no pitch.
- Email 2: short value hook tied to the role (`engineering_leader` → team observability; `ic_engineer` → personal productivity). Ask if a 15-min call is useful.
- Email 3: different angle (e.g. a teammate rollout), specific time offer.
- Email 4 (optional): bump.

**cold_contact (brief, research-backed)**
- Hard rules: ≤90 words per email, one question per email, one value hook grounded in the public artifact. No praise ("I love what you're building"), no CTA stacking, no "hope this finds you well".
- Email 1: reference the public artifact, connect it to your product, ask one question.
- Email 2: brief value point with a concrete usage stat from step 2. One soft ask.
- Email 3: final attempt. One sentence, one question.

### 4. Surface reasoning
Before the drafts, print 3–5 bullets covering:

- Which class you assigned and the concrete HubSpot signal(s) that drove it.
- The one piece of in-product evidence you chose to lead with, and why.
- What you deliberately left out (e.g. "didn't mention pricing — they haven't asked").
- For cold contacts: the public artifact you used (with URL).

Keep this honest and short. The rep should be able to audit the angle in 10 seconds.

### 5. Present to the rep
Post a threaded reply on the most recent PLG digest message in channel `C0EXAMPLE000`. Thread target resolution:

1. If a digest state file exists at `plg_upsell/scripts/.last_digest_ts` (written by `scoring_digest.py`), read `thread_ts` from it.
2. Otherwise call `conversations.history` on `C0EXAMPLE000`, filter to messages authored by `plg-upsell-bot`, take the most recent one whose blocks contain the `🏆 Tier 1 Champions` header, and use its `ts`.

Message shape:
- Header: `✍️ Draft outreach for {champion_email} — {class}`
- Reasoning block (bullets from step 4)
- One section per email draft with subject, body, and a `mailto:` link with prefilled subject and body
- Footer: `_Phase 1: review drafts manually. Phase 2 will add Send / Edit / Cancel buttons in-thread._`

### 6. Send / edit / cancel (phase 2 — do not build yet)
Not implemented in phase 1. Documented here as the target UX:

- A Slack action block below each draft with `Send`, `Edit`, `Cancel` buttons.
- `Send` → write the email via HubSpot (`POST /crm/v3/objects/emails` or `Engagements` API) and enroll the contact in a HubSpot Sequence carrying the remaining drafts.
- `Edit` → open a Slack modal with the subject/body pre-filled so the rep can tweak before sending.
- `Cancel` → mark the draft as discarded and log a note on the contact.

Requires a Slack interactivity backend (events subscription + interactivity request URL + a signed-secret handler). Tracked separately.

### 7. Follow-up enrollment (phase 2)
On `Send`, queue the remaining drafts as a HubSpot Sequence. Prerequisite: a Sequence template named `PLG champion outreach — {class}` must already exist in HubSpot. The skill should *not* create Sequence templates on the fly; if the template is missing, fall back to staging the remaining drafts as manual Scheduled Email tasks and notify the rep in-thread.

## Notes
- Never fabricate in-product signals. If the BigQuery lookup fails or the numbers are zero, say so in the reasoning block and lean harder on the public artifact (cold) or prior engagement history (warm).
- Never include the rep's internal notes or HubSpot comments in the outbound body.
- Respect HubSpot unsubscribe / GDPR flags. If `hs_email_optout_all` is set on the contact, stop immediately and report that in the Slack thread — do not draft anything.
- The skill should be idempotent: running it twice for the same champion on the same day should produce similar drafts but not re-post if a previous draft set already exists in the same thread in the last 4 hours. In that case, post `_Draft set already generated for {email} at {ts} — scroll up or re-run after 4h._` instead.
