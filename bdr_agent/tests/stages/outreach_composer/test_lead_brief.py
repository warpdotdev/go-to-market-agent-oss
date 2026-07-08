import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from bdr_agent.stages.company_research.config import HOOKS_TABLE, OUTPUTS_TABLE, RUNS_TABLE, bigquery_table_id
from bdr_agent.stages.company_research.storage import validate_bigquery_row_shape
from bdr_agent.outreach_writeback.config import (
    CREATED_AT_PROPERTY_NAME,
    HOOK_PROPERTY_NAME,
    SOURCES_PROPERTY_NAME,
    WRITEBACK_STATUS_SKIPPED_DRY_RUN,
    WRITEBACK_STATUS_SUCCEEDED,
)
from bdr_agent.stages.outreach_composer.cli import main as lead_brief_cli_main
from bdr_agent.stages.outreach_composer.company_research import load_company_research_output
from bdr_agent.stages.outreach_composer.artifacts import (
    build_authenticated_gcs_url,
    render_lead_brief_markdown_html,
)
from bdr_agent.stages.outreach_composer.config import (
    CANONICAL_STAGE,
    DELIVERY_MODE_BOTH,
    DELIVERY_MODE_HUBSPOT,
    DELIVERY_MODE_SLACK,
    LEGACY_STAGE,
    PERSISTED_STAGE_MODE_CANONICAL,
    PERSISTED_STAGE_MODE_ENV_VAR,
    PERSISTED_STAGE_MODE_LEGACY,
    RUNTIME_STAGE,
    STAGE,
)
from bdr_agent.stages.outreach_composer.run import run_lead_brief
from bdr_agent.stages.outreach_composer.slack import (
    SLACK_REVIEW_HEADER,
    SLACK_STATUS_SKIPPED,
    SLACK_STATUS_SUCCEEDED,
)
from bdr_agent.stages.outreach_composer.storage import (
    SLACK_DELIVERY_OUTPUT_TYPE,
    build_run_metadata_row,
    build_slack_delivery_marker_row,
    build_slack_delivery_idempotency_key,
)
from bdr_agent.stages.outreach_composer.validation import normalize_lead_brief_packet


BDR_AGENT_ROOT = Path(__file__).parents[3]
FIXTURE_PATH = BDR_AGENT_ROOT / "tests" / "fixtures" / "company_research_output.json"
OUTREACH_COMPOSER_SKILL_PATH = (
    BDR_AGENT_ROOT / "skills" / "outreach-composer" / "SKILL.md"
)
POSITIONING_GUIDE_PATH = (
    BDR_AGENT_ROOT / "references" / "outreach_positioning_guide.md"
)
OUTREACH_STYLE_PATH = (
    BDR_AGENT_ROOT / "references" / "outreach_style_guide.md"
)
POSITIONING_FRAME_LABELS = [
    "capability_gap",
    "workflow_orchestration",
    "governance",
]


class FakeQueryJob:
    def __init__(self, rows) -> None:
        self.rows = rows

    def result(self, max_results=None):
        return self.rows[:max_results]


class FakeBigQueryClient:
    def __init__(self, rows=None) -> None:
        self.rows = rows or []
        self.queries = []
        self.inserted = []

    def query(self, query_text, job_config=None):
        self.queries.append({"query_text": query_text, "job_config": job_config})
        output_id = _query_parameter_value(job_config=job_config, name="output_id")
        if output_id is not None:
            return FakeQueryJob([row for row in self.rows if row.get("output_id") == output_id])
        return FakeQueryJob(self.rows)

    def insert_rows_json(self, table_id, rows, **kwargs):
        self.inserted.append((table_id, rows, kwargs.get("row_ids")))
        self.rows.extend(
            row for row in rows if row.get("output_type") == SLACK_DELIVERY_OUTPUT_TYPE
        )
        return []


def _query_parameter_value(*, job_config, name: str):
    if job_config is None:
        return None
    if isinstance(job_config, dict):
        parameters = job_config.get("query_parameters") or {}
        if isinstance(parameters, dict):
            return parameters.get(name)
    for parameter in getattr(job_config, "query_parameters", []) or []:
        if getattr(parameter, "name", None) == name:
            return getattr(parameter, "value", None)
    return None


class FakeGcsClient:
    def __init__(self) -> None:
        self.uploads = []

    def upload_text(self, gcs_uri, content):
        self.uploads.append((gcs_uri, content))


class FakeHubSpotClient:
    def __init__(self) -> None:
        self.updates = []

    def update_properties(self, object_type, object_id, properties) -> None:
        self.updates.append((object_type, object_id, properties))


class FakeSlackClient:
    def __init__(self) -> None:
        self.messages = []

    def post_message(self, payload):
        self.messages.append(payload)
        return {"ok": True, "ts": "1716240000.000100"}


def company_research_output() -> dict:
    return json.loads(FIXTURE_PATH.read_text())


def company_research_row(*, output_id="company_output_123", created_at="2026-05-19T00:00:00+00:00") -> dict:
    fixture = company_research_output()
    return {
        "output_id": output_id,
        "run_id": fixture["run_id"],
        "lead_id": fixture["lead"]["lead_id"],
        "contact_id": fixture["contact"]["contact_id"],
        "company_id": fixture["company"]["company_id"],
        "resolved_company_domain": fixture["hydration"]["resolved_company_domain"],
        "trigger_source": fixture["trigger_source"],
        "hydration_status": fixture["hydration"]["hydration_status"],
        "company_context_json": json.dumps(
            {
                "lead": fixture["lead"],
                "contact": fixture["contact"],
                "company": fixture["company"],
                "hydration": fixture["hydration"],
            }
        ),
        "tier_1_internal_metrics_json": json.dumps(fixture["tier_1_internal_metrics"]),
        "tier_2_public_research_json": json.dumps(fixture["tier_2_public_company_research"]),
        "tier_3_external_research_json": json.dumps(fixture["tier_3_external_research"]),
        "reuse_json": json.dumps(fixture["reuse"]),
        "research_status": "research_complete",
        "schema_version": fixture["schema_version"],
        "gcs_uri": fixture["storage"]["gcs_uri"],
        "created_at": created_at,
    }


