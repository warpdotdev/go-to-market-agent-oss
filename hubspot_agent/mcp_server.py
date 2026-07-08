"""HubSpot MCP Server — exposes HubSpot CRM operations as MCP tools.

Run with:
    fastmcp run hubspot_agent/mcp_server.py
    # or
    python -m hubspot_agent.mcp_server
"""

from fastmcp import FastMCP

from . import workflows, lists, duplicates, properties

mcp = FastMCP(
    "HubSpot",
    instructions=(
        "HubSpot CRM management tools. Use these to manage workflows, lists, "
        "contacts, companies, deals, and properties in HubSpot. "
        "Requires HUBSPOT_PRIVATE_APP_TOKEN in the environment or a .env file."
    ),
)


# ---------------------------------------------------------------------------
# Workflows
# ---------------------------------------------------------------------------

@mcp.tool
def list_workflows() -> list[dict]:
    """List all HubSpot workflows with metadata (id, name, enabled, object type, dates)."""
    return workflows.list_workflows()


@mcp.tool
def get_workflow(flow_id: str) -> dict:
    """Get the full detail/spec of a single HubSpot workflow by its ID."""
    return workflows.get_workflow(flow_id)


@mcp.tool
def create_workflow(spec: dict) -> dict:
    """Create a new HubSpot workflow from a JSON spec.

    The spec should follow the HubSpot v4 Automation API schema, including
    fields like name, type, objectTypeId, isEnabled, triggers, and actions.
    """
    return workflows.create_workflow(spec)


@mcp.tool
def delete_workflow(flow_id: str) -> str:
    """Delete a HubSpot workflow by its ID. Returns confirmation."""
    workflows.delete_workflow(flow_id)
    return f"Workflow {flow_id} deleted."


@mcp.tool
def toggle_workflow(flow_id: str, enabled: bool) -> str:
    """Enable or disable a HubSpot workflow.

    Args:
        flow_id: The workflow ID.
        enabled: True to enable, False to disable.
    """
    workflows.toggle_workflow(flow_id, enabled)
    state = "enabled" if enabled else "disabled"
    return f"Workflow {flow_id} {state}."


# ---------------------------------------------------------------------------
# Lists
# ---------------------------------------------------------------------------

@mcp.tool
def list_lists(object_type_id: str = "0-1", limit: int = 50) -> list[dict]:
    """List HubSpot lists, optionally filtered by object type.

    Common object type IDs: 0-1=contacts, 0-2=companies, 0-3=deals, 0-5=tickets.
    """
    return lists.list_lists(object_type_id=object_type_id, limit=limit)


@mcp.tool
def create_list(
    name: str,
    object_type_id: str = "0-1",
    processing_type: str = "MANUAL",
) -> dict:
    """Create a new HubSpot list (static or dynamic).

    Args:
        name: Display name for the list.
        object_type_id: 0-1=contacts, 0-2=companies, 0-3=deals, 0-5=tickets.
        processing_type: MANUAL (static) or DYNAMIC.

    Returns dict with the new list_id.
    """
    list_id = lists.create_list(name, object_type_id, processing_type)
    return {"list_id": list_id, "name": name}


@mcp.tool
def add_records_to_list(list_id: str, record_ids: list[str]) -> dict:
    """Add record IDs to an existing HubSpot list. Batches in groups of 250.

    Args:
        list_id: The HubSpot list ID.
        record_ids: List of HubSpot record ID strings to add.
    """
    added = lists.add_records_to_list(list_id, record_ids)
    return {"list_id": list_id, "added": added}


@mcp.tool
def remove_records_from_list(list_id: str, record_ids: list[str]) -> dict:
    """Remove record IDs from a HubSpot list.

    Args:
        list_id: The HubSpot list ID.
        record_ids: List of HubSpot record ID strings to remove.
    """
    removed = lists.remove_records_from_list(list_id, record_ids)
    return {"list_id": list_id, "removed": removed}


@mcp.tool
def import_csv_to_list(
    csv_path: str,
    list_name: str,
    object_type_id: str = "0-1",
    id_column: str = "hs_object_id",
) -> dict:
    """Create a new HubSpot list and populate it from a CSV file.

    The CSV must have a column with HubSpot record IDs.

    Args:
        csv_path: Absolute path to the CSV file.
        list_name: Name for the new list.
        object_type_id: 0-1=contacts, 0-2=companies, etc.
        id_column: Column name containing HubSpot record IDs.
    """
    list_id, count = lists.import_csv_to_list(csv_path, list_name, object_type_id, id_column)
    return {"list_id": list_id, "records_imported": count}


# ---------------------------------------------------------------------------
# Duplicates
# ---------------------------------------------------------------------------

def _serialize_clusters(dupes: dict) -> list[dict]:
    """Convert duplicate cluster dict to a JSON-friendly list."""
    clusters = []
    for key, records in dupes.items():
        clusters.append({
            "key": key,
            "count": len(records),
            "records": [
                {
                    "id": r["id"],
                    "properties": r.get("properties", {}),
                }
                for r in records
            ],
        })
    return clusters


