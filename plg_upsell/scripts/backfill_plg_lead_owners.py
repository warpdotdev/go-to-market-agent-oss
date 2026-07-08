#!/usr/bin/env python3
"""Backfill PLG Lead owner from the associated Contact owner.

Finds Product Qualified Leads created by the PLG Dagster sync (`plg_source=pqa`)
that do not have `hubspot_owner_id`, reads their associated contact owner, falls
back to company owner when the contact is unowned, and updates the Lead owner.

Usage:
    python plg_upsell/scripts/backfill_plg_lead_owners.py --dry-run
    python plg_upsell/scripts/backfill_plg_lead_owners.py --confirm
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from hubspot_agent.client import hubspot_request
from plg_upsell.dagster.core import pql_owner_for_crm_owner

LEAD_OBJECT_TYPE = os.environ.get("PLG_HUBSPOT_LEAD_OBJECT_TYPE", "leads")
BATCH_SIZE = 100


def fetch_unowned_plg_leads() -> list[dict[str, Any]]:
    """Return PLG Leads with no explicit Lead owner."""
    leads: list[dict[str, Any]] = []
    after: str | None = None
    while True:
        body: dict[str, Any] = {
            "filterGroups": [{
                "filters": [
                    {"propertyName": "plg_source", "operator": "EQ", "value": "pqa"},
                    {"propertyName": "hubspot_owner_id", "operator": "NOT_HAS_PROPERTY"},
                ]
            }],
            "properties": [
                "hs_object_id",
                "hubspot_owner_id",
                "plg_contact_id",
                "hs_primary_contact_id",
                "plg_company_id",
                "hs_primary_company_id",
                "plg_champion_email",
                "pqa_tier",
                "plg_lead_role",
            ],
            "limit": BATCH_SIZE,
            "sorts": [{"propertyName": "hs_createdate", "direction": "DESCENDING"}],
        }
        if after:
            body["after"] = after
        resp = hubspot_request("POST", f"/crm/v3/objects/{LEAD_OBJECT_TYPE}/search", data=body)
        for row in resp.get("results", []) or []:
            props = row.get("properties") or {}
            contact_id = props.get("plg_contact_id") or props.get("hs_primary_contact_id")
            company_id = props.get("plg_company_id") or props.get("hs_primary_company_id")
            leads.append({
                "lead_id": row.get("id"),
                "contact_id": str(contact_id) if contact_id else None,
                "company_id": str(company_id) if company_id else None,
                "champion_email": props.get("plg_champion_email"),
                "tier": props.get("pqa_tier"),
                "lead_role": props.get("plg_lead_role"),
            })
        after = (resp.get("paging") or {}).get("next", {}).get("after")
        if not after:
            break
    return leads


def fetch_contact_owners(contact_ids: list[str]) -> dict[str, str | None]:
    """Return {contact_id: hubspot_owner_id} for the given contacts."""
    owners: dict[str, str | None] = {}
    unique_ids = sorted({cid for cid in contact_ids if cid})
    for i in range(0, len(unique_ids), BATCH_SIZE):
        batch = unique_ids[i:i + BATCH_SIZE]
        resp = hubspot_request("POST", "/crm/v3/objects/contacts/batch/read", data={
            "properties": ["email", "hubspot_owner_id"],
            "inputs": [{"id": cid} for cid in batch],
        })
        for row in resp.get("results", []) or []:
            props = row.get("properties") or {}
            owners[str(row.get("id"))] = props.get("hubspot_owner_id")
    return owners


def fetch_company_owners(company_ids: list[str]) -> dict[str, str | None]:
    """Return {company_id: hubspot_owner_id} for the given companies."""
    owners: dict[str, str | None] = {}
    unique_ids = sorted({cid for cid in company_ids if cid})
    for i in range(0, len(unique_ids), BATCH_SIZE):
        batch = unique_ids[i:i + BATCH_SIZE]
        resp = hubspot_request("POST", "/crm/v3/objects/companies/batch/read", data={
            "properties": ["domain", "name", "hubspot_owner_id"],
            "inputs": [{"id": cid} for cid in batch],
        })
        for row in resp.get("results", []) or []:
            props = row.get("properties") or {}
            owners[str(row.get("id"))] = props.get("hubspot_owner_id")
    return owners


def update_lead_owner(lead_id: str, owner_id: str) -> None:
    hubspot_request(
        "PATCH",
        f"/crm/v3/objects/{LEAD_OBJECT_TYPE}/{lead_id}",
        data={"properties": {"hubspot_owner_id": owner_id}},
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Preview updates without writing to HubSpot.")
    parser.add_argument("--confirm", action="store_true", help="Write Lead owner updates to HubSpot.")
    args = parser.parse_args()
    if not args.dry_run and not args.confirm:
        parser.error("Pass --dry-run to preview or --confirm to update HubSpot.")

    leads = fetch_unowned_plg_leads()
    owners = fetch_contact_owners([lead["contact_id"] for lead in leads if lead.get("contact_id")])
    company_owners = fetch_company_owners([lead["company_id"] for lead in leads if lead.get("company_id")])

    candidates = []
    skipped_no_contact = 0
    skipped_no_owner = 0
    contact_owner_count = 0
    company_owner_count = 0
    for lead in leads:
        contact_id = lead.get("contact_id")
        if not contact_id:
            skipped_no_contact += 1
            continue
        owner_id = pql_owner_for_crm_owner(owners.get(contact_id))
        owner_source = "contact"
        if not owner_id:
            owner_id = pql_owner_for_crm_owner(company_owners.get(lead.get("company_id")))
            owner_source = "company"
        if not owner_id:
            skipped_no_owner += 1
            continue
        if owner_source == "contact":
            contact_owner_count += 1
        else:
            company_owner_count += 1
        candidates.append({**lead, "owner_id": owner_id, "owner_source": owner_source})

    print(f"Unowned PLG Leads found : {len(leads)}")
    print(f"Assignable to owner     : {len(candidates)}")
    print(f"  from contact owner    : {contact_owner_count}")
    print(f"  from company owner    : {company_owner_count}")
    print(f"Skipped no contact      : {skipped_no_contact}")
    print(f"Skipped no owner        : {skipped_no_owner}")

    for item in candidates[:20]:
        print(
            f"  lead={item['lead_id']} contact={item['contact_id']} "
            f"company={item.get('company_id')} owner={item['owner_id']} "
            f"source={item['owner_source']} tier={item.get('tier')} role={item.get('lead_role')}"
        )
    if len(candidates) > 20:
        print(f"  ... {len(candidates) - 20} more")

    if args.dry_run:
        print("\n[DRY RUN] No Lead owners were updated.")
        return 0

    for item in candidates:
        update_lead_owner(str(item["lead_id"]), str(item["owner_id"]))
    print(f"\nUpdated {len(candidates)} Lead owner(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
