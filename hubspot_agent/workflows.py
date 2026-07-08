"""Workflow management via the HubSpot v4 Automation API."""

import json

from .client import hubspot_request, paginated_get

# Object type IDs for display
OBJECT_TYPES = {
    "0-1": "Contacts",
    "0-2": "Companies",
    "0-3": "Deals",
    "0-5": "Tickets",
}


def list_workflows():
    """Return all workflows with key metadata."""
    flows = list(paginated_get("/automation/v4/flows", key="results"))
    summaries = []
    for f in flows:
        summaries.append({
            "id": f.get("id"),
            "name": f.get("name", "(unnamed)"),
            "enabled": f.get("isEnabled", False),
            "type": f.get("type", ""),
            "objectTypeId": f.get("objectTypeId", ""),
            "objectType": OBJECT_TYPES.get(f.get("objectTypeId", ""), f.get("objectTypeId", "")),
            "createdAt": f.get("createdAt", ""),
            "updatedAt": f.get("updatedAt", ""),
        })
    return summaries


def get_workflow(flow_id):
    """Get full detail for a single workflow."""
    return hubspot_request("GET", f"/automation/v4/flows/{flow_id}")


def create_workflow(spec):
    """Create a workflow from a JSON spec (dict or file path).

    If *spec* is a string, it is treated as a file path to a JSON file.
    """
    if isinstance(spec, str):
        with open(spec) as f:
            spec = json.load(f)
    return hubspot_request("POST", "/automation/v4/flows", data=spec)


def delete_workflow(flow_id):
    """Delete a workflow by ID."""
    return hubspot_request("DELETE", f"/automation/v4/flows/{flow_id}")


def toggle_workflow(flow_id, enabled):
    """Enable or disable a workflow.

    Fetches the current spec, flips isEnabled, and PUTs it back.
    """
    flow = get_workflow(flow_id)
    flow["isEnabled"] = enabled
    return hubspot_request("PUT", f"/automation/v4/flows/{flow_id}", data=flow)


def print_workflow_table(workflows):
    """Pretty-print a list of workflow summaries."""
    if not workflows:
        print("  No workflows found.")
        return
    print(f"  {'ID':<12} {'Enabled':<9} {'Object':<12} {'Name'}")
    print(f"  {'—'*12} {'—'*9} {'—'*12} {'—'*40}")
    for w in workflows:
        enabled = "✓" if w["enabled"] else "✗"
        print(f"  {w['id']:<12} {enabled:<9} {w['objectType']:<12} {w['name']}")
