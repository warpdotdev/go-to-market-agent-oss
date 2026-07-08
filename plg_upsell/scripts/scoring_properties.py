#!/usr/bin/env python3
"""
Initialize HubSpot property group and properties for PQA/PQL scoring.

Creates:
  - Property group: pqa_scoring  (on both companies and contacts)
  - 17 company properties under pqa_scoring
  -  7 contact properties under pqa_scoring

Idempotent — safe to re-run. Skips any properties or groups that already exist.

Usage:
    python plg_upsell/scripts/scoring_properties.py
    python plg_upsell/scripts/scoring_properties.py --dry-run
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from hubspot_agent.client import hubspot_request

PORTAL_ID = os.environ.get("HUBSPOT_PORTAL_ID", "000000000")

COMPANY_GROUP_NAME  = "pqa_scoring"
COMPANY_GROUP_LABEL = "PQA Scoring"

CONTACT_GROUP_NAME  = "pql_scoring"
CONTACT_GROUP_LABEL = "PQL Scoring"
CONTACT_SEQUENCE_GROUP_NAME = "plg_sequence_sync"
CONTACT_SEQUENCE_GROUP_LABEL = "PLG Sequence Sync"


# ---------------------------------------------------------------------------
# Property definitions
# ---------------------------------------------------------------------------

COMPANY_PROPERTIES = [
    # ── Synced from BigQuery ─────────────────────────────────────────────────
    {
        "name": "pqa_score",
        "label": "PQA Score",
        "type": "number",
        "fieldType": "number",
        "description": "Composite domain-level PLG score (0–100). Refreshed weekly from BigQuery pqa_scores_latest.",
    },
    {
        "name": "pqa_avg_wau",
        "label": "PQA Avg WAU (4-week)",
        "type": "number",
        "fieldType": "number",
        "description": "Average weekly active users over the past 4 weeks. Source: pqa_scores_latest.avg_wau_4w.",
    },
    {
        "name": "pqa_paid_seats",
        "label": "PQA Paid Seats",
        "type": "number",
        "fieldType": "number",
        "description": "Distinct users on paid teams. Source: pqa_scores_latest.paid_seats. Score weight: 20%.",
    },
    {
        "name": "pqa_ai_credits_30d",
        "label": "PQA AI Credits (30d)",
        "type": "number",
        "fieldType": "number",
        "description": "Total AI credits consumed in the last 30 days. Source: pqa_scores_latest.total_ai_credits_30d. Score weight: 20%.",
    },
    {
        "name": "pqa_credits_per_user_30d",
        "label": "PQA Credits per User (30d)",
        "type": "number",
        "fieldType": "number",
        "description": "Credits per user in the last 30 days. Source: pqa_scores_latest.credits_per_user_30d. Score weight: 0% (currently zeroed).",
    },
    {
        "name": "pqa_wow_growth",
        "label": "PQA WoW Credit Growth %",
        "type": "number",
        "fieldType": "number",
        "description": "Week-over-week credit consumption growth %. Source: pqa_scores_latest.wow_credit_growth. Score weight: 15%.",
    },
    {
        "name": "pqa_users_hitting_limits_14d",
        "label": "PQA Users Hitting Limits (14d)",
        "type": "number",
        "fieldType": "number",
        "description": "Users who hit credit limits in the last 14 days. Source: pqa_scores_latest.users_hitting_limits_14d. Score weight: 5%.",
    },
    {
        "name": "pqa_reload_spend_14d",
        "label": "PQA Reload Spend $ (14d)",
        "type": "number",
        "fieldType": "number",
        "description": "Reload credit spend (USD) in the last 14 days. Source: pqa_scores_latest.reload_credit_spend_14d. Score weight: 15%.",
    },
    {
        "name": "pqa_free_to_paid_30d",
        "label": "PQA Free-to-Paid Upgrades (30d)",
        "type": "number",
        "fieldType": "number",
        "description": "Free-to-paid seat upgrades in the last 30 days. Source: pqa_scores_latest.free_to_paid_upgrades_30d. Score weight: 10%.",
    },
    {
        "name": "pqa_new_members_14d",
        "label": "PQA New Members (14d)",
        "type": "number",
        "fieldType": "number",
        "description": "New domain members added in the last 14 days. Source: pqa_scores_latest.new_domain_members_14d. Score weight: 10%.",
    },
    # ── Computed by sync script ──────────────────────────────────────────────
    {
        "name": "pqa_score_prev",
        "label": "PQA Score (Previous Week)",
        "type": "number",
        "fieldType": "number",
        "description": "PQA score from the previous weekly sync. Copied from pqa_score before overwriting.",
    },
    {
        "name": "pqa_score_delta",
        "label": "PQA Score Delta (WoW)",
        "type": "number",
        "fieldType": "number",
        "description": "Week-over-week change in PQA score (pqa_score - pqa_score_prev). Positive = improving.",
    },
    {
        "name": "pqa_tier",
        "label": "PQA Tier",
        "type": "enumeration",
        "fieldType": "select",
        "description": "Scoring tier derived from pqa_score. Tier 1 >= 75, Tier 2 50-74, Tier 3 25-49.",
        "options": [
            {"label": "Tier 1", "value": "tier_1", "displayOrder": 0, "hidden": False},
            {"label": "Tier 2", "value": "tier_2", "displayOrder": 1, "hidden": False},
            {"label": "Tier 3", "value": "tier_3", "displayOrder": 2, "hidden": False},
        ],
    },
    {
        "name": "pqa_last_scored_at",
        "label": "PQA Last Scored At",
        "type": "datetime",
        "fieldType": "date",
        "description": "Timestamp of the most recent PQA weekly sync run.",
    },
    # ── Lifecycle state (managed by sync script) ─────────────────────────────
    {
        "name": "pqa_status",
        "label": "PQA Status",
        "type": "enumeration",
        "fieldType": "select",
        "description": "Pipeline membership status. Controlled by dampening logic in the weekly sync.",
        "options": [
            {"label": "Active",          "value": "active",         "displayOrder": 0, "hidden": False},
            {"label": "De-prioritized",  "value": "deprioritized",  "displayOrder": 1, "hidden": False},
            {"label": "Nurture",         "value": "nurture",        "displayOrder": 2, "hidden": False},
        ],
    },
    {
        "name": "pqa_weeks_above_threshold",
        "label": "PQA Weeks Above Threshold",
        "type": "number",
        "fieldType": "number",
        "description": "Consecutive weeks scoring Hot/Warm. Promoted to Active after 2. Resets to 0 on score drop.",
    },
    {
        "name": "pqa_weeks_below_threshold",
        "label": "PQA Weeks Below Threshold",
        "type": "number",
        "fieldType": "number",
        "description": "Consecutive weeks below threshold while Active. De-prioritized after 2. Resets to 0 on recovery.",
    },
]

CONTACT_SEQUENCE_PROPERTIES = [
    {
        "name": "plg_sequence_enrollment_requested",
        "label": "PLG Sequence Enrollment Requested",
        "type": "bool",
        "fieldType": "booleancheckbox",
        "description": "Dagster requested that this Product Qualified contact be enrolled in a PLG sequence.",
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
        "description": "HubSpot sequence ID selected by Dagster for this Product Qualified contact.",
    },
    {
        "name": "plg_sequence_name",
        "label": "PLG Sequence Name",
        "type": "string",
        "fieldType": "text",
        "description": "Human-readable sequence name selected by Dagster for this Product Qualified contact.",
    },
    {
        "name": "plg_sequence_enrollment_mode",
        "label": "PLG Sequence Enrollment Mode",
        "type": "enumeration",
        "fieldType": "select",
        "description": "Manual vs automated enrollment intent for Product Qualified contact outreach.",
        "options": [
            {"label": "Manual", "value": "manual", "displayOrder": 0, "hidden": False},
            {"label": "Automated", "value": "automated", "displayOrder": 1, "hidden": False},
        ],
    },
    {
        "name": "plg_sequence_requested_at",
        "label": "PLG Sequence Requested At",
        "type": "datetime",
        "fieldType": "date",
        "description": "Timestamp when Dagster requested Product Qualified sequence enrollment.",
    },
    {
        "name": "plg_sequence_sync_run_id",
        "label": "PLG Sequence Sync Run ID",
        "type": "string",
        "fieldType": "text",
        "description": "Dagster run ID that requested Product Qualified sequence enrollment.",
    },
    {
        "name": "plg_sequence_processed",
        "label": "PLG Sequence Processed",
        "type": "bool",
        "fieldType": "booleancheckbox",
        "description": "Stamped by the HubSpot workflow after Product Qualified sequence enrollment is processed.",
        "options": [
            {"label": "Yes", "value": "true", "displayOrder": 0, "hidden": False},
            {"label": "No", "value": "false", "displayOrder": 1, "hidden": False},
        ],
    },
    {
        "name": "plg_sequence_processed_at",
        "label": "PLG Sequence Processed At",
        "type": "datetime",
        "fieldType": "date",
        "description": "Timestamp stamped by the HubSpot workflow after Product Qualified sequence enrollment is processed.",
    },
]

CONTACT_PROPERTIES = [
    # ── Synced from BigQuery ─────────────────────────────────────────────────
    {
        "name": "pql_score",
        "label": "PQL Score",
        "type": "number",
        "fieldType": "number",
        "description": "User champion score (0–100). Source: pql_champions_latest.pql_score.",
    },
    {
        "name": "pql_champion_rank",
        "label": "PQL Champion Rank",
        "type": "number",
        "fieldType": "number",
        "description": "Rank within domain champion list (1 = top). Source: pql_champions_latest.champion_rank.",
    },
    {
        "name": "pql_is_team_admin",
        "label": "PQL Is Team Admin",
        "type": "bool",
        "fieldType": "booleancheckbox",
        "description": "Has billing/admin authority on their team. Score weight: 40%.",
        "options": [
            {"label": "Yes", "value": "true",  "displayOrder": 0, "hidden": False},
            {"label": "No",  "value": "false", "displayOrder": 1, "hidden": False},
        ],
    },
    {
        "name": "pql_ai_credit_usage_30d",
        "label": "PQL AI Credit Usage (30d)",
        "type": "number",
        "fieldType": "number",
        "description": "Individual AI credit consumption in the last 30 days. Score weight: 30%.",
    },
    {
        "name": "pql_activity_frequency",
        "label": "PQL Activity Frequency",
        "type": "number",
        "fieldType": "number",
        "description": "Active days in the scoring period. Score weight: 15%.",
    },
    {
        "name": "pql_hit_credit_limit_14d",
        "label": "PQL Hit Credit Limit (14d)",
        "type": "bool",
        "fieldType": "booleancheckbox",
        "description": "Personally hit a credit limit in the last 14 days. Score weight: 15%.",
        "options": [
            {"label": "Yes", "value": "true",  "displayOrder": 0, "hidden": False},
            {"label": "No",  "value": "false", "displayOrder": 1, "hidden": False},
        ],
    },
    # ── Computed by sync script ──────────────────────────────────────────────
    {
        "name": "pql_last_scored_at",
        "label": "PQL Last Scored At",
        "type": "datetime",
        "fieldType": "date",
        "description": "Timestamp of the most recent PQL weekly sync run.",
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ensure_property_group(object_type: str, group_name: str, group_label: str, dry_run: bool = False) -> None:
    """Create a property group if it doesn't already exist."""
    if dry_run:
        print(f"  [dry-run] Would ensure group '{group_name}' on {object_type}")
        return

    try:
        hubspot_request("POST", f"/crm/v3/properties/{object_type}/groups", {
            "name": group_name,
            "label": group_label,
            "displayOrder": -1,
        })
        print(f"  ✓ Created group '{group_name}' on {object_type}")
    except Exception as e:
        err = str(e)
        if "409" in err or "already exists" in err.lower() or "CONFLICT" in err:
            print(f"  — Group '{group_name}' already exists on {object_type}")
        else:
            raise