def skill_frontmatter(path: Path) -> dict[str, str]:
    lines = path.read_text().splitlines()
    if not lines or lines[0] != "---":
        raise ValueError(f"{path} is missing frontmatter")
    closing_index = lines[1:].index("---") + 1
    frontmatter = {}
    for line in lines[1:closing_index]:
        key, value = line.split(":", 1)
        frontmatter[key.strip()] = value.strip()
    return frontmatter


def valid_packet() -> dict:
    return {
        "brief_markdown": (
            "# Lead brief: Ada Lovelace | Example\n\n"
            "## Lead details\n- **Lead:** Ada Lovelace\n- **Company:** Example\n\n"
            "## Company research findings\nExample has published about AI agents for engineering productivity.\n"
        ),
        "email_body_drafts": [
            {
                "rank": 1,
                "label": "AI agents angle",
                "why_this_may_work": "Connects public AI-agent work to the product without over-explaining.",
                "body": (
                    "Saw Example has been writing about AI agents for engineering productivity. "
                    "It made me think our platform might be relevant if your team is trying to make that kind of work easier to run outside one-off experiments.\n\n"
                    "Curious if making agent workflows more repeatable is something your team is looking at?"
                ),
                "source_refs": ["https://example.com/engineering/ai-agents"],
            },
            {
                "rank": 2,
                "label": "Engineering workflow angle",
                "why_this_may_work": "Uses the VP Engineering role as the bridge.",
                "body": (
                    "Your role sits close to the kinds of engineering workflow questions that tend to come up once teams start using AI agents seriously. "
                    "Our platform is focused on giving developers a practical way to hand off real work to agents while keeping the process visible.\n\n"
                    "Would it be worth comparing notes on what teams are actually standardizing around?"
                ),
                "source_refs": [],
            },
            {
                "rank": 3,
                "label": "Light usage angle",
                "why_this_may_work": "Keeps internal product activity qualitative and low-pressure.",
                "body": (
                    "There are signs Example may already have some engineers trying our product, so I wanted to reach out with a lighter note rather than a generic pitch. "
                    "The newer platform work is aimed at teams that want agents to handle longer-running engineering tasks.\n\n"
                    "If that is anywhere near your current workflow conversations, I would be glad to share what we are seeing."
                ),
                "source_refs": [],
            },
        ],
        "evaluation": {"status": "passed", "notes": "All drafts are body-only and source-backed."},
        "rewrite": {"attempted": False, "reason": None},
    }


class LeadBriefSkillContractTest(unittest.TestCase):
    def test_outreach_composer_skill_exists_with_current_runtime_contract(self) -> None:
        self.assertTrue(OUTREACH_COMPOSER_SKILL_PATH.exists())
        frontmatter = skill_frontmatter(OUTREACH_COMPOSER_SKILL_PATH)
        self.assertEqual(frontmatter["name"], "bdr-outreach-composer")
        self.assertIn("bdr_agent.stages.outreach_composer", frontmatter["description"])
        skill = OUTREACH_COMPOSER_SKILL_PATH.read_text()
        self.assertIn("BDR_AGENT_STAGE=lead_brief", skill)
        self.assertIn("$PYTHON -m bdr_agent.stages.outreach_composer.cli", skill)
        self.assertIn("--lead-brief-packet-json-file", skill)
        self.assertIn("gs://example-artifacts-bucket/bdr-agent/lead_brief", skill)

    def test_positioning_guide_has_current_version_and_frames(self) -> None:
        guide = POSITIONING_GUIDE_PATH.read_text()

        self.assertIn("Version: v1.0", guide)
        self.assertIn("Source: Example template", guide)
        for frame_label in POSITIONING_FRAME_LABELS:
            self.assertIn(f"`{frame_label}`", guide)

    def test_skill_uses_two_canonical_references_as_language_sources_not_verbatim_copy(self) -> None:
        skill = OUTREACH_COMPOSER_SKILL_PATH.read_text()

        self.assertIn("references/outreach_positioning_guide.md", skill)
        self.assertIn("references/outreach_style_guide.md", skill)
        self.assertIn("Read exactly these two durable editable references", skill)
        self.assertIn("Do not create or consult a separate standalone pattern library.", skill)
        self.assertRegex(skill, r"do not paste guide, deck, style-guide, or internal GTM phrasing verbatim")
        self.assertRegex(skill, r"not as copy to paste verbatim into the brief or email bodies")
        self.assertTrue(OUTREACH_STYLE_PATH.exists())

    def test_skill_requires_pre_draft_reasoning_and_final_self_check(self) -> None:
        skill = OUTREACH_COMPOSER_SKILL_PATH.read_text()

        self.assertIn("## Pre-draft reasoning check", skill)
        self.assertIn("The strongest credible source signal", skill)
        self.assertIn("How a human BDR would cite where the signal came from", skill)
        self.assertIn("Which style-guide principle applies", skill)
        self.assertIn("Which positioning-guide frame, buyer problem, concrete product capability", skill)
        self.assertIn("Final self-check:", skill)
        self.assertIn("source-specific signal a human BDR would plausibly mention", skill)
        self.assertIn("The product bridge is concrete enough", skill)

    def test_skill_preserves_lead_brief_runtime_compatibility_wording(self) -> None:
        skill = OUTREACH_COMPOSER_SKILL_PATH.read_text()

        self.assertIn("bdr_agent.stages.outreach_composer", skill)
        self.assertIn("$PYTHON -m bdr_agent.stages.outreach_composer.cli", skill)
        self.assertIn("--lead-brief-packet-json-file", skill)

    def test_skills_instruct_final_delivery_to_inherit_environment_mode(self) -> None:
        skill = OUTREACH_COMPOSER_SKILL_PATH.read_text()

        self.assertIn("Final persistence and delivery", skill)
        self.assertIn("Use the environment's configured delivery mode", skill)
        self.assertIn("Do not override `BDR_AGENT_REVIEW_DELIVERY_MODE`", skill)
        self.assertIn("--allow-hubspot-writeback", skill)
        self.assertIn("BDR_AGENT_REVIEW_DELIVERY_MODE=slack-and-hubspot", skill)
        self.assertIn("Slack-only test delivery is a special case", skill)


