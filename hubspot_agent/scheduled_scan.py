#!/usr/bin/env python3
"""Scheduled duplicate scan — run via cron on Mon/Wed/Fri at 9am.

Scans contacts, companies, and deals created in the last 3 days for
duplicates, then posts a summary to #your-hubspot-alerts-channel via Slack webhook.

Usage:
    python3 -m hubspot_agent.scheduled_scan           # run scan + post to Slack
    python3 -m hubspot_agent.scheduled_scan --dry-run  # run scan, print only (no Slack)

Cron setup (Mon/Wed/Fri at 9am Pacific):
    0 9 * * 1,3,5 cd /path/to/gtm-agents && /usr/bin/python3 -m hubspot_agent.scheduled_scan >> /tmp/hubspot_scan.log 2>&1
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime

from .duplicates import scan_recent_duplicates, generate_duplicate_report

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Load Slack webhook from env or .env file
SLACK_WEBHOOK_URL = os.environ.get("HUBSPOT_SLACK_WEBHOOK", "")
if not SLACK_WEBHOOK_URL:
    _env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if os.path.exists(_env_path):
        with open(_env_path) as _f:
            for _line in _f:
                _line = _line.strip()
                if _line.startswith("HUBSPOT_SLACK_WEBHOOK="):
                    SLACK_WEBHOOK_URL = _line.split("=", 1)[1].strip()

SCAN_DAYS = 3  # look back 3 days (overlap ensures nothing is missed)
OBJECT_TYPES = ["contacts", "companies", "deals"]
PORTAL_ID = os.environ.get("HUBSPOT_PORTAL_ID", "000000000")


# ---------------------------------------------------------------------------
# Slack posting
# ---------------------------------------------------------------------------

def post_to_slack(message, dry_run=False):
    """Post a message to #your-hubspot-alerts-channel via Slack webhook."""
    if dry_run:
        print("\n[DRY RUN] Would post to Slack:")
        print(message)
        return

    if not SLACK_WEBHOOK_URL:
        print("\n⚠  SLACK_WEBHOOK_URL not configured — printing to stdout instead.")
        print(message)
        return

    payload = json.dumps({"text": message}).encode()
    req = urllib.request.Request(
        SLACK_WEBHOOK_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            if resp.status == 200:
                print("  Posted to #your-hubspot-alerts-channel ✓")
    except urllib.error.HTTPError as e:
        print(f"  Slack webhook error: HTTP {e.code} — {e.read().decode()}")


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------

def _format_cluster(key, records, object_type):
    """Format a single duplicate cluster for Slack."""
    ids = ", ".join(r["id"] for r in records)
    dates = [r.get("properties", {}).get("createdate", "?")[:10] for r in records]
    portal_url = f"https://app.hubspot.com/contacts/{PORTAL_ID}"
    # Link to the oldest record
    oldest = min(records, key=lambda r: r.get("properties", {}).get("createdate", ""))
    obj_path = {"contacts": "contact", "companies": "company", "deals": "deal"}.get(object_type, "record")
    link = f"<{portal_url}/{obj_path}/{oldest['id']}|{key}>"
    return f"  • {link} — {len(records)} records (IDs: {ids}), created: {', '.join(dates)}"


def build_slack_message(results):
    """Build the full Slack message from scan results."""
    now = datetime.now().strftime("%A, %B %d %Y at %I:%M %p")
    lines = [f":mag: *HubSpot Duplicate Scan — {now}*\n"]

    total_clusters = 0
    total_records = 0

    for obj_type, dupes in results.items():
        count = len(dupes)
        records = sum(len(v) for v in dupes.values())
        total_clusters += count
        total_records += records

        emoji = {"contacts": ":bust_in_silhouette:", "companies": ":office:", "deals": ":handshake:"}.get(obj_type, ":mag:")

        if count == 0:
            lines.append(f"{emoji} *{obj_type.title()}*: No duplicates found ✓")
        else:
            lines.append(f"{emoji} *{obj_type.title()}*: *{count}* duplicate clusters ({records} records)")
            # Show top 10 clusters
            sorted_dupes = sorted(dupes.items(), key=lambda x: -len(x[1]))
            for key, records_list in sorted_dupes[:10]:
                lines.append(_format_cluster(key, records_list, obj_type))
            if count > 10:
                lines.append(f"  _… and {count - 10} more clusters (see CSV report)_")
        lines.append("")

    if total_clusters == 0:
        lines.append(":white_check_mark: *All clear — no duplicates detected in the last 3 days.*")
    else:
        lines.append(f":warning: *Total: {total_clusters} clusters across {total_records} records. Review and merge as needed.*")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    dry_run = "--dry-run" in sys.argv
    print(f"=== HubSpot Scheduled Duplicate Scan — {datetime.now().isoformat()} ===")
    if dry_run:
        print("  (dry-run mode — no Slack posting)")

    results = {}
    csv_reports = []

    for obj_type in OBJECT_TYPES:
        print(f"\n--- {obj_type.title()} ---")
        try:
            dupes = scan_recent_duplicates(obj_type, days=SCAN_DAYS)
            results[obj_type] = dupes
            if dupes:
                report = generate_duplicate_report(obj_type, dupes)
                csv_reports.append(report)
        except Exception as e:
            print(f"  ERROR scanning {obj_type}: {e}")
            results[obj_type] = {}

    # Build and post Slack message
    message = build_slack_message(results)
    post_to_slack(message, dry_run=dry_run)

    if csv_reports:
        print(f"\nCSV reports written: {', '.join(csv_reports)}")
    print("\n=== Done ===")


if __name__ == "__main__":
    main()