def create_properties(
    object_type: str,
    group_name: str,
    prop_defs: list[dict],
    dry_run: bool = False,
) -> tuple[int, int]:
    """Create properties under group_name, skipping any that already exist. Returns (created, skipped)."""
    if dry_run:
        for prop in prop_defs:
            print(f"  [dry-run] Would create: {prop['name']:<40} ({prop['type']})")
        return len(prop_defs), 0

    existing = hubspot_request("GET", f"/crm/v3/properties/{object_type}")
    existing_names = {p["name"] for p in existing.get("results", [])}

    created = skipped = 0

    for prop in prop_defs:
        name = prop["name"]

        if name in existing_names:
            print(f"  — {name:<40} already exists, skipping")
            skipped += 1
            continue

        payload = {
            "name": name,
            "label": prop["label"],
            "type": prop["type"],
            "fieldType": prop["fieldType"],
            "groupName": group_name,
            "description": prop.get("description", ""),
        }
        if "options" in prop:
            payload["options"] = prop["options"]

        try:
            hubspot_request("POST", f"/crm/v3/properties/{object_type}", payload)
            print(f"  ✓ {name:<40} ({prop['type']})")
            created += 1
        except Exception as e:
            print(f"  ✗ Failed to create {name}: {e}")

    return created, skipped


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Initialize PQA/PQL scoring properties in HubSpot."
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

    # ── Companies (group: pqa_scoring) ───────────────────────────────────────
    print("--- Companies ---")
    ensure_property_group("companies", COMPANY_GROUP_NAME, COMPANY_GROUP_LABEL, dry_run=dry_run)
    created_co, skipped_co = create_properties("companies", COMPANY_GROUP_NAME, COMPANY_PROPERTIES, dry_run=dry_run)

    # ── Contacts (group: pql_scoring) ────────────────────────────────────────
    print("\n--- Contacts ---")
    ensure_property_group("contacts", CONTACT_GROUP_NAME, CONTACT_GROUP_LABEL, dry_run=dry_run)
    created_ct, skipped_ct = create_properties("contacts", CONTACT_GROUP_NAME, CONTACT_PROPERTIES, dry_run=dry_run)
    ensure_property_group("contacts", CONTACT_SEQUENCE_GROUP_NAME, CONTACT_SEQUENCE_GROUP_LABEL, dry_run=dry_run)
    created_seq, skipped_seq = create_properties(
        "contacts",
        CONTACT_SEQUENCE_GROUP_NAME,
        CONTACT_SEQUENCE_PROPERTIES,
        dry_run=dry_run,
    )

    # ── Summary ──────────────────────────────────────────────────────────────
    total_created = created_co + created_ct + created_seq
    total_skipped = skipped_co + skipped_ct + skipped_seq
    print(f"\n=== Summary ===")
    print(f"  Companies : {created_co} created, {skipped_co} skipped")
    print(f"  Contacts  : {created_ct + created_seq} created, {skipped_ct + skipped_seq} skipped")
    print(f"  Total     : {total_created} created, {total_skipped} skipped")
    if not dry_run and total_created > 0:
        print(f"\n  View company properties:")
        print(f"  https://app.hubspot.com/property-settings/{PORTAL_ID}/properties?type=0-2&search=pqa")
        print(f"\n  View contact properties:")
        print(f"  https://app.hubspot.com/property-settings/{PORTAL_ID}/properties?type=0-1&search=pql")


if __name__ == "__main__":
    main()