class LeadBriefCompanyResearchTest(unittest.TestCase):
    def test_loads_latest_company_research_by_lead_when_output_id_absent(self) -> None:
        client = FakeBigQueryClient([company_research_row(output_id="company_output_latest")])

        output = load_company_research_output(lead_id="lead_123", bigquery_client=client)

        self.assertEqual(output["output_id"], "company_output_latest")
        self.assertEqual(output["company"]["company_name"], "Example")
        self.assertEqual(len(client.queries), 1)
        self.assertIn("cast(lead_id as string) = @lead_id", client.queries[0]["query_text"])
        self.assertIn("order by created_at desc", client.queries[0]["query_text"].lower())

    def test_prefers_explicit_company_research_output_id(self) -> None:
        client = FakeBigQueryClient([company_research_row(output_id="company_output_123")])

        output = load_company_research_output(
            lead_id="lead_123",
            company_research_output_id="company_output_123",
            bigquery_client=client,
        )

        self.assertEqual(output["output_id"], "company_output_123")
        self.assertIn("where output_id = @output_id", client.queries[0]["query_text"])


class LeadBriefValidationTest(unittest.TestCase):
    def test_accepts_three_ranked_multi_paragraph_email_bodies(self) -> None:
        packet = normalize_lead_brief_packet(valid_packet())

        self.assertEqual([draft["rank"] for draft in packet["email_body_drafts"]], [1, 2, 3])
        self.assertIn("\n\n", packet["email_body_drafts"][0]["body"])

    def test_rejects_missing_ranked_draft(self) -> None:
        packet = valid_packet()
        packet["email_body_drafts"] = packet["email_body_drafts"][:2]

        with self.assertRaisesRegex(ValueError, "exactly three"):
            normalize_lead_brief_packet(packet)

    def test_rejects_greeting_signoff_sender_and_multiple_questions(self) -> None:
        for bad_body, expected in [
            ("Hi Ada,\n\nSaw Example has AI work.\n\nCurious to chat?", "greeting"),
            ("Saw Example has AI work.\n\nCurious to chat?\n\nBest,", "sign-off"),
            ("Saw Example has AI work.\n\nCurious to chat?\n\nIan", "sender name"),
            ("Saw Example has AI work?\n\nCould this be useful?", "more than one soft question"),
        ]:
            packet = valid_packet()
            packet["email_body_drafts"][0]["body"] = bad_body
            with self.assertRaisesRegex(ValueError, expected):
                normalize_lead_brief_packet(packet)

    def test_rejects_email_body_over_word_limit(self) -> None:
        packet = valid_packet()
        packet["email_body_drafts"][0]["body"] = (
            " ".join(["word"] * 43)
            + "\n\n"
            + " ".join(["word"] * 43)
        )

        with self.assertRaisesRegex(ValueError, "85 words or fewer"):
            normalize_lead_brief_packet(packet)

    def test_rejects_sweeping_insider_assessment_openings(self) -> None:
        for opening in [
            "Your push into",
            "Your investment in",
            "The work your team is doing around",
            "One of the most ambitious efforts in the space",
        ]:
            packet = valid_packet()
            packet["email_body_drafts"][0]["body"] = (
                f"{opening} AI agent workflows stood out from a quick read of public material.\n\n"
                "I thought our product might be relevant if developer workflows are part of the conversation."
            )

            with self.assertRaisesRegex(ValueError, "sweeping or insider-assessment"):
                normalize_lead_brief_packet(packet)


