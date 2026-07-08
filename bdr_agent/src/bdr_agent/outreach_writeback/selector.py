"""Deterministic hook angle selection and hook text assembly."""

from __future__ import annotations

import re
from typing import Any

# Legal entity suffix pattern — stripped before display to keep brand names clean.
# Matches one or more consecutive legal-type suffixes at the end of a company label.
_LEGAL_SUFFIX_RE = re.compile(
    r"(?:,?\s+(?:llc|inc\.?|corp\.?|ltd\.?|co\.?|plc|gmbh|oda|s\.a\.|pvt\.?|nv|ag|se|pte"
    r"|incorporated|limited|corporation))+\.?\s*$",
    re.IGNORECASE,
)

# Facts matching these patterns are research template artifacts, not hook-ready copy.
# They indicate the research pipeline produced a verbatim product-doc excerpt or a
# generic observation rather than a concrete, named-company evidence point.
_GENERIC_FACT_PATTERNS = [
    re.compile(r"\bhas company.owned content related to\b", re.IGNORECASE),
    re.compile(r"\bcompany.owned content\b", re.IGNORECASE),
    re.compile(r"^the company is\b", re.IGNORECASE),
    re.compile(r"^the company has\b", re.IGNORECASE),
]

from bdr_agent.outreach_writeback.config import (
    HOOK_ANGLE_COMPANY_RESEARCH,
    HOOK_ANGLE_DEFAULT,
    HOOK_ANGLE_PERSON_RESEARCH,
    HOOK_ANGLE_PRODUCT_TRACTION,
)


def select_hook(
    *,
    synthesis_brief: str | None,
    evidence_packet: dict | None = None,
    company_research: dict | None = None,
    person_research: dict | None = None,
    resolved_company_domain: str | None = None,
) -> dict:
    """Select the strongest evidence-backed hook instead of blindly using first-match priority."""
    company_label = _company_label(company_research, resolved_company_domain, evidence_packet)
    contact_role = _clean_text((evidence_packet or {}).get("contact_role"))

    packet_product_hook = _evidence_packet_product_hook(
        company_label=company_label,
        evidence_packet=evidence_packet,
    )
    packet_company_hook = _evidence_packet_company_hook(
        company_label=company_label,
        evidence_packet=evidence_packet,
        contact_role=contact_role,
    )
    packet_hybrid_hook = _hybrid_hook(
        company_label=company_label,
        product_hook=packet_product_hook,
        company_hook=packet_company_hook,
        contact_role=contact_role,
    )
    packet_candidates = [
        hook for hook in (packet_hybrid_hook, packet_company_hook, packet_product_hook) if hook
    ]
    if packet_candidates:
        return max(packet_candidates, key=_hook_quality_score)

    product_hook = _product_traction_hook(company_label=company_label, company_research=company_research)
    brief_product_signal = _first_matching_brief_line(
        synthesis_brief,
        (
            "active user",
            "product user",
            "usage",
            "credits",
            "limit hit",
            "growth",
            "traction",
        ),
    )
    if brief_product_signal and product_hook is None:
        product_hook = _with_positioning({
            "hook_angle": HOOK_ANGLE_PRODUCT_TRACTION,
            "hook_text": _trim_hook(
                f"Saw some existing product activity at {company_label}: {brief_product_signal.rstrip('.')}. "
                "That can be a useful moment to make AI-assisted development more repeatable across engineering."
            ),
            "source_labels": ["synthesis_brief:product_traction"],
            "evidence_summary": brief_product_signal,
        })

    company_hook = _company_research_hook(
        company_label=company_label,
        company_research=company_research,
        synthesis_brief=synthesis_brief,
        contact_role=contact_role,
    )
    positioned_company_hook = _with_positioning(company_hook) if company_hook else None
    hybrid_hook = _hybrid_hook(
        company_label=company_label,
        product_hook=_with_positioning(product_hook) if product_hook else None,
        company_hook=positioned_company_hook,
        contact_role=contact_role,
    )
    candidates = [
        hook
        for hook in (
            hybrid_hook,
            positioned_company_hook,
            _with_positioning(product_hook) if product_hook else None,
        )
        if hook
    ]
    if candidates:
        return max(candidates, key=_hook_quality_score)

    person_hook = _person_research_hook(company_label=company_label, person_research=person_research)
    if person_hook:
        return _with_positioning(person_hook)

    return _with_positioning({
        "hook_angle": HOOK_ANGLE_DEFAULT,
        "hook_text": _trim_hook(
            f"I saw {company_label} may be a fit for faster, more auditable AI-assisted development workflows. "
            "[Your product] helps engineering teams move from individual AI usage to repeatable team workflows."
        ),
        "source_labels": ["default"],
        "evidence_summary": None,
    })


