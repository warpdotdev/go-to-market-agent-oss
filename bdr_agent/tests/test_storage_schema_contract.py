import re
import unittest
from pathlib import Path

from bdr_agent.stages.company_research.config import (
    BDR_AGENT_BIGQUERY_TABLES,
    COMPANY_RESEARCH_OUTPUTS_TABLE,
    HOOKS_TABLE,
    OUTPUTS_TABLE,
    RUNS_TABLE,
    bigquery_table_id,
)
from bdr_agent.stages.company_research.schemas import build_minimal_company_research_output
from bdr_agent.stages.company_research.storage import (
    BIGQUERY_TABLE_DEFINITIONS,
    build_company_research_output_row,
    build_output_index_row as build_company_research_output_index_row,
    build_run_metadata_row as build_company_research_run_metadata_row,
    validate_bigquery_row_shape,
)
from bdr_agent.outreach_writeback.schemas import build_hook_row
from bdr_agent.outreach_writeback.storage import (
    build_hook_output_index_row,
    build_hook_run_metadata_row,
    insert_rows as insert_hook_rows,
)


class FakeBigQueryClient:
    def __init__(self) -> None:
        self.inserted = []

    def insert_rows_json(self, table_id, rows):
        self.inserted.append((table_id, rows))
        return []


def company_research_result() -> dict:
    output = build_minimal_company_research_output(
        lead_id="lead_123",
        trigger_source="inbound_oz_campaign_pdf_download",
        hydration_status="hydrated",
        resolved_company_domain="example.com",
        resolved_company_domain_source="company.email_domain",
        lead={"lead_id": "lead_123", "created_at": "2026-05-18T00:00:00+00:00"},
        contact={"contact_id": "contact_123", "associated_company_id": "company_123"},
        company={"company_id": "company_123", "company_name": "Example"},
        run_id="company_run_123",
        output_id="company_output_123",
        generated_at="2026-05-19T00:00:00+00:00",
    )
    return {
        "status": "research_complete",
        "stage": output["stage"],
        "lead_id": "lead_123",
        "run_id": output["run_id"],
        "output_id": output["output_id"],
        "output": output,
        "failure_reason": None,
    }



def hook_row() -> dict:
    return build_hook_row(
        selected_hook={
            "hook_angle": "default_angle",
            "hook_text": "A deterministic schema contract hook.",
            "source_labels": ["default"],
            "evidence_summary": None,
        },
        lead_id="lead_123",
        contact_id="contact_123",
        company_id="company_123",
        resolved_company_domain="example.com",
        company_research_output_id="company_output_123",
        synthesis_run_id="synthesis_run_123",
        synthesis_output_id="synthesis_output_123",
        synthesis_gcs_uri="gs://example-artifacts-bucket/bdr-agent/synthesis/synthesis_run_123/synthesis_output_123.md",
        run_id="hook_run_123",
        output_id="hook_output_123",
        hook_id="hook_123",
        created_at="2026-05-19T02:00:00+00:00",
    )


def parsed_reference_sql_schema() -> dict:
    sql_path = Path(__file__).parents[1] / "sql" / "tables" / "storage_tables.sql"
    sql = sql_path.read_text()
    schemas = {}
    for match in re.finditer(
        r"CREATE TABLE IF NOT EXISTS `(?P<table_id>[^`]+)` \(\n(?P<body>.*?)\n\)",
        sql,
        re.DOTALL,
    ):
        table_name = match.group("table_id").split(".")[-1]
        columns = []
        for line in match.group("body").splitlines():
            stripped = line.strip().rstrip(",")
            if not stripped:
                continue
            name, field_type, *rest = stripped.split()
            normalized_type = "FLOAT" if field_type == "FLOAT64" else field_type
            mode = "REQUIRED" if "NOT NULL" in " ".join(rest) else "NULLABLE"
            columns.append((name, normalized_type, mode))
        schemas[table_name] = tuple(columns)
    return schemas


