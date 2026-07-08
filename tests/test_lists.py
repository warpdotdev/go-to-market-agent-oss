import csv
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from hubspot_agent import lists


class ListTests(unittest.TestCase):
    def test_list_lists_filters_by_object_type(self):
        response = {
            "lists": [
                {"listId": "1", "objectTypeId": "0-1", "name": "Contacts"},
                {"listId": "2", "objectTypeId": "0-2", "name": "Companies"},
            ]
        }
        with mock.patch("hubspot_agent.lists.hubspot_request", return_value=response):
            result = lists.list_lists(object_type_id="0-2")

        self.assertEqual(result, [{"listId": "2", "objectTypeId": "0-2", "name": "Companies"}])

    def test_add_records_to_list_batches_in_chunks_of_250(self):
        record_ids = [str(i) for i in range(501)]
        with mock.patch("hubspot_agent.lists.hubspot_request") as request_mock:
            added = lists.add_records_to_list("list-1", record_ids)

        self.assertEqual(added, 501)
        self.assertEqual(request_mock.call_count, 3)
        self.assertEqual(request_mock.call_args_list[0].args[2][-1], "249")
        self.assertEqual(request_mock.call_args_list[1].args[2][0], "250")
        self.assertEqual(request_mock.call_args_list[2].args[2], ["500"])

    def test_remove_records_from_list_batches_in_chunks_of_250(self):
        record_ids = [str(i) for i in range(251)]
        with mock.patch("hubspot_agent.lists.hubspot_request") as request_mock:
            removed = lists.remove_records_from_list("list-1", record_ids)

        self.assertEqual(removed, 251)
        self.assertEqual(request_mock.call_count, 2)

    def test_import_csv_to_list_raises_for_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            lists.import_csv_to_list("/tmp/does-not-exist.csv", "My List")

    def test_import_csv_to_list_raises_for_missing_id_column(self):
        with tempfile.NamedTemporaryFile("w+", newline="", suffix=".csv") as tmp:
            writer = csv.DictWriter(tmp, fieldnames=["wrong_column"])
            writer.writeheader()
            writer.writerow({"wrong_column": "123"})
            tmp.flush()

            with self.assertRaises(ValueError):
                lists.import_csv_to_list(tmp.name, "My List")

    def test_import_csv_to_list_deduplicates_ids_preserving_order(self):
        with tempfile.NamedTemporaryFile("w+", newline="", suffix=".csv") as tmp:
            writer = csv.DictWriter(tmp, fieldnames=["hs_object_id"])
            writer.writeheader()
            writer.writerow({"hs_object_id": "123"})
            writer.writerow({"hs_object_id": "123"})
            writer.writerow({"hs_object_id": "456"})
            writer.writerow({"hs_object_id": ""})
            tmp.flush()

            with mock.patch("hubspot_agent.lists.create_list", return_value="list-99"), mock.patch(
                "hubspot_agent.lists.add_records_to_list"
            ) as add_mock:
                list_id, count = lists.import_csv_to_list(tmp.name, "Imported List")

        self.assertEqual((list_id, count), ("list-99", 2))
        add_mock.assert_called_once_with("list-99", ["123", "456"])

    def test_print_list_table_handles_empty_state(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            lists.print_list_table([])

        self.assertIn("No lists found", buffer.getvalue())
