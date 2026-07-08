---
name: bdr-immediate-rewrite
description: Use for Slack-thread BDR Outreach Composer rewrite requests when a human mentions @BDR Agent or asks for an immediate rewrite of a generated lead email body. Handles source-safe rewriting, preview posting, and safe HubSpot writeback for the tied record.
---
# BDR Immediate Rewrite
## Purpose
Use this skill when a BDR asks for an immediate rewrite in a Slack review thread for a generated Outreach Composer lead email body. The goal is to revise the body using the human's exact feedback, keep the generated email inside template boundaries, post the revised preview back to the thread, and write back only when the thread safely maps to one HubSpot object.
## Runtime sources
Use only these sources:
- Slack thread context, including the parent review message, replies, reactions, and any block-derived text or links.
- The linked or full lead brief and all ranked drafts from that review output.
- `bdr_agent/references/outreach_positioning_guide.md`.
- `bdr_agent/references/outreach_style_guide.md`.
- The exact Slack feedback text from the human request.
Do not broaden the task into unrelated repo docs, local dry-run scaffolding, archived notes, or whole directories. If a needed detail is not in the thread, linked lead brief, ranked drafts, or the two guide files above, treat it as unavailable rather than searching for more runtime policy.
## Identification and missing metadata
First identify the single lead, generated output, and HubSpot object tied to the Slack thread. Use the thread metadata, parent message content, lead brief link, ranked drafts, and available HubSpot record link before asking for anything.
Ask a narrow clarifying question only when you cannot identify exactly one lead/output/HubSpot record after checking the available context. Do not ask for a second approval if the human already tagged `@BDR Agent` with rewrite guidance.
If you can produce a safe revised preview but writeback is not safe because the HubSpot object is missing, ambiguous, or not tied to the thread, post the preview in Slack and explicitly skip HubSpot writeback.
## Rewrite process
Read the lead brief and all ranked drafts before writing. Consider the existing rank 2 and rank 3 drafts before drafting from scratch:
- If an alternate ranked draft already satisfies the feedback, use it or lightly adapt it.
- If none of the ranked drafts satisfy the feedback, write a new revision grounded in the same source evidence.
Use the positioning guide to choose a concrete product bridge, buyer problem, and product surface. Use the style guide for tone, opener shape, evidence handling, and CTA strength. Keep claims narrow and source-safe; do not add unsupported company strategy, intent, internal priorities, or private product usage.
## Email body boundaries
Rewrite only the body content that belongs inside the existing HubSpot template:
- No greeting line such as `Hi Name,`, `Hello`, or `Hey`.
- No sign-off such as `Best,`, `Thanks,`, or `Regards`.
- No sender name.
Keep the body concise, human, and usable as a cold outbound email. Prefer light, concrete personalization over broad company summaries. Use at most one soft question.
## Slack and writeback behavior
A human `@BDR Agent` rewrite prompt is approval to write back after you produce the revised body, provided the thread maps to exactly one safe HubSpot object. Do not require a second explicit approval.
Safe immediate HubSpot updates are limited to:
- `ai_hook_intro`: the revised body only, preserving the template boundaries above.
- `ai_personalized_at`: the writeback timestamp.
All unrelated fields remain forbidden, including names, email, owner, lifecycle, sequence/enrollment fields, source fields, and any property not listed as safe for this immediate flow.
Post the revised preview back to the same Slack thread whether or not writeback is safe. If writeback succeeds, say which safe fields were updated. If writeback is skipped, say it was skipped because the thread did not map to exactly one safe HubSpot object or because the environment lacks the required writeback gate.
## Final response checklist
Before posting or writing back, confirm:
- The revision directly addresses the exact Slack feedback.
- Rank 2 and rank 3 were considered before writing from scratch.
- The revised body has no greeting, sign-off, or sender name.
- Claims are supported by the lead brief, ranked drafts, Slack context, or the two guide files.
- Only `ai_hook_intro` and `ai_personalized_at` would be updated for immediate writeback.
- Missing metadata behavior stayed narrow: ask only for ambiguous identity, otherwise preview and skip unsafe writeback.
