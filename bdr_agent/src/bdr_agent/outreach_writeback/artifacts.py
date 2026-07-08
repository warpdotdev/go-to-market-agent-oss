"""Artifact loading helpers for hook/writeback inputs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bdr_agent.stages.company_research.config import GCP_PROJECT_ID
from bdr_agent.outreach_writeback.config import GCS_ARTIFACT_PREFIX, STAGE


def build_candidate_hook_artifact_uri(
    *,
    run_id: str,
    output_id: str,
    artifact_base_uri: str = GCS_ARTIFACT_PREFIX,
) -> str:
    base_uri = artifact_base_uri.rstrip("/")
    if not base_uri.startswith("gs://"):
        raise ValueError("artifact_base_uri must be a gs:// URI")
    return f"{base_uri}/{STAGE}/{run_id}/{output_id}/candidate_hook.json"


def build_evaluate_input_artifact_uri(
    *,
    run_id: str,
    output_id: str,
    artifact_base_uri: str = GCS_ARTIFACT_PREFIX,
) -> str:
    base_uri = artifact_base_uri.rstrip("/")
    if not base_uri.startswith("gs://"):
        raise ValueError("artifact_base_uri must be a gs:// URI")
    return f"{base_uri}/{STAGE}/{run_id}/{output_id}/evaluate_and_writeback_input.json"


def write_json_to_gcs(*, gcs_uri: str, artifact: dict, client: Any | None = None) -> None:
    content = json.dumps(artifact, indent=2, sort_keys=True)
    if client is not None and hasattr(client, "upload_text"):
        client.upload_text(gcs_uri, content)
        return

    bucket_name, blob_name = parse_gcs_uri(gcs_uri)
    if client is None:
        from google.cloud import storage

        client = storage.Client(project=GCP_PROJECT_ID)
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.upload_from_string(content, content_type="application/json; charset=utf-8")


def load_synthesis_brief(
    *,
    synthesis_brief_file: str | None = None,
    synthesis_gcs_uri: str | None = None,
    fetch_synthesis_artifact: bool = False,
    storage_client: Any | None = None,
) -> str | None:
    """Load the synthesis brief from a local file or, when explicit, from GCS."""
    if synthesis_brief_file:
        return Path(synthesis_brief_file).read_text()
    if fetch_synthesis_artifact:
        if not synthesis_gcs_uri:
            raise ValueError("--fetch-synthesis-artifact requires --synthesis-gcs-uri.")
        return read_gcs_text(synthesis_gcs_uri, client=storage_client)
    return None


def load_evidence_packet(
    *,
    evidence_packet_json_file: str | None = None,
    synthesis_brief: str | None = None,
) -> dict | None:
    """Load the downstream hook evidence packet from JSON or a synthesis brief."""
    if evidence_packet_json_file:
        packet = json.loads(Path(evidence_packet_json_file).read_text())
        if not isinstance(packet, dict):
            raise ValueError("Evidence packet JSON must contain an object.")
        return packet
    if synthesis_brief:
        return extract_downstream_hook_evidence_packet(synthesis_brief)
    return None


def extract_downstream_hook_evidence_packet(synthesis_brief: str) -> dict | None:
    """Extract the Phase 3 downstream hook evidence packet from a synthesis brief."""
    section_marker = "## Downstream hook evidence packet"
    section_start = synthesis_brief.find(section_marker)
    if section_start == -1:
        return None
    block_start_marker = "```json"
    block_start = synthesis_brief.find(block_start_marker, section_start)
    if block_start == -1:
        return None
    block_start += len(block_start_marker)
    block_end = synthesis_brief.find("```", block_start)
    if block_end == -1:
        raise ValueError("Downstream hook evidence packet JSON block is not closed.")
    packet = json.loads(synthesis_brief[block_start:block_end])
    if not isinstance(packet, dict):
        raise ValueError("Downstream hook evidence packet must contain a JSON object.")
    return packet


def read_gcs_text(gcs_uri: str, *, client: Any | None = None) -> str:
    bucket_name, blob_name = parse_gcs_uri(gcs_uri)
    if client is None:
        try:
            from google.cloud import storage
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "google-cloud-storage is required to fetch SYNTHESIS_GCS_URI artifacts."
            ) from exc
        client = storage.Client(project=GCP_PROJECT_ID)
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    return blob.download_as_text()


def parse_gcs_uri(gcs_uri: str) -> tuple[str, str]:
    if not gcs_uri.startswith("gs://"):
        raise ValueError(f"Expected a gs:// URI, got: {gcs_uri}")
    remainder = gcs_uri.removeprefix("gs://")
    bucket_name, separator, blob_name = remainder.partition("/")
    if not bucket_name or not separator or not blob_name:
        raise ValueError(f"Expected a full gs://bucket/path URI, got: {gcs_uri}")
    return bucket_name, blob_name
