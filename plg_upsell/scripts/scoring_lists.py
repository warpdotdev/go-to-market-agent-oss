#!/usr/bin/env python3
"""
Initialize dynamic HubSpot lists for PQA/PQL pipeline management.

Creates 6 DYNAMIC company lists that auto-maintain membership based on
pqa_status and pqa_tier properties written by the weekly sync script.
The sync never touches list membership directly — it only updates properties.

Lists created:
  [PLG] Active — All       pqa_status = active
  [PLG] Tier 1             pqa_status = active AND pqa_tier = tier_1
  [PLG] Tier 2             pqa_status = active AND pqa_tier = tier_2
  [PLG] Tier 3             pqa_tier = tier_3  (marketing-only, no BDR)
  [PLG] De-prioritized     pqa_status = deprioritized
  [PLG] Marketing Nurture  pqa_status = nurture

Idempotent — safe to re-run. Skips lists that already exist by name.

Usage:
    python plg_upsell/scripts/scoring_lists.py
    python plg_upsell/scripts/scoring_lists.py --dry-run
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from hubspot_agent.client import hubspot_request

PORTAL_ID = os.environ.get("HUBSPOT_PORTAL_ID", "000000000")
COMPANY_TYPE_ID = "0-2"


# ---------------------------------------------------------------------------
# Filter helpers
# ---------------------------------------------------------------------------

def _enum_filter(property_name: str, values: list[str]) -> dict:
    """Build a single enumeration property filter."""
    return {
        "filterType": "PROPERTY",
        "property": property_name,
        "operation": {
            "operationType": "ENUMERATION",
            "operator": "IS_ANY_OF",
            "values": values,
            "includeObjectsWithNoValueSet": False,
        },
    }


def _root_filter(filters: list[dict]) -> dict:
    """Build a valid root filter branch: OR containing a single AND branch.

    HubSpot requires the root to be OR with at least one nested AND branch.
    """
    return {
        "filterBranchType": "OR",
        "filterBranches": [
            {
                "filterBranchType": "AND",
                "filterBranches": [],
                "filters": filters,
            }
        ],
        "filters": [],
    }


# ---------------------------------------------------------------------------
# List definitions
# ---------------------------------------------------------------------------

PLG_LISTS = [
    {
        "name": "[PLG] Active — All",
        "description": "All PQA-active companies (Tier 1 + Tier 2). Primary BDR working view.",
        "filterBranch": _root_filter([
            _enum_filter("pqa_status", ["active"]),
        ]),
    },
    {
        "name": "[PLG] Tier 1",
        "description": "Active PQA companies scoring >= 75. Full BDR attention + Tier 1 sequence.",
        "filterBranch": _root_filter([
            _enum_filter("pqa_status", ["active"]),
            _enum_filter("pqa_tier", ["tier_1"]),
        ]),
    },
    {
        "name": "[PLG] Tier 2",
        "description": "Active PQA companies scoring 50–74. Sequence + regular cadence + marketing aircover.",
        "filterBranch": _root_filter([
            _enum_filter("pqa_status", ["active"]),
            _enum_filter("pqa_tier", ["tier_2"]),
        ]),
    },
    {
        "name": "[PLG] Tier 3",
        "description": "PQA companies scoring 25–49. Marketing-only — no BDR unless they tier up.",
        "filterBranch": _root_filter([
            _enum_filter("pqa_tier", ["tier_3"]),
        ]),
    },
    {
        "name": "[PLG] De-prioritized",
        "description": "Companies removed from active pipeline after 2 consecutive weeks below threshold.",
        "filterBranch": _root_filter([
            _enum_filter("pqa_status", ["deprioritized"]),
        ]),
    },
    {
        "name": "[PLG] Marketing Nurture",
        "description": "Always-on awareness for nurture and de-prioritized accounts. Synced to Primer/LinkedIn.",
        "filterBranch": _root_filter([
            _enum_filter("pqa_status", ["nurture"]),
        ]),
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_existing_list_names() -> dict[str, str]:
    """Return {name: listId} for all existing company lists."""
    result = hubspot_request("POST", "/crm/v3/lists/search", {
        "objectTypeId": COMPANY_TYPE_ID,
        "processingTypes": ["DYNAMIC", "MANUAL", "SNAPSHOT"],
        "count": 500,
        "offset": 0,
    })
    return {
        lst["name"]: str(lst["listId"])
        for lst in result.get("lists", [])
    }


def create_dynamic_list(name: str, filter_branch: dict) -> str:
    """Create a dynamic company list. Returns the new listId."""
    result = hubspot_request("POST", "/crm/v3/lists/", {
        "name": name,
        "objectTypeId": COMPANY_TYPE_ID,
        "processingType": "DYNAMIC",
        "filterBranch": filter_branch,
    })
    return str(result.get("listId") or result.get("list", {}).get("listId", "?"))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Initialize dynamic PLG pipeline lists in HubSpot."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without writing to HubSpot.",
    )
    args = parser.parse_args()
    dry_run: bool = args.dry_run

    if dry_run:
        print("=== DRY RUN — no changes will be made ===\n")
        for lst in PLG_LISTS:
            filters = lst["filterBranch"]["filters"]
            conditions = " AND ".join(
                f"{f['property']} IN {f['operation']['values']}" for f in filters
            )
            print(f"  [dry-run] Would create: {lst['name']}")
            print(f"            Filter: {conditions}")
        print(f"\n  Total: {len(PLG_LISTS)} lists")
        return

    print("Fetching existing lists…")
    existing = get_existing_list_names()

    created = skipped = 0

    for lst in PLG_LISTS:
        name = lst["name"]

        if name in existing:
            print(f"  — {name:<35} already exists (ID: {existing[name]}), skipping")
            skipped += 1
            continue

        try:
            list_id = create_dynamic_list(name, lst["filterBranch"])
            print(f"  ✓ {name:<35} (ID: {list_id})")
            created += 1
        except Exception as e:
            print(f"  ✗ Failed to create '{name}': {e}")

    print(f"\n=== Summary ===")
    print(f"  Created : {created}")
    print(f"  Skipped : {skipped}")
    if created > 0:
        print(f"\n  View lists in HubSpot:")
        print(f"  https://app.hubspot.com/contacts/{PORTAL_ID}/objects/0-2/views/all/list")


if __name__ == "__main__":
    main()