def _evidence_packet_product_hook(
    *,
    company_label: str,
    evidence_packet: dict | None,
) -> dict | None:
    signals = _as_list((evidence_packet or {}).get("top_internal_traction_signals"))
    scored_parts: list[tuple[int, str, str]] = []
    for signal in signals:
        signal = _as_dict(signal)
        signal_name = _clean_text(signal.get("signal"))
        value = signal.get("value")
        if not signal_name or _is_unknown(value):
            continue
        if value is False or value == 0 or value == "0":
            continue
        formatted_signal = _format_evidence_packet_signal(signal_name, value)
        if not formatted_signal:
            continue
        scored_parts.append(
            (
                _internal_signal_copy_score(signal_name),
                formatted_signal,
                f"evidence_packet:top_internal_traction_signals.{signal_name}",
            )
        )
    if not scored_parts:
        return None
    scored_parts.sort(key=lambda part: part[0], reverse=True)
    evidence_parts = [part for _, part, _ in scored_parts[:2]]
    source_labels = [source_label for _, _, source_label in scored_parts[:2]]
    evidence = "; ".join(evidence_parts)
    return _with_positioning({
        "hook_angle": HOOK_ANGLE_PRODUCT_TRACTION,
        "hook_text": _trim_hook(
            f"Saw some existing product activity at {company_label}, including {evidence}. "
            "That can be a useful moment to make AI-assisted development more repeatable across engineering."
        ),
        "source_labels": source_labels,
        "evidence_summary": evidence,
    })


def _evidence_packet_company_hook(
    *,
    company_label: str,
    evidence_packet: dict | None,
    contact_role: str | None = None,
) -> dict | None:
    facts = _as_list((evidence_packet or {}).get("top_company_research_facts"))
    for fact in facts:
        fact = _as_dict(fact)
        fact_text = _clean_text(fact.get("hook_ready_fact")) or _clean_text(fact.get("fact"))
        if not fact_text or fact_text == "unknown":
            continue
        if not _is_hook_ready_fact(fact_text):
            continue
        fact_text = _trim_evidence_fact(fact_text)
        if not fact_text:
            continue
        source_url = _clean_text(fact.get("source_url"))
        source_labels = ["evidence_packet:top_company_research_facts"]
        if source_url and source_url != "unknown":
            source_labels.append(source_url)
        return _with_positioning({
            "hook_angle": HOOK_ANGLE_COMPANY_RESEARCH,
            "hook_text": _trim_hook(
                f"I saw {fact_text.rstrip('.')}. "
                f"{_role_aware_positioning(company_label=company_label, contact_role=contact_role)}"
            ),
            "source_labels": source_labels,
            "evidence_summary": fact_text,
        })
    return None


def _product_traction_hook(*, company_label: str, company_research: dict | None) -> dict | None:
    tier_1 = (company_research or {}).get("tier_1_internal_metrics") or {}
    if tier_1.get("status") != "found":
        return None

    metrics = tier_1.get("metrics") or {}
    signal_candidates = [
        (
            "ai_requests_30d",
            _positive_number(metrics.get("ai_requests_30d")),
            "AI feature activity in the last 30 days",
            "company_research:tier_1_internal_metrics.metrics.ai_requests_30d",
        ),
        (
            "active_users_30d",
            _positive_number(metrics.get("active_users_30d")),
            "active users in the last 30 days",
            "company_research:tier_1_internal_metrics.metrics.active_users_30d",
        ),
        (
            "active_users_90d",
            _positive_number(metrics.get("active_users_90d")),
            "active users in the last 90 days",
            "company_research:tier_1_internal_metrics.metrics.active_users_90d",
        ),
        (
            "known_users_total",
            _positive_number(metrics.get("known_users_total")),
            "known product users",
            "company_research:tier_1_internal_metrics.metrics.known_users_total",
        ),
        (
            "avg_wau_last_4_weeks",
            _positive_number(metrics.get("avg_wau_last_4_weeks")),
            "average weekly active users over the last 4 weeks",
            "company_research:tier_1_internal_metrics.metrics.avg_wau_last_4_weeks",
        ),
        (
            "active_subscription_teams",
            _positive_number(metrics.get("active_subscription_teams")),
            "subscribed team activity",
            "company_research:tier_1_internal_metrics.metrics.active_subscription_teams",
        ),
        (
            "limit_hits_14d",
            _positive_number(metrics.get("limit_hits_14d")),
            "limit hits in the last 14 days",
            "company_research:tier_1_internal_metrics.metrics.limit_hits_14d",
        ),
        (
            "reload_dollars_90d",
            _positive_number(metrics.get("reload_dollars_90d")),
            "recent reloads",
            "company_research:tier_1_internal_metrics.metrics.reload_dollars_90d",
        ),
    ]
    scored_parts = [
        (_internal_signal_copy_score(signal_name), f"{_format_number(value)} {label}", source_label)
        for signal_name, value, label, source_label in signal_candidates
        if value is not None
    ]
    if not scored_parts:
        return None

    scored_parts.sort(key=lambda part: part[0], reverse=True)
    evidence_parts = [part for _, part, _ in scored_parts[:2]]
    source_labels = [source_label for _, _, source_label in scored_parts[:2]]
    evidence = "; ".join(evidence_parts)
    return {
        "hook_angle": HOOK_ANGLE_PRODUCT_TRACTION,
        "hook_text": _trim_hook(
            f"Saw some existing product activity at {company_label}, including {evidence}. "
            "That can be a useful moment to make AI-assisted development more repeatable across engineering."
        ),
        "source_labels": source_labels,
        "evidence_summary": evidence,
    }


