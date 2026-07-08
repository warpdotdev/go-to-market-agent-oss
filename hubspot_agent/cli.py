"""Interactive menu-driven CLI for the HubSpot Agent."""

import json
import sys

from . import workflows, lists, duplicates, properties


def _input(prompt, default=None):
    """Prompt for input with an optional default value."""
    if default:
        val = input(f"{prompt} [{default}]: ").strip()
        return val or default
    return input(f"{prompt}: ").strip()


def _confirm(prompt):
    """Ask for yes/no confirmation."""
    return input(f"{prompt} (y/n): ").strip().lower() in ("y", "yes")


# ---------------------------------------------------------------------------
# Workflow sub-menu
# ---------------------------------------------------------------------------

def workflow_menu():
    while True:
        print("\n— Workflows —")
        print("  1. List all workflows")
        print("  2. View workflow detail")
        print("  3. Create workflow from JSON file")
        print("  4. Delete a workflow")
        print("  5. Enable/disable a workflow")
        print("  0. Back")
        choice = _input("Choice", "0")

        if choice == "1":
            wfs = workflows.list_workflows()
            workflows.print_workflow_table(wfs)

        elif choice == "2":
            flow_id = _input("Workflow ID")
            detail = workflows.get_workflow(flow_id)
            print(json.dumps(detail, indent=2))

        elif choice == "3":
            path = _input("Path to workflow JSON file")
            result = workflows.create_workflow(path)
            print(f"  Created workflow: {result.get('id')}")

        elif choice == "4":
            flow_id = _input("Workflow ID to delete")
            if _confirm(f"  Delete workflow {flow_id}?"):
                workflows.delete_workflow(flow_id)
                print("  Deleted.")

        elif choice == "5":
            flow_id = _input("Workflow ID")
            action = _input("Enable or disable? (e/d)")
            enabled = action.lower().startswith("e")
            workflows.toggle_workflow(flow_id, enabled)
            print(f"  Workflow {'enabled' if enabled else 'disabled'}.")

        elif choice == "0":
            break


# ---------------------------------------------------------------------------
# Lists sub-menu
# ---------------------------------------------------------------------------

def list_menu():
    while True:
        print("\n— Lists —")
        print("  1. List all lists")
        print("  2. Create a new list")
        print("  3. Import CSV to a new list")
        print("  4. Add records to a list")
        print("  5. Remove records from a list")
        print("  0. Back")
        choice = _input("Choice", "0")

        if choice == "1":
            obj = _input("Object type ID (0-1=contacts, 0-2=companies)", "0-1")
            ls = lists.list_lists(object_type_id=obj)
            lists.print_list_table(ls)

        elif choice == "2":
            name = _input("List name")
            obj = _input("Object type ID", "0-1")
            ptype = _input("Processing type (MANUAL/DYNAMIC)", "MANUAL")
            list_id = lists.create_list(name, obj, ptype)
            print(f"  Created list ID: {list_id}")

        elif choice == "3":
            csv_path = _input("CSV file path")
            list_name = _input("List name")
            obj = _input("Object type ID", "0-1")
            id_col = _input("ID column name", "hs_object_id")
            lid, count = lists.import_csv_to_list(csv_path, list_name, obj, id_col)
            print(f"  Imported {count} records into list {lid}")

        elif choice == "4":
            list_id = _input("List ID")
            ids_str = _input("Record IDs (comma-separated)")
            record_ids = [i.strip() for i in ids_str.split(",") if i.strip()]
            lists.add_records_to_list(list_id, record_ids)

        elif choice == "5":
            list_id = _input("List ID")
            ids_str = _input("Record IDs (comma-separated)")
            record_ids = [i.strip() for i in ids_str.split(",") if i.strip()]
            lists.remove_records_from_list(list_id, record_ids)

        elif choice == "0":
            break


# ---------------------------------------------------------------------------
# Duplicates sub-menu
# ---------------------------------------------------------------------------

