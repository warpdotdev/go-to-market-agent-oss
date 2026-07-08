"""Shared GCS artifact helpers for BDR Agent runtime code."""

from __future__ import annotations

from urllib.parse import urlparse

from bdr_agent.stages.company_research.config import GCP_PROJECT_ID


def write_text_to_gcs(*, gcs_uri: str, content: str, client: object | None = None) -> None:
    if client is not None and hasattr(client, "upload_text"):
        client.upload_text(gcs_uri, content)
        return

    bucket_name, blob_name = parse_gcs_uri(gcs_uri)
    if client is None:
        from google.cloud import storage

        client = storage.Client(project=GCP_PROJECT_ID)
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.upload_from_string(content, content_type="text/markdown; charset=utf-8")


def read_text_from_gcs(*, gcs_uri: str, client: object | None = None) -> str:
    if client is not None and hasattr(client, "download_text"):
        return client.download_text(gcs_uri)

    bucket_name, blob_name = parse_gcs_uri(gcs_uri)
    if client is None:
        from google.cloud import storage

        client = storage.Client(project=GCP_PROJECT_ID)
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    return blob.download_as_text()


def parse_gcs_uri(gcs_uri: str) -> tuple[str, str]:
    parsed = urlparse(gcs_uri)
    if parsed.scheme != "gs" or not parsed.netloc or not parsed.path.strip("/"):
        raise ValueError(f"Invalid GCS URI: {gcs_uri}")
    return parsed.netloc, parsed.path.lstrip("/")
