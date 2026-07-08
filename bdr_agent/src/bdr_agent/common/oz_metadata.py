"""Helpers for reading Oz runtime metadata from the agent environment."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
import os
from urllib.parse import urlparse, urlunparse


@dataclass(frozen=True)
class OzRunMetadata:
    oz_run_id: str | None = None
    oz_run_link: str | None = None
    oz_session_link: str | None = None
    oz_credits_used: float | None = None

    def as_bigquery_fields(self) -> dict:
        return {
            "oz_run_id": self.oz_run_id,
            "oz_run_link": self.oz_run_link,
            "oz_session_link": self.oz_session_link,
            "oz_credits_used": self.oz_credits_used,
        }


def runtime_oz_metadata(environ: Mapping[str, str] | None = None) -> OzRunMetadata:
    env = environ if environ is not None else os.environ
    oz_run_id = _clean(env.get("OZ_RUN_ID"))
    return OzRunMetadata(
        oz_run_id=oz_run_id,
        oz_run_link=_runtime_oz_run_link(env=env, oz_run_id=oz_run_id),
        oz_session_link=_clean(env.get("WARP_FOCUS_URL")),
        oz_credits_used=_runtime_credits_used(env),
    )


def _runtime_oz_run_link(*, env: Mapping[str, str], oz_run_id: str | None) -> str | None:
    explicit_link = _clean(env.get("OZ_RUN_LINK") or env.get("OZ_RUN_URL"))
    if explicit_link:
        return explicit_link
    if oz_run_id is None:
        return None
    server_root_url = _clean(env.get("WARP_SERVER_ROOT_URL"))
    if server_root_url is None:
        return None
    return _build_oz_run_link(server_root_url=server_root_url, oz_run_id=oz_run_id)


def _build_oz_run_link(*, server_root_url: str, oz_run_id: str) -> str | None:
    parsed = urlparse(server_root_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    host = parsed.netloc
    if host == "staging.example.com":
        host = "oz.staging.example.com"
    elif host == "example.com":
        host = "oz.example.com"
    elif not host.startswith("oz.") and host.endswith(".example.com"):
        host = f"oz.{host.removeprefix('app.')}"
    return urlunparse((parsed.scheme, host, f"/runs/{oz_run_id}", "", "", ""))


def _runtime_credits_used(env: Mapping[str, str]) -> float | None:
    for key in ("OZ_CREDITS_USED", "WARP_CREDITS_USED"):
        value = _non_negative_float(env.get(key))
        if value is not None:
            return value
    return None


def _non_negative_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    if not math.isfinite(parsed) or parsed < 0:
        return None
    return parsed


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None
