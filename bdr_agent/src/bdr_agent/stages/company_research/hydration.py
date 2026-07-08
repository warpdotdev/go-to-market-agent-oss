"""Hydration helpers and company-backed domain resolution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from bdr_agent.stages.company_research.config import (
    GCP_PROJECT_ID,
    HYDRATION_HYDRATED,
    HYDRATION_MISSING_REQUIRED_COMPANY_CONTEXT,
    HYDRATION_NOT_READY,
)

HYDRATION_QUERY_PATH = (
    Path(__file__).resolve().parents[4]
    / "sql"
    / "queries"
    / "hydration_query.sql"
)


@dataclass(frozen=True)
class DomainResolution:
    resolved_company_domain: str | None
    resolved_company_domain_source: str | None


@dataclass(frozen=True)
class HydrationResult:
    hydration_status: str
    missing_fields: list[str]
    resolved_company_domain: str | None
    resolved_company_domain_source: str | None
    lead: dict
    contact: dict
    company: dict


def normalize_domain(value: str | None) -> str | None:
    if not value:
        return None

    candidate = value.strip().lower()
    if not candidate:
        return None

    if "://" not in candidate and "/" not in candidate:
        host = candidate
    else:
        parsed = urlparse(candidate if "://" in candidate else f"https://{candidate}")
        host = parsed.netloc or parsed.path.split("/", 1)[0]

    if "@" in host:
        host = host.rsplit("@", 1)[-1]

    if ":" in host:
        host = host.split(":", 1)[0]

    if host.startswith("www."):
        host = host[4:]

    host = host.strip(".")
    return host or None


def resolve_company_domain(
    *,
    email_domain: str | None,
    alternative_email_domain: str | None,
    website: str | None,
) -> DomainResolution:
    candidates = (
        ("company.email_domain", email_domain),
        ("company.alternative_email_domain", alternative_email_domain),
        ("company.website", website),
    )
    for source, value in candidates:
        normalized = normalize_domain(value)
        if normalized:
            return DomainResolution(
                resolved_company_domain=normalized,
                resolved_company_domain_source=source,
            )
    return DomainResolution(
        resolved_company_domain=None,
        resolved_company_domain_source=None,
    )


def build_hydration_result_from_webhook_payload(
    payload: dict | None,
    *,
    fallback_lead_id: str | None = None,
) -> HydrationResult:
    """Build the identity/company context directly from the HubSpot webhook payload.

    This is the production path for HubSpot-triggered runs. BigQuery is still used later
    for Tier 1 product-usage metrics by resolved company domain, but it is no longer the
    source of truth for the fresh Lead/Contact/Company shell.
    """
    payload = payload or {}
    lead_id = _first_present(payload, "lead_id", "hs_object_id") or fallback_lead_id
    lead = {
        "lead_id": lead_id,
        "created_at": _first_present(payload, "lead_created_at", "created_at"),
        "hubspot_owner_id": _string_or_none(
            _first_present(payload, "hubspot_owner_id", "lead_owner_id")
        ),
        "source_detailed": _first_present(payload, "lead_source_detailed", "source_detailed"),
    }
    contact_id = _first_present(payload, "contact_id", "associated_contact_id")
    company_id = _first_present(payload, "company_id", "associated_company_id")
    contact = {
        "contact_id": contact_id,
        "email": _first_present(payload, "contact_email", "email"),
        "first_name": _first_present(payload, "contact_first_name", "first_name", "firstname"),
        "last_name": _first_present(payload, "contact_last_name", "last_name", "lastname"),
        "job_title": _first_present(payload, "contact_job_title", "job_title"),
        "associated_company_id": _first_present(
            payload,
            "contact_associated_company_id",
            "associated_company_id",
            default=company_id,
        ),
    }
    company = {
        "company_id": company_id,
        "company_name": _first_present(payload, "company_name", "name"),
        "email_domain": _first_present(payload, "company_domain", "company_email_domain", "domain"),
        "alternative_email_domain": _first_present(
            payload,
            "company_alternative_domain",
            "company_alternative_email_domain",
            "alternative_email_domain",
        ),
        "website": _first_present(payload, "company_website", "website"),
        "industry": _first_present(payload, "company_industry", "industry"),
        "num_employees": _first_present(
            payload,
            "company_num_employees",
            "company_number_of_employees",
            "num_employees",
        ),
        "icp_tier": _first_present(payload, "company_icp_tier", "icp_tier"),
    }

    if not lead["lead_id"]:
        return HydrationResult(
            hydration_status=HYDRATION_NOT_READY,
            missing_fields=["lead"],
            resolved_company_domain=None,
            resolved_company_domain_source=None,
            lead=lead,
            contact=contact,
            company=company,
        )

    missing_fields = []
    if not contact["contact_id"]:
        missing_fields.append("contact")
    if not company["company_id"]:
        missing_fields.append("company")
    if missing_fields:
        return HydrationResult(
            hydration_status=HYDRATION_NOT_READY,
            missing_fields=missing_fields,
            resolved_company_domain=None,
            resolved_company_domain_source=None,
            lead=lead,
            contact=contact,
            company=company,
        )

    domain_resolution = resolve_webhook_company_domain(
        company_domain=company["email_domain"],
        company_alternative_domain=company["alternative_email_domain"],
        company_website=company["website"],
    )
    if domain_resolution.resolved_company_domain is None:
        return HydrationResult(
            hydration_status=HYDRATION_MISSING_REQUIRED_COMPANY_CONTEXT,
            missing_fields=["company_domain_or_website"],
            resolved_company_domain=None,
            resolved_company_domain_source=None,
            lead=lead,
            contact=contact,
            company=company,
        )

    return HydrationResult(
        hydration_status=HYDRATION_HYDRATED,
        missing_fields=[],
        resolved_company_domain=domain_resolution.resolved_company_domain,
        resolved_company_domain_source=domain_resolution.resolved_company_domain_source,
        lead=lead,
        contact=contact,
        company=company,
    )


def merge_hydration_results(
    *,
    primary: HydrationResult,
    fallback: HydrationResult | None,
) -> HydrationResult:
    """Merge webhook-primary context with fallback hydration context.

    Non-blank values from ``primary`` always win. ``fallback`` only fills fields
    that are absent or blank in the webhook payload-derived result.
    """
    if fallback is None:
        fallback = HydrationResult(
            hydration_status=HYDRATION_NOT_READY,
            missing_fields=["lead"],
            resolved_company_domain=None,
            resolved_company_domain_source=None,
            lead={},
            contact={},
            company={},
        )

    lead = _merge_nonblank_dicts(primary=primary.lead, fallback=fallback.lead)
    contact = _merge_nonblank_dicts(primary=primary.contact, fallback=fallback.contact)
    company = _merge_nonblank_dicts(primary=primary.company, fallback=fallback.company)

    if not lead.get("lead_id"):
        return HydrationResult(
            hydration_status=HYDRATION_NOT_READY,
            missing_fields=["lead"],
            resolved_company_domain=None,
            resolved_company_domain_source=None,
            lead=lead,
            contact=contact,
            company=company,
        )

    missing_fields = []
    if not contact.get("contact_id"):
        missing_fields.append("contact")
    if not company.get("company_id"):
        missing_fields.append("company")
    if missing_fields:
        return HydrationResult(
            hydration_status=HYDRATION_NOT_READY,
            missing_fields=missing_fields,
            resolved_company_domain=None,
            resolved_company_domain_source=None,
            lead=lead,
            contact=contact,
            company=company,
        )

    domain_resolution = _resolve_merged_company_domain(
        primary_company=primary.company,
        fallback_company=fallback.company,
        merged_company=company,
    )
    if domain_resolution.resolved_company_domain is None:
        return HydrationResult(
            hydration_status=HYDRATION_MISSING_REQUIRED_COMPANY_CONTEXT,
            missing_fields=["company_domain_or_website"],
            resolved_company_domain=None,
            resolved_company_domain_source=None,
            lead=lead,
            contact=contact,
            company=company,
        )

    return HydrationResult(
        hydration_status=HYDRATION_HYDRATED,
        missing_fields=[],
        resolved_company_domain=domain_resolution.resolved_company_domain,
        resolved_company_domain_source=domain_resolution.resolved_company_domain_source,
        lead=lead,
        contact=contact,
        company=company,
    )

def resolve_webhook_company_domain(
    *,
    company_domain: str | None,
    company_alternative_domain: str | None,
    company_website: str | None,
) -> DomainResolution:
    candidates = (
        ("webhook.company_domain", company_domain),
        ("webhook.company_alternative_domain", company_alternative_domain),
        ("webhook.company_website", company_website),
    )
    for source, value in candidates:
        normalized = normalize_domain(value)
        if normalized:
            return DomainResolution(
                resolved_company_domain=normalized,
                resolved_company_domain_source=source,
            )
    return DomainResolution(
        resolved_company_domain=None,
        resolved_company_domain_source=None,
    )


def build_hydration_result(row: dict | None) -> HydrationResult:
    if row is None:
        return HydrationResult(
            hydration_status=HYDRATION_NOT_READY,
            missing_fields=["lead"],
            resolved_company_domain=None,
            resolved_company_domain_source=None,
            lead={},
            contact={},
            company={},
        )

    lead = {
        "lead_id": row.get("lead_id"),
        "created_at": row.get("lead_created_at"),
        "hubspot_owner_id": _string_or_none(row.get("hubspot_owner_id")),
        "source_detailed": row.get("lead_source_detailed"),
    }
    contact = {
        "contact_id": row.get("contact_id"),
        "email": row.get("contact_email"),
        "first_name": row.get("contact_first_name"),
        "last_name": row.get("contact_last_name"),
        "job_title": row.get("contact_job_title"),
        "associated_company_id": row.get("contact_associated_company_id"),
    }
    company = {
        "company_id": row.get("company_id"),
        "company_name": row.get("company_name"),
        "email_domain": row.get("company_email_domain"),
        "alternative_email_domain": row.get("company_alternative_email_domain"),
        "website": row.get("company_website"),
        "industry": row.get("company_industry"),
        "num_employees": row.get("company_num_employees"),
        "icp_tier": row.get("company_icp_tier"),
    }

    if not contact["contact_id"]:
        return HydrationResult(
            hydration_status=HYDRATION_NOT_READY,
            missing_fields=["contact"],
            resolved_company_domain=None,
            resolved_company_domain_source=None,
            lead=lead,
            contact=contact,
            company=company,
        )

    if not company["company_id"]:
        return HydrationResult(
            hydration_status=HYDRATION_NOT_READY,
            missing_fields=["company"],
            resolved_company_domain=None,
            resolved_company_domain_source=None,
            lead=lead,
            contact=contact,
            company=company,
        )

    domain_resolution = resolve_company_domain(
        email_domain=company["email_domain"],
        alternative_email_domain=company["alternative_email_domain"],
        website=company["website"],
    )
    if domain_resolution.resolved_company_domain is None:
        return HydrationResult(
            hydration_status=HYDRATION_MISSING_REQUIRED_COMPANY_CONTEXT,
            missing_fields=["company_domain_or_website"],
            resolved_company_domain=None,
            resolved_company_domain_source=None,
            lead=lead,
            contact=contact,
            company=company,
        )

    return HydrationResult(
        hydration_status=HYDRATION_HYDRATED,
        missing_fields=[],
        resolved_company_domain=domain_resolution.resolved_company_domain,
        resolved_company_domain_source=domain_resolution.resolved_company_domain_source,
        lead=lead,
        contact=contact,
        company=company,
    )


def load_hydration_query(path: Path = HYDRATION_QUERY_PATH) -> str:
    return path.read_text().replace("example-gcp-project", GCP_PROJECT_ID)


def fetch_hydration_row(*, lead_id: str, project_id: str = GCP_PROJECT_ID) -> dict | None:
    try:
        from google.cloud import bigquery
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "google-cloud-bigquery is required for BigQuery hydration. "
            "Install repository requirements or run with --skip-bigquery for local validation."
        ) from exc

    client = bigquery.Client(project=project_id)
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("lead_id", "STRING", lead_id),
        ],
    )
    rows = list(client.query(load_hydration_query(), job_config=job_config).result(max_results=1))
    if not rows:
        return None
    return {key: to_json_safe(value) for key, value in rows[0].items()}


def to_json_safe(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _string_or_none(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _first_present(payload: dict, *keys: str, default=None):
    for key in keys:
        value = payload.get(key)
        if value is not None and str(value).strip() != "":
            return value
    return default


def _merge_nonblank_dicts(*, primary: dict, fallback: dict) -> dict:
    merged = {}
    for key in fallback.keys() | primary.keys():
        primary_value = primary.get(key)
        merged[key] = primary_value if _is_present(primary_value) else fallback.get(key)
    return merged


def _resolve_merged_company_domain(
    *,
    primary_company: dict,
    fallback_company: dict,
    merged_company: dict,
) -> DomainResolution:
    primary_resolution = resolve_webhook_company_domain(
        company_domain=primary_company.get("email_domain"),
        company_alternative_domain=primary_company.get("alternative_email_domain"),
        company_website=primary_company.get("website"),
    )
    if primary_resolution.resolved_company_domain:
        return primary_resolution

    fallback_resolution = resolve_company_domain(
        email_domain=fallback_company.get("email_domain"),
        alternative_email_domain=fallback_company.get("alternative_email_domain"),
        website=fallback_company.get("website"),
    )
    if fallback_resolution.resolved_company_domain:
        return fallback_resolution

    return resolve_company_domain(
        email_domain=merged_company.get("email_domain"),
        alternative_email_domain=merged_company.get("alternative_email_domain"),
        website=merged_company.get("website"),
    )


def _is_present(value) -> bool:
    return value is not None and str(value).strip() != ""