def parsed_hooks_migration_columns() -> dict:
    sql_path = (
        Path(__file__).parents[1]
        / "sql"
        / "migrations"
        / "20260520_alter_bdr_agent_hooks_candidate_columns.sql"
    )
    sql = sql_path.read_text()
    columns = {}
    for match in re.finditer(
        r"ADD COLUMN IF NOT EXISTS (?P<name>\w+) (?P<field_type>\w+);",
        sql,
    ):
        columns[match.group("name")] = match.group("field_type")
    return columns


def parsed_lead_brief_migration_columns() -> dict:
    sql_path = (
        Path(__file__).parents[1]
        / "sql"
        / "migrations"
        / "20260521_alter_bdr_agent_hooks_lead_brief_columns.sql"
    )
    sql = sql_path.read_text()
    columns = {}
    for match in re.finditer(
        r"ADD COLUMN IF NOT EXISTS (?P<name>\w+) (?P<field_type>\w+);",
        sql,
    ):
        columns[match.group("name")] = match.group("field_type")
    return columns

def outreach_composer_compat_view_sql() -> str:
    sql_path = (
        Path(__file__).parents[1]
        / "sql"
        / "migrations"
        / "20260602_create_outreach_composer_compat_views.sql"
    )
    return sql_path.read_text()


def parsed_outreach_composer_compat_view_names() -> set[str]:
    sql = outreach_composer_compat_view_sql()
    return {
        match.group("view_id").split(".")[-1]
        for match in re.finditer(
            r"CREATE OR REPLACE VIEW `(?P<view_id>[^`]+)` AS",
            sql,
        )
    }


