"""Stable contracts for hook candidate generation, evaluation, and writeback."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from bdr_agent.outreach_writeback.config import (
    GENERATION_STATUS_CANDIDATE_GENERATED,
    GENERATION_STATUS_QUALITY_FAILED,
    GENERATION_STATUS_QUALITY_PASSED,
    GENERATION_STATUS_REWRITE_REQUESTED,
    GENERATION_STATUS_REWRITTEN_QUALITY_PASSED,
    GENERATION_STATUS_WRITEBACK_FAILED,
    GENERATION_STATUS_WRITEBACK_SUCCEEDED,
    HOOK_PROPERTY_NAME,
    SCHEMA_VERSION,
    SOURCES_PROPERTY_NAME,
    VALID_GENERATION_STATUSES,
)

CANDIDATE_HOOK_ARTIFACT_TYPE = "candidate_hook"
EVALUATE_HOOK_INPUT_ARTIFACT_TYPE = "evaluate_hook_input"
EVALUATE_HOOK_OUTPUT_ARTIFACT_TYPE = "evaluate_hook_output"

CANDIDATE_HOOK_ARTIFACT_REQUIRED_FIELDS = {
    "schema_version",
    "artifact_type",
    "hook_id",
    "lead_id",
    "synthesis_output_id",
    "synthesis_gcs_uri",
    "style_profile_id",
    "style_profile_version",
    "positioning_snapshot_version",
    "positioning_pillar",
    "positioning_value_prop",
    "writer_mode",
    "candidate_hook_text",
    "generation_status",
    "idempotency_key",
}

EVALUATE_HOOK_INPUT_ARTIFACT_REQUIRED_FIELDS = {
    "schema_version",
    "artifact_type",
    "hook_id",
    "lead_id",
    "candidate_hook_artifact_ref",
    "candidate_generation_idempotency_key",
    "target_hubspot_object_type",
    "target_hubspot_object_id",
    "target_hubspot_hook_property_name",
    "target_hubspot_sources_property_name",
}

EVALUATE_HOOK_OUTPUT_ARTIFACT_REQUIRED_FIELDS = {
    "schema_version",
    "artifact_type",
    "hook_id",
    "lead_id",
    "candidate_hook_artifact_ref",
    "final_hook_text",
    "generation_status",
    "rewrite_attempted",
    "rewrite_reason",
    "lint_result_json",
    "critic_result_json",
    "final_writeback_idempotency_key",
    "selected_source",
    "writeback_result",
    "stable_refs",
}


def build_candidate_generation_idempotency_key(
    *,
    lead_id: str,
    synthesis_output_id: str,
    style_profile_version: str,
    positioning_snapshot_version: str,
    writer_mode: str,
) -> str:
    """Key deterministic candidate retries by stable inputs, not prompt payloads."""
    return _stable_key(
        "candidate_generation",
        {
            "lead_id": lead_id,
            "synthesis_output_id": synthesis_output_id,
            "style_profile_version": style_profile_version,
            "positioning_snapshot_version": positioning_snapshot_version,
            "writer_mode": writer_mode,
        },
    )


def build_final_writeback_idempotency_key(
    *,
    hubspot_object_type: str,
    hubspot_object_id: str,
    hubspot_property_name: str = HOOK_PROPERTY_NAME,
    candidate_hook_id: str | None = None,
    final_output_id: str | None = None,
) -> str:
    """Key final HubSpot writeback by final hook identity and target property."""
    if not candidate_hook_id and not final_output_id:
        raise ValueError("candidate_hook_id or final_output_id is required")
    return _stable_key(
        "final_writeback",
        {
            "candidate_hook_id": candidate_hook_id,
            "final_output_id": final_output_id,
            "hubspot_object_type": hubspot_object_type,
            "hubspot_object_id": hubspot_object_id,
            "hubspot_property_name": hubspot_property_name,
        },
    )


def build_candidate_hook_artifact(*, hook_row: dict) -> dict:
    artifact = {
        "schema_version": hook_row["schema_version"],
        "artifact_type": CANDIDATE_HOOK_ARTIFACT_TYPE,
        "hook_id": hook_row["hook_id"],
        "run_id": hook_row["run_id"],
        "output_id": hook_row["output_id"],
        "lead_id": hook_row["lead_id"],
        "contact_id": hook_row["contact_id"],
        "company_id": hook_row["company_id"],
        "resolved_company_domain": hook_row["resolved_company_domain"],
        "synthesis_output_id": hook_row["synthesis_output_id"],
        "synthesis_gcs_uri": hook_row["synthesis_gcs_uri"],
        "ai_hook_sources_url": hook_row["ai_hook_sources_url"],
        "style_profile_id": hook_row["style_profile_id"],
        "style_profile_version": hook_row["style_profile_version"],
        "style_profile_fallback_reason": hook_row["style_profile_fallback_reason"],
        "positioning_snapshot_version": hook_row["positioning_snapshot_version"],
        "positioning_pillar": hook_row["positioning_pillar"],
        "positioning_value_prop": hook_row["positioning_value_prop"],
        "writer_mode": hook_row["writer_mode"],
        "candidate_hook_text": hook_row["candidate_hook_text"],
        "hook_angle": hook_row["hook_angle"],
        "source_labels": hook_row.get("source_labels", []),
        "evidence_summary": hook_row.get("evidence_summary"),
        "generation_status": hook_row["generation_status"],
        "idempotency_key": hook_row["candidate_generation_idempotency_key"],
    }
    validate_candidate_hook_artifact(artifact)
    return artifact


def build_evaluate_hook_input_artifact(
    *,
    hook_row: dict,
    candidate_hook_artifact_ref: str,
    target_hubspot_object_type: str,
    target_hubspot_object_id: str,
) -> dict:
    artifact = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": EVALUATE_HOOK_INPUT_ARTIFACT_TYPE,
        "hook_id": hook_row["hook_id"],
        "lead_id": hook_row["lead_id"],
        "candidate_hook_artifact_ref": candidate_hook_artifact_ref,
        "candidate_generation_idempotency_key": hook_row["candidate_generation_idempotency_key"],
        "target_hubspot_object_type": target_hubspot_object_type,
        "target_hubspot_object_id": target_hubspot_object_id,
        "target_hubspot_hook_property_name": HOOK_PROPERTY_NAME,
        "target_hubspot_sources_property_name": SOURCES_PROPERTY_NAME,
    }
    validate_evaluate_hook_input_artifact(artifact)
    return artifact


def build_evaluate_hook_output_artifact(
    *,
    hook_row: dict,
    candidate_hook_artifact_ref: str,
    target_hubspot_object_type: str,
    target_hubspot_object_id: str,
    final_hook_text: str,
    generation_status: str = GENERATION_STATUS_QUALITY_PASSED,
    rewrite_attempted: bool = False,
    rewrite_reason: str | None = None,
    lint_result_json: dict | None = None,
    critic_result_json: dict | None = None,
    final_output_id: str | None = None,
    selected_source: str = "candidate",
    writeback_result: dict | None = None,
    stable_refs: dict | None = None,
) -> dict:
    artifact = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": EVALUATE_HOOK_OUTPUT_ARTIFACT_TYPE,
        "hook_id": hook_row["hook_id"],
        "lead_id": hook_row["lead_id"],
        "candidate_hook_artifact_ref": candidate_hook_artifact_ref,
        "final_hook_text": final_hook_text,
        "generation_status": generation_status,
        "rewrite_attempted": rewrite_attempted,
        "rewrite_reason": rewrite_reason,
        "lint_result_json": lint_result_json or {},
        "critic_result_json": critic_result_json or {},
        "final_writeback_idempotency_key": build_final_writeback_idempotency_key(
            candidate_hook_id=hook_row["hook_id"],
            final_output_id=final_output_id,
            hubspot_object_type=target_hubspot_object_type,
            hubspot_object_id=target_hubspot_object_id,
            hubspot_property_name=HOOK_PROPERTY_NAME,
        ),
        "selected_source": selected_source,
        "writeback_result": writeback_result or {},
        "stable_refs": stable_refs or {
            "candidate_hook_artifact_ref": candidate_hook_artifact_ref,
            "target_hubspot_object_type": target_hubspot_object_type,
            "target_hubspot_object_id": target_hubspot_object_id,
            "target_hubspot_hook_property_name": HOOK_PROPERTY_NAME,
            "target_hubspot_sources_property_name": SOURCES_PROPERTY_NAME,
        },
    }
    validate_evaluate_hook_output_artifact(artifact)
    return artifact


def validate_candidate_hook_artifact(artifact: dict) -> None:
    _validate_required_fields(
        artifact=artifact,
        required_fields=CANDIDATE_HOOK_ARTIFACT_REQUIRED_FIELDS,
        artifact_type=CANDIDATE_HOOK_ARTIFACT_TYPE,
    )
    if artifact["generation_status"] != GENERATION_STATUS_CANDIDATE_GENERATED:
        raise ValueError("candidate hook artifacts must have candidate_generated status")
    if not artifact["candidate_hook_text"]:
        raise ValueError("candidate_hook_text is required")


def validate_evaluate_hook_input_artifact(artifact: dict) -> None:
    _validate_required_fields(
        artifact=artifact,
        required_fields=EVALUATE_HOOK_INPUT_ARTIFACT_REQUIRED_FIELDS,
        artifact_type=EVALUATE_HOOK_INPUT_ARTIFACT_TYPE,
    )


def validate_evaluate_hook_output_artifact(artifact: dict) -> None:
    _validate_required_fields(
        artifact=artifact,
        required_fields=EVALUATE_HOOK_OUTPUT_ARTIFACT_REQUIRED_FIELDS,
        artifact_type=EVALUATE_HOOK_OUTPUT_ARTIFACT_TYPE,
    )
    if artifact["generation_status"] not in {
        GENERATION_STATUS_QUALITY_PASSED,
        GENERATION_STATUS_QUALITY_FAILED,
        GENERATION_STATUS_REWRITE_REQUESTED,
        GENERATION_STATUS_REWRITTEN_QUALITY_PASSED,
        GENERATION_STATUS_WRITEBACK_SUCCEEDED,
        GENERATION_STATUS_WRITEBACK_FAILED,
    }:
        raise ValueError(f"Invalid evaluate output status: {artifact['generation_status']}")
    if artifact["generation_status"] != GENERATION_STATUS_QUALITY_FAILED and not artifact["final_hook_text"]:
        raise ValueError("final_hook_text is required")


def validate_generation_status(status: str) -> None:
    if status not in VALID_GENERATION_STATUSES:
        raise ValueError(f"Invalid generation_status: {status}")


def _validate_required_fields(
    *,
    artifact: dict,
    required_fields: set[str],
    artifact_type: str,
) -> None:
    missing = required_fields - artifact.keys()
    if missing:
        raise ValueError(f"Missing {artifact_type} artifact fields: {sorted(missing)}")
    if artifact["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"Unexpected schema_version: {artifact['schema_version']}")
    if artifact["artifact_type"] != artifact_type:
        raise ValueError(f"Unexpected artifact_type: {artifact['artifact_type']}")


def _stable_key(prefix: str, payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return f"{prefix}:{digest}"
