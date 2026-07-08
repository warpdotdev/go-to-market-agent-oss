import unittest

from bdr_agent.stages.company_research.config import SCHEMA_VERSION, STAGE
from bdr_agent.stages.company_research.schemas import (
    build_minimal_company_research_output,
    validate_company_research_output,
)


class SchemaTest(unittest.TestCase):
    def test_build_minimal_not_ready_output(self) -> None:
        output = build_minimal_company_research_output(
            lead_id="123",
            trigger_source="inbound_oz_campaign_pdf_download",
            hydration_status="not_ready",
            missing_fields=["contact"],
        )

        self.assertEqual(output["schema_version"], SCHEMA_VERSION)
        self.assertEqual(output["stage"], STAGE)
        self.assertEqual(output["lead"]["lead_id"], "123")
        self.assertEqual(output["hydration"]["hydration_status"], "not_ready")
        self.assertEqual(output["tier_3_external_research"]["reason"], "tier_3_disabled_for_mvp")

    def test_validate_rejects_unknown_hydration_status(self) -> None:
        output = build_minimal_company_research_output(
            lead_id="123",
            trigger_source="inbound_oz_campaign_pdf_download",
            hydration_status="not_ready",
            missing_fields=["contact"],
        )
        output["hydration"]["hydration_status"] = "unknown"

        with self.assertRaisesRegex(ValueError, "Unexpected hydration_status"):
            validate_company_research_output(output)


if __name__ == "__main__":
    unittest.main()