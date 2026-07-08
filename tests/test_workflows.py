import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from hubspot_agent import workflows


class WorkflowTests(unittest.TestCase):
    def test_list_workflows_maps_summary_fields(self):
        flow = {
            "id": "wf-1",
            "name": "Welcome flow",
            "isEnabled": True,
            "type": "DRIP_DELAY",
            "objectTypeId": "0-2",
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-02T00:00:00Z",
        }
        with mock.patch("hubspot_agent.workflows.paginated_get", return_value=[flow]):
            result = workflows.list_workflows()

        self.assertEqual(
            result,
            [
                {
                    "id": "wf-1",
                    "name": "Welcome flow",
                    "enabled": True,
                    "type": "DRIP_DELAY",
                    "objectTypeId": "0-2",
                    "objectType": "Companies",
                    "createdAt": "2026-01-01T00:00:00Z",
                    "updatedAt": "2026-01-02T00:00:00Z",
                }
            ],
        )

    def test_create_workflow_accepts_dict(self):
        spec = {"name": "Test workflow", "isEnabled": False}
        with mock.patch(
            "hubspot_agent.workflows.hubspot_request",
            return_value={"id": "wf-123"},
        ) as request_mock:
            result = workflows.create_workflow(spec)

        self.assertEqual(result, {"id": "wf-123"})
        request_mock.assert_called_once_with("POST", "/automation/v4/flows", data=spec)

    def test_create_workflow_accepts_json_file_path(self):
        with tempfile.NamedTemporaryFile("w+", suffix=".json") as tmp:
            json.dump({"name": "From file"}, tmp)
            tmp.flush()
            with mock.patch(
                "hubspot_agent.workflows.hubspot_request",
                return_value={"id": "wf-file"},
            ) as request_mock:
                result = workflows.create_workflow(tmp.name)

        self.assertEqual(result, {"id": "wf-file"})
        request_mock.assert_called_once_with(
            "POST",
            "/automation/v4/flows",
            data={"name": "From file"},
        )

    def test_toggle_workflow_updates_enabled_state(self):
        current = {"id": "wf-1", "name": "Flow", "isEnabled": False}
        with mock.patch("hubspot_agent.workflows.get_workflow", return_value=current.copy()), mock.patch(
            "hubspot_agent.workflows.hubspot_request",
            return_value={"ok": True},
        ) as request_mock:
            workflows.toggle_workflow("wf-1", True)

        request_mock.assert_called_once_with(
            "PUT",
            "/automation/v4/flows/wf-1",
            data={"id": "wf-1", "name": "Flow", "isEnabled": True},
        )

    def test_print_workflow_table_handles_empty_state(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            workflows.print_workflow_table([])

        self.assertIn("No workflows found", buffer.getvalue())

    def test_print_workflow_table_prints_rows(self):
        buffer = io.StringIO()
        rows = [{"id": "wf-1", "enabled": True, "objectType": "Contacts", "name": "Flow A"}]
        with redirect_stdout(buffer):
            workflows.print_workflow_table(rows)

        output = buffer.getvalue()
        self.assertIn("ID", output)
        self.assertIn("wf-1", output)
        self.assertIn("Flow A", output)
