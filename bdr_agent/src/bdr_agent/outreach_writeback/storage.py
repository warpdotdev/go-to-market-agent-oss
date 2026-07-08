"""Storage row helpers for hook/writeback outputs."""

from __future__ import annotations

import json
from typing import Any

from bdr_agent.stages.company_research.config import HOOKS_TABLE, OUTPUTS_TABLE, RUNS_TABLE, bigquery_table_id
from bdr_agent.stages.company_research.storage import build_run_metadata_row, validate_bigquery_rows
from bdr_agent.outreach_writeback.config import SCHEMA_VERSION


def _json_field(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


HOOK_JSON_FIELD_NAMES = ("lint_result_json", "critic_result_json", "lead_brief_eval_json")


def _serialize_hook_json_fields(hook_row: dict) -> dict:
    serialized = dict(hook_row)
    for field_name in HOOK_JSON_FIELD_NAMES:
        value = serialized.get(field_name)
        if value is not None and not isinstance(value, str):
            serialized[field_name] = _json_field(value)
    return serialized


def build_hook_output_index_row(*, hook_row: dict, gcs_uri: str | None = None) -> dict:
    return {
        "output_id": hook_row["output_id"],
        "run_id": hook_row["run_id"],
        "stage": "outreach_writeback",
        "lead_id": hook_row.get("lead_id"),
        "contact_id": hook_row.get("contact_id"),
        "company_id": hook_row.get("company_id"),
        "resolved_company_domain": hook_row.get("resolved_company_domain"),
        "output_type": "hook_json",
        "schema_version": hook_row.get("schema_version", SCHEMA_VERSION),
        "bigquery_table": bigquery_table_id(HOOKS_TABLE),
        "bigquery_row_id": hook_row["hook_id"],
        "gcs_uri": gcs_uri,
        "created_at": hook_row["created_at"],
    }


def build_hook_run_metadata_row(*, result: dict) -> dict:
    hook_row = result["hook_row"]
    compatible_result = {
        "run_id": hook_row["run_id"],
        "stage": "outreach_writeback",
        "lead_id": hook_row["lead_id"],
        "status": result["status"],
        "failure_reason": result.get("failure_reason"),
        "output": {
            "trigger_source": result.get("trigger_source"),
            "contact": {"contact_id": hook_row.get("contact_id")},
            "company": {"company_id": hook_row.get("company_id")},
            "hydration": {"resolved_company_domain": hook_row.get("resolved_company_domain")},
        },
    }
    return build_run_metadata_row(result=compatible_result, external_service_costs={})


def insert_rows(*, client: Any, table_id: str, rows: list[dict], row_ids: list[str] | None = None) -> None:
    validate_bigquery_rows(table_id=table_id, rows=rows)
    kwargs: dict = {}
    if row_ids is not None:
        kwargs["row_ids"] = row_ids
    errors = client.insert_rows_json(table_id, rows, **kwargs)
    if errors:
        raise RuntimeError(f"BigQuery insert failed for {table_id}: {errors}")


def persist_hook_result(*, result: dict, client: Any, gcs_uri: str | None = None) -> dict:
    hook_row = dict(result["hook_row"])
    hook_bigquery_row = _serialize_hook_json_fields(hook_row)
    # Insert hook row first so that a partial failure leaves no orphan run/output rows.
    # Deterministic row_ids enable BigQuery streaming deduplication on retries.
    insert_rows(
        client=client,
        table_id=bigquery_table_id(HOOKS_TABLE),
        rows=[hook_bigquery_row],
        row_ids=[hook_row["hook_id"]],
    )
    insert_rows(
        client=client,
        table_id=bigquery_table_id(OUTPUTS_TABLE),
        rows=[build_hook_output_index_row(hook_row=hook_row, gcs_uri=gcs_uri)],
        row_ids=[hook_row["output_id"]],
    )
    insert_rows(
        client=client,
        table_id=bigquery_table_id(RUNS_TABLE),
        rows=[build_hook_run_metadata_row(result=result)],
        row_ids=[hook_row["run_id"]],
    )
    return {
        "status": "persisted",
        "tables": [
            bigquery_table_id(HOOKS_TABLE),
            bigquery_table_id(OUTPUTS_TABLE),
            bigquery_table_id(RUNS_TABLE),
        ],
        "hook_bigquery_table": bigquery_table_id(HOOKS_TABLE),
        "hook_bigquery_row_id": hook_row["hook_id"],
    }

