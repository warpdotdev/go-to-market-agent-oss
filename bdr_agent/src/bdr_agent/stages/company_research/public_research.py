"""Tier 2 public company-owned research.

This module keeps Exa-backed research deterministic and testable: query
construction, response normalization, source filtering, and output shaping all
live here, while live calls read only the BDR-specific Exa key.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import os
import re
from typing import Any
from urllib.parse import urlparse

from bdr_agent.stages.company_research.config import (
    DEFAULT_TIER_2_EXA_MAX_QUERIES,
    DEFAULT_TIER_2_EXA_NUM_RESULTS,
    EXA_API_KEY_ENV_VAR,
    EXA_SEARCH_URL,
    POSITIONING_TAXONOMY_VERSION,
    TIER_2_STRATEGY,
)


@dataclass(frozen=True)
class Tier2QuerySpec:
    signal_type: str
    terms: tuple[str, ...]
    source_hint: str


POSITIONING_QUERY_SPECS = (
    Tier2QuerySpec(
        signal_type="agentic_development",
        terms=("agentic development", "AI agents", "agent automation"),
        source_hint="agentic development, AI agents, and agent automation",
    ),
    Tier2QuerySpec(
        signal_type="multi_agent_orchestration",
        terms=("multi-agent orchestration", "agent orchestration", "AI orchestration"),
        source_hint="multi-agent orchestration and AI orchestration",
    ),
    Tier2QuerySpec(
        signal_type="developer_productivity",
        terms=("developer productivity", "developer experience", "engineering productivity"),
        source_hint="developer productivity and engineering productivity",
    ),
)


def run_fresh_tier_2_public_research(
    *,
    resolved_company_domain: str,
    company_name: str | None = None,
    client: Any | None = None,
    api_key: str | None = None,
    max_queries: int = DEFAULT_TIER_2_EXA_MAX_QUERIES,
    num_results: int = DEFAULT_TIER_2_EXA_NUM_RESULTS,
    timeout_seconds: float = 20.0,
) -> dict:
    """Run low-count, company-domain scoped Exa searches and return the Tier 2 block."""
    normalized_domain = _normalize_domain(resolved_company_domain)
    if not normalized_domain:
        return _tier_2_error("resolved_company_domain is required for Tier 2 public research.")

    effective_api_key = api_key if api_key is not None else os.getenv(EXA_API_KEY_ENV_VAR)
    if not effective_api_key:
        return _tier_2_error(f"{EXA_API_KEY_ENV_VAR} is not configured for Tier 2 public research.")

    findings: list[dict] = []
    source_attempts: list[dict] = []
    total_cost = 0.0
    saw_results = False
    had_error = False
    errors: list[str] = []

    for query_spec in POSITIONING_QUERY_SPECS[:max_queries]:
        payload = build_exa_search_payload(
            resolved_company_domain=normalized_domain,
            query_spec=query_spec,
            num_results=num_results,
        )
        attempt = {
            "query": payload["query"],
            "scope": normalized_domain,
            "num_results_requested": num_results,
            "result_count": 0,
            "kept_findings": 0,
            "cost_dollars": 0,
            "status": "not_run",
            "request_id": None,
        }
        try:
            response_json = _post_exa_search(
                payload=payload,
                api_key=effective_api_key,
                client=client,
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:
            had_error = True
            attempt["status"] = "error"
            attempt["error"] = str(exc)
            errors.append(str(exc))
            source_attempts.append(attempt)
            continue

        results = response_json.get("results") or []
        cost_dollars = _cost_to_float(response_json.get("costDollars"))
        kept_findings = _findings_from_results(
            results=results,
            query_spec=query_spec,
            resolved_company_domain=normalized_domain,
            company_name=company_name,
            starting_index=len(findings) + 1,
        )
        findings.extend(kept_findings)
        total_cost += cost_dollars
        saw_results = saw_results or bool(results)
        attempt.update(
            {
                "result_count": len(results),
                "kept_findings": len(kept_findings),
                "cost_dollars": cost_dollars,
                "status": "success",
                "request_id": response_json.get("requestId"),
            }
        )
        source_attempts.append(attempt)

    if findings:
        status = "partial" if had_error else "found"
    elif had_error and not saw_results:
        status = "error"
    elif had_error:
        status = "partial"
    else:
        status = "not_found"

    output = {
        "status": status,
        "strategy": TIER_2_STRATEGY,
        "positioning_taxonomy_version": POSITIONING_TAXONOMY_VERSION,
        "reuse_status": "fresh",
        "findings": findings,
        "source_attempts": source_attempts,
        "external_service_cost_dollars": round(total_cost, 6),
    }
    if errors:
        output["errors"] = errors
    return output


def apply_tier_2_public_research(output: dict, tier_2_result: dict) -> None:
    output["tier_2_public_company_research"].update(tier_2_result)
    output["reuse"].update(
        {
            "reuse_status": "fresh",
            "reused_tiers": [],
            "reused_from_run_id": None,
            "reused_from_output_id": None,
            "reused_at": None,
        }
    )
    if output["reuse"].get("non_reuse_reason") is None:
        output["reuse"]["non_reuse_reason"] = "no_prior_output"


def build_exa_search_payload(
    *,
    resolved_company_domain: str,
    query_spec: Tier2QuerySpec,
    num_results: int = DEFAULT_TIER_2_EXA_NUM_RESULTS,
) -> dict:
    domain = _normalize_domain(resolved_company_domain)
    quoted_terms = " OR ".join(f'"{term}"' for term in query_spec.terms)
    return {
        "query": f"site:{domain} ({quoted_terms})",
        "type": "auto",
        "numResults": num_results,
        "includeDomains": [domain],
        "contents": {"highlights": True},
    }


def _post_exa_search(
    *,
    payload: dict,
    api_key: str,
    client: Any | None,
    timeout_seconds: float,
) -> dict:
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
    }
    if client is not None:
        response = client.post(EXA_SEARCH_URL, headers=headers, json=payload, timeout=timeout_seconds)
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        return response.json()

    try:
        import httpx
    except ModuleNotFoundError as exc:
        raise RuntimeError("httpx is required for live Exa Tier 2 research.") from exc

    with httpx.Client(timeout=timeout_seconds) as httpx_client:
        response = httpx_client.post(EXA_SEARCH_URL, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()


def _findings_from_results(
    *,
    results: list[dict],
    query_spec: Tier2QuerySpec,
    resolved_company_domain: str,
    company_name: str | None,
    starting_index: int,
) -> list[dict]:
    findings = []
    for result in results:
        source_url = str(result.get("url") or result.get("id") or "")
        if not _is_company_owned_url(source_url, resolved_company_domain):
            continue
        evidence_quote = _first_highlight(result)
        if not evidence_quote:
            continue
        finding_index = starting_index + len(findings)
        findings.append(
            {
                "finding_id": f"tier2_{finding_index:03d}",
                "signal_type": query_spec.signal_type,
                "fact": _fact_from_quote(
                    evidence_quote=evidence_quote,
                    company_name=company_name,
                    resolved_company_domain=resolved_company_domain,
                    source_hint=query_spec.source_hint,
                ),
                "source_type": _classify_source_type(source_url),
                "source_url": source_url,
                "source_title": result.get("title"),
                "published_at": result.get("publishedDate"),
                "evidence_quote": evidence_quote,
                "confidence": _confidence_from_result(result),
            }
        )
    return findings


def _tier_2_error(error_message: str) -> dict:
    return {
        "status": "error",
        "strategy": TIER_2_STRATEGY,
        "positioning_taxonomy_version": POSITIONING_TAXONOMY_VERSION,
        "reuse_status": "fresh",
        "findings": [],
        "source_attempts": [],
        "external_service_cost_dollars": 0,
        "errors": [error_message],
    }


def _normalize_domain(domain: str | None) -> str:
    if not domain:
        return ""
    value = domain.strip().lower()
    if "://" in value:
        value = urlparse(value).netloc
    return value.removeprefix("www.").strip("/")


def _is_company_owned_url(url: str, resolved_company_domain: str) -> bool:
    if not url:
        return False
    hostname = (urlparse(url).hostname or "").lower().removeprefix("www.")
    domain = _normalize_domain(resolved_company_domain)
    return hostname == domain or hostname.endswith(f".{domain}")


def _first_highlight(result: dict) -> str | None:
    highlights = result.get("highlights") or []
    if isinstance(highlights, str):
        highlights = [highlights]
    for highlight in highlights:
        cleaned = _clean_text(str(highlight))
        if cleaned:
            return cleaned
    return None


def _fact_from_quote(
    *,
    evidence_quote: str,
    company_name: str | None,
    resolved_company_domain: str,
    source_hint: str,
) -> str:
    company_label = company_name or resolved_company_domain
    quote = evidence_quote.rstrip(".")
    if len(quote) > 220:
        quote = f"{quote[:217].rstrip()}..."
    return f"{company_label} has company-owned content related to {source_hint}: {quote}."


def _classify_source_type(url: str) -> str:
    path = urlparse(url).path.lower()
    if any(part in path for part in ("engineering", "eng-blog")):
        return "engineering_blog"
    if any(part in path for part in ("developer", "developers", "dev-blog")):
        return "developer_blog"
    if "blog" in path:
        return "company_blog"
    if any(part in path for part in ("product", "platform", "solutions", "features")):
        return "product_page"
    if any(part in path for part in ("career", "careers", "jobs", "life-at")):
        return "careers_page"
    if any(part in path for part in ("news", "press", "release", "releases")):
        return "news_page"
    return "other_company_owned"


def _confidence_from_result(result: dict) -> str:
    scores = result.get("highlightScores") or []
    score = scores[0] if scores else result.get("score")
    try:
        numeric_score = float(score)
    except (TypeError, ValueError):
        return "medium"
    if numeric_score >= 0.8:
        return "high"
    if numeric_score >= 0.45:
        return "medium"
    return "low"


def _cost_to_float(cost: Any) -> float:
    if cost is None:
        return 0.0
    if isinstance(cost, Decimal):
        return float(cost)
    if isinstance(cost, int | float):
        return float(cost)
    if isinstance(cost, str):
        try:
            return float(cost)
        except ValueError:
            return 0.0
    if isinstance(cost, dict):
        for total_key in ("total", "totalDollars", "total_dollars"):
            if total_key in cost:
                return _cost_to_float(cost[total_key])
        return sum(_cost_to_float(value) for value in cost.values())
    if isinstance(cost, list | tuple):
        return sum(_cost_to_float(value) for value in cost)
    return 0.0


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
