"""Duplicate detection and merging for contacts and companies.

HubSpot has no duplicate-detection API, so we pull records and group them
locally by key fields (email, domain, name) to find clusters.

Merge direction: always merge the *newer* record into the *older* one
(the older record is the primary/surviving record).
"""

import csv
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from .client import hubspot_request, paginated_get

PORTAL_ID = os.environ.get("HUBSPOT_PORTAL_ID", "000000000")


# ---------------------------------------------------------------------------
# Fetching records
# ---------------------------------------------------------------------------

def _fetch_all(object_type, properties, limit=100):
    """Fetch all records of a given type with specified properties."""
    props = ",".join(properties)
    path = f"/crm/v3/objects/{object_type}?limit={limit}&properties={props}"
    return list(paginated_get(path, key="results"))


def _search_recent(object_type, properties, days=3):
    """Search for records created in the last *days* days via the Search API.

    Much faster than _fetch_all for large portals — only pulls recent records.

    For contacts, the Search API doesn't reliably support date filters, so
    we sort by createdate descending and paginate until we pass the cutoff.
    For other object types, we use a GTE filter on createdate.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff_ms = str(int(cutoff.timestamp() * 1000))

    results = []
    after = None
    use_filter = object_type != "contacts"

    while True:
        body = {
            "sorts": [{"propertyName": "createdate", "direction": "DESCENDING"}],
            "properties": properties,
            "limit": 100,
        }
        if use_filter:
            body["filterGroups"] = [{
                "filters": [{
                    "propertyName": "createdate",
                    "operator": "GTE",
                    "value": cutoff_ms,
                }]
            }]
        if after:
            body["after"] = after
        resp = hubspot_request("POST", f"/crm/v3/objects/{object_type}/search", data=body)
        page = resp.get("results", [])

        if not use_filter:
            # Client-side date filtering for contacts
            for r in page:
                cd = r.get("properties", {}).get("createdate", "")
                if cd and _parse_date(cd) < cutoff:
                    return results  # hit records older than cutoff, stop
                results.append(r)
        else:
            results.extend(page)

        after = resp.get("paging", {}).get("next", {}).get("after")
        if not after:
            break
        # Safety cap for contacts: stop after 10k records max
        if not use_filter and len(results) >= 10000:
            print(f"  (capped at {len(results)} contacts)")
            break
    return results


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------

def find_duplicate_contacts(match_on="email"):
    """Find duplicate contacts (full scan — slow for large portals).

    match_on:
      "email"  — group by email (most reliable)
      "name"   — group by lowercase firstname+lastname
    """
    if match_on == "email":
        properties = ["email", "firstname", "lastname", "createdate"]
    else:
        properties = ["email", "firstname", "lastname", "company", "createdate"]

    print("  Fetching all contacts…")
    contacts = _fetch_all("contacts", properties)
    print(f"  Retrieved {len(contacts)} contacts")

    groups = defaultdict(list)
    for c in contacts:
        props = c.get("properties", {})
        if match_on == "email":
            key = (props.get("email") or "").strip().lower()
        else:
            fn = (props.get("firstname") or "").strip().lower()
            ln = (props.get("lastname") or "").strip().lower()
            key = f"{fn}|{ln}" if fn or ln else ""
        if key:
            groups[key].append(c)

    # Only keep groups with more than one record
    dupes = {k: v for k, v in groups.items() if len(v) > 1}
    print(f"  Found {len(dupes)} duplicate clusters ({sum(len(v) for v in dupes.values())} total records)")
    return dupes


def find_duplicate_companies(match_on="domain"):
    """Find duplicate companies (full scan — slow for large portals).

    match_on:
      "domain" — group by domain
      "name"   — group by lowercase company name
    """
    properties = ["domain", "name", "createdate"]
    print("  Fetching all companies…")
    companies = _fetch_all("companies", properties)
    print(f"  Retrieved {len(companies)} companies")

    groups = defaultdict(list)
    for c in companies:
        props = c.get("properties", {})
        if match_on == "domain":
            key = (props.get("domain") or "").strip().lower()
        else:
            key = (props.get("name") or "").strip().lower()
        if key:
            groups[key].append(c)

    dupes = {k: v for k, v in groups.items() if len(v) > 1}
    print(f"  Found {len(dupes)} duplicate clusters ({sum(len(v) for v in dupes.values())} total records)")
    return dupes


# ---------------------------------------------------------------------------
# Incremental duplicate scan (for scheduled runs)
# ---------------------------------------------------------------------------

# Properties to request per object type (avoids requesting invalid fields)
_OBJECT_PROPERTIES = {
    "contacts":  ["email", "firstname", "lastname", "createdate"],
    "companies": ["domain", "name", "createdate"],
    "deals":     ["dealname", "amount", "dealstage", "createdate"],
}


def _search_existing_by_key(object_type, prop_name, value):
    """Search for existing records matching a specific property value."""
    props = _OBJECT_PROPERTIES.get(object_type, [prop_name, "createdate"])
    body = {
        "filterGroups": [{
            "filters": [{
                "propertyName": prop_name,
                "operator": "EQ",
                "value": value,
            }]
        }],
        "properties": props,
        "limit": 10,
    }
    resp = hubspot_request("POST", f"/crm/v3/objects/{object_type}/search", data=body)
    return resp.get("results", [])


def scan_recent_duplicates(object_type, days=3):
    """Incremental scan: find duplicates among recently created records.

    Strategy varies by volume:
      - **Contacts** (high volume): group recent records locally by email.
        HubSpot auto-deduplicates contacts by email on creation, so cross-DB
        lookups aren't needed — local grouping catches API/import bypasses.
      - **Companies / Deals** (lower volume): for each unique key in the
        recent batch, search the full database for matches.

    Returns dict {key: [records]} of duplicate clusters.
    """
    config = {
        "contacts":  {"key": "email",    "props": ["email", "firstname", "lastname", "createdate"]},
        "companies": {"key": "domain",   "props": ["domain", "name", "createdate"]},
        "deals":     {"key": "dealname", "props": ["dealname", "amount", "dealstage", "createdate"]},
    }
    cfg = config.get(object_type)
    if not cfg:
        print(f"  Unsupported object type for incremental scan: {object_type}")
        return {}

    key_field = cfg["key"]
    properties = cfg["props"]

    print(f"  Scanning {object_type} created in the last {days} days…")
    recent = _search_recent(object_type, properties, days=days)
    print(f"  Found {len(recent)} recent {object_type}")

    if not recent:
        return {}

    # Group the recent batch locally by key field
    groups = defaultdict(list)
    for r in recent:
        val = (r.get("properties", {}).get(key_field) or "").strip().lower()
        if val:
            groups[val].append(r)

    # Duplicates found within the recent batch itself (instant)
    local_dupes = {k: v for k, v in groups.items() if len(v) > 1}
    if local_dupes:
        print(f"  Found {len(local_dupes)} duplicate clusters within recent records")

    # For small batches (<= 200 unique keys), also check each key against
    # the full database to catch recent-vs-old duplicates.
    # For large batches, skip cross-DB lookups (too many API calls).
    unique_keys = {k for k, v in groups.items() if len(v) == 1}
    cross_db_dupes = {}

    if len(unique_keys) <= 200:
        print(f"  Checking {len(unique_keys)} unique {key_field} values against full database…")
        for i, key_val in enumerate(unique_keys):
            matches = _search_existing_by_key(object_type, key_field, key_val)
            if len(matches) > 1:
                cross_db_dupes[key_val] = matches
            if (i + 1) % 50 == 0:
                print(f"    Checked {i + 1}/{len(unique_keys)}…")
        if cross_db_dupes:
            print(f"  Found {len(cross_db_dupes)} additional clusters matching older records")
    else:
        print(f"  Skipping cross-DB lookup ({len(unique_keys)} unique keys — too many for per-key API calls)")

    # Merge local and cross-DB results
    dupes = {**local_dupes, **cross_db_dupes}
    total_records = sum(len(v) for v in dupes.values())
    print(f"  Total: {len(dupes)} duplicate clusters ({total_records} records)")
    return dupes


# ---------------------------------------------------------------------------
# Merging
# ---------------------------------------------------------------------------

def _parse_date(date_str):
    """Parse an ISO date string to a datetime for comparison."""
    if not date_str:
        return datetime.min
    try:
        return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return datetime.min


def merge_records(object_type, primary_id, secondary_id):
    """Merge secondary_id into primary_id (primary survives).

    POST /crm/v3/objects/{objectType}/merge
    """
    data = {
        "primaryObjectId": str(primary_id),
        "objectIdToMerge": str(secondary_id),
    }
    return hubspot_request("POST", f"/crm/v3/objects/{object_type}/merge", data=data)


def auto_merge_cluster(object_type, cluster):
    """Merge all records in a cluster into the oldest one.

    Returns (primary_id, merged_ids).
    """
    # Sort by createdate ascending — oldest first
    sorted_records = sorted(
        cluster,
        key=lambda r: _parse_date(r.get("properties", {}).get("createdate")),
    )
    primary = sorted_records[0]
    primary_id = primary["id"]
    merged_ids = []

    for record in sorted_records[1:]:
        secondary_id = record["id"]
        print(f"    Merging {secondary_id} → {primary_id}")
        merge_records(object_type, primary_id, secondary_id)
        merged_ids.append(secondary_id)

    return primary_id, merged_ids


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def generate_duplicate_report(object_type, dupes, filename=None):
    """Write a CSV report of duplicate clusters.

    Returns the file path written.
    """
    if filename is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"duplicate_{object_type}_{ts}.csv"

    reports_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "reports")
    os.makedirs(reports_dir, exist_ok=True)
    filepath = os.path.join(reports_dir, filename)

    with open(filepath, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["cluster_key", "record_id", "email_or_domain", "name", "created_at"])
        for key, records in dupes.items():
            for r in records:
                props = r.get("properties", {})
                writer.writerow([
                    key,
                    r["id"],
                    props.get("email") or props.get("domain", ""),
                    f"{props.get('firstname', '')} {props.get('lastname', '')}".strip()
                    or props.get("name", ""),
                    props.get("createdate", ""),
                ])

    print(f"  Report written to {filepath}")
    return filepath


def print_duplicate_summary(dupes, object_type="contacts"):
    """Print a summary of duplicate clusters."""
    if not dupes:
        print("  No duplicates found.")
        return
    print(f"\n  {'Key':<35} {'Count':<7} {'Record IDs'}")
    print(f"  {'—'*35} {'—'*7} {'—'*40}")
    for key, records in sorted(dupes.items(), key=lambda x: -len(x[1]))[:25]:
        ids = ", ".join(r["id"] for r in records)
        print(f"  {key:<35} {len(records):<7} {ids}")
    if len(dupes) > 25:
        print(f"  … and {len(dupes) - 25} more clusters")
