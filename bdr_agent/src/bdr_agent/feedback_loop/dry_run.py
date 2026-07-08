"""Local dry-run classifier for BDR Slack feedback loop events.

This module intentionally has no Slack, HubSpot, BigQuery, or Oz side effects.
It encodes the feedback-loop contract so the planned automation can be tested
against representative messages before any credential-dependent plumbing exists.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from difflib import SequenceMatcher
import json
import re
from typing import Any


STYLE_GUIDE = "bdr_style_guide"
POSITIONING_GUIDE = "positioning_guide"
NO_GUIDE = "none"
HOOK_PROPERTY_NAME = "ai_hook_intro"
SOURCES_PROPERTY_NAME = "ai_hook_sources"
CREATED_AT_PROPERTY_NAME = "ai_personalized_at"
ALLOWED_IMMEDIATE_WRITEBACK_FIELDS = (HOOK_PROPERTY_NAME, CREATED_AT_PROPERTY_NAME)
FORBIDDEN_IMMEDIATE_WRITEBACK_FIELDS = (
    "email",
    "firstname",
    "lastname",
    "hubspot_owner_id",
    "lifecyclestage",
    "hs_sequences_enrolled_count",
    SOURCES_PROPERTY_NAME,
)
REACTION_SEMANTICS = {
    "+1": {"signal": "positive", "collect_for_learning": True, "allows_rewrite": False},
    "thumbsup": {"signal": "positive", "collect_for_learning": True, "allows_rewrite": False},
    "white_check_mark": {"signal": "landed", "collect_for_learning": True, "allows_rewrite": False},
    "pencil2": {"signal": "edit_requested", "collect_for_learning": True, "allows_rewrite": False},
    "x": {"signal": "negative", "collect_for_learning": True, "allows_rewrite": False},
    "thumbsdown": {"signal": "negative", "collect_for_learning": True, "allows_rewrite": False},
    "eyes": {"signal": "acknowledged", "collect_for_learning": False, "allows_rewrite": False},
}
STYLE_KEYWORDS = (
    "voice",
    "opener",
    "cta",
    "call to action",
    "specificity",
    "specific",
    "human",
    "bdr note",
    "sounds",
    "credibility",
    "credible",
    "too generic",
    "product-positioning paragraph",
)
POSITIONING_KEYWORDS = (
    "cloud agent",
    "cloud agents",
    "multi-harness",
    "persistent memory",
    "background orchestration",
    "slack",
    "github",
    "ci",
    "cron",
    "webhook",
    "api",
    "sdk",
    "product naming",
    "buyer problem",
    "product surface",
    "capability",
    "stale messaging",
    "governance",
    "control plane",
)

DEFAULT_ORIGINAL_DRAFT = (
    "I saw Example talking about AI agents for engineering work.\n\n"
    "Our platform helps teams make agent workflows easier to run, review, and control."
)
DEFAULT_DRY_RUN_SCENARIOS = [
    {
        "scenario_id": "no_signal",
        "original_draft": DEFAULT_ORIGINAL_DRAFT,
        "feedback_text": "",
        "reactions": [],
    },
    {
        "scenario_id": "thumbs_up",
        "original_draft": DEFAULT_ORIGINAL_DRAFT,
        "feedback_text": "",
        "reactions": [{"name": "+1", "user": "U_BDR"}],
    },
    {
        "scenario_id": "explicit_rewrite",
        "original_draft": DEFAULT_ORIGINAL_DRAFT,
        "feedback_text": (
            "@bdr-agent rewrite this so it sounds less like a product-positioning paragraph. "
            "Make the opener more concrete and use one human CTA."
        ),
        "rewrite_body": (
            "I saw Example's note about putting AI agents closer to engineering work.\n\n"
            "Our platform is relevant because it gives teams cloud runs, reviewable sessions, and Slack-triggered workflows "
            "without turning the first note into a product tour. Worth comparing notes?"
        ),
        "reactions": [{"name": "pencil2", "user": "U_BDR"}],
        "thread_contract": {
            "lead_id": "lead_123",
            "lead_brief_output_id": "bdr_output_123",
            "hubspot_object_type": "contact",
            "hubspot_object_id": "contact_123",
            "hook_property_name": HOOK_PROPERTY_NAME,
            "slack_channel_id": "C_REVIEW",
            "slack_message_ts": "1716240000.000100",
            "slack_thread_ts": "1716240000.000100",
        },
    },
    {
        "scenario_id": "lead_specific_redundant",
        "original_draft": DEFAULT_ORIGINAL_DRAFT,
        "feedback_text": (
            "For this lead only, avoid mentioning the webinar because I already know them. "
            "Also, the guide already says not to use infrastructure side."
        ),
        "final_landed_body": (
            "I saw Example has been testing AI agents in support workflows.\n\n"
            "Our platform could be relevant if those experiments need background cloud runs and reviewable handoffs."
        ),
        "feedback_kind": "lead_specific",
        "reactions": [{"name": "white_check_mark", "user": "U_BDR"}],
    },
]


def classify_feedback_scope(feedback_text: str, *, feedback_kind: str | None = None) -> dict[str, str]:
    """Classify feedback into the durable guide it could affect."""
    if feedback_kind == "lead_specific":
        return {
            "scope": "lead_specific",
            "guide_target": NO_GUIDE,
            "reason": "Feedback explicitly applies to one lead or account.",
        }
    normalized = _normalize_text(feedback_text)
    if not normalized:
        return {"scope": "no_signal", "guide_target": NO_GUIDE, "reason": "No feedback text."}
    if any(phrase in normalized for phrase in ("already in the guide", "guide already says", "covered in guide")):
        return {
            "scope": "redundant",
            "guide_target": NO_GUIDE,
            "reason": "Feedback appears already covered by an existing guide.",
        }
    if any(keyword in normalized for keyword in POSITIONING_KEYWORDS):
        return {
            "scope": "positioning",
            "guide_target": POSITIONING_GUIDE,
            "reason": "Feedback concerns product naming, buyer problem, product surface, or capability framing.",
        }
    if any(keyword in normalized for keyword in STYLE_KEYWORDS):
        return {
            "scope": "style",
            "guide_target": STYLE_GUIDE,
            "reason": "Feedback concerns voice, opener, CTA, specificity, or example credibility.",
        }
    return {
        "scope": "insufficient_evidence",
        "guide_target": NO_GUIDE,
        "reason": "Feedback is not yet general enough to route to a durable guide.",
    }


def classify_feedback_event(event: dict[str, Any]) -> dict[str, Any]:
    """Return the safe action plan for one normalized Slack feedback event."""
    feedback_text = str(event.get("feedback_text") or "").strip()
    original_draft = str(event.get("original_draft") or "").strip()
    rewrite_body = str(event.get("rewrite_body") or "").strip()
    final_landed_body = str(event.get("final_landed_body") or "").strip()
    reaction_names = _reaction_names(event.get("reactions") or [])
    reaction_details = [_reaction_detail(name) for name in reaction_names]
    meaningful_reactions = [
        detail for detail in reaction_details if detail["collect_for_learning"]
    ]
    delta = _delta_summary(original_draft=original_draft, final_landed_body=final_landed_body)
    has_signal = bool(feedback_text or rewrite_body or delta["changed"] or meaningful_reactions)
    if not has_signal:
        return {
            "scenario_id": event.get("scenario_id"),
            "action": "skip",
            "skip_reason": "no_signal",
            "has_signal": False,
            "reaction_signals": reaction_details,
            "guide_target": NO_GUIDE,
            "should_create_guide_pr": False,
            "should_end_silently": True,
        }

    route = classify_feedback_scope(
        feedback_text,
        feedback_kind=event.get("feedback_kind"),
    )
    immediate_action = _immediate_action(
        feedback_text=feedback_text,
        rewrite_body=rewrite_body,
        reaction_details=reaction_details,
        delta_changed=delta["changed"],
    )
    safe_writeback = _safe_writeback_contract(
        event=event,
        immediate_action=immediate_action,
        rewrite_body=rewrite_body,
    )
    durable_learning_candidate = route["guide_target"] != NO_GUIDE and route["scope"] not in {
        "lead_specific",
        "redundant",
        "insufficient_evidence",
    }
    return {
        "scenario_id": event.get("scenario_id"),
        "action": immediate_action,
        "has_signal": True,
        "reaction_signals": reaction_details,
        "feedback_scope": route["scope"],
        "guide_target": route["guide_target"],
        "routing_reason": route["reason"],
        "collected_fields": [
            field
            for field in (
                "original_draft",
                "feedback_text",
                "rewrite_body",
                "final_landed_body",
                "reactions",
            )
            if event.get(field)
        ],
        "original_to_final_delta": delta,
        "durable_learning_candidate": durable_learning_candidate,
        "guide_update_preconditions": [
            "same pattern recurs across unrelated leads or one severe broadly applicable regression",
            "existing guide content was checked first",
            "edit, sharpen, or delete stale guidance before appending",
            "only positioning guide or BDR style guide can change",
        ],
        "should_create_guide_pr": False,
        "should_end_silently": not durable_learning_candidate,
        "safe_writeback": safe_writeback,
    }


def run_dry_run(scenarios: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Classify dry-run scenarios without side effects."""
    return [classify_feedback_event(event) for event in deepcopy(scenarios or DEFAULT_DRY_RUN_SCENARIOS)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario-json-file",
        help="Optional JSON file containing a list of normalized feedback events.",
    )
    args = parser.parse_args(argv)
    scenarios = DEFAULT_DRY_RUN_SCENARIOS
    if args.scenario_json_file:
        with open(args.scenario_json_file, encoding="utf-8") as handle:
            scenarios = json.load(handle)
    print(json.dumps({"results": run_dry_run(scenarios)}, indent=2, sort_keys=True))
    return 0


