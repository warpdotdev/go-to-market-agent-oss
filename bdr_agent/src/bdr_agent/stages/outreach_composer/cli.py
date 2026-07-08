"""Command-line entrypoint for persisting a skill-authored BDR lead brief."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from bdr_agent.outreach_writeback.config import HUBSPOT_CONTACT_OBJECT_TYPE
from bdr_agent.stages.outreach_composer.config import (
    DEFAULT_ARTIFACT_BASE_URI,
    DEFAULT_TRIGGER_SOURCE,
    DELIVERY_MODE_BOTH,
    DELIVERY_MODE_DRY_RUN,
    DELIVERY_MODE_HUBSPOT,
    DELIVERY_MODE_SLACK,
    DELIVERY_MODE_SLACK_AND_HUBSPOT,
    PERSISTED_STAGE_MODE_CANONICAL,
    PERSISTED_STAGE_MODE_LEGACY,
)
from bdr_agent.stages.outreach_composer.run import run_lead_brief


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Persist a skill-authored BDR lead brief and ranked email bodies.")
    parser.add_argument("--lead-id", required=True)
    parser.add_argument(
        "--lead-brief-packet-json-file",
        "--outreach-composer-packet-json-file",
        dest="lead_brief_packet_json_file",
        required=True,
    )
    parser.add_argument("--company-research-json-file")
    parser.add_argument("--contact-id")
    parser.add_argument("--company-id")
    parser.add_argument("--resolved-company-domain")
    parser.add_argument("--company-research-run-id")
    parser.add_argument("--company-research-output-id")
    parser.add_argument("--company-research-bigquery-table")
    parser.add_argument("--trigger-source", default=DEFAULT_TRIGGER_SOURCE)
    parser.add_argument("--artifact-base-uri", default=DEFAULT_ARTIFACT_BASE_URI)
    parser.add_argument("--persist-bigquery", action="store_true")
    parser.add_argument(
        "--persisted-stage-mode",
        choices=[PERSISTED_STAGE_MODE_LEGACY, PERSISTED_STAGE_MODE_CANONICAL],
        help=(
            "Persist metadata under the legacy lead_brief stage or the canonical "
            "outreach_composer stage. Defaults to the legacy-compatible mode."
        ),
    )
    parser.add_argument(
        "--delivery-mode",
        choices=[
            DELIVERY_MODE_DRY_RUN,
            DELIVERY_MODE_SLACK,
            DELIVERY_MODE_HUBSPOT,
            DELIVERY_MODE_BOTH,
            DELIVERY_MODE_SLACK_AND_HUBSPOT,
        ],
    )
    parser.add_argument("--allow-hubspot-writeback", action="store_true")
    parser.add_argument("--hubspot-object-type", default=HUBSPOT_CONTACT_OBJECT_TYPE)
    parser.add_argument("--hubspot-object-id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_lead_brief(
        lead_id=args.lead_id,
        lead_brief_packet=_read_json_file(args.lead_brief_packet_json_file),
        contact_id=args.contact_id,
        company_id=args.company_id,
        resolved_company_domain=args.resolved_company_domain,
        company_research_run_id=args.company_research_run_id,
        company_research_output_id=args.company_research_output_id,
        company_research_output=_read_json_file(args.company_research_json_file),
        company_research_bigquery_table=args.company_research_bigquery_table,
        trigger_source=args.trigger_source,
        artifact_base_uri=args.artifact_base_uri,
        persist_bigquery=args.persist_bigquery,
        persisted_stage_mode=args.persisted_stage_mode,
        delivery_mode=args.delivery_mode,
        allow_hubspot_writeback=args.allow_hubspot_writeback,
        hubspot_object_type=args.hubspot_object_type,
        hubspot_object_id=args.hubspot_object_id,
    )
    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0 if result["status"] == "completed" else 1


def _read_json_file(path: str | None) -> dict | None:
    if not path:
        return None
    return json.loads(Path(path).read_text())


if __name__ == "__main__":
    raise SystemExit(main())