def _company_research_hook(
    *,
    company_label: str,
    company_research: dict | None,
    synthesis_brief: str | None,
    contact_role: str | None = None,
) -> dict | None:
    tier_2 = (company_research or {}).get("tier_2_public_company_research") or {}
    for finding in tier_2.get("findings") or []:
        fact = _clean_text(finding.get("hook_ready_fact")) or _clean_text(finding.get("fact"))
        if not fact:
            continue
        if not _is_hook_ready_fact(fact):
            continue
        fact = _trim_evidence_fact(fact)
        if not fact:
            continue
        source_url = finding.get("source_url")
        source_labels = ["company_research:tier_2_public_company_research.findings"]
        if source_url:
            source_labels.append(str(source_url))
        return {
            "hook_angle": HOOK_ANGLE_COMPANY_RESEARCH,
            "hook_text": _trim_hook(
                f"I saw {fact.rstrip('.')}. "
                f"{_role_aware_positioning(company_label=company_label, contact_role=contact_role)}"
            ),
            "source_labels": source_labels,
            "evidence_summary": fact,
        }

    brief_company_signal = _first_matching_brief_line(
        synthesis_brief,
        (
            "engineering",
            "developer",
            "agent",
            "ai",
            "automation",
            "platform",
            "hiring",
            "careers",
            "productivity",
        ),
    )
    if brief_company_signal and _is_hook_ready_fact(brief_company_signal):
        return {
            "hook_angle": HOOK_ANGLE_COMPANY_RESEARCH,
            "hook_text": _trim_hook(
                f"I noticed this signal about {company_label}: {brief_company_signal.rstrip('.')}. "
                f"{_role_aware_positioning(company_label=company_label, contact_role=contact_role)}"
            ),
            "source_labels": ["synthesis_brief:company_research"],
            "evidence_summary": brief_company_signal,
        }
    return None


def _person_research_hook(*, company_label: str, person_research: dict | None) -> dict | None:
    for finding in (person_research or {}).get("findings") or []:
        fact = _clean_text(finding.get("fact") if isinstance(finding, dict) else finding)
        if not fact:
            continue
        return {
            "hook_angle": HOOK_ANGLE_PERSON_RESEARCH,
            "hook_text": _trim_hook(
                f"I saw your work related to {fact.rstrip('.')}. "
                f"If {company_label} is exploring AI-assisted development workflows, [your product] may be useful for making that work repeatable across the team."
            ),
            "source_labels": ["person_research:findings"],
            "evidence_summary": fact,
        }
    return None


def _hybrid_hook(
    *,
    company_label: str,
    product_hook: dict | None,
    company_hook: dict | None,
    contact_role: str | None,
) -> dict | None:
    if not product_hook or not company_hook:
        return None
    company_evidence = _clean_text(company_hook.get("evidence_summary"))
    product_evidence = _clean_text(product_hook.get("evidence_summary"))
    if not company_evidence or not product_evidence:
        return None
    role_positioning = _role_aware_positioning(
        company_label=company_label,
        contact_role=contact_role,
    )
    follow_on = (
        f"it could be timely to discuss how [your product] can help {role_positioning.removeprefix('[Your product] can help ')}"
        if role_positioning.startswith("[Your product] can help ")
        else role_positioning
    )
    return _with_positioning({
        "hook_angle": HOOK_ANGLE_COMPANY_RESEARCH,
        "hook_text": _trim_hook(
            f"I saw {company_evidence.rstrip('.')}. Given some existing product activity at {company_label}, "
            f"including {product_evidence}, {follow_on}"
        ),
        "source_labels": _unique_list(
            _as_list(company_hook.get("source_labels")) + _as_list(product_hook.get("source_labels"))
        ),
        "evidence_summary": f"{company_evidence}; {product_evidence}",
    })


