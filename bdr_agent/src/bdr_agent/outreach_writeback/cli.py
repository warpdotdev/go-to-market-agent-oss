"""Command-line entrypoint for BDR candidate hook generation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from bdr_agent.outreach_writeback.artifacts import load_evidence_packet, load_synthesis_brief
from bdr_agent.outreach_writeback.config import HUBSPOT_CONTACT_OBJECT_TYPE
from bdr_agent.outreach_writeback.run import run_outreach_writeback


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run BDR candidate hook generation.")
    parser.add_argument("--lead-id", required=True)
    parser.add_argument("--contact-id")
    parser.add_argument("--company-id")
    parser.add_argument("--resolved-company-domain")
    parser.add_argument("--company-research-output-id")
    parser.add_argument("--company-research-json-file")
    parser.add_argument(
        "--evidence-packet-json-file",
        help="Optional local downstream hook evidence packet JSON. Defaults to parsing the synthesis brief.",
    )
    parser.add_argument("--hubspot-owner-id")
    parser.add_argument("--synthesis-run-id")
    parser.add_argument("--synthesis-output-id", required=True)
    parser.add_argument("--synthesis-gcs-uri", required=True)
    parser.add_argument("--synthesis-brief-file")
    parser.add_argument(
        "--fetch-synthesis-artifact",
        action="store_true",
        help="Fetch --synthesis-gcs-uri with google-cloud-storage when no local synthesis brief file is provided.",
    )
    parser.add_argument("--ai-hook-sources-url")
    parser.add_argument("--trigger-source", default="inbound_oz_campaign_pdf_download")
    parser.add_argument("--hubspot-object-type", default=HUBSPOT_CONTACT_OBJECT_TYPE)
    parser.add_argument("--hubspot-object-id")
    parser.add_argument(
        "--allow-writes",
        action="store_true",
        help="Legacy writeback path. Do not use for candidate-only write-hook stage runs.",
    )
    parser.add_argument(
        "--persist-bigquery",
        action="store_true",
        help="Persist the candidate artifacts, candidate row, and output index without HubSpot writes.",
    )
    parser.add_argument(
        "--skip-candidate-artifact-persistence",
        action="store_true",
        help="Persist BigQuery rows without writing candidate/evaluate artifacts to GCS. This also prevents stage-completion handoff.",
    )
    parser.add_argument(
        "--stage-completion-webhook-url",
        help="Optional agent_orchestrator stage-completion webhook URL. If omitted, the runner reads BDR_AGENT_STAGE_COMPLETION_WEBHOOK_URL.",
    )
    parser.add_argument(
        "--skip-stage-completion",
        action="store_true",
        help="Skip evaluate_and_writeback stage-completion handoff even after successful candidate persistence.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    company_research = _read_json_file(args.company_research_json_file)
    synthesis_brief = load_synthesis_brief(
        synthesis_brief_file=args.synthesis_brief_file,
        synthesis_gcs_uri=args.synthesis_gcs_uri,
        fetch_synthesis_artifact=args.fetch_synthesis_artifact,
    )
    evidence_packet = load_evidence_packet(
        evidence_packet_json_file=args.evidence_packet_json_file,
        synthesis_brief=synthesis_brief,
    )
    result = run_outreach_writeback(
        lead_id=args.lead_id,
        contact_id=args.contact_id,
        company_id=args.company_id,
        resolved_company_domain=args.resolved_company_domain,
        company_research_output_id=args.company_research_output_id,
        synthesis_run_id=args.synthesis_run_id,
        synthesis_output_id=args.synthesis_output_id,
        synthesis_gcs_uri=args.synthesis_gcs_uri,
        synthesis_brief=synthesis_brief,
        evidence_packet=evidence_packet,
        company_research=company_research,
        hubspot_owner_id=args.hubspot_owner_id,
        ai_hook_sources_url=args.ai_hook_sources_url,
        trigger_source=args.trigger_source,
        allow_writes=args.allow_writes,
        hubspot_object_type=args.hubspot_object_type,
        hubspot_object_id=args.hubspot_object_id,
        persist_bigquery=args.persist_bigquery,
        persist_candidate_artifacts=not args.skip_candidate_artifact_persistence,
        stage_completion_webhook_url=args.stage_completion_webhook_url,
        send_stage_completion_on_success=not args.skip_stage_completion,
    )
    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0 if not result.get("status", "").endswith("_failed") else 1


def _read_json_file(path: str | None) -> dict | None:
    if not path:
        return None
    return json.loads(Path(path).read_text())


if __name__ == "__main__":
    raise SystemExit(main())

