import csv
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

from hubspot_agent import properties


class PropertyTests(unittest.TestCase):
    def test_find_stale_properties_skips_hubspot_defined_and_recent(self):
        fresh_time = datetime.now(timezone.utc).isoformat()
        mock_props = [
            {
                "name": "old_custom",
                "label": "Old Custom",
                "groupName": "contactinformation",
                "type": "string",
                "updatedAt": "2000-01-01T00:00:00Z",
            },
            {
                "name": "fresh_custom",
                "label": "Fresh Custom",
                "groupName": "contactinformation",
                "type": "string",
                "updatedAt": fresh_time,
            },
            {
                "name": "built_in",
                "label": "Built In",
                "hubspotDefined": True,
                "updatedAt": "2000-01-01T00:00:00Z",
            },
        ]
        with mock.patch("hubspot_agent.properties.list_properties", return_value=mock_props):
            result = properties.find_stale_properties("contacts", days=365)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "old_custom")

    def test_find_duplicate_properties_uses_fuzzy_match(self):
        mock_props = [
            {"name": "team_size", "label": "Team Size"},
            {"name": "teamsize", "label": "Team size"},
            {"name": "industry", "label": "Industry"},
            {"name": "firstname", "label": "First Name", "hubspotDefined": True},
        ]
        with mock.patch("hubspot_agent.properties.list_properties", return_value=mock_props):
            result = properties.find_duplicate_properties("contacts", similarity=0.9)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["property_a"], "team_size")
        self.assertEqual(result[0]["property_b"], "teamsize")

    def test_find_orphan_properties_filters_to_zero_fill(self):
        mock_low_fill = [
            {"name": "unused_prop", "filledRecords": 0},
            {"name": "rare_prop", "filledRecords": 2},
        ]
        with mock.patch("hubspot_agent.properties.find_low_fill_properties", return_value=mock_low_fill):
            result = properties.find_orphan_properties("contacts")

        self.assertEqual(result, [{"name": "unused_prop", "filledRecords": 0}])

    def test_generate_property_audit_report_writes_summary_csv(self):
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch(
            "hubspot_agent.properties.find_stale_properties",
            return_value=[
                {
                    "name": "old_prop",
                    "label": "Old Prop",
                    "groupName": "grp",
                    "updatedAt": "2000-01-01T00:00:00Z",
                }
            ],
        ), mock.patch(
            "hubspot_agent.properties.find_low_fill_properties",
            return_value=[
                {
                    "name": "unused_prop",
                    "label": "Unused Prop",
                    "groupName": "grp",
                    "fillRate": 0.0,
                    "filledRecords": 0,
                    "sampledRecords": 100,
                },
                {
                    "name": "rare_prop",
                    "label": "Rare Prop",
                    "groupName": "grp",
                    "fillRate": 0.02,
                    "filledRecords": 2,
                    "sampledRecords": 100,
                },
            ],
        ), mock.patch(
            "hubspot_agent.properties.find_duplicate_properties",
            return_value=[
                {
                    "property_a": "team_size",
                    "label_a": "Team Size",
                    "property_b": "teamsize",
                    "label_b": "Team size",
                    "similarity": 0.95,
                }
            ],
        ), mock.patch(
            "hubspot_agent.properties.os.path.dirname",
            return_value=tmpdir,
        ):
            filepath, summary = properties.generate_property_audit_report(
                "contacts",
                filename="audit.csv",
            )

            self.assertTrue(os.path.exists(filepath))
            with open(filepath, newline="") as handle:
                rows = list(csv.reader(handle))
        self.assertEqual(
            summary,
            {"stale": 1, "orphans": 1, "low_fill": 2, "duplicate_names": 1},
        )
        self.assertEqual(rows[0], ["category", "property_name", "label", "group", "detail"])
        self.assertEqual(len(rows), 5)