class StorageSchemaContractTest(unittest.TestCase):
    def assert_row_matches_table(self, table_name: str, row: dict) -> None:
        table_id = bigquery_table_id(table_name)

        validate_bigquery_row_shape(table_id=table_id, row=row)

        expected_columns = {name for name, _, _ in BIGQUERY_TABLE_DEFINITIONS[table_name]["schema"]}
        self.assertEqual(set(row), expected_columns)

    def test_python_table_definitions_match_reference_sql_for_all_mvp_tables(self) -> None:
        reference_schema = parsed_reference_sql_schema()

        self.assertEqual(set(reference_schema), set(BDR_AGENT_BIGQUERY_TABLES))
        self.assertEqual(set(BIGQUERY_TABLE_DEFINITIONS), set(BDR_AGENT_BIGQUERY_TABLES))
        for table_name in BDR_AGENT_BIGQUERY_TABLES:
            self.assertEqual(
                BIGQUERY_TABLE_DEFINITIONS[table_name]["schema"],
                reference_schema[table_name],
            )

    def test_hooks_migration_contains_all_candidate_lifecycle_columns(self) -> None:
        migration_columns = parsed_hooks_migration_columns()
        expected_columns = {
            "style_profile_id": "STRING",
            "style_profile_version": "STRING",
            "style_profile_fallback_reason": "STRING",
            "positioning_snapshot_version": "STRING",
            "positioning_pillar": "STRING",
            "positioning_value_prop": "STRING",
            "writer_mode": "STRING",
            "candidate_hook_text": "STRING",
            "final_hook_text": "STRING",
            "generation_status": "STRING",
            "rewrite_attempted": "BOOL",
            "rewrite_reason": "STRING",
            "lint_result_json": "JSON",
            "critic_result_json": "JSON",
            "candidate_generation_idempotency_key": "STRING",
        }

        self.assertEqual(migration_columns, expected_columns)
        hook_schema = dict(
            (name, field_type)
            for name, field_type, _ in BIGQUERY_TABLE_DEFINITIONS[HOOKS_TABLE]["schema"]
        )
        for column_name, field_type in expected_columns.items():
            self.assertEqual(hook_schema[column_name], field_type)

    def test_lead_brief_migration_contains_all_ranked_email_columns(self) -> None:
        migration_columns = parsed_lead_brief_migration_columns()
        expected_columns = {
            "company_research_output_id": "STRING",
            "lead_brief_output_id": "STRING",
            "lead_brief_gcs_uri": "STRING",
            "content_kind": "STRING",
            "email_rank": "INTEGER",
            "email_label": "STRING",
            "why_this_may_work": "STRING",
            "selected_for_hubspot": "BOOL",
            "lead_brief_eval_json": "JSON",
        }

        self.assertEqual(migration_columns, expected_columns)
        hook_schema = dict(
            (name, field_type)
            for name, field_type, _ in BIGQUERY_TABLE_DEFINITIONS[HOOKS_TABLE]["schema"]
        )
        for column_name, field_type in expected_columns.items():
            self.assertEqual(hook_schema[column_name], field_type)

    def test_outreach_composer_compat_views_are_additive_aliases_only(self) -> None:
        sql = outreach_composer_compat_view_sql()

        self.assertEqual(
            parsed_outreach_composer_compat_view_names(),
            {
                "bdr_agent_outreach_composer_runs",
                "bdr_agent_outreach_composer_outputs",
                "bdr_agent_outreach_composer_email_bodies",
            },
        )
        self.assertNotRegex(sql, r"\bDROP\b")
        self.assertNotRegex(sql, r"\bALTER\s+TABLE\b")
        self.assertNotRegex(sql, r"\bCREATE\s+TABLE\b")
        self.assertIn("'outreach_composer' AS canonical_stage", sql)
        self.assertIn("runs.stage AS legacy_stage", sql)
        self.assertIn("outputs.stage AS legacy_stage", sql)
        self.assertIn("outputs.output_type AS legacy_output_type", sql)
        self.assertIn("outputs.gcs_uri AS outreach_composer_gcs_uri", sql)
        self.assertIn("hooks.lead_brief_output_id AS outreach_composer_output_id", sql)
        self.assertIn("hooks.lead_brief_gcs_uri AS outreach_composer_gcs_uri", sql)
        self.assertIn("hooks.lead_brief_eval_json AS outreach_composer_eval_json", sql)
        self.assertIn(
            "COALESCE(hooks.final_hook_text, hooks.hook_text, hooks.candidate_hook_text) AS email_body_text",
            sql,
        )
        self.assertIn("WHERE runs.stage = 'lead_brief'", sql)
        self.assertIn("outputs.stage IN ('lead_brief', 'lead_brief_slack_delivery')", sql)

    def test_company_research_rows_match_canonical_table_shapes(self) -> None:
        result = company_research_result()

        self.assert_row_matches_table(
            RUNS_TABLE,
            build_company_research_run_metadata_row(result=result),
        )
        self.assert_row_matches_table(
            OUTPUTS_TABLE,
            build_company_research_output_index_row(output=result["output"]),
        )
        self.assert_row_matches_table(
            COMPANY_RESEARCH_OUTPUTS_TABLE,
            build_company_research_output_row(output=result["output"], status=result["status"]),
        )

    def test_hook_rows_match_canonical_table_shapes(self) -> None:
        row = hook_row()
        result = {
            "status": row["hook_status"],
            "trigger_source": "stage_completion",
            "hook_row": row,
            "failure_reason": None,
        }

        self.assert_row_matches_table(RUNS_TABLE, build_hook_run_metadata_row(result=result))
        self.assert_row_matches_table(OUTPUTS_TABLE, build_hook_output_index_row(hook_row=row))
        self.assert_row_matches_table(HOOKS_TABLE, row)

    def test_row_shape_validation_rejects_missing_and_extra_columns_before_insert(self) -> None:
        client = FakeBigQueryClient()
        row = build_hook_output_index_row(hook_row=hook_row())
        row.pop("stage")
        row["unexpected_column"] = "unexpected"

        with self.assertRaisesRegex(ValueError, "missing=\\['stage'\\].*extra=\\['unexpected_column'\\]"):
            insert_hook_rows(client=client, table_id=bigquery_table_id(OUTPUTS_TABLE), rows=[row])

        self.assertEqual(client.inserted, [])

    def test_hook_insert_rejects_unknown_table_before_write(self) -> None:
        client = FakeBigQueryClient()

        with self.assertRaisesRegex(ValueError, "unknown BigQuery table"):
            insert_hook_rows(
                client=client,
                table_id="example-gcp-project.gtm_agents.not_allowed",
                rows=[hook_row()],
            )

        self.assertEqual(client.inserted, [])


if __name__ == "__main__":
    unittest.main()
