"""IDs, timestamps, and validation helpers for BDR Agent runtime code."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4


def new_run_id() -> str:
    return f"bdr_run_{uuid4().hex}"


def new_output_id() -> str:
    return f"bdr_output_{uuid4().hex}"


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def require_non_empty(value: str | None, field_name: str) -> str:
    if value is None or not str(value).strip():
        raise ValueError(f"{field_name} is required")
    return str(value).strip()
