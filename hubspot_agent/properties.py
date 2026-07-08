"""Property audit — find stale, unused, low-fill, and duplicate-named properties.

Helps keep HubSpot properties lean by surfacing candidates for cleanup.
"""

import csv
import difflib
import os
from datetime import datetime, timedelta, timezone

from .client import hubspot_request, paginated_get


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------

def list_properties(object_type):
    """Return all properties for an object type with full metadata."""
    result = hubspot_request("GET", f"/crm/v3/properties/{object_type}")
    return result.get("results", [])


def _sample_records(object_type, properties, sample_size=1000):
    """Fetch a sample of records to estimate property fill rates."""
    props = ",".join(properties[:50])  # API limits query param length
    path = f"/crm/v3/objects/{object_type}?limit=100&properties={props}"
    records = []
    for item in paginated_get(path, key="results"):
        records.append(item)
        if len(records) >= sample_size:
            break
    return records


# ---------------------------------------------------------------------------
# Stale properties
# ---------------------------------------------------------------------------

def find_stale_properties(object_type, days=365):
    """Find custom properties whose definition hasn't been updated in *days*.

    Uses the property's `updatedAt` metadata field.
    """
    all_props = list_properties(object_type)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    stale = []
    for p in all_props:
        if p.get("hubspotDefined"):
            continue  # skip built-in properties
        updated = p.get("updatedAt") or p.get("createdAt", "")
        try:
            updated_dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            updated_dt = datetime.min.replace(tzinfo=timezone.utc)
        if updated_dt < cutoff:
            stale.append({
                "name": p["name"],
                "label": p.get("label", ""),
                "groupName": p.get("groupName", ""),
                "type": p.get("type", ""),
                "updatedAt": updated,
                "createdAt": p.get("createdAt", ""),
            })
    return stale


# ---------------------------------------------------------------------------
# Low-fill properties
# ---------------------------------------------------------------------------

def find_low_fill_properties(object_type, threshold=0.05, sample_size=1000):
    """Find custom properties with fill rate below *threshold* (0.0–1.0).

    Samples up to *sample_size* records to estimate fill rate.
    """
    all_props = list_properties(object_type)
    custom_props = [p for p in all_props if not p.get("hubspotDefined")]
    prop_names = [p["name"] for p in custom_props]

    if not prop_names:
        return []

    # Fetch in chunks (API limits properties per request)
    chunk_size = 50
    fill_counts = {name: 0 for name in prop_names}
    total_records = 0

    for i in range(0, len(prop_names), chunk_size):
        chunk = prop_names[i : i + chunk_size]
        records = _sample_records(object_type, chunk, sample_size)
        if i == 0:
            total_records = len(records)
        for r in records:
            props = r.get("properties", {})
            for name in chunk:
                val = props.get(name)
                if val is not None and val != "":
                    fill_counts[name] += 1

    if total_records == 0:
        return []

    low_fill = []
    prop_map = {p["name"]: p for p in custom_props}
    for name, count in fill_counts.items():
        rate = count / total_records
        if rate < threshold:
            p = prop_map[name]
            low_fill.append({
                "name": name,
                "label": p.get("label", ""),
                "groupName": p.get("groupName", ""),
                "fillRate": round(rate, 4),
                "filledRecords": count,
                "sampledRecords": total_records,
            })
    low_fill.sort(key=lambda x: x["fillRate"])
    return low_fill


# ---------------------------------------------------------------------------
# Orphan properties (zero fill in sample)
# ---------------------------------------------------------------------------

def find_orphan_properties(object_type, sample_size=1000):
    """Find custom properties with absolutely no data in a sample of records."""
    return [p for p in find_low_fill_properties(object_type, threshold=0.001, sample_size=sample_size)
            if p["filledRecords"] == 0]


# ---------------------------------------------------------------------------
# Duplicate-named properties (fuzzy match)
# ---------------------------------------------------------------------------

def find_duplicate_properties(object_type, similarity=0.80):
    """Find properties with suspiciously similar labels (fuzzy match).

    Returns list of (prop_a, prop_b, similarity_score) tuples.
    """
    all_props = list_properties(object_type)
    custom_props = [p for p in all_props if not p.get("hubspotDefined")]
    matches = []
    seen = set()

    for i, a in enumerate(custom_props):
        for b in custom_props[i + 1 :]:
            label_a = (a.get("label") or a["name"]).lower()
            label_b = (b.get("label") or b["name"]).lower()
            ratio = difflib.SequenceMatcher(None, label_a, label_b).ratio()
            if ratio >= similarity:
                pair_key = tuple(sorted([a["name"], b["name"]]))
                if pair_key not in seen:
                    seen.add(pair_key)
                    matches.append({
                        "property_a": a["name"],
                        "label_a": a.get("label", ""),
                        "property_b": b["name"],
                        "label_b": b.get("label", ""),
                        "similarity": round(ratio, 3),
                    })
    matches.sort(key=lambda x: -x["similarity"])
    return matches


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def generate_property_audit_report(object_type, filename=None):
    """Run full property audit and write a consolidated CSV report.

    Returns (filepath, summary_dict).
    """
    print(f"  Auditing {object_type} properties…")

    print("  → Finding stale properties…")
    stale = find_stale_properties(object_type)

    print("  → Estimating fill rates…")
    low_fill = find_low_fill_properties(object_type)
    orphans = [p for p in low_fill if p["filledRecords"] == 0]

    print("  → Checking for duplicate names…")
    dupe_names = find_duplicate_properties(object_type)

    # Build unified report
    if filename is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"property_audit_{object_type}_{ts}.csv"
    reports_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "reports")
    os.makedirs(reports_dir, exist_ok=True)
    filepath = os.path.join(reports_dir, filename)

    with open(filepath, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["category", "property_name", "label", "group", "detail"])

        for p in stale:
            writer.writerow(["stale", p["name"], p["label"], p["groupName"],
                             f"last updated {p['updatedAt']}"])
        for p in orphans:
            writer.writerow(["orphan", p["name"], p["label"], p["groupName"],
                             "0 records filled in sample"])
        for p in low_fill:
            if p["filledRecords"] > 0:
                writer.writerow(["low_fill", p["name"], p["label"], p["groupName"],
                                 f"fill rate {p['fillRate']:.1%} ({p['filledRecords']}/{p['sampledRecords']})"])
        for d in dupe_names:
            writer.writerow(["duplicate_name", d["property_a"], d["label_a"], "",
                             f"similar to '{d['label_b']}' ({d['property_b']}) — {d['similarity']:.0%} match"])

    summary = {
        "stale": len(stale),
        "orphans": len(orphans),
        "low_fill": len(low_fill),
        "duplicate_names": len(dupe_names),
    }
    print(f"\n  Audit complete:")
    print(f"    Stale (>1yr):      {summary['stale']}")
    print(f"    Orphan (0% fill):  {summary['orphans']}")
    print(f"    Low fill (<5%):    {summary['low_fill']}")
    print(f"    Duplicate names:   {summary['duplicate_names']}")
    print(f"  Report: {filepath}")
    return filepath, summary


def print_stale_table(stale_props):
    """Pretty-print stale properties."""
    if not stale_props:
        print("  No stale properties found.")
        return
    print(f"  {'Name':<35} {'Label':<30} {'Last Updated'}")
    print(f"  {'—'*35} {'—'*30} {'—'*25}")
    for p in stale_props[:30]:
        print(f"  {p['name']:<35} {p['label']:<30} {p['updatedAt'][:10]}")
    if len(stale_props) > 30:
        print(f"  … and {len(stale_props) - 30} more")