def _company_label(
    company_research: dict | None,
    resolved_company_domain: str | None,
    evidence_packet: dict | None = None,
) -> str:
    company = (company_research or {}).get("company") or {}
    label = (
        _clean_text((evidence_packet or {}).get("company_name"))
        or _clean_text(company.get("company_name"))
        or _clean_text(company.get("name"))
        or _clean_text((evidence_packet or {}).get("resolved_company_domain"))
        or _clean_text(resolved_company_domain)
        or "your team"
    )
    return _display_company_label(label)


def _display_company_label(label: str) -> str:
    if label == "your team":
        return label
    # Strip legal entity type suffixes (llc, inc, oda, etc.) before display.
    # Apply iteratively so compound suffixes like "oda llc" are fully removed.
    while True:
        stripped = _LEGAL_SUFFIX_RE.sub("", label).strip().rstrip(",").strip()
        if not stripped or stripped == label:
            break
        label = stripped
    if "." in label or not label.islower():
        return label
    return label[:1].upper() + label[1:]


def _first_matching_brief_line(synthesis_brief: str | None, keywords: tuple[str, ...]) -> str | None:
    if not synthesis_brief:
        return None
    for raw_line in synthesis_brief.splitlines():
        line = (_clean_text(raw_line) or "").lstrip("-*•0123456789. ")
        if len(line) < 12:
            continue
        lower_line = line.lower()
        if any(keyword in lower_line for keyword in keywords):
            return _trim_sentence(line)
    return None


def _positive_number(value: Any) -> float | None:
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return None
    return numeric_value if numeric_value > 0 else None


def _format_number(value: float) -> str:
    if value.is_integer():
        return f"{int(value):,}"
    return f"{value:,.1f}"


def _format_evidence_packet_signal(signal_name: str, value: Any) -> str | None:
    label_by_signal = {
        "active_users_30d": "active users in the last 30 days",
        "ai_requests_30d": "AI feature activity in the last 30 days",
        "usage_units_30d": "recent usage unit consumption",
        "active_subscription_teams": "subscribed team activity",
        "known_users_total": "known product users",
        "active_users_90d": "active users in the last 90 days",
        "avg_wau_last_4_weeks": "average weekly active users over the last 4 weeks",
        "teams_total": "product teams",
        "has_recent_product_usage": "recent product activity",
        "has_product_usage": "existing product activity",
    }
    if signal_name == "has_paid_signal":
        return None
    label = label_by_signal.get(signal_name, signal_name.replace("_", " "))
    if value is True:
        return label
    if isinstance(value, (int, float)):
        return f"{_format_number(float(value))} {label}"
    return f"{value} {label}"


def _internal_signal_copy_score(signal_name: str) -> int:
    if signal_name in {"ai_requests_30d", "active_users_30d", "active_users_90d"}:
        return 80
    if signal_name in {"known_users_total", "avg_wau_last_4_weeks", "usage_units_30d"}:
        return 70
    if signal_name in {"active_subscription_teams", "teams_total"}:
        return 65
    if signal_name == "has_recent_product_usage":
        return 45
    if signal_name == "has_product_usage":
        return 30
    return 20


def _role_aware_positioning(*, company_label: str, contact_role: str | None) -> str:
    role = (contact_role or "").lower()
    if any(keyword in role for keyword in ("vp", "vice president", "cto", "engineering")):
        return (
            "[Your product] can help engineering leaders turn AI-assisted development from individual "
            "experimentation into repeatable team workflows."
        )
    if any(keyword in role for keyword in ("platform", "infrastructure", "observability", "devops")):
        return (
            "[Your product] can help platform teams make AI-assisted development workflows more repeatable "
            "and visible across engineering."
        )
    return (
        f"[Your product] can help {company_label} turn AI-assisted development from individual "
        "experimentation into repeatable team workflows."
    )


