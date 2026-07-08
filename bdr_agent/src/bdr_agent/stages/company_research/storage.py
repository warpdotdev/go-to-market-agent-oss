"""Storage helpers for company research outputs."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from typing import Any
from bdr_agent.common.oz_metadata import runtime_oz_metadata

from bdr_agent.stages.company_research.config import (
    COMPANY_RESEARCH_BIGQUERY_TABLES,
    COMPANY_RESEARCH_OUTPUTS_TABLE,
    GCP_PROJECT_ID,
    GCS_ARTIFACT_BUCKET,
    GCS_ARTIFACT_PREFIX,
    HOOKS_TABLE,
    OUTPUTS_TABLE,
    RUNS_TABLE,
    SCHEMA_VERSION,
    STAGE,
    bigquery_table_id,
)


BIGQUERY_TABLE_DEFINITIONS = {
    RUNS_TABLE: {
        "schema": (
            ("run_id", "STRING", "REQUIRED"),
            ("stage", "STRING", "NULLABLE"),
            ("trigger_source", "STRING", "NULLABLE"),
            ("lead_id", "STRING", "NULLABLE"),
            ("contact_id", "STRING", "NULLABLE"),
            ("company_id", "STRING", "NULLABLE"),
            ("resolved_company_domain", "STRING", "NULLABLE"),
            ("started_at", "TIMESTAMP", "NULLABLE"),
            ("completed_at", "TIMESTAMP", "NULLABLE"),
            ("duration_seconds", "FLOAT", "NULLABLE"),
            ("status", "STRING", "NULLABLE"),
            ("failure_reason", "STRING", "NULLABLE"),
            ("oz_run_id", "STRING", "NULLABLE"),
            ("oz_run_link", "STRING", "NULLABLE"),
            ("oz_session_link", "STRING", "NULLABLE"),
            ("oz_credits_used", "FLOAT", "NULLABLE"),
            ("external_service_costs", "JSON", "NULLABLE"),
            ("created_at", "TIMESTAMP", "NULLABLE"),
        ),
        "partition_field": "created_at",
        "clustering_fields": ("stage", "lead_id", "resolved_company_domain"),
    },
    OUTPUTS_TABLE: {
        "schema": (
            ("output_id", "STRING", "REQUIRED"),
            ("run_id", "STRING", "NULLABLE"),
            ("stage", "STRING", "NULLABLE"),
            ("lead_id", "STRING", "NULLABLE"),
            ("contact_id", "STRING", "NULLABLE"),
            ("company_id", "STRING", "NULLABLE"),
            ("resolved_company_domain", "STRING", "NULLABLE"),
            ("output_type", "STRING", "NULLABLE"),
            ("schema_version", "STRING", "NULLABLE"),
            ("bigquery_table", "STRING", "NULLABLE"),
            ("bigquery_row_id", "STRING", "NULLABLE"),
            ("gcs_uri", "STRING", "NULLABLE"),
            ("created_at", "TIMESTAMP", "NULLABLE"),
        ),
        "partition_field": "created_at",
        "clustering_fields": ("stage", "lead_id", "resolved_company_domain"),
    },
    COMPANY_RESEARCH_OUTPUTS_TABLE: {
        "schema": (
            ("output_id", "STRING", "REQUIRED"),
            ("run_id", "STRING", "NULLABLE"),
            ("lead_id", "STRING", "NULLABLE"),
            ("contact_id", "STRING", "NULLABLE"),
            ("company_id", "STRING", "NULLABLE"),
            ("resolved_company_domain", "STRING", "NULLABLE"),
            ("trigger_source", "STRING", "NULLABLE"),
            ("hydration_status", "STRING", "NULLABLE"),
            ("company_context_json", "JSON", "NULLABLE"),
            ("tier_1_internal_metrics_json", "JSON", "NULLABLE"),
            ("tier_2_public_research_json", "JSON", "NULLABLE"),
            ("tier_3_external_research_json", "JSON", "NULLABLE"),
            ("reuse_json", "JSON", "NULLABLE"),
            ("research_status", "STRING", "NULLABLE"),
            ("schema_version", "STRING", "NULLABLE"),
            ("gcs_uri", "STRING", "NULLABLE"),
            ("created_at", "TIMESTAMP", "NULLABLE"),
        ),
        "partition_field": "created_at",
        "clustering_fields": ("resolved_company_domain", "hydration_status", "lead_id"),
    },
    HOOKS_TABLE: {
        "schema": (
            ("hook_id", "STRING", "REQUIRED"),
            ("output_id", "STRING", "NULLABLE"),
            ("run_id", "STRING", "NULLABLE"),
            ("lead_id", "STRING", "NULLABLE"),
            ("contact_id", "STRING", "NULLABLE"),
            ("company_id", "STRING", "NULLABLE"),
            ("resolved_company_domain", "STRING", "NULLABLE"),
            ("company_research_output_id", "STRING", "NULLABLE"),
            ("synthesis_output_id", "STRING", "NULLABLE"),
            ("synthesis_gcs_uri", "STRING", "NULLABLE"),
            ("lead_brief_output_id", "STRING", "NULLABLE"),
            ("lead_brief_gcs_uri", "STRING", "NULLABLE"),
            ("content_kind", "STRING", "NULLABLE"),
            ("email_rank", "INTEGER", "NULLABLE"),
            ("email_label", "STRING", "NULLABLE"),
            ("why_this_may_work", "STRING", "NULLABLE"),
            ("selected_for_hubspot", "BOOL", "NULLABLE"),
            ("lead_brief_eval_json", "JSON", "NULLABLE"),
            ("ai_hook_sources_url", "STRING", "NULLABLE"),
            ("style_profile_id", "STRING", "NULLABLE"),
            ("style_profile_version", "STRING", "NULLABLE"),
            ("style_profile_fallback_reason", "STRING", "NULLABLE"),
            ("positioning_snapshot_version", "STRING", "NULLABLE"),
            ("positioning_pillar", "STRING", "NULLABLE"),
            ("positioning_value_prop", "STRING", "NULLABLE"),
            ("writer_mode", "STRING", "NULLABLE"),
            ("candidate_hook_text", "STRING", "NULLABLE"),
            ("final_hook_text", "STRING", "NULLABLE"),
            ("generation_status", "STRING", "NULLABLE"),
            ("rewrite_attempted", "BOOL", "NULLABLE"),
            ("rewrite_reason", "STRING", "NULLABLE"),
            ("lint_result_json", "JSON", "NULLABLE"),
            ("critic_result_json", "JSON", "NULLABLE"),
            ("candidate_generation_idempotency_key", "STRING", "NULLABLE"),
            ("hook_text", "STRING", "NULLABLE"),
            ("hook_angle", "STRING", "NULLABLE"),
            ("hook_status", "STRING", "NULLABLE"),
            ("hubspot_hook_property_name", "STRING", "NULLABLE"),
            ("hubspot_sources_property_name", "STRING", "NULLABLE"),
            ("hubspot_outreach_writeback_status", "STRING", "NULLABLE"),
            ("hubspot_sources_writeback_status", "STRING", "NULLABLE"),
            ("hubspot_writeback_at", "TIMESTAMP", "NULLABLE"),
            ("hubspot_writeback_error", "STRING", "NULLABLE"),
            ("used_by_bdr", "BOOL", "NULLABLE"),
            ("edited_hook_text", "STRING", "NULLABLE"),
            ("outcome_status", "STRING", "NULLABLE"),
            ("schema_version", "STRING", "NULLABLE"),
            ("created_at", "TIMESTAMP", "NULLABLE"),
            ("updated_at", "TIMESTAMP", "NULLABLE"),
        ),
        "partition_field": "created_at",
        "clustering_fields": ("resolved_company_domain", "lead_id", "hook_status"),
    },
}

KNOWN_BIGQUERY_TABLE_IDS = {
    bigquery_table_id(table_name) for table_name in BIGQUERY_TABLE_DEFINITIONS
}
BIGQUERY_TABLE_NAMES_BY_ID = {
    bigquery_table_id(table_name): table_name for table_name in BIGQUERY_TABLE_DEFINITIONS
}


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _json_field(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


def build_external_service_costs(result: dict) -> dict:
    output = result.get("output") or {}
    tier_2 = output.get("tier_2_public_company_research") or {}
    tier_2_exa_cost = _non_negative_float(
        tier_2.get(
            "incremental_external_service_cost_dollars",
            tier_2.get("external_service_cost_dollars"),
        )
    )
    return {
        "tier_2": {
            "exa": tier_2_exa_cost,
        },
        "total": tier_2_exa_cost,
    }


def build_gcs_object_name(
    *,
    stage: str,
    run_id: str,
    output_id: str,
    extension: str = "json",
    prefix: str = GCS_ARTIFACT_PREFIX,
) -> str:
    clean_prefix = _clean_gcs_path_component(prefix, allow_slash=True)
    clean_stage = _clean_gcs_path_component(stage)
    clean_run_id = _clean_gcs_path_component(run_id)
    clean_output_id = _clean_gcs_path_component(output_id)
    clean_extension = extension.lstrip(".").strip() or "json"
    return f"{clean_prefix}/{clean_stage}/{clean_run_id}/{clean_output_id}.{clean_extension}"


def build_company_research_gcs_uri(
    *,
    output: dict,
    bucket_name: str = GCS_ARTIFACT_BUCKET,
    prefix: str = GCS_ARTIFACT_PREFIX,
) -> str:
    object_name = build_gcs_object_name(
        stage=output["stage"],
        run_id=output["run_id"],
        output_id=output["output_id"],
        prefix=prefix,
    )
    return f"gs://{bucket_name}/{object_name}"


def mark_dry_run_storage(output: dict) -> dict:
    storage = {
        "status": "dry_run_not_persisted",
        "gcs_uri": None,
        "bigquery_table": None,
        "bigquery_row_id": None,
    }
    output["storage"] = storage
    return storage


def mark_not_persisted_storage(output: dict) -> dict:
    storage = {
        "status": "not_persisted",
        "gcs_uri": None,
        "bigquery_table": None,
        "bigquery_row_id": None,
    }
    output["storage"] = storage
    return storage


def build_run_metadata_row(
    *,
    result: dict,
    completed_at: str | None = None,
    started_at: str | None = None,
    oz_run_id: str | None = None,
    oz_run_link: str | None = None,
    oz_session_link: str | None = None,
    oz_credits_used: float | None = None,
    external_service_costs: dict | None = None,
) -> dict:
    output = result["output"]
    completed_at = completed_at or result.get("completed_at") or _utc_now_iso()
    return {
        "run_id": result["run_id"],
        "stage": result["stage"],
        "trigger_source": output["trigger_source"],
        "lead_id": result["lead_id"],
        "contact_id": output["contact"].get("contact_id"),
        "company_id": output["company"].get("company_id"),
        "resolved_company_domain": output["hydration"].get("resolved_company_domain"),
        "started_at": started_at or result.get("started_at"),
        "completed_at": completed_at,
        "duration_seconds": result.get("duration_seconds"),
        "status": result["status"],
        "failure_reason": result.get("failure_reason"),
        "oz_run_id": oz_run_id,
        "oz_run_link": oz_run_link,
        "oz_session_link": oz_session_link,
        "oz_credits_used": oz_credits_used,
        "external_service_costs": _json_field(
            external_service_costs
            if external_service_costs is not None
            else build_external_service_costs(result)
        ),
        "created_at": completed_at,
    }


def build_output_index_row(
    *,
    output: dict,
    output_type: str = "company_research_json",
    gcs_uri: str | None = None,
) -> dict:
    return {
        "output_id": output["output_id"],
        "run_id": output["run_id"],
        "stage": output["stage"],
        "lead_id": output["lead"].get("lead_id"),
        "contact_id": output["contact"].get("contact_id"),
        "company_id": output["company"].get("company_id"),
        "resolved_company_domain": output["hydration"].get("resolved_company_domain"),
        "output_type": output_type,
        "schema_version": output["schema_version"],
        "bigquery_table": bigquery_table_id(COMPANY_RESEARCH_OUTPUTS_TABLE),
        "bigquery_row_id": output["output_id"],
        "gcs_uri": gcs_uri,
        "created_at": output["generated_at"],
    }


def build_company_research_output_row(*, output: dict, status: str, gcs_uri: str | None = None) -> dict:
    return {
        "output_id": output["output_id"],
        "run_id": output["run_id"],
        "lead_id": output["lead"].get("lead_id"),
        "contact_id": output["contact"].get("contact_id"),
        "company_id": output["company"].get("company_id"),
        "resolved_company_domain": output["hydration"].get("resolved_company_domain"),
        "trigger_source": output["trigger_source"],
        "hydration_status": output["hydration"].get("hydration_status"),
        "company_context_json": _json_field(
            {
                "lead": output["lead"],
                "contact": output["contact"],
                "company": output["company"],
                "hydration": output["hydration"],
            }
        ),
        "tier_1_internal_metrics_json": _json_field(output["tier_1_internal_metrics"]),
        "tier_2_public_research_json": _json_field(output["tier_2_public_company_research"]),
        "tier_3_external_research_json": _json_field(output["tier_3_external_research"]),
        "reuse_json": _json_field(output["reuse"]),
        "research_status": status,
        "schema_version": output.get("schema_version", SCHEMA_VERSION),
        "gcs_uri": gcs_uri,
        "created_at": output["generated_at"],
    }


def insert_rows(*, client: Any, table_id: str, rows: list[dict]) -> None:
    validate_bigquery_rows(table_id=table_id, rows=rows)
    errors = client.insert_rows_json(table_id, rows)
    if errors:
        raise RuntimeError(f"BigQuery insert failed for {table_id}: {errors}")


def ensure_bigquery_tables(*, client: Any, table_names: tuple[str, ...] = COMPANY_RESEARCH_BIGQUERY_TABLES) -> None:
    for table_name in table_names:
        if table_name not in BIGQUERY_TABLE_DEFINITIONS:
            raise ValueError(f"Refusing to ensure unknown BigQuery table: {table_name}")
        table_id = bigquery_table_id(table_name)
        _validate_known_table_id(table_id)
        definition = BIGQUERY_TABLE_DEFINITIONS[table_name]
        if hasattr(client, "ensure_table"):
            client.ensure_table(table_id=table_id, definition=definition)
            continue

        try:
            from google.cloud import bigquery
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "google-cloud-bigquery is required for BigQuery table creation. "
                "Install repository requirements or inject a fake client for tests."
            ) from exc

        table = bigquery.Table(
            table_id,
            schema=[
                bigquery.SchemaField(name, field_type, mode=mode)
                for name, field_type, mode in definition["schema"]
            ],
        )
        table.time_partitioning = bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY,
            field=definition["partition_field"],
        )
        table.clustering_fields = list(definition["clustering_fields"])
        client.create_table(table, exists_ok=True)


def write_company_research_artifact(
    *,
    output: dict,
    client: Any | None = None,
    bucket_name: str = GCS_ARTIFACT_BUCKET,
    prefix: str = GCS_ARTIFACT_PREFIX,
) -> str:
    object_name = build_gcs_object_name(
        stage=output["stage"],
        run_id=output["run_id"],
        output_id=output["output_id"],
        prefix=prefix,
    )
    payload = json.dumps(output, indent=2, sort_keys=True)
    if client is None:
        try:
            from google.cloud import storage
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "google-cloud-storage is required for GCS artifact writing. "
                "Install repository requirements or inject a fake storage client for tests."
            ) from exc

        client = storage.Client(project=GCP_PROJECT_ID)

    bucket = client.bucket(bucket_name)
    blob = bucket.blob(object_name)
    blob.upload_from_string(payload, content_type="application/json")
    return f"gs://{bucket_name}/{object_name}"


def persist_company_research_result(
    *,
    result: dict,
    client: Any | None = None,
    storage_client: Any | None = None,
    gcs_uri: str | None = None,
    write_gcs_artifact: bool = True,
    ensure_tables: bool = True,
) -> dict:
    """Persist run metadata, output index, company research rows, and a GCS artifact."""
    if client is None:
        from google.cloud import bigquery
        client = bigquery.Client(project=GCP_PROJECT_ID)
    output = result["output"]
    if output["stage"] != STAGE:
        raise ValueError(f"Refusing to persist unexpected stage: {output['stage']}")
    wrote_gcs_artifact = False

    if write_gcs_artifact and gcs_uri is None:
        output["storage"] = {
            "status": "pending_persistence",
            "gcs_uri": build_company_research_gcs_uri(output=output),
            "bigquery_table": bigquery_table_id(COMPANY_RESEARCH_OUTPUTS_TABLE),
            "bigquery_row_id": output["output_id"],
        }
        gcs_uri = write_company_research_artifact(output=output, client=storage_client)
        wrote_gcs_artifact = True

    if gcs_uri is not None and not gcs_uri.startswith(f"gs://{GCS_ARTIFACT_BUCKET}/{GCS_ARTIFACT_PREFIX}/"):
        raise ValueError(f"Refusing to persist unexpected GCS URI prefix: {gcs_uri}")

    if ensure_tables:
        ensure_bigquery_tables(client=client)
    oz_metadata = runtime_oz_metadata().as_bigquery_fields()
    rows_by_table = {
        bigquery_table_id(RUNS_TABLE): [build_run_metadata_row(result=result, **oz_metadata)],
        bigquery_table_id(OUTPUTS_TABLE): [build_output_index_row(output=output, gcs_uri=gcs_uri)],
        bigquery_table_id(COMPANY_RESEARCH_OUTPUTS_TABLE): [
            build_company_research_output_row(output=output, status=result["status"], gcs_uri=gcs_uri)
        ],
    }
    for table_id, rows in rows_by_table.items():
        insert_rows(client=client, table_id=table_id, rows=rows)

    output["storage"] = {
        "status": "persisted",
        "gcs_uri": gcs_uri,
        "bigquery_table": bigquery_table_id(COMPANY_RESEARCH_OUTPUTS_TABLE),
        "bigquery_row_id": output["output_id"],
    }
    if wrote_gcs_artifact:
        write_company_research_artifact(output=output, client=storage_client)
    return output["storage"]


def validate_known_table_id(table_id: str) -> None:
    if table_id not in KNOWN_BIGQUERY_TABLE_IDS:
        raise ValueError(f"Refusing to write to unknown BigQuery table: {table_id}")

def validate_bigquery_row_shape(*, table_id: str, row: dict) -> None:
    validate_known_table_id(table_id)
    table_name = BIGQUERY_TABLE_NAMES_BY_ID[table_id]
    expected_columns = {name for name, _, _ in BIGQUERY_TABLE_DEFINITIONS[table_name]["schema"]}
    actual_columns = set(row)
    missing_columns = expected_columns - actual_columns
    extra_columns = actual_columns - expected_columns
    if missing_columns or extra_columns:
        details = []
        if missing_columns:
            details.append(f"missing={sorted(missing_columns)}")
        if extra_columns:
            details.append(f"extra={sorted(extra_columns)}")
        raise ValueError(f"Row for {table_id} does not match schema: {', '.join(details)}")


def validate_bigquery_rows(*, table_id: str, rows: list[dict]) -> None:
    validate_known_table_id(table_id)
    if not rows:
        raise ValueError(f"No rows provided for {table_id}")
    for row in rows:
        validate_bigquery_row_shape(table_id=table_id, row=row)


def _validate_known_table_id(table_id: str) -> None:
    validate_known_table_id(table_id)


def _non_negative_float(value: Any) -> float:
    try:
        cost = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(cost) or cost < 0:
        return 0.0
    return round(cost, 6)


def _clean_gcs_path_component(value: str, *, allow_slash: bool = False) -> str:
    cleaned = str(value).strip().strip("/")
    if not cleaned:
        raise ValueError("GCS path components must be non-empty")
    disallowed = {"..", ".", ""}
    parts = cleaned.split("/")
    if any(part in disallowed for part in parts):
        raise ValueError(f"Unsafe GCS path component: {value!r}")
    if not allow_slash and "/" in cleaned:
        raise ValueError(f"GCS path component must not contain '/': {value!r}")
    return cleaned
