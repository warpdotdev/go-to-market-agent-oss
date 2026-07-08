import unittest

from bdr_agent.hubspot_workflow_validation import (
    DEFAULT_WORKFLOW_ID,
    validate_bdr_workflow,
)


def base_workflow(webhook_action):
    return {
        "id": DEFAULT_WORKFLOW_ID,
        "name": "[TEST] BDR Agent PDF lead qualification + 30m wait - disabled",
        "isEnabled": False,
        "objectTypeId": "0-136",
        "type": "PLATFORM_FLOW",
        "actions": [webhook_action],
    }


class HubSpotWorkflowValidationTest(unittest.TestCase):
    def test_validates_disabled_post_webhook_with_lead_id_json_body_mapping(self):
        workflow = base_workflow(
            {
                "type": "WEBHOOK",
                "method": "POST",
                "webhookUrl": "https://budserver.example/webhooks/bdr-agent-pdf-lead",
                "requestBody": {
                    "lead_id": {
                        "type": "OBJECT_PROPERTY",
                        "propertyName": "hs_object_id",
                    },
                    "contact_id": {
                        "type": "OBJECT_PROPERTY",
                        "propertyName": "hs_object_id",
                    },
                    "company_id": {
                        "type": "OBJECT_PROPERTY",
                        "propertyName": "hs_primary_company_id",
                    },
                    "company_domain": {
                        "type": "OBJECT_PROPERTY",
                        "propertyName": "hs_associated_company_domain",
                    },
                },
            }
        )

        report = validate_bdr_workflow(workflow)

        self.assertTrue(report["valid"])
        self.assertTrue(report["webhook_actions"][0]["has_lead_id_body_mapping"])
        self.assertEqual(report["webhook_actions"][0]["missing_required_body_keys"], [])
        self.assertTrue(report["webhook_actions"][0]["has_company_domain_body_key"])

    def test_minimal_lead_id_body_mapping_is_valid_with_recommended_field_warnings(self):
        workflow = base_workflow(
            {
                "type": "WEBHOOK",
                "method": "POST",
                "webhookUrl": "https://budserver.example/webhooks/bdr-agent-pdf-lead",
                "requestBody": {
                    "lead_id": {
                        "type": "OBJECT_PROPERTY",
                        "propertyName": "hs_object_id",
                    },
                },
            }
        )

        report = validate_bdr_workflow(workflow)

        self.assertTrue(report["valid"])
        self.assertEqual(report["webhook_actions"][0]["missing_required_body_keys"], [])
        self.assertFalse(report["webhook_actions"][0]["has_company_domain_body_key"])
        warning_checks = {check["name"] for check in report["checks"] if check["severity"] == "warning"}
        self.assertIn("company_domain_json_body_key", warning_checks)

    def test_rejects_enabled_workflow_even_when_body_mapping_is_valid(self):
        workflow = base_workflow(
            {
                "type": "WEBHOOK",
                "method": "POST",
                "webhookUrl": "https://budserver.example/webhooks/bdr-agent-pdf-lead",
                "requestBody": '{"lead_id":"{{ hs_object_id }}","contact_id":"{{ hs_object_id }}","company_id":"{{ hs_primary_company_id }}","company_domain":"{{ hs_associated_company_domain }}"}',
            }
        )
        workflow["isEnabled"] = True

        report = validate_bdr_workflow(workflow)

        self.assertFalse(report["valid"])
        failed_checks = {check["name"] for check in report["checks"] if not check["passed"]}
        self.assertIn("workflow_disabled", failed_checks)

    def test_query_param_mapping_does_not_satisfy_json_body_contract(self):
        workflow = base_workflow(
            {
                "type": "WEBHOOK",
                "method": "POST",
                "webhookUrl": "https://budserver.example/webhooks/bdr-agent-pdf-lead",
                "queryParams": [
                    {
                        "name": "lead_id",
                        "value": {
                            "type": "OBJECT_PROPERTY",
                            "propertyName": "hs_object_id",
                        },
                    }
                ],
            }
        )

        report = validate_bdr_workflow(workflow)

        self.assertFalse(report["valid"])
        self.assertTrue(report["webhook_actions"][0]["has_lead_id_query_param_mapping"])
        failed_checks = {check["name"] for check in report["checks"] if not check["passed"]}
        self.assertIn("lead_id_json_body_mapping", failed_checks)

    def test_finds_nested_webhook_action_body_mapping(self):
        workflow = base_workflow(
            {
                "type": "IF_THEN",
                "branches": [
                    {
                        "actions": [
                            {
                                "fields": {
                                    "type": "WEBHOOK",
                                    "method": "POST",
                                    "webhookUrl": "https://budserver.example/webhooks/bdr-agent-pdf-lead",
                                    "customRequestBody": {
                                        "lead_id": {
                                            "propertyName": "hs_object_id",
                                        },
                                        "contact_id": {
                                            "propertyName": "hs_object_id",
                                        },
                                        "company_id": {
                                            "propertyName": "hs_primary_company_id",
                                        },
                                        "company_website": {
                                            "propertyName": "website",
                                        },
                                    },
                                }
                            }
                        ]
                    }
                ],
            }
        )

        report = validate_bdr_workflow(workflow)

        self.assertTrue(report["valid"])
        self.assertEqual(len(report["webhook_actions"]), 1)


if __name__ == "__main__":
    unittest.main()