def duplicate_menu():
    while True:
        print("\n— Duplicates —")
        print("  1. Scan contacts for duplicates")
        print("  2. Scan companies for duplicates")
        print("  3. Merge a specific pair")
        print("  4. Auto-merge all duplicates (with approval)")
        print("  5. Export duplicate report")
        print("  0. Back")
        choice = _input("Choice", "0")

        if choice == "1":
            match = _input("Match on (email/name)", "email")
            dupes = duplicates.find_duplicate_contacts(match_on=match)
            duplicates.print_duplicate_summary(dupes, "contacts")
            # Stash for report/merge
            duplicate_menu._last_dupes = ("contacts", dupes)

        elif choice == "2":
            match = _input("Match on (domain/name)", "domain")
            dupes = duplicates.find_duplicate_companies(match_on=match)
            duplicates.print_duplicate_summary(dupes, "companies")
            duplicate_menu._last_dupes = ("companies", dupes)

        elif choice == "3":
            obj = _input("Object type (contacts/companies)", "contacts")
            primary = _input("Primary record ID (older, will survive)")
            secondary = _input("Secondary record ID (newer, will be merged)")
            if _confirm(f"  Merge {secondary} into {primary}?"):
                duplicates.merge_records(obj, primary, secondary)
                print("  Merged.")

        elif choice == "4":
            if not hasattr(duplicate_menu, "_last_dupes") or not duplicate_menu._last_dupes:
                print("  Run a scan first (option 1 or 2).")
                continue
            obj_type, dupes = duplicate_menu._last_dupes
            total = sum(len(v) - 1 for v in dupes.values())
            print(f"\n  This will merge {total} records across {len(dupes)} clusters.")
            print("  Merge direction: newer → older (older record survives).")
            if not _confirm("  Proceed?"):
                continue
            for key, cluster in dupes.items():
                print(f"  Cluster: {key}")
                duplicates.auto_merge_cluster(obj_type, cluster)
            print("  All merges complete.")

        elif choice == "5":
            if not hasattr(duplicate_menu, "_last_dupes") or not duplicate_menu._last_dupes:
                print("  Run a scan first (option 1 or 2).")
                continue
            obj_type, dupes = duplicate_menu._last_dupes
            duplicates.generate_duplicate_report(obj_type, dupes)

        elif choice == "0":
            break

duplicate_menu._last_dupes = None


# ---------------------------------------------------------------------------
# Properties sub-menu
# ---------------------------------------------------------------------------

def property_menu():
    while True:
        print("\n— Property Audit —")
        print("  1. Full audit (stale + low-fill + duplicates → CSV)")
        print("  2. Find stale properties (>1 year)")
        print("  3. Find low-fill properties (<5%)")
        print("  4. Find orphan properties (0% fill)")
        print("  5. Find duplicate-named properties")
        print("  6. List all custom properties")
        print("  0. Back")
        choice = _input("Choice", "0")

        obj = None
        if choice in ("1", "2", "3", "4", "5", "6"):
            obj = _input("Object type (contacts/companies/deals/tickets)", "contacts")

        if choice == "1":
            properties.generate_property_audit_report(obj)

        elif choice == "2":
            days = int(_input("Stale threshold in days", "365"))
            stale = properties.find_stale_properties(obj, days)
            properties.print_stale_table(stale)

        elif choice == "3":
            threshold = float(_input("Fill rate threshold (0.0–1.0)", "0.05"))
            low = properties.find_low_fill_properties(obj, threshold)
            print(f"  Found {len(low)} properties below {threshold:.0%} fill rate")
            for p in low[:20]:
                print(f"    {p['name']:<35} {p['fillRate']:.1%}  ({p['filledRecords']}/{p['sampledRecords']})")

        elif choice == "4":
            orphans = properties.find_orphan_properties(obj)
            print(f"  Found {len(orphans)} orphan properties (0 filled records)")
            for p in orphans[:20]:
                print(f"    {p['name']:<35} {p['label']}")

        elif choice == "5":
            sim = float(_input("Similarity threshold (0.0–1.0)", "0.80"))
            dupes = properties.find_duplicate_properties(obj, sim)
            print(f"  Found {len(dupes)} similar property pairs")
            for d in dupes[:20]:
                print(f"    {d['label_a']:<30} ↔ {d['label_b']:<30} ({d['similarity']:.0%})")

        elif choice == "6":
            all_props = properties.list_properties(obj)
            custom = [p for p in all_props if not p.get("hubspotDefined")]
            print(f"  {len(custom)} custom properties (of {len(all_props)} total)")
            for p in custom:
                print(f"    {p['name']:<35} {p.get('label', ''):<30} [{p.get('type', '')}]")

        elif choice == "0":
            break


# ---------------------------------------------------------------------------
# Main menu
# ---------------------------------------------------------------------------

def main():
    print("╔══════════════════════════════════╗")
    print("║       HubSpot Agent CLI          ║")
    print("╚══════════════════════════════════╝")

    while True:
        print("\n— Main Menu —")
        print("  1. Workflows")
        print("  2. Lists")
        print("  3. Duplicates")
        print("  4. Properties")
        print("  5. Exit")
        choice = _input("Choice", "5")

        if choice == "1":
            workflow_menu()
        elif choice == "2":
            list_menu()
        elif choice == "3":
            duplicate_menu()
        elif choice == "4":
            property_menu()
        elif choice == "5":
            print("Bye!")
            sys.exit(0)
