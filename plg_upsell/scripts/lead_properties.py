#!/usr/bin/env python3
"""Verify/create HubSpot Lead properties required by the PLG Dagster sync.

Usage:
    python plg_upsell/scripts/lead_properties.py --dry-run
    python plg_upsell/scripts/lead_properties.py --confirm

The Lead object type varies by portal. This script tries `leads` first and
falls back to `0-136`; override with `--object-type` if needed.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from hubspot_agent.client import hubspot_request

DEFAULT_OBJECT_TYPES = ["leads", "0-136"]
GROUP_NAME = "plg_lead_sync"
GROUP_LABEL = "PLG Lead Sync"

LEAD_PROPERTIES = [
    {
        "name": "plg_source",
        "label": "PLG Source",
        "type": "string",
        "fieldType": "text",
        "description": "Source system for PLG-created Leads. Expected value: pqa.",
    },
    {
        "name": "plg_email_domain",
        "label": "PLG Email Domain",
        "type": "string",
        "fieldType": "text",
        "description": "Email domain from the PQA scoring table.",
    },
    {
        "name": "plg_company_id",
        "label": "PLG Company ID",
        "type": "string",
        "fieldType": "text",
        "description": "Associated HubSpot company ID resolved by the PLG Dagster sync.",
    },
    {
        "name": "plg_contact_id",
        "label": "PLG Contact ID",
        "type": "string",
        "fieldType": "text",
        "description": "Associated HubSpot contact ID resolved by the PLG Dagster sync.",
    },
    {
        "name": "plg_champion_email",
        "label": "PLG Champion Email",
        "type": "string",
        "fieldType": "text",
        "description": "Email address of the top PQL champion selected for this Lead.",
    },
    {
        "name": "plg_lead_role",
        "label": "PLG Lead Role",
        "type": "enumeration",
        "fieldType": "select",
        "description": "Why this PQL contact was selected for a PLG Lead.",
        "options": [
            {"label": "Primary champion", "value": "primary_champion", "displayOrder": 0, "hidden": False},
            {"label": "Admin champion", "value": "admin_champion", "displayOrder": 1, "hidden": False},
            {"label": "Primary admin champion", "value": "primary_admin_champion", "displayOrder": 2, "hidden": False},
            {"label": "Champion", "value": "champion", "displayOrder": 3, "hidden": False},
        ],
    },
    {
        "name": "pqa_score",
        "label": "PQA Score",
        "type": "number",
        "fieldType": "number",
        "description": "Account-level PQA score at the time of Lead sync.",
    },
    {
        "name": "pqa_tier",
        "label": "PQA Tier",
        "type": "enumeration",
        "fieldType": "select",
        "description": "PQA tier used for routing.",
        "options": [
            {"label": "Tier 1", "value": "tier_1", "displayOrder": 0, "hidden": False},
            {"label": "Tier 2", "value": "tier_2", "displayOrder": 1, "hidden": False},
            {"label": "Tier 3", "value": "tier_3", "displayOrder": 2, "hidden": False},
        ],
    },
    {
        "name": "pqa_routing_motion",
        "label": "PQA Routing Motion",
        "type": "enumeration",
        "fieldType": "select",
        "description": "Routing motion selected by Dagster from the PQA tier.",
        "options": [
            {"label": "Manual BDR", "value": "manual_bdr", "displayOrder": 0, "hidden": False},
            {"label": "Automated BDR", "value": "automated_bdr", "displayOrder": 1, "hidden": False},
            {"label": "Marketing Touch", "value": "marketing_touch", "displayOrder": 2, "hidden": False},
            {"label": "Held", "value": "held", "displayOrder": 3, "hidden": False},
        ],
    },
    {
        "name": "pql_score",
        "label": "PQL Score",
        "type": "number",
        "fieldType": "number",
        "description": "Champion-level PQL score at the time of Lead sync.",
    },
    {
        "name": "pql_champion_rank",
        "label": "PQL Champion Rank",
        "type": "number",
        "fieldType": "number",
        "description": "Champion rank within the account domain.",
    },
    {
        "name": "plg_sequence_enrollment_requested",
        "label": "PLG Sequence Enrollment Requested",
        "type": "bool",
        "fieldType": "booleancheckbox",
        "description": "Whether this Lead should enter a PLG sequence.",
        "options": [
            {"label": "Yes", "value": "true", "displayOrder": 0, "hidden": False},
            {"label": "No", "value": "false", "displayOrder": 1, "hidden": False},
        ],
    },
    {
        "name": "plg_sequence_id",
        "label": "PLG Sequence ID",
        "type": "string",
        "fieldType": "text",
        "description": "HubSpot sequence ID selected for this Lead.",
    },
    {
        "name": "plg_sequence_name",
        "label": "PLG Sequence Name",
        "type": "string",
        "fieldType": "text",
        "description": "Human-readable sequence name selected for this Lead.",
    },
    {
        "name": "plg_sequence_enrollment_mode",
        "label": "PLG Sequence Enrollment Mode",
        "type": "enumeration",
        "fieldType": "select",
        "description": "Manual vs automated sequence enrollment intent.",
        "options": [
            {"label": "Manual", "value": "manual", "displayOrder": 0, "hidden": False},
            {"label": "Automated", "value": "automated", "displayOrder": 1, "hidden": False},
        ],
    },
    {
        "name": "plg_enrichment_requested",
        "label": "PLG Enrichment Requested",
        "type": "bool",
        "fieldType": "booleancheckbox",
        "description": "Whether Dagster requested enrichment for this Lead/contact.",
        "options": [
            {"label": "Yes", "value": "true", "displayOrder": 0, "hidden": False},
            {"label": "No", "value": "false", "displayOrder": 1, "hidden": False},
        ],
    },
    {
        "name": "plg_route_via_workflow",
        "label": "PLG Route via Workflow",
        "type": "bool",
        "fieldType": "booleancheckbox",
        "description": "Whether Dagster expects existing HubSpot workflows to route this Lead.",
        "options": [
            {"label": "Yes", "value": "true", "displayOrder": 0, "hidden": False},
            {"label": "No", "value": "false", "displayOrder": 1, "hidden": False},
        ],
    },
    {
        "name": "plg_account_routing_workflow_id",
        "label": "PLG Account Routing Workflow ID",
        "type": "string",
        "fieldType": "text",
        "description": "Account workflow ID expected to route the PLG Lead/account.",
    },
    {
        "name": "plg_contact_routing_workflow_id",
        "label": "PLG Contact Routing Workflow ID",
        "type": "string",
        "fieldType": "text",
        "description": "Contact workflow ID expected to route the PLG Lead/contact.",
    },
    {
        "name": "plg_sync_run_id",
        "label": "PLG Sync Run ID",
        "type": "string",
        "fieldType": "text",
        "description": "Dagster run identifier for the most recent PLG Lead sync.",
    },
    {
        "name": "plg_last_synced_at",
        "label": "PLG Last Synced At",
        "type": "datetime",
        "fieldType": "date",
        "description": "Timestamp of the most recent PLG Lead sync.",
    },
]


def resolve_object_type(explicit: str | None = None) -> str:
    candidates = [explicit] if explicit else DEFAULT_OBJECT_TYPES
    for object_type in candidates:
        if not object_type:
            continue
        try:
            hubspot_request("GET", f"/crm/v3/properties/{object_type}")
            return object_type
        except Exception as exc:  # noqa: BLE001
            print(f"  Could not read Lead properties using object type {object_type}: {exc}")
    raise RuntimeError("Could not resolve HubSpot Lead object type")


def ensure_group(object_type: str, dry_run: bool) -> None:
    if dry_run:
        print(f"  [dry-run] Would ensure property group {GROUP_NAME} on {object_type}")
        return
    try:
        hubspot_request("POST", f"/crm/v3/properties/{object_type}/groups", {
            "name": GROUP_NAME,
            "label": GROUP_LABEL,
            "displayOrder": -1,
        })
        print(f"  ✓ Created group {GROUP_NAME}")
    except Exception as exc:  # noqa: BLE001
        err = str(exc).lower()
        if "409" in err or "already exists" in err or "conflict" in err:
            print(f"  — Group {GROUP_NAME} already exists")
        else:
            raise


def ensure_properties(object_type: str, dry_run: bool) -> tuple[int, int]:
    existing = hubspot_request("GET", f"/crm/v3/properties/{object_type}")
    existing_names = {p["name"] for p in existing.get("results", [])}
    created = skipped = 0

    for prop in LEAD_PROPERTIES:
        if prop["name"] in existing_names:
            print(f"  — {prop['name']:<40} already exists")
            skipped += 1
            continue
        if dry_run:
            print(f"  [dry-run] Would create {prop['name']:<40} ({prop['type']})")
            created += 1
            continue
        payload = {
            "name": prop["name"],
            "label": prop["label"],
            "type": prop["type"],
            "fieldType": prop["fieldType"],
            "groupName": GROUP_NAME,
            "description": prop.get("description", ""),
        }
        if "options" in prop:
            payload["options"] = prop["options"]
        hubspot_request("POST", f"/crm/v3/properties/{object_type}", payload)
        print(f"  ✓ Created {prop['name']}")
        created += 1
    return created, skipped


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify/create PLG Lead properties.")
    parser.add_argument("--dry-run", action="store_true", help="Preview missing properties only.")
    parser.add_argument("--confirm", action="store_true", help="Actually create missing properties.")
    parser.add_argument("--object-type", default=None, help="HubSpot Lead object type, e.g. leads or 0-136.")
    args = parser.parse_args()

    if not args.dry_run and not args.confirm:
        parser.error("Pass --dry-run to verify or --confirm to create missing properties.")

    object_type = resolve_object_type(args.object_type)
    print(f"Using Lead object type: {object_type}")
    ensure_group(object_type, dry_run=args.dry_run)
    created, skipped = ensure_properties(object_type, dry_run=args.dry_run)
    print(f"\nSummary: {created} {'would be created' if args.dry_run else 'created'}, {skipped} already existed")


if __name__ == "__main__":
    main()
