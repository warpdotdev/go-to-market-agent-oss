"""Shared HubSpot API client.

Handles authentication, rate-limit retries, and cursor-based pagination.
Uses only the Python standard library (urllib).
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://api.hubapi.com"
_token = None


def _load_token():
    """Load the HubSpot private app token from env or .env file."""
    global _token
    if _token:
        return _token

    # Support multiple env var names for the HubSpot private app token
    _token = (
        os.environ.get("HUBSPOT_API_KEY")
        or os.environ.get("HUBSPOT_PRIVATE_APP_TOKEN")
        or os.environ.get("GENERAL_HUBSPOT_APP_TOKEN")
        or os.environ.get("HUBSPOT_ACCESS_TOKEN")
    )
    if not _token:
        # Walk up from this file to find .env next to the package
        env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("HUBSPOT_API_KEY="):
                        _token = line.split("=", 1)[1]
                    elif not _token and line.startswith("HUBSPOT_PRIVATE_APP_TOKEN="):
                        _token = line.split("=", 1)[1]
                    elif not _token and line.startswith("GENERAL_HUBSPOT_APP_TOKEN="):
                        _token = line.split("=", 1)[1]
                    elif not _token and line.startswith("HUBSPOT_ACCESS_TOKEN="):
                        _token = line.split("=", 1)[1]
    if not _token:
        raise RuntimeError(
            "HUBSPOT_API_KEY or HUBSPOT_PRIVATE_APP_TOKEN not found in environment or .env file"
        )
    return _token


def hubspot_request(method, path, data=None, max_retries=3):
    """Make an authenticated request to the HubSpot API.

    Automatically retries on 429 (rate limit) responses.
    Returns the parsed JSON response body, or None for 204 responses.
    """
    token = _load_token()
    url = f"{BASE_URL}{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    body = json.dumps(data).encode() if data is not None else None

    for attempt in range(max_retries):
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as resp:
                if resp.status == 204:
                    return None
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < max_retries - 1:
                retry_after = int(e.headers.get("Retry-After", 1))
                print(f"  Rate limited — waiting {retry_after}s…")
                time.sleep(retry_after)
                continue
            if e.code in (502, 503, 504) and attempt < max_retries - 1:
                wait = 2 ** attempt  # 1s, 2s, 4s …
                print(f"  HTTP {e.code} (transient) — retrying in {wait}s (attempt {attempt + 1}/{max_retries})…")
                time.sleep(wait)
                continue
            error_body = e.read().decode()[:500]
            print(f"  HTTP {e.code}: {error_body}")
            raise


def paginated_get(path, key="results", params=None):
    """Auto-paginate a GET endpoint that uses cursor-based paging.

    Yields individual items from the *key* list in each response page.
    """
    sep = "&" if "?" in path else "?"
    after = None
    while True:
        page_path = path
        parts = []
        if params:
            parts.extend(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
        if after:
            parts.append(f"after={urllib.parse.quote(str(after))}")
        if parts:
            page_path = f"{path}{sep}{'&'.join(parts)}"
        result = hubspot_request("GET", page_path)
        for item in result.get(key, []):
            yield item
        paging = result.get("paging", {})
        next_page = paging.get("next", {})
        after = next_page.get("after")
        if not after:
            break
