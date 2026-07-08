---
name: bdr-self-improvement
description: Run the scheduled BDR Self-Improvement Agent. Use this skill for weekday Oz scheduled jobs that review BDR Slack feedback, recent sent/edited BDR emails, and product positioning-source changes, then open a tightly scoped PR to improve the Outreach Composer positioning guide or style guide only when evidence is generalizable.
---
# BDR Self-Improvement Agent
## Purpose
Use this skill for the weekday scheduled agent that improves BDR Outreach Composer references over time.
The agent is not part of the per-lead writing path. It should not rewrite active HubSpot records, generate one-off prospect copy, or add another durable pattern library. Its job is to learn from real feedback and source changes, then keep these two editable references current:
- `bdr_agent/references/outreach_positioning_guide.md`
- `bdr_agent/references/outreach_style_guide.md`
Open a PR only when a guide change is warranted. If there is not enough signal, end silently.
## Scheduled run contract
Expected Oz agent name: `BDR Self-Improvement Agent`.
Expected trigger: weekday schedule.
Expected repo context:
- Your GTM agents repository with this skill available.
- Access to `[your-marketing-repo]` for the canonical positioning source files named below.
Expected safe credentials:
- BigQuery read access to BDR output tables and recent HubSpot email data.
- Slack read access for the BDR review channel.
- GitHub access to create branches and PRs in `gtm-agents`.
- No HubSpot writeback permission is required for this skill.
Useful environment variables:
- `BDR_AGENT_SLACK_BOT_TOKEN` for Slack thread/reaction reads.
- `BDR_AGENT_SLACK_REVIEW_CHANNEL_ID` preferred for the BDR review channel.
- `BDR_AGENT_REVIEW_SLACK_CHANNEL_ID` accepted as a compatibility fallback.
- `GOOGLE_CLOUD_PROJECT=example-gcp-project` or fully qualified BigQuery table names.
Never print secret values. Check only whether required variables are present.
## Inputs
The schedule prompt may be minimal. Infer sensible defaults:
- `LOOKBACK_DAYS`: default to 1 weekday, or 3 on Monday to cover the weekend.
- `RUN_DATE`: default to today in the schedule timezone.
- `REVIEW_CHANNEL_ID`: use `BDR_AGENT_SLACK_REVIEW_CHANNEL_ID`, then `BDR_AGENT_REVIEW_SLACK_CHANNEL_ID`.
- `MARKETING_REPO_PATH`: use the prompt value if provided; otherwise inspect likely sibling checkout paths before cloning.
- `DRY_RUN`: if true, produce a local summary but do not create a branch or PR.
## Canonical positioning sources
Use `[your-marketing-repo]` source files first. Treat public URLs as canonical route labels for humans, not as arbitrary web-scrape targets.
Only these public pages and repo sources are in scope for scheduled positioning-source review (replace with your product's equivalent routes):
- Homepage: homepage source files and product callout data.
- Enterprise page: enterprise content page.
- Core product page: main product surface content page.
- AI agent page: AI/agent surface content page.
- Platform page: cloud platform / orchestration content page, including shared platform block data files when referenced by that page.
Do not explore unrelated marketing pages, changelogs, docs, launch copy, blog posts, or versioned experiment pages unless a human explicitly adds them to the schedule prompt for a one-off run or updates this skill.
If CMS-backed runtime content is available through the repo tooling, prefer reviewed current CMS page data for the configured routes over checked-in fallbacks. Do not broaden the route set.
## Preflight
Run from the `gtm-agents` repository root.
1. Confirm the working tree is clean except for known local notes or untracked investigation files that are unrelated to this run. Do not overwrite user work.
2. Ensure the base branch is current before creating a run branch.
3. Create a branch named `bdr/outreach-learning-YYYY-MM-DD`.
4. Read exactly the two durable guide files before collecting evidence, so you can identify redundant guidance:
   - `bdr_agent/references/outreach_positioning_guide.md`
   - `bdr_agent/references/outreach_style_guide.md`
5. Confirm credentials by presence only. Do not print Slack tokens, GitHub tokens, HubSpot tokens, or service-account JSON.
## Evidence collection
Collect enough context to decide whether guide changes are justified. Prefer structured source data over anecdotes.
### Slack review threads
Read review-channel parent messages and replies in the lookback window.
Normalize each event into:
- parent message identifiers: channel, parent timestamp, thread timestamp;
- lead and output identifiers if present;
- original rank-1 body and ranked drafts if available;
- feedback text;
- generated rewrite if the thread contains one;
- final landed body if a BDR pasted or confirmed it;
- reactions and actor IDs.
Use these reaction semantics:
- `+1` / `thumbsup`: positive signal only.
- `white_check_mark`: landed or review complete signal.
- `pencil2`: edit requested, but text is needed before learning.
- `eyes`: acknowledgement only, skip when alone.
- `x` / `thumbsdown`: negative signal only when paired with text or a concrete edit.
Skip no-signal messages silently.
### Recent BDR emails
Read recent BDR-sent or BDR-edited outreach from BigQuery. Start with:
- `example-gcp-project.your_dagster_dataset.hubspot_emails`
- `example-gcp-project.gtm_agents.bdr_agent_hooks`
Join or compare where identifiers allow:
- generated original;
- rewritten preview;
- final landed or sent version;
- BDR owner;
- lead/company metadata;
- source references;
- selected positioning frame and style version.
If the schema differs, inspect only metadata and a small sample before adapting the query. Do not query more rows than needed for the lookback window.
### Positioning source changes
Check current product messaging sources that should inform the positioning guide:
- the configured canonical public pages and `[your-marketing-repo]` source files listed in `Canonical positioning sources`;
- `references/outreach_positioning_guide.md`.
Do not scrape arbitrary websites. Prefer the marketing repo because it is a reviewable source of truth, and use public URLs only to label the source-route relationship in notes or PR context.
## Analysis
Create a private working note for the run. It can live in `/tmp` and should not be committed unless it becomes useful PR context.
For each evidence item, classify it as:
- `style-guide`: voice, opener shape, CTA strength, specificity, example credibility, phrase naturalness, BDR owner style.
- `positioning-guide`: product naming, buyer problem framing, product surface selection, concrete capability framing, stale or inaccurate product messaging.
- `lead-specific`: useful for one lead only, not a durable guide change.
- `redundant`: already covered by the guide.
- `insufficient-evidence`: too weak, ambiguous, or isolated.
Compare original to rewrite/final versions and track:
- deleted generic phrases;
- added source specificity;
- changed opener shape;
- changed CTA;
- product/capability substitutions;
- source-citation changes;
- whether the change made the product bridge more concrete.
Apply the recurrence/generalization test before editing:
- repeated evidence across unrelated leads; or
- one severe broadly applicable regression; or
- a clearly stale positioning claim contradicted by current reviewed source copy.
If a guide already contains the right instruction and outputs still miss it, do not append a duplicate. Prefer sharpening examples, making retrieval cues clearer, or leaving a PR note that runtime retrieval/eval may need attention later.
## Editing rules
Modify only:
- `bdr_agent/references/outreach_positioning_guide.md`
- `bdr_agent/references/outreach_style_guide.md`
Do not create:
- a third durable pattern library;
- per-lead example archives;
- generated prospect copy files;
- HubSpot writeback scripts;
- Slack event handlers.
Prefer edits in this order:
1. tighten stale or vague existing guidance;
2. replace weak examples with better generalized examples;
3. delete guidance that is contradicted by current source material;
4. append a new principle only when no existing section can hold it cleanly.
Keep examples generalized and source-safe. Do not include private Slack user IDs, internal exact usage metrics, sensitive customer details, or unsupported claims.
## PR behavior
Open at most one PR per scheduled run, and only if a guide file changed.
PR requirements:
- Branch name: `bdr/outreach-learning-YYYY-MM-DD`.
- Title pattern: `Improve BDR outreach guides from recent feedback YYYY-MM-DD`.
- Body includes:
  - evidence window;
  - sources reviewed;
  - concise summary of changed guide principles;
  - examples of generalized deltas, not private raw thread dumps;
  - validation commands and results;
  - a note that no HubSpot records were written.
- Request the designated reviewers using known GitHub handles when available. If a reviewer handle cannot be resolved, mention them in the PR body and state that manual reviewer assignment is needed.
- Add `Co-Authored-By: Oz <oz-agent@warp.dev>` at the end of the PR description.
Post to Slack only if a PR is opened. Include the PR link and a short summary in the original BDR review channel or configured reporting channel. If no PR is opened, do not post a Slack message.
## Validation
Before opening a PR:
- Run `git diff --check`.
- Run the feedback-loop dry run: `python3 -m bdr_agent.feedback_loop.dry_run`.
- If tests are available in the environment, run `python3 -m unittest bdr_agent.tests.feedback_loop.test_dry_run`.
- Inspect the final diff to confirm only allowed guide files changed.
If validation fails, do not open a PR. Leave the branch with changes and report the blocker in the run output.
## Stop conditions
End silently without a PR when:
- only no-signal Slack events exist;
- feedback is lead-specific or redundant;
- a pattern appears once and is not severe;
- source changes are not relevant to BDR outreach positioning;
- current guides already cover the finding well;
- required read credentials are unavailable.
Block and report rather than guessing when:
- the working tree contains user changes that would be overwritten;
- Slack or BigQuery access is missing and no useful local evidence exists;
- `[your-marketing-repo]` is required by the prompt but unavailable;
- reviewer assignment or PR creation fails after guide changes were made.