def _immediate_action(
    *,
    feedback_text: str,
    rewrite_body: str,
    reaction_details: list[dict[str, Any]],
    delta_changed: bool,
) -> str:
    normalized = _normalize_text(feedback_text)
    if rewrite_body or "@bdr-agent" in normalized or "rewrite" in normalized:
        return "rewrite_and_writeback_if_safe"
    if delta_changed:
        return "record_final_landed_delta"
    if any(detail["signal"] in {"positive", "landed"} for detail in reaction_details):
        return "record_reaction_signal"
    return "collect_feedback_only"


def _safe_writeback_contract(
    *,
    event: dict[str, Any],
    immediate_action: str,
    rewrite_body: str,
) -> dict[str, Any]:
    thread_contract = event.get("thread_contract") or {}
    writeback_requested = immediate_action == "rewrite_and_writeback_if_safe"
    tied_to_known_record = all(
        thread_contract.get(field)
        for field in (
            "lead_id",
            "lead_brief_output_id",
            "hubspot_object_type",
            "hubspot_object_id",
            "hook_property_name",
            "slack_channel_id",
            "slack_thread_ts",
        )
    )
    hook_property_is_known = thread_contract.get("hook_property_name") == HOOK_PROPERTY_NAME
    can_writeback = bool(writeback_requested and rewrite_body and tied_to_known_record and hook_property_is_known)
    return {
        "writeback_requested": writeback_requested,
        "can_writeback_immediately": can_writeback,
        "requires_second_explicit_approval": False,
        "should_post_preview": writeback_requested,
        "preview_when_writeback_unsafe": writeback_requested and not can_writeback,
        "allowed_field_updates": list(ALLOWED_IMMEDIATE_WRITEBACK_FIELDS) if writeback_requested else [],
        "forbidden_field_updates": list(FORBIDDEN_IMMEDIATE_WRITEBACK_FIELDS),
        "preserve_template_boundaries": True,
        "record_revision_source": writeback_requested,
        "thread_contract_complete": tied_to_known_record,
        "missing_metadata_behavior": (
            "ask_only_if_no_single_lead_output_or_hubspot_record"
            if writeback_requested and not tied_to_known_record
            else "no_extra_approval_needed"
        ),
    }