class LeadBriefRuntimeTest(unittest.TestCase):
    def test_authenticated_gcs_url_uses_private_storage_host(self) -> None:
        self.assertEqual(
            build_authenticated_gcs_url(
                gcs_uri=(
                    "gs://example-artifacts-bucket/bdr-agent/lead_brief/"
                    "run_123/output_123.html"
                )
            ),
            (
                "https://storage.cloud.google.com/example-artifacts-bucket/"
                "bdr-agent/lead_brief/run_123/output_123.html?authuser=0"
            ),
        )

    def test_render_lead_brief_markdown_html_renders_and_escapes_content(self) -> None:
        rendered = render_lead_brief_markdown_html(
            markdown=(
                "# Lead brief: Ada <script>\n\n"
                "## Lead details\n"
                "- **Lead:** Ada Lovelace\n"
                "- **Profile:** [Example](https://example.com/?a=1&b=2)\n\n"
                "Use `snippet` safely."
            )
        )

        self.assertIn("<h1>Lead brief: Ada &lt;script&gt;</h1>", rendered)
        self.assertIn("<strong>Lead:</strong> Ada Lovelace", rendered)
        self.assertIn(
            '<a href="https://example.com/?a=1&amp;b=2" target="_blank" rel="noopener noreferrer">Example</a>',
            rendered,
        )
        self.assertIn("<code>snippet</code>", rendered)
        self.assertNotIn("<script>", rendered)

    def test_stage_constants_keep_legacy_runtime_value_with_outreach_composer_alias(self) -> None:
        self.assertEqual(CANONICAL_STAGE, "outreach_composer")
        self.assertEqual(LEGACY_STAGE, "lead_brief")
        self.assertEqual(RUNTIME_STAGE, "lead_brief")
        self.assertEqual(STAGE, "lead_brief")
        self.assertEqual(PERSISTED_STAGE_MODE_LEGACY, "legacy")
        self.assertEqual(PERSISTED_STAGE_MODE_CANONICAL, "canonical")

    def test_dry_run_builds_three_email_body_hook_rows_without_writes(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "BDR_AGENT_OUTREACH_COMPOSER_DELIVERY_MODE": "",
                "BDR_AGENT_REVIEW_DELIVERY_MODE": "",
                "BDR_AGENT_LEAD_BRIEF_DELIVERY_MODE": "",
                "OZ_RUN_ID": "00000000-0000-0000-0000-000000000000",
                "WARP_SERVER_ROOT_URL": "https://staging.example.com",
                "WARP_FOCUS_URL": "warpdev://session/50c0d275c2574b9da0cb89eb8c22c06d",
            },
            clear=True,
        ):
            result = run_lead_brief(
                lead_id="lead_123",
                lead_brief_packet=valid_packet(),
                company_research_output=company_research_output(),
                run_id="lead_brief_run_123",
                output_id="lead_brief_output_123",
                email_draft_ids=["hook_1", "hook_2", "hook_3"],
                started_at="2026-05-21T00:00:00+00:00",
                completed_at="2026-05-21T00:00:03+00:00",
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["stage"], "lead_brief")
        self.assertEqual(result["runtime_stage"], "lead_brief")
        self.assertEqual(result["persisted_stage"], "lead_brief")
        self.assertEqual(result["persisted_stage_mode"], "legacy")
        self.assertEqual(result["lead_brief_gcs_uri"], "gs://example-artifacts-bucket/bdr-agent/lead_brief/lead_brief_run_123/lead_brief_output_123.md")
        self.assertEqual(result["lead_brief_html_gcs_uri"], "gs://example-artifacts-bucket/bdr-agent/lead_brief/lead_brief_run_123/lead_brief_output_123.html")
        self.assertEqual(
            result["lead_brief_url"],
            "https://storage.cloud.google.com/example-artifacts-bucket/bdr-agent/lead_brief/lead_brief_run_123/lead_brief_output_123.html?authuser=0",
        )
        self.assertEqual(result["hubspot_writeback"]["hook_property"]["status"], WRITEBACK_STATUS_SKIPPED_DRY_RUN)
        self.assertEqual(result["hubspot_writeback"]["created_at_property"]["status"], WRITEBACK_STATUS_SKIPPED_DRY_RUN)
        self.assertEqual(result["delivery_mode"], "dry_run")
        self.assertEqual(result["slack_notification"]["status"], SLACK_STATUS_SKIPPED)
        rows = result["email_body_hook_rows"]
        self.assertEqual([row["email_rank"] for row in rows], [1, 2, 3])
        self.assertEqual(rows[0]["content_kind"], "email_body")
        self.assertTrue(rows[0]["selected_for_hubspot"])
        self.assertFalse(rows[1]["selected_for_hubspot"])
        self.assertEqual(rows[0]["hook_text"], valid_packet()["email_body_drafts"][0]["body"])
        self.assertEqual(rows[0]["ai_hook_sources_url"], result["lead_brief_url"])
        self.assertIsNone(rows[0]["synthesis_output_id"])
        self.assertEqual(rows[0]["lead_brief_output_id"], "lead_brief_output_123")
        validate_bigquery_row_shape(table_id=bigquery_table_id(HOOKS_TABLE), row=rows[0])
        validate_bigquery_row_shape(table_id=bigquery_table_id(OUTPUTS_TABLE), row=result["output_index_row"])
        validate_bigquery_row_shape(table_id=bigquery_table_id(RUNS_TABLE), row=result["run_metadata_row"])

    def test_run_metadata_row_includes_runtime_oz_metadata(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "OZ_RUN_ID": "00000000-0000-0000-0000-000000000000",
                "WARP_SERVER_ROOT_URL": "https://staging.example.com",
                "WARP_FOCUS_URL": "warpdev://session/50c0d275c2574b9da0cb89eb8c22c06d",
            },
            clear=True,
        ):
            result = run_lead_brief(
                lead_id="lead_123",
                lead_brief_packet=valid_packet(),
                company_research_output=company_research_output(),
                run_id="lead_brief_run_123",
                output_id="lead_brief_output_123",
                email_draft_ids=["hook_1", "hook_2", "hook_3"],
            )

        row = result["run_metadata_row"]
        self.assertEqual(row["oz_run_id"], "00000000-0000-0000-0000-000000000000")
        self.assertEqual(
            row["oz_run_link"],
            "https://oz.staging.example.com/runs/00000000-0000-0000-0000-000000000000",
        )
        self.assertEqual(row["oz_session_link"], "warpdev://session/50c0d275c2574b9da0cb89eb8c22c06d")
        self.assertIsNone(row["oz_credits_used"])

    def test_build_run_metadata_row_accepts_explicit_oz_metadata(self) -> None:
        row = build_run_metadata_row(
            result={
                "run_id": "lead_brief_run_123",
                "stage": "lead_brief",
                "trigger_source": "hubspot_workflow",
                "lead_id": "lead_123",
                "completed_at": "2026-05-21T00:00:03+00:00",
                "status": "completed",
            },
            oz_run_id="run_123",
            oz_run_link="https://oz.example.test/runs/run_123",
            oz_session_link="warpdev://session/session_123",
            oz_credits_used=0.5,
        )

        self.assertEqual(row["oz_run_id"], "run_123")
        self.assertEqual(row["oz_run_link"], "https://oz.example.test/runs/run_123")
        self.assertEqual(row["oz_session_link"], "warpdev://session/session_123")
        self.assertEqual(row["oz_credits_used"], 0.5)

    def test_canonical_persisted_stage_mode_switches_stage_metadata_without_schema_migration(self) -> None:
        result = run_lead_brief(
            lead_id="lead_123",
            lead_brief_packet=valid_packet(),
            company_research_output=company_research_output(),
            run_id="outreach_composer_run_123",
            output_id="outreach_composer_output_123",
            email_draft_ids=["hook_1", "hook_2", "hook_3"],
            persisted_stage_mode=PERSISTED_STAGE_MODE_CANONICAL,
        )

        self.assertEqual(result["stage"], "outreach_composer")
        self.assertEqual(result["runtime_stage"], "lead_brief")
        self.assertEqual(result["persisted_stage"], "outreach_composer")
        self.assertEqual(result["persisted_stage_mode"], "canonical")
        self.assertEqual(
            result["lead_brief_gcs_uri"],
            (
                "gs://example-artifacts-bucket/bdr-agent/outreach_composer/"
                "outreach_composer_run_123/outreach_composer_output_123.md"
            ),
        )
        self.assertEqual(result["run_metadata_row"]["stage"], "outreach_composer")
        self.assertEqual(result["output_index_row"]["stage"], "outreach_composer")
        self.assertEqual(
            result["email_body_hook_rows"][0]["lint_result_json"]["stage"],
            "outreach_composer",
        )
        self.assertEqual(result["email_body_hook_rows"][0]["lead_brief_output_id"], result["output_id"])
        self.assertEqual(
            build_slack_delivery_idempotency_key(result=result),
            "outreach_composer_slack:lead_123:outreach_composer_output_123",
        )
        self.assertEqual(
            build_slack_delivery_marker_row(result=result)["stage"],
            "outreach_composer_slack_delivery",
        )
        validate_bigquery_row_shape(table_id=bigquery_table_id(HOOKS_TABLE), row=result["email_body_hook_rows"][0])
        validate_bigquery_row_shape(table_id=bigquery_table_id(OUTPUTS_TABLE), row=result["output_index_row"])
        validate_bigquery_row_shape(table_id=bigquery_table_id(RUNS_TABLE), row=result["run_metadata_row"])

    def test_canonical_persisted_stage_mode_can_be_enabled_by_env(self) -> None:
        with patch.dict("os.environ", {PERSISTED_STAGE_MODE_ENV_VAR: "outreach_composer"}, clear=False):
            result = run_lead_brief(
                lead_id="lead_123",
                lead_brief_packet=valid_packet(),
                company_research_output=company_research_output(),
                run_id="outreach_composer_run_123",
                output_id="outreach_composer_output_123",
                email_draft_ids=["hook_1", "hook_2", "hook_3"],
            )

        self.assertEqual(result["stage"], "outreach_composer")
        self.assertEqual(result["persisted_stage_mode"], "canonical")
        self.assertIn(
            "/outreach_composer/outreach_composer_run_123/outreach_composer_output_123.md",
            result["lead_brief_gcs_uri"],
        )

    def test_persist_writes_gcs_and_bigquery_but_skips_hubspot_by_default(self) -> None:
        bigquery_client = FakeBigQueryClient()
        gcs_client = FakeGcsClient()
        hubspot_client = FakeHubSpotClient()

        with patch.dict(
            "os.environ",
            {
                "BDR_AGENT_OUTREACH_COMPOSER_DELIVERY_MODE": "",
                "BDR_AGENT_REVIEW_DELIVERY_MODE": "",
                "BDR_AGENT_LEAD_BRIEF_DELIVERY_MODE": "",
                "OZ_RUN_ID": "00000000-0000-0000-0000-000000000000",
                "WARP_SERVER_ROOT_URL": "https://staging.example.com",
                "WARP_FOCUS_URL": "warpdev://session/50c0d275c2574b9da0cb89eb8c22c06d",
            },
            clear=True,
        ):
            result = run_lead_brief(
                lead_id="lead_123",
                lead_brief_packet=valid_packet(),
                company_research_output=company_research_output(),
                bigquery_client=bigquery_client,
                gcs_client=gcs_client,
                hubspot_client=hubspot_client,
                persist_bigquery=True,
                run_id="lead_brief_run_123",
                output_id="lead_brief_output_123",
                email_draft_ids=["hook_1", "hook_2", "hook_3"],
            )

        self.assertEqual(result["bigquery_persistence"]["status"], "persisted")
        self.assertEqual(len(gcs_client.uploads), 2)
        self.assertEqual(len(bigquery_client.inserted), 3)
        self.assertEqual(bigquery_client.inserted[0][0], bigquery_table_id(HOOKS_TABLE))
        self.assertEqual(len(bigquery_client.inserted[0][1]), 3)
        run_row = bigquery_client.inserted[2][1][0]
        self.assertEqual(run_row["oz_run_id"], "00000000-0000-0000-0000-000000000000")
        self.assertEqual(
            run_row["oz_run_link"],
            "https://oz.staging.example.com/runs/00000000-0000-0000-0000-000000000000",
        )
        self.assertEqual(run_row["oz_session_link"], "warpdev://session/50c0d275c2574b9da0cb89eb8c22c06d")
        self.assertIsNone(run_row["oz_credits_used"])
        self.assertEqual(hubspot_client.updates, [])

    def test_live_gate_writes_rank_one_body_sources_and_created_at_to_hubspot(self) -> None:
        hubspot_client = FakeHubSpotClient()

        result = run_lead_brief(
            lead_id="lead_123",
            lead_brief_packet=valid_packet(),
            company_research_output=company_research_output(),
            hubspot_client=hubspot_client,
            delivery_mode=DELIVERY_MODE_HUBSPOT,
            allow_hubspot_writeback=True,
        )

        self.assertEqual(result["hubspot_writeback"]["hook_property"]["status"], WRITEBACK_STATUS_SUCCEEDED)
        self.assertEqual(result["hubspot_writeback"]["created_at_property"]["status"], WRITEBACK_STATUS_SUCCEEDED)
        self.assertEqual(len(hubspot_client.updates), 3)
        self.assertEqual(hubspot_client.updates[0][2], {HOOK_PROPERTY_NAME: valid_packet()["email_body_drafts"][0]["body"]})
        self.assertEqual(hubspot_client.updates[1][2].keys(), {SOURCES_PROPERTY_NAME})
        self.assertEqual(hubspot_client.updates[2][2].keys(), {CREATED_AT_PROPERTY_NAME})
        self.assertTrue(hubspot_client.updates[2][2][CREATED_AT_PROPERTY_NAME].isdigit())

    def test_env_gate_allows_live_hubspot_write(self) -> None:
        hubspot_client = FakeHubSpotClient()
        with patch.dict(
            "os.environ",
            {
                "BDR_AGENT_ALLOW_HUBSPOT_WRITEBACK": "true",
                "BDR_AGENT_OUTREACH_COMPOSER_DELIVERY_MODE": "",
                "BDR_AGENT_REVIEW_DELIVERY_MODE": "",
                "BDR_AGENT_LEAD_BRIEF_DELIVERY_MODE": "hubspot",
            },
            clear=False,
        ):
            result = run_lead_brief(
                lead_id="lead_123",
                lead_brief_packet=valid_packet(),
                company_research_output=company_research_output(),
                hubspot_client=hubspot_client,
            )

        self.assertEqual(result["hubspot_writeback"]["hook_property"]["status"], WRITEBACK_STATUS_SUCCEEDED)
        self.assertEqual(result["hubspot_writeback"]["created_at_property"]["status"], WRITEBACK_STATUS_SUCCEEDED)
        self.assertEqual(len(hubspot_client.updates), 3)
        self.assertEqual(hubspot_client.updates[2][2].keys(), {CREATED_AT_PROPERTY_NAME})

    def test_default_delivery_mode_ignores_legacy_hubspot_allow_env(self) -> None:
        hubspot_client = FakeHubSpotClient()
        with patch.dict(
            "os.environ",
            {
                "BDR_AGENT_ALLOW_HUBSPOT_WRITEBACK": "true",
                "BDR_AGENT_OUTREACH_COMPOSER_DELIVERY_MODE": "",
                "BDR_AGENT_REVIEW_DELIVERY_MODE": "",
                "BDR_AGENT_LEAD_BRIEF_DELIVERY_MODE": "",
            },
            clear=False,
        ):
            result = run_lead_brief(
                lead_id="lead_123",
                lead_brief_packet=valid_packet(),
                company_research_output=company_research_output(),
                hubspot_client=hubspot_client,
            )

        self.assertEqual(result["delivery_mode"], "dry_run")
        self.assertTrue(result["hubspot_writeback_requested"])
        self.assertFalse(result["allow_hubspot_writeback"])
        self.assertEqual(result["hubspot_writeback"]["hook_property"]["status"], WRITEBACK_STATUS_SKIPPED_DRY_RUN)
        self.assertEqual(hubspot_client.updates, [])

    def test_slack_mode_persists_posts_review_and_skips_hubspot_writeback(self) -> None:
        bigquery_client = FakeBigQueryClient()
        gcs_client = FakeGcsClient()
        hubspot_client = FakeHubSpotClient()
        slack_client = FakeSlackClient()

        with patch.dict("os.environ", {"BDR_AGENT_ALLOW_HUBSPOT_WRITEBACK": "true"}, clear=False):
            result = run_lead_brief(
                lead_id="lead_123",
                lead_brief_packet=valid_packet(),
                company_research_output=company_research_output(),
                bigquery_client=bigquery_client,
                gcs_client=gcs_client,
                hubspot_client=hubspot_client,
                slack_client=slack_client,
                slack_channel_id="C123REVIEW",
                hubspot_portal_id="123456",
                persist_bigquery=True,
                delivery_mode=DELIVERY_MODE_SLACK,
                run_id="lead_brief_run_123",
                output_id="lead_brief_output_123",
                email_draft_ids=["hook_1", "hook_2", "hook_3"],
            )

        self.assertEqual(result["delivery_mode"], DELIVERY_MODE_SLACK)
        self.assertTrue(result["hubspot_writeback_requested"])
        self.assertFalse(result["allow_hubspot_writeback"])
        self.assertEqual(result["hubspot_writeback"]["hook_property"]["status"], WRITEBACK_STATUS_SKIPPED_DRY_RUN)
        self.assertEqual(result["bigquery_persistence"]["status"], "persisted")
        self.assertEqual(len(gcs_client.uploads), 2)
        self.assertEqual(gcs_client.uploads[0][0], "gs://example-artifacts-bucket/bdr-agent/lead_brief/lead_brief_run_123/lead_brief_output_123.md")
        self.assertEqual(gcs_client.uploads[1][0], "gs://example-artifacts-bucket/bdr-agent/lead_brief/lead_brief_run_123/lead_brief_output_123.html")
        self.assertIn("<h1>Lead brief: Ada Lovelace | Example</h1>", gcs_client.uploads[1][1])
        self.assertIn("<strong>Lead:</strong> Ada Lovelace", gcs_client.uploads[1][1])
        self.assertEqual(len(bigquery_client.inserted), 4)
        self.assertEqual(result["slack_delivery_marker"]["status"], "claimed")
        self.assertEqual(
            result["slack_delivery_marker"]["idempotency_key"],
            "lead_brief_slack:lead_123:lead_brief_output_123",
        )
        self.assertEqual(bigquery_client.inserted[3][0], bigquery_table_id(OUTPUTS_TABLE))
        self.assertEqual(bigquery_client.inserted[3][1][0]["output_type"], SLACK_DELIVERY_OUTPUT_TYPE)
        self.assertEqual(hubspot_client.updates, [])
        self.assertEqual(result["slack_notification"]["status"], SLACK_STATUS_SUCCEEDED)
        self.assertEqual(result["slack_notification"]["channel_id"], "C123REVIEW")
        self.assertEqual(result["slack_notification"]["message_ts"], "1716240000.000100")
        self.assertEqual(
            result["slack_notification"]["hubspot_record_url"],
            "https://app.hubspot.com/contacts/123456/record/0-1/contact_123",
        )
        self.assertIn("Hi Ada,", result["slack_notification"]["rendered_top_email_body"])
        self.assertIn("Best,", result["slack_notification"]["rendered_top_email_body"])
        self.assertEqual(len(slack_client.messages), 1)
        payload = slack_client.messages[0]
        self.assertEqual(payload["channel"], "C123REVIEW")
        self.assertIn("lead_brief_run_123/lead_brief_output_123.html", json.dumps(payload))
        self.assertNotIn("gs://example-artifacts-bucket", json.dumps(payload))
        self.assertEqual(payload["text"], f"{SLACK_REVIEW_HEADER}: <https://app.hubspot.com/contacts/123456/record/0-1/contact_123|Ada Lovelace> | VP Engineering | Example")
        self.assertIn(f"*{SLACK_REVIEW_HEADER}*", payload["blocks"][0]["text"]["text"])
        self.assertIn("<https://app.hubspot.com/contacts/123456/record/0-1/contact_123|Ada Lovelace> | VP Engineering | Example", payload["blocks"][0]["text"]["text"])
        self.assertEqual(
            payload["blocks"][1]["text"]["text"],
            "*Research brief:* <https://storage.cloud.google.com/example-artifacts-bucket/bdr-agent/lead_brief/lead_brief_run_123/lead_brief_output_123.html?authuser=0|Open research brief>",
        )
        self.assertNotIn("Lead ID", json.dumps(payload))
        self.assertNotIn("*HubSpot:*", json.dumps(payload))
        self.assertNotIn("Lead brief GCS URI", json.dumps(payload))
        self.assertIn("Hi Ada,", json.dumps(payload))
        self.assertIn("Best,", json.dumps(payload))

    def test_slack_payload_capitalizes_simple_lowercase_names(self) -> None:
        research_output = copy.deepcopy(company_research_output())
        research_output["contact"]["first_name"] = "baig"
        research_output["contact"]["last_name"] = "najib"
        research_output["contact"]["job_title"] = "senior manager - cyber engineering"
        research_output["company"]["company_name"] = "coalfire"
        slack_client = FakeSlackClient()
        bigquery_client = FakeBigQueryClient()
        gcs_client = FakeGcsClient()

        result = run_lead_brief(
            lead_id="lead_123",
            lead_brief_packet=valid_packet(),
            company_research_output=research_output,
            bigquery_client=bigquery_client,
            gcs_client=gcs_client,
            slack_client=slack_client,
            slack_channel_id="C123REVIEW",
            hubspot_portal_id="123456",
            persist_bigquery=True,
            delivery_mode=DELIVERY_MODE_SLACK,
        )

        self.assertIn("Hi Baig,", result["slack_notification"]["rendered_top_email_body"])
        self.assertEqual(len(slack_client.messages), 1)
        payload = slack_client.messages[0]
        expected_identity = "<https://app.hubspot.com/contacts/123456/record/0-1/contact_123|Baig Najib> | Senior Manager - Cyber Engineering | Coalfire"
        self.assertEqual(payload["text"], f"{SLACK_REVIEW_HEADER}: {expected_identity}")
        self.assertIn(expected_identity, payload["blocks"][0]["text"]["text"])

    def test_slack_mode_retry_same_output_is_not_posted_twice(self) -> None:
        bigquery_client = FakeBigQueryClient()
        gcs_client = FakeGcsClient()
        slack_client = FakeSlackClient()
        common_kwargs = {
            "lead_id": "lead_123",
            "lead_brief_packet": valid_packet(),
            "company_research_output": company_research_output(),
            "bigquery_client": bigquery_client,
            "gcs_client": gcs_client,
            "slack_client": slack_client,
            "slack_channel_id": "C123REVIEW",
            "persist_bigquery": True,
            "delivery_mode": DELIVERY_MODE_SLACK,
            "run_id": "lead_brief_run_123",
            "output_id": "lead_brief_output_123",
            "email_draft_ids": ["hook_1", "hook_2", "hook_3"],
        }

        first_result = run_lead_brief(**common_kwargs)
        retry_result = run_lead_brief(**common_kwargs)

        self.assertEqual(first_result["slack_delivery_marker"]["status"], "claimed")
        self.assertEqual(first_result["slack_notification"]["status"], SLACK_STATUS_SUCCEEDED)
        self.assertEqual(retry_result["slack_delivery_marker"]["status"], "duplicate")
        self.assertEqual(retry_result["slack_notification"]["status"], SLACK_STATUS_SKIPPED)
        self.assertFalse(retry_result["slack_notification"]["attempted"])
        self.assertEqual(retry_result["slack_notification"]["reason"], "duplicate_delivery_marker")
        self.assertEqual(len(slack_client.messages), 1)
        self.assertEqual(
            retry_result["slack_notification"]["idempotency_key"],
            build_slack_delivery_idempotency_key(result=retry_result),
        )

    def test_slack_mode_distinct_output_still_posts(self) -> None:
        bigquery_client = FakeBigQueryClient()
        gcs_client = FakeGcsClient()
        slack_client = FakeSlackClient()

        for output_id, hook_ids in [
            ("lead_brief_output_123", ["hook_1", "hook_2", "hook_3"]),
            ("lead_brief_output_456", ["hook_4", "hook_5", "hook_6"]),
        ]:
            result = run_lead_brief(
                lead_id="lead_123",
                lead_brief_packet=valid_packet(),
                company_research_output=company_research_output(),
                bigquery_client=bigquery_client,
                gcs_client=gcs_client,
                slack_client=slack_client,
                slack_channel_id="C123REVIEW",
                persist_bigquery=True,
                delivery_mode=DELIVERY_MODE_SLACK,
                run_id=f"run_for_{output_id}",
                output_id=output_id,
                email_draft_ids=hook_ids,
            )
            self.assertEqual(result["slack_delivery_marker"]["status"], "claimed")
            self.assertEqual(result["slack_notification"]["status"], SLACK_STATUS_SUCCEEDED)

        self.assertEqual(len(slack_client.messages), 2)

    def test_slack_delivery_requires_bigquery_persistence(self) -> None:
        slack_client = FakeSlackClient()

        result = run_lead_brief(
            lead_id="lead_123",
            lead_brief_packet=valid_packet(),
            company_research_output=company_research_output(),
            slack_client=slack_client,
            slack_channel_id="C123REVIEW",
            delivery_mode=DELIVERY_MODE_SLACK,
        )

        self.assertEqual(result["delivery_mode"], DELIVERY_MODE_SLACK)
        self.assertEqual(result["slack_delivery_marker"]["status"], "not_requested")
        self.assertEqual(result["slack_notification"]["status"], SLACK_STATUS_SKIPPED)
        self.assertEqual(result["slack_notification"]["reason"], "slack_delivery_requires_persistence")
        self.assertFalse(result["slack_notification"]["attempted"])
        self.assertEqual(slack_client.messages, [])

    def test_review_env_aliases_enable_slack_mode(self) -> None:
        slack_client = FakeSlackClient()
        bigquery_client = FakeBigQueryClient()
        gcs_client = FakeGcsClient()

        with patch.dict(
            "os.environ",
            {
                "BDR_AGENT_REVIEW_DELIVERY_MODE": "slack",
                "BDR_AGENT_REVIEW_SLACK_CHANNEL_ID": "C0REVIEWEXAMPLE",
                "BDR_AGENT_OUTREACH_COMPOSER_DELIVERY_MODE": "",
                "BDR_AGENT_OUTREACH_COMPOSER_SLACK_CHANNEL_ID": "",
                "BDR_AGENT_LEAD_BRIEF_DELIVERY_MODE": "",
                "BDR_AGENT_LEAD_BRIEF_SLACK_CHANNEL_ID": "",
            },
            clear=False,
        ):
            result = run_lead_brief(
                lead_id="lead_123",
                lead_brief_packet=valid_packet(),
                company_research_output=company_research_output(),
                bigquery_client=bigquery_client,
                gcs_client=gcs_client,
                slack_client=slack_client,
                hubspot_portal_id="123456",
                persist_bigquery=True,
            )

        self.assertEqual(result["delivery_mode"], DELIVERY_MODE_SLACK)
        self.assertEqual(result["slack_notification"]["status"], SLACK_STATUS_SUCCEEDED)
        self.assertEqual(result["slack_notification"]["channel_id"], "C0REVIEWEXAMPLE")
        self.assertEqual(slack_client.messages[0]["channel"], "C0REVIEWEXAMPLE")

    def test_outreach_composer_env_aliases_take_precedence_over_legacy_names(self) -> None:
        slack_client = FakeSlackClient()
        bigquery_client = FakeBigQueryClient()
        gcs_client = FakeGcsClient()

        with patch.dict(
            "os.environ",
            {
                "BDR_AGENT_OUTREACH_COMPOSER_DELIVERY_MODE": "slack",
                "BDR_AGENT_OUTREACH_COMPOSER_SLACK_CHANNEL_ID": "C0CANONICAL",
                "BDR_AGENT_REVIEW_DELIVERY_MODE": "",
                "BDR_AGENT_REVIEW_SLACK_CHANNEL_ID": "",
                "BDR_AGENT_LEAD_BRIEF_DELIVERY_MODE": "hubspot",
                "BDR_AGENT_LEAD_BRIEF_SLACK_CHANNEL_ID": "C0LEGACY",
            },
            clear=False,
        ):
            result = run_lead_brief(
                lead_id="lead_123",
                lead_brief_packet=valid_packet(),
                company_research_output=company_research_output(),
                bigquery_client=bigquery_client,
                gcs_client=gcs_client,
                slack_client=slack_client,
                hubspot_portal_id="123456",
                persist_bigquery=True,
            )

        self.assertEqual(result["delivery_mode"], DELIVERY_MODE_SLACK)
        self.assertEqual(result["slack_notification"]["status"], SLACK_STATUS_SUCCEEDED)
        self.assertEqual(result["slack_notification"]["channel_id"], "C0CANONICAL")
        self.assertEqual(slack_client.messages[0]["channel"], "C0CANONICAL")

    def test_slack_and_hubspot_delivery_mode_alias_maps_to_both(self) -> None:
        result = run_lead_brief(
            lead_id="lead_123",
            lead_brief_packet=valid_packet(),
            company_research_output=company_research_output(),
            bigquery_client=FakeBigQueryClient(),
            gcs_client=FakeGcsClient(),
            delivery_mode="slack-and-hubspot",
            slack_client=FakeSlackClient(),
            slack_channel_id="C123REVIEW",
            persist_bigquery=True,
        )

        self.assertEqual(result["delivery_mode"], DELIVERY_MODE_BOTH)
        self.assertEqual(result["slack_notification"]["status"], SLACK_STATUS_SUCCEEDED)

    def test_cli_json_output_no_network_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            packet_path = Path(tmpdir) / "packet.json"
            research_path = Path(tmpdir) / "research.json"
            packet_path.write_text(json.dumps(valid_packet()))
            research_path.write_text(json.dumps(company_research_output()))
            stdout = io.StringIO()
            with (
                patch.dict(
                    "os.environ",
                    {
                        "BDR_AGENT_OUTREACH_COMPOSER_DELIVERY_MODE": "",
                        "BDR_AGENT_REVIEW_DELIVERY_MODE": "",
                        "BDR_AGENT_LEAD_BRIEF_DELIVERY_MODE": "",
                    },
                    clear=False,
                ),
                redirect_stdout(stdout),
            ):
                exit_code = lead_brief_cli_main(
                    [
                        "--lead-id",
                        "lead_123",
                        "--lead-brief-packet-json-file",
                        str(packet_path),
                        "--company-research-json-file",
                        str(research_path),
                    ]
                )

        result = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(result["stage"], "lead_brief")
        self.assertEqual(len(result["email_body_drafts"]), 3)
        self.assertEqual(result["bigquery_persistence"]["status"], "not_requested")

    def test_cli_accepts_canonical_persisted_stage_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            packet_path = Path(tmpdir) / "packet.json"
            research_path = Path(tmpdir) / "research.json"
            packet_path.write_text(json.dumps(valid_packet()))
            research_path.write_text(json.dumps(company_research_output()))
            stdout = io.StringIO()
            with (
                patch.dict(
                    "os.environ",
                    {
                        "BDR_AGENT_OUTREACH_COMPOSER_DELIVERY_MODE": "",
                        "BDR_AGENT_REVIEW_DELIVERY_MODE": "",
                        "BDR_AGENT_LEAD_BRIEF_DELIVERY_MODE": "",
                    },
                    clear=False,
                ),
                redirect_stdout(stdout),
            ):
                exit_code = lead_brief_cli_main(
                    [
                        "--lead-id",
                        "lead_123",
                        "--lead-brief-packet-json-file",
                        str(packet_path),
                        "--company-research-json-file",
                        str(research_path),
                        "--persisted-stage-mode",
                        "canonical",
                    ]
                )

        result = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(result["stage"], "outreach_composer")
        self.assertEqual(result["persisted_stage_mode"], "canonical")
        self.assertIn("/outreach_composer/", result["lead_brief_gcs_uri"])

    def test_cli_accepts_outreach_composer_packet_json_file_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            packet_path = Path(tmpdir) / "packet.json"
            research_path = Path(tmpdir) / "research.json"
            packet_path.write_text(json.dumps(valid_packet()))
            research_path.write_text(json.dumps(company_research_output()))
            stdout = io.StringIO()
            with (
                patch.dict(
                    "os.environ",
                    {
                        "BDR_AGENT_OUTREACH_COMPOSER_DELIVERY_MODE": "",
                        "BDR_AGENT_REVIEW_DELIVERY_MODE": "",
                        "BDR_AGENT_LEAD_BRIEF_DELIVERY_MODE": "",
                    },
                    clear=False,
                ),
                redirect_stdout(stdout),
            ):
                exit_code = lead_brief_cli_main(
                    [
                        "--lead-id",
                        "lead_123",
                        "--outreach-composer-packet-json-file",
                        str(packet_path),
                        "--company-research-json-file",
                        str(research_path),
                    ]
                )

        result = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(result["stage"], "lead_brief")
        self.assertEqual(len(result["email_body_drafts"]), 3)
        self.assertEqual(result["bigquery_persistence"]["status"], "not_requested")


if __name__ == "__main__":
    unittest.main()
