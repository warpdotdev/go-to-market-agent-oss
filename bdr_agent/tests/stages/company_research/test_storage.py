import json
import unittest
from unittest.mock import patch

from bdr_agent.stages.company_research.config import (
    COMPANY_RESEARCH_OUTPUTS_TABLE,
    GCS_ARTIFACT_BUCKET,
    GCS_ARTIFACT_PREFIX,
    OUTPUTS_TABLE,
    RUNS_TABLE,
    bigquery_table_id,
)
from bdr_agent.stages.company_research.schemas import build_minimal_company_research_output
from bdr_agent.stages.company_research.storage import (
    build_external_service_costs,
    build_company_research_gcs_uri,
    build_company_research_output_row,
    build_gcs_object_name,
    build_output_index_row,
    build_run_metadata_row,
    ensure_bigquery_tables,
    insert_rows,
    mark_dry_run_storage,
    persist_company_research_result,
    write_company_research_artifact,
)


class FakeBigQueryClient:
    def __init__(self) -> None:
        self.inserted = []
        self.ensured = []

    def insert_rows_json(self, table_id, rows):
        self.inserted.append((table_id, rows))
        return []
    def ensure_table(self, *, table_id, definition):
        self.ensured.append((table_id, definition))


class FakeBlob:
    def __init__(self, name) -> None:
        self.name = name
        self.uploads = []

    def upload_from_string(self, payload, content_type=None):
        self.uploads.append({"payload": payload, "content_type": content_type})


class FakeBucket:
    def __init__(self, name) -> None:
        self.name = name
        self.blobs = {}

    def blob(self, name):
        blob = FakeBlob(name)
        self.blobs[name] = blob
        return blob


class FakeStorageClient:
    def __init__(self) -> None:
        self.buckets = {}

    def bucket(self, name):
        bucket = FakeBucket(name)
        self.buckets[name] = bucket
        return bucket


def hydrated_result() -> dict:
    output = build_minimal_company_research_output(
        lead_id="lead_123",
        trigger_source="inbound_oz_campaign_pdf_download",
        hydration_status="hydrated",
        resolved_company_domain="example.com",
        resolved_company_domain_source="company.email_domain",
        lead={"lead_id": "lead_123", "created_at": "2026-05-18T00:00:00+00:00"},
        contact={"contact_id": "contact_123", "associated_company_id": "company_123"},
        company={"company_id": "company_123", "company_name": "Example"},
    )
    output["tier_1_internal_metrics"]["status"] = "found"
    output["tier_2_public_company_research"]["status"] = "found"
    return {
        "status": "research_complete",
        "stage": output["stage"],
        "lead_id": "lead_123",
        "source_system": "hubspot_workflow",
        "hubspot_workflow_id": "0000000000",
        "dry_run": False,
        "skip_bigquery": False,
        "run_id": output["run_id"],
        "output_id": output["output_id"],
        "started_at": "2026-05-18T00:59:58+00:00",
        "completed_at": "2026-05-18T01:00:00+00:00",
        "duration_seconds": 2.0,
        "output": output,
        "failure_reason": None,
    }


