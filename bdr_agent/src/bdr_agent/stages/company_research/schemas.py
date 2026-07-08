"""Schema assembly and validation for company research outputs."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from bdr_agent.stages.company_research.config import (
    HYDRATION_MISSING_REQUIRED_COMPANY_CONTEXT,
    HYDRATION_NOT_READY,
    POSITIONING_TAXONOMY_VERSION,
    SCHEMA_VERSION,
    STAGE,
    TIER_2_STRATEGY,
    VALID_HYDRATION_STATUSES,
)


def new_run_id() -> str:
    return f"bdr_run_{uuid4().hex}"


def new_output_id() -> str:
    return f"bdr_output_{uuid4().hex}"


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def build_minimal_company_research_output(
    *,
    lead_id: str,
    trigger_source: str,
    run_id: str | None = None,
    output_id: str | None = None,
    generated_at: str | None = None,
    hydration_status: str = HYDRATION_NOT_READY,
    missing_fields: list[str] | None = None,
    resolved_company_domain: str | None = None,
    resolved_company_domain_source: str | None = None,
    lead: dict | None = None,
    contact: dict | None = None,
    company: dict | None = None,
) -> dict:
    lead_data = {"lead_id": lead_id, "created_at": None, "hubspot_owner_id": None}
    lead_data.update(lead or {})
    if lead_data.get("lead_id") is None:
        lead_data["lead_id"] = lead_id

    contact_data = {
        "contact_id": None,
        "email": None,
        "first_name": None,
        "last_name": None,
        "job_title": None,
        "associated_company_id": None,
    }
    contact_data.update(contact or {})

    company_data = {
        "company_id": None,
        "company_name": None,
        "email_domain": None,
        "alternative_email_domain": None,
        "website": None,
        "industry": None,
        "num_employees": None,
        "icp_tier": None,
    }
    company_data.update(company or {})
    output = {
        "schema_version": SCHEMA_VERSION,
        "stage": STAGE,
        "trigger_source": trigger_source,
        "run_id": run_id or new_run_id(),
        "output_id": output_id or new_output_id(),
        "generated_at": generated_at or utc_now_iso(),
        "lead": lead_data,
        "contact": contact_data,
        "company": company_data,
        "hydration": {
            "resolved_company_domain": resolved_company_domain,
            "resolved_company_domain_source": resolved_company_domain_source,
            "hydration_status": hydration_status,
            "missing_fields": missing_fields or [],
        },
        "tier_1_internal_metrics": {
            "status": "not_run",
            "email_domain": resolved_company_domain,
            "metrics_as_of": None,
            "is_enterprise_domain": None,
            "is_public_email_domain": None,
            "has_product_usage": None,
            "has_recent_product_usage": None,
            "has_paid_signal": None,
            "data_notes": None,
            "metrics": None,
        },
        "tier_2_public_company_research": {
            "status": "not_run",
            "strategy": TIER_2_STRATEGY,
            "positioning_taxonomy_version": POSITIONING_TAXONOMY_VERSION,
            "findings": [],
            "source_attempts": [],
            "external_service_cost_dollars": 0,
        },
        "tier_3_external_research": {
            "status": "skipped",
            "reason": "tier_3_disabled_for_mvp",
        },
        "reuse": {
            "reuse_key": resolved_company_domain,
            "reuse_status": "not_reusable",
            "reused_tiers": [],
            "reused_from_run_id": None,
            "reused_from_output_id": None,
            "reused_at": None,
            "non_reuse_reason": None,
        },
        "storage": {
            "status": "not_persisted",
            "gcs_uri": None,
            "bigquery_table": None,
            "bigquery_row_id": None,
        },
    }
    validate_company_research_output(output)
    return output


def validate_company_research_output(output: dict) -> None:
    required_top_level = {
        "schema_version",
        "stage",
        "trigger_source",
        "run_id",
        "output_id",
        "generated_at",
        "lead",
        "contact",
        "company",
        "hydration",
        "tier_1_internal_metrics",
        "tier_2_public_company_research",
        "tier_3_external_research",
        "reuse",
        "storage",
    }
    missing = required_top_level - output.keys()
    if missing:
        raise ValueError(f"Missing top-level fields: {sorted(missing)}")

    if output["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"Unexpected schema_version: {output['schema_version']}")

    if output["stage"] != STAGE:
        raise ValueError(f"Unexpected stage: {output['stage']}")

    hydration = output["hydration"]
    hydration_status = hydration.get("hydration_status")
    if hydration_status not in VALID_HYDRATION_STATUSES:
        raise ValueError(f"Unexpected hydration_status: {hydration_status}")

    if hydration_status in {HYDRATION_NOT_READY, HYDRATION_MISSING_REQUIRED_COMPANY_CONTEXT}:
        if not hydration.get("missing_fields"):
            raise ValueError("Incomplete hydration outputs must include missing_fields")
