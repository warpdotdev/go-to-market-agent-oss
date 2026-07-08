---
name: bdr-lead-brief
description: Legacy compatibility alias for BDR Outreach Composer; create and persist a lead_brief-compatible brief plus three ranked full cold-email body drafts using the current bdr_agent.stages.outreach_composer runtime contract.
---
# BDR lead brief
## Compatibility note
This skill path is the legacy compatibility alias for the Outreach Composer stage. Keep `.warp/skills/bdr-lead-brief/SKILL.md` importable and usable for saved configs while new configs should prefer `.warp/skills/bdr-outreach-composer/SKILL.md`. The runtime module, CLI, packet schema, persisted stage values, BigQuery columns, GCS paths, and HubSpot property names remain `lead_brief` until runtime aliases are integrated.
## Purpose
Use this skill for the `lead_brief` stage of the BDR agent pipeline. The human-readable stage concept is Outreach Composer, but the current compatibility/runtime module remains `bdr_agent.stages.outreach_composer` and the existing CLI packet shape is unchanged.
You are responsible for the judgment-heavy writing step: read the latest completed company research for `LEAD_ID`, write a concise human-readable lead brief, draft exactly three ranked full email bodies, evaluate the draft set, rewrite if needed, then hand the accepted packet to the deterministic `bdr_agent.stages.outreach_composer` CLI for validation, storage, configured delivery, and gated HubSpot writeback.
The deterministic CLI owns persistence and delivery gates. Do not write directly to BigQuery, GCS, Slack, or HubSpot outside the CLI.
## Compact prompt contract
The prompt should be small and identifier-based. Expect these stable fields:
- `LEAD_ID`
- `BDR_AGENT_STAGE=lead_brief`
- `BDR_AGENT_TRIGGER=stage_completion`
- `SOURCE_SYSTEM=agent_orchestrator_stage_completion`
- `SOURCE_STAGE=company_research`
- `PREVIOUS_RUN_ID`
- `PREVIOUS_OUTPUT_ID`
- Optional stable references such as `COMPANY_RESEARCH_OUTPUT_ID`, `COMPANY_RESEARCH_GCS_URI`, `BIGQUERY_TABLE`, and `BIGQUERY_ROW_ID`
Do not ask agent_orchestrator to include company details, full research JSON, synthesis content, style snapshots, giant prompt variables, or draft copy in the Oz prompt. BigQuery is the source of truth.
## Runtime preflight
Run from the `gtm-agents` repository root and use the prepared Python environment instead of the system `python`. Start each Oz run with:
```bash
PYTHON="${PYTHON:-/tmp/gtm-agents-venv/bin/python}"
if [ ! -x "$PYTHON" ] && [ -x /tmp/gtm-agents-company-research-venv/bin/python ]; then
  PYTHON=/tmp/gtm-agents-company-research-venv/bin/python
fi
if [ ! -x "$PYTHON" ]; then
  python3 -m venv /tmp/gtm-agents-venv
  PYTHON=/tmp/gtm-agents-venv/bin/python
  "$PYTHON" -m pip install -r requirements.txt
fi
```
export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}bdr_agent/src"
Use `$PYTHON` for all repo module commands below. Do not install dependencies into the system Python, and do not use `--break-system-packages`.
## Data retrieval
Run from the `gtm-agents` repository root.
Load company research from `example-gcp-project.gtm_agents.bdr_agent_company_research_outputs`. Prefer `COMPANY_RESEARCH_OUTPUT_ID` or `PREVIOUS_OUTPUT_ID` when supplied; otherwise select the latest completed row for `LEAD_ID`.
Use the repo reader or an equivalent BigQuery query:
```bash
$PYTHON - <<'PY'
from bdr_agent.stages.outreach_composer.company_research import load_company_research_output
import json
output = load_company_research_output(
    lead_id="<LEAD_ID>",
    company_research_output_id="<COMPANY_RESEARCH_OUTPUT_ID_OR_PREVIOUS_OUTPUT_ID>",
)
print(json.dumps(output, indent=2, sort_keys=True))
PY
```
If you use `bq`, query only the needed row. Do not paste large research JSON back into the Oz prompt.
Read exactly these two durable editable references before drafting:
- `bdr_agent/references/outreach_positioning_guide.md`: the positioning guide for frame, capability, buyer-problem, product-surface, and source-signal selection.
- `bdr_agent/references/outreach_style_guide.md`: the BDR style guide for tone, opener shape, evidence handling, BDR-specific preferences when available, and bad-vs-better examples.
Use the references as decision guides, not as prompt payload and not as copy to paste verbatim into the brief or email bodies. Do not create or consult a separate standalone pattern library.
Stop and report a blocker if no completed company research row exists, hydration failed, or the research is too incomplete to support a brief.
## Pre-draft reasoning check
Before drafting, make a compact reasoning note for yourself and reflect the outcome in the lead brief's narrative direction or evaluation. Do not include this reasoning note in the final email body. Cover:
- The strongest credible source signal, with the source URL/title if available and why it is stronger than other signals.
- How a human BDR would cite where the signal came from in one natural opener, without overexplaining the prospect's own company back to them.
- Which style-guide principle applies from `bdr_agent/references/outreach_style_guide.md`, including any owner-specific style when available.
- Which positioning-guide frame, buyer problem, concrete product capability, and product surface applies from `bdr_agent/references/outreach_positioning_guide.md`.
- What to exclude because it is internal-only, generic, unsupported, inaccurate, too feature-listy, or likely to sound like a product-positioning paragraph.
If this check cannot produce a credible public or buyer-safe source signal plus a concrete product bridge, mark the direction low-confidence or block for better research instead of forcing a personalized opener.
## Writing requirements
Produce:
1. A human-readable lead brief in Markdown.
2. Exactly three ranked full email body drafts.
These are email bodies, not intro hooks. The HubSpot property remains `ai_hook_intro` for compatibility, but the selected rank-1 content will be the full email body excluding the greeting line, sign-off line, and sender name because the HubSpot template supplies those.
Email body requirements:
- No `Hi Name,`, `Hello`, `Hey`, or other greeting line.
- No `Best,`, `Thanks,`, sign-off line, or sender name.
- Multi-paragraph body copy, usually 2 to 4 short paragraphs.
- Keep each email body to 85 words or fewer. The deterministic CLI enforces this limit, so revise before handoff rather than relying on validation to catch it.
- Natural, casual, first cold outbound email.
- One soft question where appropriate, never more than one question mark.
- Light personalization that does not feel creepy or over-researched. Reference only verifiable public facts and attribute observations with understated language like "I noticed", "I saw", or "It looks like".
- When referencing a public launch, product update, or company announcement, keep the opener light and conversational. Do not cram the product name, feature detail, and use case into one sentence, such as "I saw Example launched Product X with Feature Y for Use Case Z." They know their own launch. Prefer a shorter reference like "I saw Example's recent Product X launch" or "I saw the recent update around Product X", then use the next sentence to connect the broader relevance if needed.
- Keep claims narrow and factual. Do not imply detailed knowledge of the prospect's strategy, priorities, roadmap, internal challenges, or specific initiatives unless that exact information is explicitly present in source material.
- Avoid sweeping or insider-sounding openings such as "Your push into...", "Your investment in...", "The work your team is doing around...", or "One of the most ambitious efforts in the space...".
- If personalization is weak, prefer a strong industry observation over speculative company-specific commentary.
- If research is too thin, say that in the brief or evaluation, not in the final email body. Never write lines like "I did not find a strong public signal" to the prospect.
- Never write personalization that sounds like an insider assessment. If the sender could not reasonably know it from a quick review of public information, do not say it. When in doubt, understate rather than overstate.
- Do not overstate prospect intent, pain, evaluation, or internal product usage.
- Do not use em dashes.
- Avoid AI-ish enumeration, feature lists, and phrases like “I noticed X, especially around Y, Z, and W.”
- Use at most one concrete product, integration, or trigger detail per draft unless the source specifically supports more. Do not list Slack, Linear, GitHub, cron, webhooks, and CLI/API all in one body.
- Give each draft a concrete product bridge. Choose one mechanism or surface from `bdr_agent/references/outreach_positioning_guide.md` when useful. Keep it conversational and do not cram multiple surfaces into one body.
- Avoid vague bridge language such as "related problem", "similar layer", "infrastructure side", or generic "run, review, and control" unless paired with a concrete mechanism or surface that makes the claim clear.
- Paraphrase the selected frame across the three drafts. Do not repeat exact guide phrases such as "review, route, and control", "running agents in the cloud with more visibility", or "observable and governed" in more than one final body.
The three drafts should be meaningfully different and ranked by likely performance.
## Positioning frame guidance
Before drafting, select exactly one primary positioning frame from `bdr_agent/references/outreach_positioning_guide.md` and one applicable style-guide principle from `bdr_agent/references/outreach_style_guide.md`. Use the references to choose the frame and borrow the level of specificity and plainspoken language, but do not paste guide, deck, style-guide, or internal GTM phrasing verbatim into final email copy.
Supported frame reminders (example frames — customize for your product; see the positioning guide above):
- `capability_gap`: use when public evidence shows the prospect adopting a capability that tends to outgrow its first, ad-hoc implementation. Keep the angle around early experiments turning into real team workflows.
- `workflow_orchestration`: use when evidence points to distributed teams, automation, or workflows that should run beyond one person's machine. Keep the angle around running the work reliably from the right trigger with enough visibility to trust it.
- `governance`: use for leadership, platform ownership, security, compliance, cost discipline, or scaling adoption across teams. Keep the angle around visibility, permissions, and review; do not imply the buyer already has a governance problem.
- Product familiarity is quiet timing context, not a frame: use it only as a soft second sentence after stronger public evidence, and never expose exact usage, paid status, credits, activity, or anything that sounds like monitoring people. Prefer public evidence first. Safer body-copy options include "in case [your product] is already on your radar" or "there may already be some familiarity with [your product]".
Do not mix frames into a product tour. One frame can have a soft secondary clause, but the email should still feel like one observation and one simple reason to talk.
## Lead brief structure
Use concise Markdown:
# Lead brief: [Lead Name or lead_id] | [Company]
## Lead details
- **Lead:** ...
- **Role:** ...
- **Company:** ...
- **Domain:** ...
- **Lead source:** ...
- **Research status:** ...
- **Positioning frame:** ...
- **Style principle:** ...
- **Strongest source signal:** ...
- **Concrete product bridge:** ...
## Company research findings
Write 3 to 4 short titled paragraphs interpreting the useful signals. Include source links when public research supports a point. Use internal metrics only as safe qualitative context; do not dump exact internal numbers unless they are already approved for outreach.
## Suggested narrative direction
Write 1 to 2 paragraphs explaining the selected positioning frame, applicable style-guide principle, strongest source signal, concrete product bridge, why the frame matches the lead/company evidence, how a human BDR would cite the source, and what deck/internal/generic language must stay out of final body copy. Explicitly call out whether public evidence or quiet product-traction context is doing the work. If product traction is used, keep it qualitative and buyer-safe, and prefer stronger public evidence whenever available.
If public evidence is weak and product traction is absent or unsafe to mention, mark the direction low-confidence or block for better research instead of forcing a personalized opener.
## Ranked email body drafts
For each draft:
### 1. [Short label]
**Why this may work:** [one sentence]
**Body:**
[multi-paragraph body copy]
## Evaluation
Before persistence, evaluate the brief and all drafts against both references. If any draft exceeds 85 words, includes greeting/sign-off/sender boundaries, has more than one soft question, sounds over-automated, opens with sweeping company praise, implies unsupported inside knowledge, mentions weak/missing research in the body, repeats the same guide phrase across drafts, feature-lists integrations, or makes unsupported claims, rewrite before calling the CLI.
Final self-check:
- The opener cites a source-specific signal a human BDR would plausibly mention after a quick public read, without sounding creepy or like a company summary.
- The product bridge is concrete enough to explain [your product] through at least one mechanism, buyer problem, or product surface from `bdr_agent/references/outreach_positioning_guide.md`.
- The tone follows the applicable principle from `bdr_agent/references/outreach_style_guide.md` and sounds like a human BDR note rather than a positioning paragraph.
- The body avoids weak generic phrases such as "related problem", "similar layer", "infrastructure side", and ungrounded "run, review, and control".
- Rank 1 is the best commercial draft, not merely the safest draft, and the three drafts are meaningfully different.
The final evaluation status passed to the CLI must be `passed`, `rewritten_passed`, or `accepted`.
## Deterministic CLI handoff
After writing and evaluation, save a local JSON packet and run the CLI. The packet shape is:
```json
{
  "brief_markdown": "# Lead brief: ...",
  "email_body_drafts": [
    {
      "rank": 1,
      "label": "short label",
      "why_this_may_work": "one sentence",
      "body": "I noticed Example published a practical note on [topic relevant to your product].\n\n[Your product] is focused on [the specific step or problem your product addresses — customize]. Curious if this is useful to compare notes on?",
      "source_refs": ["https://example.com/source"]
    },
    {
      "rank": 2,
      "label": "short label",
      "why_this_may_work": "one sentence",
      "body": "A lot of [target persona] teams seem to be moving from [early-stage approach] toward [mature approach your product supports].\n\n[Your product] gives teams [one concrete capability — customize]. Thought this might be relevant if [the relevant problem or initiative] is coming up for Example.",
      "source_refs": []
    },
    {
      "rank": 3,
      "label": "short label",
      "why_this_may_work": "one sentence",
      "body": "I saw a few public signals that Example is paying attention to [relevant area — customize].\n\n[Your product] focuses on [one specific surface or workflow — customize]. Thought it might be relevant if your team is evaluating options in this area.",
      "source_refs": []
    }
  ],
  "evaluation": {
    "status": "passed",
    "notes": "No greeting/sign-off/sender boundaries; exactly three ranked email bodies; each body is 85 words or fewer; claims are narrow and source-backed."
  },
  "rewrite": {
    "attempted": false,
    "reason": null
  },
  "source_references": []
}
```
Dry-run validation and JSON output:
```bash
$PYTHON -m bdr_agent.stages.outreach_composer.cli \
  --lead-id "<LEAD_ID>" \
  --company-research-output-id "<COMPANY_RESEARCH_OUTPUT_ID_OR_PREVIOUS_OUTPUT_ID>" \
  --lead-brief-packet-json-file /tmp/lead_brief_packet.json
```
Final persistence and delivery:
Use the environment's configured delivery mode for the final persistence command. Do not override `BDR_AGENT_REVIEW_DELIVERY_MODE`, `BDR_AGENT_LEAD_BRIEF_DELIVERY_MODE`, or `BDR_AGENT_OUTREACH_COMPOSER_DELIVERY_MODE` in the command unless the prompt explicitly instructs a one-off override. When the prompt includes `BDR_AGENT_ALLOW_HUBSPOT_WRITEBACK=true`, include `--allow-hubspot-writeback` in the final persistence command so a configured `hubspot` or `both` delivery mode can write HubSpot properties:
```bash
$PYTHON -m bdr_agent.stages.outreach_composer.cli \
  --lead-id "<LEAD_ID>" \
  --company-research-output-id "<COMPANY_RESEARCH_OUTPUT_ID_OR_PREVIOUS_OUTPUT_ID>" \
  --lead-brief-packet-json-file /tmp/lead_brief_packet.json \
  --persist-bigquery \
  --allow-hubspot-writeback
```
Slack-only test delivery is a special case, not the default path. Only force Slack-only delivery when the prompt explicitly asks for Slack test mode:
```bash
BDR_AGENT_REVIEW_DELIVERY_MODE=slack \
$PYTHON -m bdr_agent.stages.outreach_composer.cli \
  --lead-id "<LEAD_ID>" \
  --company-research-output-id "<COMPANY_RESEARCH_OUTPUT_ID_OR_PREVIOUS_OUTPUT_ID>" \
  --lead-brief-packet-json-file /tmp/lead_brief_packet.json \
  --persist-bigquery
```
## CLI contract notes
The CLI validates exactly three ranked full email bodies and rejects bodies over 85 words, greeting/sign-off/sender boundaries, and common sweeping insider-assessment openings before persistence.
It writes the lead brief markdown to `gs://example-artifacts-bucket/bdr-agent/lead_brief/<run_id>/<output_id>.md`.
It inserts three rows into `bdr_agent_hooks` with `content_kind=email_body`, `email_rank=1..3`, and backward-compatible `hook_text` populated with the full email body.
Default delivery mode is safe dry-run delivery. `BDR_AGENT_ALLOW_HUBSPOT_WRITEBACK=true` alone does not write to HubSpot unless the effective delivery mode is `hubspot`, `both`, `slack-and-hubspot`, or another accepted HubSpot-enabled alias. For live Slack and HubSpot delivery, the Oz team/environment should include:
- `BDR_AGENT_REVIEW_DELIVERY_MODE=slack-and-hubspot` or `BDR_AGENT_REVIEW_DELIVERY_MODE=both`
- `BDR_AGENT_SLACK_BOT_TOKEN` preferred, or `SLACK_BOT_TOKEN`
- `BDR_AGENT_REVIEW_SLACK_CHANNEL_ID` preferred, or `BDR_AGENT_LEAD_BRIEF_SLACK_CHANNEL_ID`
- `BDR_AGENT_HUBSPOT_PORTAL_ID` for HubSpot record links in Slack when a contact/lead id is available
- `BDR_AGENT_HUBSPOT_API_KEY` preferred, or one of the fallback HubSpot token env vars supported by the runtime
For live writeback, the runtime writes only the rank-1 full email body to HubSpot `ai_hook_intro`; the HubSpot template supplies greeting, sign-off, and sender name. It also writes the lead brief/source URL to `ai_hook_sources` and the writeback timestamp to `ai_personalized_at`.
