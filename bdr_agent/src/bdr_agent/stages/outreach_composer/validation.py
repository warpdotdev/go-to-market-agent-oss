"""Validation for skill-authored lead brief packets and email body drafts."""

from __future__ import annotations

import re
from typing import Any


GREETING_RE = re.compile(r"^(hi|hello|hey|dear)\b.*[,]?$", re.IGNORECASE)
SIGN_OFF_RE = re.compile(
    r"^(best|thanks|thank you|regards|cheers|sincerely|warmly|talk soon|best regards)[,!.-]?$",
    re.IGNORECASE,
)
SENDER_NAME_RE = re.compile(r"^[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}$")
MAX_EMAIL_BODY_WORDS = 85
SWEEPING_OR_INSIDER_OPENING_RE = re.compile(
    r"^\s*(?:your push into|your investment in|the work your team is doing around|one of the most ambitious efforts in the space)\b",
    re.IGNORECASE,
)


def normalize_lead_brief_packet(packet: dict) -> dict:
    if not isinstance(packet, dict):
        raise ValueError("lead brief packet must be a JSON object")
    brief_markdown = _required_string(packet.get("brief_markdown"), "brief_markdown")
    raw_drafts = packet.get("email_body_drafts") or packet.get("email_bodies") or packet.get("email_drafts")
    if not isinstance(raw_drafts, list):
        raise ValueError("email_body_drafts must be a list")
    normalized_drafts = [_normalize_draft(draft, index=index) for index, draft in enumerate(raw_drafts, start=1)]
    evaluation = _normalize_evaluation(packet.get("evaluation") or packet.get("lead_brief_eval_json"))
    rewrite = packet.get("rewrite") or packet.get("rewrite_metadata") or {"attempted": False}
    normalized = {
        "brief_markdown": brief_markdown,
        "email_body_drafts": normalized_drafts,
        "evaluation": evaluation,
        "rewrite": rewrite,
        "source_references": packet.get("source_references") or packet.get("source_refs") or [],
    }
    validate_lead_brief_packet(normalized)
    return normalized


def validate_lead_brief_packet(packet: dict) -> None:
    _required_string(packet.get("brief_markdown"), "brief_markdown")
    drafts = packet.get("email_body_drafts")
    if not isinstance(drafts, list) or len(drafts) != 3:
        raise ValueError("lead brief packet must include exactly three email_body_drafts")
    ranks = [draft.get("rank") for draft in drafts]
    if sorted(ranks) != [1, 2, 3] or len(set(ranks)) != 3:
        raise ValueError("email_body_drafts must be ranked exactly 1, 2, and 3")
    bodies = []
    for draft in sorted(drafts, key=lambda item: item["rank"]):
        validate_email_body_draft(draft)
        bodies.append(_normalize_whitespace(draft["body"]))
    if len(set(bodies)) != 3:
        raise ValueError("email_body_drafts must be meaningfully different")
    evaluation = packet.get("evaluation")
    if not isinstance(evaluation, dict):
        raise ValueError("evaluation must be a JSON object")
    if evaluation.get("status") not in {"passed", "rewritten_passed", "accepted"}:
        raise ValueError("evaluation.status must indicate the final packet passed before persistence")


def validate_email_body_draft(draft: dict) -> None:
    rank = draft.get("rank")
    if rank not in {1, 2, 3}:
        raise ValueError("email draft rank must be 1, 2, or 3")
    _required_string(draft.get("label"), f"email_body_drafts[{rank}].label")
    _required_string(draft.get("why_this_may_work"), f"email_body_drafts[{rank}].why_this_may_work")
    body = _required_string(draft.get("body"), f"email_body_drafts[{rank}].body")
    paragraphs = _paragraphs(body)
    if len(paragraphs) < 2:
        raise ValueError("email body drafts must be multi-paragraph body copy")
    if _word_count(body) > MAX_EMAIL_BODY_WORDS:
        raise ValueError(f"email body drafts must be {MAX_EMAIL_BODY_WORDS} words or fewer")
    if SWEEPING_OR_INSIDER_OPENING_RE.match(body):
        raise ValueError("email body drafts must not open with sweeping or insider-assessment personalization")
    if body.count("?") > 1:
        raise ValueError("email body drafts must contain no more than one soft question")
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    if GREETING_RE.match(lines[0]):
        raise ValueError("email body drafts must not include a greeting line")
    for line in lines:
        if SIGN_OFF_RE.match(line):
            raise ValueError("email body drafts must not include a sign-off line")
    if len(lines[-1].split()) <= 3 and SENDER_NAME_RE.match(lines[-1]):
        raise ValueError("email body drafts must not include the sender name")


def _normalize_draft(draft: Any, *, index: int) -> dict:
    if not isinstance(draft, dict):
        raise ValueError("each email body draft must be a JSON object")
    rank = draft.get("rank", index)
    try:
        rank = int(rank)
    except (TypeError, ValueError) as exc:
        raise ValueError("email draft rank must be an integer") from exc
    return {
        "rank": rank,
        "label": draft.get("label") or draft.get("email_label") or f"Option {rank}",
        "why_this_may_work": draft.get("why_this_may_work") or draft.get("rationale"),
        "body": draft.get("body") or draft.get("email_body") or draft.get("hook_text") or "",
        "source_refs": draft.get("source_refs") or draft.get("source_references") or [],
    }


def _normalize_evaluation(value: Any) -> dict:
    if value is None:
        raise ValueError("evaluation is required")
    if not isinstance(value, dict):
        raise ValueError("evaluation must be a JSON object")
    normalized = dict(value)
    if normalized.get("status") == "pass":
        normalized["status"] = "passed"
    return normalized


def _required_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} is required")
    return value.strip()


def _paragraphs(value: str) -> list[str]:
    return [paragraph.strip() for paragraph in re.split(r"\n\s*\n", value.strip()) if paragraph.strip()]


def _normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


def _word_count(value: str) -> int:
    return len(re.findall(r"\b[\w']+\b", value))
