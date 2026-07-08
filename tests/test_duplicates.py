import csv
import os
import tempfile
import unittest
from unittest import mock

from hubspot_agent import duplicates


class DuplicateTests(unittest.TestCase):
    def test_find_duplicate_contacts_groups_by_email_case_insensitively(self):
        contacts = [
            {"id": "1", "properties": {"email": "A@Example.com", "createdate": "2026-01-01T00:00:00Z"}},
            {"id": "2", "properties": {"email": "a@example.com", "createdate": "2026-01-02T00:00:00Z"}},
            {"id": "3", "properties": {"email": "b@example.com", "createdate": "2026-01-03T00:00:00Z"}},
        ]
        with mock.patch("hubspot_agent.duplicates._fetch_all", return_value=contacts):
            result = duplicates.find_duplicate_contacts(match_on="email")

        self.assertEqual(list(result.keys()), ["a@example.com"])
        self.assertEqual([record["id"] for record in result["a@example.com"]], ["1", "2"])

    def test_find_duplicate_contacts_groups_by_name(self):
        contacts = [
            {"id": "1", "properties": {"firstname": "Ada", "lastname": "Lovelace", "createdate": "2026-01-01T00:00:00Z"}},
            {"id": "2", "properties": {"firstname": "ada", "lastname": "lovelace", "createdate": "2026-01-02T00:00:00Z"}},
            {"id": "3", "properties": {"firstname": "Grace", "lastname": "Hopper", "createdate": "2026-01-03T00:00:00Z"}},
        ]
        with mock.patch("hubspot_agent.duplicates._fetch_all", return_value=contacts):
            result = duplicates.find_duplicate_contacts(match_on="name")

        self.assertIn("ada|lovelace", result)
        self.assertEqual(len(result["ada|lovelace"]), 2)

    def test_find_duplicate_companies_groups_by_domain(self):
        companies = [
            {"id": "1", "properties": {"domain": "example.com", "createdate": "2026-01-01T00:00:00Z"}},
            {"id": "2", "properties": {"domain": "Example.com", "createdate": "2026-01-02T00:00:00Z"}},
            {"id": "3", "properties": {"domain": "other.com", "createdate": "2026-01-03T00:00:00Z"}},
        ]
        with mock.patch("hubspot_agent.duplicates._fetch_all", return_value=companies):
            result = duplicates.find_duplicate_companies(match_on="domain")

        self.assertEqual(list(result.keys()), ["example.com"])

    def test_scan_recent_duplicates_returns_empty_for_unknown_type(self):
        self.assertEqual(duplicates.scan_recent_duplicates("tickets"), {})

    def test_scan_recent_duplicates_combines_local_and_cross_db_matches(self):
        recent = [
            {"id": "1", "properties": {"domain": "dup.com", "createdate": "2026-01-01T00:00:00Z"}},
            {"id": "2", "properties": {"domain": "dup.com", "createdate": "2026-01-02T00:00:00Z"}},
            {"id": "3", "properties": {"domain": "oldmatch.com", "createdate": "2026-01-03T00:00:00Z"}},
        ]
        cross_db_matches = [
            {"id": "3", "properties": {"domain": "oldmatch.com", "createdate": "2026-01-03T00:00:00Z"}},
            {"id": "9", "properties": {"domain": "oldmatch.com", "createdate": "2025-12-31T00:00:00Z"}},
        ]
        with mock.patch("hubspot_agent.duplicates._search_recent", return_value=recent), mock.patch(
            "hubspot_agent.duplicates._search_existing_by_key",
            return_value=cross_db_matches,
        ):
            result = duplicates.scan_recent_duplicates("companies", days=3)

        self.assertIn("dup.com", result)
        self.assertIn("oldmatch.com", result)
        self.assertEqual(len(result["dup.com"]), 2)
        self.assertEqual(len(result["oldmatch.com"]), 2)

    def test_merge_records_posts_expected_payload(self):
        with mock.patch("hubspot_agent.duplicates.hubspot_request", return_value={"ok": True}) as request_mock:
            duplicates.merge_records("contacts", "10", "11")

        request_mock.assert_called_once_with(
            "POST",
            "/crm/v3/objects/contacts/merge",
            data={"primaryObjectId": "10", "objectIdToMerge": "11"},
        )

    def test_auto_merge_cluster_merges_newer_records_into_oldest(self):
        cluster = [
            {"id": "2", "properties": {"createdate": "2026-01-02T00:00:00Z"}},
            {"id": "1", "properties": {"createdate": "2026-01-01T00:00:00Z"}},
            {"id": "3", "properties": {"createdate": "2026-01-03T00:00:00Z"}},
        ]
        with mock.patch("hubspot_agent.duplicates.merge_records") as merge_mock:
            primary_id, merged_ids = duplicates.auto_merge_cluster("companies", cluster)

        self.assertEqual(primary_id, "1")
        self.assertEqual(merged_ids, ["2", "3"])
        self.assertEqual(
            merge_mock.call_args_list,
            [
                mock.call("companies", "1", "2"),
                mock.call("companies", "1", "3"),
            ],
        )

    def test_generate_duplicate_report_writes_csv(self):
        dupes = {
            "example.com": [
                {
                    "id": "1",
                    "properties": {
                        "domain": "example.com",
                        "name": "Example",
                        "createdate": "2026-01-01T00:00:00Z",
                    },
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch(
            "hubspot_agent.duplicates.os.path.dirname",
            return_value=tmpdir,
        ):
            filepath = duplicates.generate_duplicate_report("companies", dupes, filename="dupes.csv")

            self.assertTrue(os.path.exists(filepath))
            with open(filepath, newline="") as handle:
                rows = list(csv.reader(handle))
        self.assertEqual(rows[0], ["cluster_key", "record_id", "email_or_domain", "name", "created_at"])
        self.assertEqual(rows[1][0], "example.com")