def _reaction_names(reactions: list[Any]) -> list[str]:
    names = []
    for reaction in reactions:
        if isinstance(reaction, str):
            names.append(reaction)
        elif isinstance(reaction, dict) and reaction.get("name"):
            names.append(str(reaction["name"]))
    return names


def _reaction_detail(name: str) -> dict[str, Any]:
    semantics = REACTION_SEMANTICS.get(
        name,
        {"signal": "unknown", "collect_for_learning": False, "allows_rewrite": False},
    )
    return {"name": name, **semantics}


def _delta_summary(*, original_draft: str, final_landed_body: str) -> dict[str, Any]:
    if not original_draft or not final_landed_body:
        return {"changed": False, "similarity": None, "deleted_terms": [], "added_terms": []}
    original_words = _word_set(original_draft)
    final_words = _word_set(final_landed_body)
    return {
        "changed": _normalize_text(original_draft) != _normalize_text(final_landed_body),
        "similarity": round(
            SequenceMatcher(None, _normalize_text(original_draft), _normalize_text(final_landed_body)).ratio(),
            3,
        ),
        "deleted_terms": sorted(original_words - final_words)[:12],
        "added_terms": sorted(final_words - original_words)[:12],
    }


def _word_set(text: str) -> set[str]:
    return {
        word
        for word in re.findall(r"[a-z0-9][a-z0-9_-]+", _normalize_text(text))
        if len(word) > 3
    }


def _normalize_text(text: str) -> str:
    return " ".join(str(text or "").lower().split())


if __name__ == "__main__":
    raise SystemExit(main())