@mcp.tool
def find_duplicate_contacts(match_on: str = "email") -> list[dict]:
    """Full scan for duplicate contacts. Slow for large portals.

    Args:
        match_on: "email" (most reliable) or "name" (firstname+lastname).

    Returns a list of duplicate clusters, each with a key, count, and records.
    """
    dupes = duplicates.find_duplicate_contacts(match_on=match_on)
    return _serialize_clusters(dupes)


@mcp.tool
def find_duplicate_companies(match_on: str = "domain") -> list[dict]:
    """Full scan for duplicate companies. Slow for large portals.

    Args:
        match_on: "domain" (most reliable) or "name".

    Returns a list of duplicate clusters.
    """
    dupes = duplicates.find_duplicate_companies(match_on=match_on)
    return _serialize_clusters(dupes)


@mcp.tool
def scan_recent_duplicates(object_type: str, days: int = 3) -> list[dict]:
    """Incremental duplicate scan — only checks records created in the last N days.

    Much faster than a full scan. Supports contacts, companies, and deals.

    Args:
        object_type: "contacts", "companies", or "deals".
        days: Look-back window (default 3).

    Returns a list of duplicate clusters found.
    """
    dupes = duplicates.scan_recent_duplicates(object_type, days=days)
    return _serialize_clusters(dupes)


@mcp.tool
def merge_records(object_type: str, primary_id: str, secondary_id: str) -> str:
    """Merge two HubSpot records. The primary record survives; the secondary is merged into it.

    Args:
        object_type: "contacts", "companies", or "deals".
        primary_id: The record ID that will survive (usually the older one).
        secondary_id: The record ID that will be merged and removed.
    """
    duplicates.merge_records(object_type, primary_id, secondary_id)
    return f"Merged {secondary_id} into {primary_id} ({object_type})."


@mcp.tool
def generate_duplicate_report(object_type: str, clusters: list[dict]) -> str:
    """Generate a CSV report of duplicate clusters and return the file path.

    Args:
        object_type: "contacts", "companies", or "deals".
        clusters: List of cluster dicts as returned by the duplicate scan tools.
                  Each must have "key" and "records" (list of {"id", "properties"}).
    """
    # Re-hydrate into the format duplicates.generate_duplicate_report expects
    dupes = {}
    for cluster in clusters:
        dupes[cluster["key"]] = cluster["records"]
    filepath = duplicates.generate_duplicate_report(object_type, dupes)
    return filepath


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

@mcp.tool
def list_properties(object_type: str) -> list[dict]:
    """List all properties for a HubSpot object type, including full metadata.

    Args:
        object_type: "contacts", "companies", "deals", or "tickets".
    """
    return properties.list_properties(object_type)


@mcp.tool
def find_stale_properties(object_type: str, days: int = 365) -> list[dict]:
    """Find custom properties whose definition hasn't been updated in N days.

    Args:
        object_type: "contacts", "companies", "deals", or "tickets".
        days: Staleness threshold (default 365).

    Returns list of stale property dicts with name, label, group, type, and dates.
    """
    return properties.find_stale_properties(object_type, days=days)


@mcp.tool
def find_low_fill_properties(
    object_type: str,
    threshold: float = 0.05,
    sample_size: int = 1000,
) -> list[dict]:
    """Find custom properties with fill rate below a threshold.

    Samples records to estimate how many have a value for each property.

    Args:
        object_type: "contacts", "companies", "deals", or "tickets".
        threshold: Fill rate cutoff, 0.0–1.0 (default 0.05 = 5%).
        sample_size: Number of records to sample (default 1000).
    """
    return properties.find_low_fill_properties(object_type, threshold, sample_size)


@mcp.tool
def find_orphan_properties(object_type: str, sample_size: int = 1000) -> list[dict]:
    """Find custom properties with zero data in a sample of records.

    Args:
        object_type: "contacts", "companies", "deals", or "tickets".
        sample_size: Number of records to sample.
    """
    return properties.find_orphan_properties(object_type, sample_size)


@mcp.tool
def find_duplicate_properties(object_type: str, similarity: float = 0.80) -> list[dict]:
    """Find properties with suspiciously similar labels (fuzzy match).

    Args:
        object_type: "contacts", "companies", "deals", or "tickets".
        similarity: Minimum similarity ratio, 0.0–1.0 (default 0.80).

    Returns list of {property_a, label_a, property_b, label_b, similarity}.
    """
    return properties.find_duplicate_properties(object_type, similarity)


@mcp.tool
def run_property_audit(object_type: str) -> dict:
    """Run a full property audit: stale, low-fill, orphan, and duplicate-name detection.

    Writes a CSV report and returns a summary with counts and the report file path.

    Args:
        object_type: "contacts", "companies", "deals", or "tickets".
    """
    filepath, summary = properties.generate_property_audit_report(object_type)
    return {"report_path": filepath, **summary}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    mcp.run()


if __name__ == "__main__":
    main()
