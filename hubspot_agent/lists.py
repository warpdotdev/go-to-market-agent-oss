"""List management — create, import, and manage HubSpot list memberships."""

import csv
import os

from .client import hubspot_request

# Common object type IDs
OBJECT_TYPES = {"contacts": "0-1", "companies": "0-2", "deals": "0-3", "tickets": "0-5"}


def list_lists(object_type_id="0-1", limit=50):
    """Search for lists, optionally filtering by object type."""
    data = {
        "listIds": [],
        "offset": 0,
        "count": limit,
        "processingTypes": [],
    }
    result = hubspot_request("POST", "/crm/v3/lists/search", data=data)
    lists = result.get("lists", [])
    filtered = [
        l for l in lists if not object_type_id or l.get("objectTypeId") == object_type_id
    ]
    return filtered


def create_list(name, object_type_id="0-1", processing_type="MANUAL"):
    """Create a static or dynamic list.

    processing_type: MANUAL (static) or DYNAMIC.
    """
    data = {
        "name": name,
        "objectTypeId": object_type_id,
        "processingType": processing_type,
    }
    result = hubspot_request("POST", "/crm/v3/lists/", data=data)
    list_id = str(result.get("listId") or result.get("list", {}).get("listId"))
    return list_id


def add_records_to_list(list_id, record_ids):
    """Add record IDs to a list in batches of 250."""
    batch_size = 250
    added = 0
    for i in range(0, len(record_ids), batch_size):
        batch = record_ids[i : i + batch_size]
        hubspot_request("PUT", f"/crm/v3/lists/{list_id}/memberships/add", batch)
        added += len(batch)
        print(f"  Added batch of {len(batch)} records (total: {added})")
    return added


def remove_records_from_list(list_id, record_ids):
    """Remove record IDs from a list in batches of 250."""
    batch_size = 250
    removed = 0
    for i in range(0, len(record_ids), batch_size):
        batch = record_ids[i : i + batch_size]
        hubspot_request("PUT", f"/crm/v3/lists/{list_id}/memberships/remove", batch)
        removed += len(batch)
        print(f"  Removed batch of {len(batch)} records (total: {removed})")
    return removed


def import_csv_to_list(csv_path, list_name, object_type_id="0-1", id_column="hs_object_id"):
    """Read a CSV file and populate a new HubSpot list with the record IDs.

    The CSV must have a column matching *id_column* containing HubSpot record IDs.
    """
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        if id_column not in reader.fieldnames:
            raise ValueError(
                f"Column '{id_column}' not found in CSV. Available: {reader.fieldnames}"
            )
        record_ids = [row[id_column].strip() for row in reader if row[id_column].strip()]

    # Deduplicate while preserving order
    seen = set()
    unique_ids = []
    for rid in record_ids:
        if rid not in seen:
            seen.add(rid)
            unique_ids.append(rid)

    print(f"  {len(unique_ids)} unique record IDs from CSV")

    list_id = create_list(list_name, object_type_id)
    print(f"  Created list '{list_name}' (ID: {list_id})")

    add_records_to_list(list_id, unique_ids)
    return list_id, len(unique_ids)


def print_list_table(lists):
    """Pretty-print a list of HubSpot lists."""
    if not lists:
        print("  No lists found.")
        return
    print(f"  {'ID':<12} {'Type':<10} {'Size':<8} {'Name'}")
    print(f"  {'—'*12} {'—'*10} {'—'*8} {'—'*40}")
    for l in lists:
        ptype = l.get("processingType", "?")[:8]
        size = l.get("size", "?")
        print(f"  {l.get('listId', '?'):<12} {ptype:<10} {str(size):<8} {l.get('name', '(unnamed)')}")