def _hook_quality_score(hook: dict) -> int:
    text = (hook.get("hook_text") or "").lower()
    score = 0
    if hook.get("hook_angle") == HOOK_ANGLE_COMPANY_RESEARCH:
        score += 45
    if any(label for label in _as_list(hook.get("source_labels")) if str(label).startswith("http")):
        score += 25
    if "existing product activity" in text:
        score += 10
    if any(keyword in text for keyword in ("ai", "agent", "engineering", "platform", "developer")):
        score += 15
    if any(unsafe in text for unsafe in ("paid product signal", "product traction", "organic product usage")):
        score -= 80
    if len(text) > 430:
        score -= 15
    return score


def _with_positioning(selected_hook: dict) -> dict:
    positioning = _positioning_metadata(
        hook_angle=selected_hook["hook_angle"],
        evidence_summary=selected_hook.get("evidence_summary"),
    )
    return {
        **selected_hook,
        "positioning_pillar": positioning["positioning_pillar"],
        "positioning_value_prop": positioning["positioning_value_prop"],
    }


def _positioning_metadata(*, hook_angle: str, evidence_summary: str | None) -> dict:
    if hook_angle == HOOK_ANGLE_PRODUCT_TRACTION:
        return {
            "positioning_pillar": "product_traction",
            "positioning_value_prop": (
                "Existing product activity can be a useful prompt to make AI-assisted development "
                "more repeatable across engineering."
            ),
        }
    if hook_angle == HOOK_ANGLE_COMPANY_RESEARCH:
        lower_evidence = (evidence_summary or "").lower()
        if any(keyword in lower_evidence for keyword in ("platform", "security", "compliance", "scale", "governance", "observability")):
            return {
                "positioning_pillar": "team_standardization_and_governance",
                "positioning_value_prop": (
                    "[Your product] gives teams more visibility and control over how AI-assisted development happens."
                ),
            }
        if any(keyword in lower_evidence for keyword in ("hiring", "growth", "delivery", "velocity", "shipping")):
            return {
                "positioning_pillar": "developer_velocity",
                "positioning_value_prop": (
                    "[Your product] reduces workflow friction so engineers can move faster with "
                    "AI-assisted development."
                ),
            }
        return {
            "positioning_pillar": "agentic_development_workflows",
            "positioning_value_prop": (
                "[Your product] helps teams turn AI-assisted development from individual experimentation into "
                "repeatable workflows."
            ),
        }
    return {
        "positioning_pillar": "agentic_development_workflows",
        "positioning_value_prop": (
            "[Your product] helps teams turn AI-assisted development from individual experimentation into "
            "repeatable workflows."
        ),
    }


def _is_hook_ready_fact(fact: str) -> bool:
    """Return False for research template artifacts that are unsuitable as hook openers.

    Filtered patterns:
    - "has company-owned content" / "company-owned content" — verbatim product-doc excerpts
      where the research pipeline confused the company's own product APIs/agents with developer
      AI tooling adoption.
    - "the company is ..." / "the company has ..." — opener lacks a named company, is too
      generic to anchor a personalised hook.
    """
    for pattern in _GENERIC_FACT_PATTERNS:
        if pattern.search(fact):
            return False
    return True


def _trim_evidence_fact(fact: str, max_chars: int = 200) -> str:
    """Trim an evidence fact to a safe length for hook assembly.

    Tries sentence-boundary trimming first so the hook opener reads as a complete thought.
    Returns an empty string if the trimmed result ends with '...' (incomplete sentence),
    so callers can fall through to the next candidate fact.
    """
    if len(fact) <= max_chars:
        return fact
    # Try to cut at a sentence-ending punctuation within the limit.
    match = re.search(r"[.!?](?=\s|$)", fact[:max_chars])
    if match:
        return fact[: match.start() + 1].strip()
    # No clean sentence boundary found; return empty so caller can skip this fragment.
    return ""


def _is_unknown(value: Any) -> bool:
    return value is None or value == "" or str(value).strip().lower() == "unknown"


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _unique_list(values: list) -> list:
    unique = []
    seen = set()
    for value in values:
        if value in seen:
            continue
        unique.append(value)
        seen.add(value)
    return unique


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", str(value)).strip()
    return cleaned or None


def _trim_sentence(value: str, max_chars: int = 260) -> str:
    cleaned = _clean_text(value) or ""
    if len(cleaned) <= max_chars:
        return cleaned
    return f"{cleaned[: max_chars - 3].rstrip()}..."


def _trim_hook(value: str, max_chars: int = 500) -> str:
    return _trim_sentence(value, max_chars=max_chars)