class StorageTest(unittest.TestCase):
    def test_build_gcs_object_name_uses_expected_artifact_prefix(self) -> None:
        object_name = build_gcs_object_name(
            stage="company_research",
            run_id="run_123",
            output_id="output_456",
        )

        self.assertEqual(object_name, "bdr-agent/company_research/run_123/output_456.json")

    def test_build_company_research_gcs_uri_uses_validated_bucket_and_prefix(self) -> None:
        output = hydrated_result()["output"]

        uri = build_company_research_gcs_uri(output=output)

        self.assertEqual(
            uri,
            f"gs://{GCS_ARTIFACT_BUCKET}/{GCS_ARTIFACT_PREFIX}/"
            f"company_research/{output['run_id']}/{output['output_id']}.json",
        )

    def test_build_gcs_object_name_rejects_unsafe_components(self) -> None:
        with self.assertRaises(ValueError):
            build_gcs_object_name(
                stage="../company_research",
                run_id="run_123",
                output_id="output_456",
            )

    def test_mark_dry_run_storage_never_sets_write_references(self) -> None:
        output = hydrated_result()["output"]

        storage = mark_dry_run_storage(output)

        self.assertEqual(storage["status"], "dry_run_not_persisted")
        self.assertIsNone(storage["gcs_uri"])
        self.assertIsNone(storage["bigquery_table"])
        self.assertIsNone(storage["bigquery_row_id"])
    def test_build_run_metadata_row(self) -> None:
        result = hydrated_result()

        row = build_run_metadata_row(result=result, completed_at="2026-05-18T01:00:00+00:00")

        self.assertEqual(row["run_id"], result["run_id"])
        self.assertEqual(row["stage"], "company_research")
        self.assertEqual(row["lead_id"], "lead_123")
        self.assertEqual(row["contact_id"], "contact_123")
        self.assertEqual(row["company_id"], "company_123")
        self.assertEqual(row["resolved_company_domain"], "example.com")
        self.assertEqual(row["started_at"], "2026-05-18T00:59:58+00:00")
        self.assertEqual(row["completed_at"], "2026-05-18T01:00:00+00:00")
        self.assertEqual(row["duration_seconds"], 2.0)
        self.assertEqual(row["status"], "research_complete")
        self.assertIsNone(row["oz_run_id"])
        self.assertIsNone(row["oz_run_link"])
        self.assertIsNone(row["oz_session_link"])
        self.assertIsNone(row["oz_credits_used"])
        self.assertEqual(json.loads(row["external_service_costs"]), {"tier_2": {"exa": 0.0}, "total": 0.0})
    def test_build_run_metadata_row_accepts_explicit_oz_metadata(self) -> None:
        result = hydrated_result()

        row = build_run_metadata_row(
            result=result,
            oz_run_id="00000000-0000-0000-0000-000000000000",
            oz_run_link="https://oz.staging.example.com/runs/00000000-0000-0000-0000-000000000000",
            oz_session_link="warpdev://session/50c0d275c2574b9da0cb89eb8c22c06d",
            oz_credits_used=1.25,
        )

        self.assertEqual(row["oz_run_id"], "00000000-0000-0000-0000-000000000000")
        self.assertEqual(
            row["oz_run_link"],
            "https://oz.staging.example.com/runs/00000000-0000-0000-0000-000000000000",
        )
        self.assertEqual(row["oz_session_link"], "warpdev://session/50c0d275c2574b9da0cb89eb8c22c06d")
        self.assertEqual(row["oz_credits_used"], 1.25)

    def test_build_run_metadata_row_rolls_up_tier_2_exa_costs(self) -> None:
        result = hydrated_result()
        result["output"]["tier_2_public_company_research"]["external_service_cost_dollars"] = 0.014

        row = build_run_metadata_row(result=result)

        self.assertEqual(json.loads(row["external_service_costs"]), {"tier_2": {"exa": 0.014}, "total": 0.014})

    def test_build_external_service_costs_prefers_incremental_cost_and_tolerates_invalid_values(self) -> None:
        result = hydrated_result()
        tier_2 = result["output"]["tier_2_public_company_research"]
        tier_2["external_service_cost_dollars"] = 0.014
        tier_2["incremental_external_service_cost_dollars"] = 0

        self.assertEqual(build_external_service_costs(result), {"tier_2": {"exa": 0.0}, "total": 0.0})

        tier_2["incremental_external_service_cost_dollars"] = "not-a-number"
        self.assertEqual(build_external_service_costs(result), {"tier_2": {"exa": 0.0}, "total": 0.0})

    def test_build_output_index_row_references_company_research_table(self) -> None:
        output = hydrated_result()["output"]

        row = build_output_index_row(output=output, gcs_uri="gs://bucket/path/output.json")

        self.assertEqual(row["output_id"], output["output_id"])
        self.assertEqual(row["bigquery_table"], bigquery_table_id(COMPANY_RESEARCH_OUTPUTS_TABLE))
        self.assertEqual(row["bigquery_row_id"], output["output_id"])
        self.assertEqual(row["gcs_uri"], "gs://bucket/path/output.json")

    def test_build_company_research_output_row_serializes_nested_json(self) -> None:
        result = hydrated_result()

        row = build_company_research_output_row(output=result["output"], status=result["status"])

        self.assertEqual(row["hydration_status"], "hydrated")
        self.assertEqual(row["research_status"], "research_complete")
        self.assertEqual(json.loads(row["company_context_json"])["hydration"]["resolved_company_domain"], "example.com")
        self.assertEqual(json.loads(row["tier_3_external_research_json"])["status"], "skipped")
    def test_ensure_bigquery_tables_uses_canonical_table_definitions(self) -> None:
        client = FakeBigQueryClient()

        ensure_bigquery_tables(client=client)

        self.assertEqual(
            [table_id for table_id, _ in client.ensured],
            [
                bigquery_table_id(RUNS_TABLE),
                bigquery_table_id(OUTPUTS_TABLE),
                bigquery_table_id(COMPANY_RESEARCH_OUTPUTS_TABLE),
            ],
        )
        self.assertEqual(client.ensured[0][1]["partition_field"], "created_at")

    def test_insert_rows_rejects_unknown_table(self) -> None:
        client = FakeBigQueryClient()

        with self.assertRaises(ValueError):
            insert_rows(client=client, table_id="example-gcp-project.gtm_agents.not_allowed", rows=[{}])

    def test_write_company_research_artifact_uploads_json_to_expected_path(self) -> None:
        storage_client = FakeStorageClient()
        output = hydrated_result()["output"]

        uri = write_company_research_artifact(output=output, client=storage_client)

        object_name = (
            f"{GCS_ARTIFACT_PREFIX}/company_research/"
            f"{output['run_id']}/{output['output_id']}.json"
        )
        self.assertEqual(uri, f"gs://{GCS_ARTIFACT_BUCKET}/{object_name}")
        blob = storage_client.buckets[GCS_ARTIFACT_BUCKET].blobs[object_name]
        self.assertEqual(blob.uploads[0]["content_type"], "application/json")
        self.assertEqual(json.loads(blob.uploads[0]["payload"])["output_id"], output["output_id"])

    def test_persist_company_research_result_inserts_three_rows_and_updates_storage(self) -> None:
        client = FakeBigQueryClient()
        storage_client = FakeStorageClient()
        result = hydrated_result()

        with patch.dict(
            "os.environ",
            {
                "OZ_RUN_ID": "00000000-0000-0000-0000-000000000000",
                "WARP_SERVER_ROOT_URL": "https://staging.example.com",
                "WARP_FOCUS_URL": "warpdev://session/50c0d275c2574b9da0cb89eb8c22c06d",
            },
            clear=True,
        ):
            storage = persist_company_research_result(
                result=result,
                client=client,
                storage_client=storage_client,
            )

        self.assertEqual(
            [table_id for table_id, _ in client.ensured],
            [
                bigquery_table_id(RUNS_TABLE),
                bigquery_table_id(OUTPUTS_TABLE),
                bigquery_table_id(COMPANY_RESEARCH_OUTPUTS_TABLE),
            ],
        )
        self.assertEqual(
            [table_id for table_id, _ in client.inserted],
            [
                bigquery_table_id(RUNS_TABLE),
                bigquery_table_id(OUTPUTS_TABLE),
                bigquery_table_id(COMPANY_RESEARCH_OUTPUTS_TABLE),
            ],
        )
        run_row = client.inserted[0][1][0]
        self.assertEqual(run_row["started_at"], result["started_at"])
        self.assertEqual(run_row["completed_at"], result["completed_at"])
        self.assertEqual(run_row["duration_seconds"], result["duration_seconds"])
        self.assertEqual(run_row["oz_run_id"], "00000000-0000-0000-0000-000000000000")
        self.assertEqual(
            run_row["oz_run_link"],
            "https://oz.staging.example.com/runs/00000000-0000-0000-0000-000000000000",
        )
        self.assertEqual(run_row["oz_session_link"], "warpdev://session/50c0d275c2574b9da0cb89eb8c22c06d")
        self.assertIsNone(run_row["oz_credits_used"])
        self.assertEqual(storage["status"], "persisted")
        self.assertEqual(storage["bigquery_table"], bigquery_table_id(COMPANY_RESEARCH_OUTPUTS_TABLE))
        self.assertTrue(storage["gcs_uri"].startswith(f"gs://{GCS_ARTIFACT_BUCKET}/{GCS_ARTIFACT_PREFIX}/"))

    def test_persist_company_research_result_rejects_unapproved_gcs_prefix(self) -> None:
        client = FakeBigQueryClient()
        result = hydrated_result()

        with self.assertRaises(ValueError):
            persist_company_research_result(
                result=result,
                client=client,
                gcs_uri="gs://other-bucket/bdr-agent/company_research/run/output.json",
                write_gcs_artifact=False,
            )


if __name__ == "__main__":
    unittest.main()
