"""Command-line entrypoint for BDR company research."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from bdr_agent.stages.company_research.run import (
    HYDRATION_COMPLETE_STATUS,
    RESEARCH_COMPLETE_STATUS,
    run_company_research,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run BDR company research.")
    parser.add_argument("--lead-id", required=True)
    parser.add_argument("--trigger-source", required=True)
    parser.add_argument("--source-system", required=True)
    parser.add_argument("--hubspot-workflow-id", required=True)
    parser.add_argument(
        "--webhook-payload-json-file",
        help="JSON file containing the HubSpot webhook lead/contact/company payload.",
    )
    parser.add_argument("--lead-created-at")
    parser.add_argument("--hubspot-owner-id")
    parser.add_argument("--lead-owner-id")
    parser.add_argument("--lead-source-detailed")
    parser.add_argument("--contact-id")
    parser.add_argument("--contact-email")
    parser.add_argument("--contact-first-name")
    parser.add_argument("--contact-last-name")
    parser.add_argument("--contact-job-title")
    parser.add_argument("--company-id")
    parser.add_argument("--company-name")
    parser.add_argument("--company-domain")
    parser.add_argument("--company-website")
    parser.add_argument("--company-alternative-domain")
    parser.add_argument("--company-industry")
    parser.add_argument("--company-num-employees")
    parser.add_argument("--company-icp-tier")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Run read-only hydration/research and do not write GCS artifacts or BigQuery rows.",
    )
    mode.add_argument(
        "--persist",
        action="store_true",
        help="Write the JSON artifact to GCS and rows to BigQuery after research completes.",
    )
    parser.add_argument(
        "--skip-bigquery",
        action="store_true",
        help="Skip BigQuery fallback hydration for missing webhook fields.",
    )
    parser.add_argument(
        "--stage-completion-webhook-url",
        help="Optional agent_orchestrator stage-completion webhook URL. If omitted, the runner reads BDR_AGENT_STAGE_COMPLETION_WEBHOOK_URL.",
    )
    parser.add_argument(
        "--skip-stage-completion",
        action="store_true",
        help="Skip downstream stage-completion handoff even after successful persistence.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    dry_run = args.dry_run or not args.persist
    result = run_company_research(
        lead_id=args.lead_id,
        trigger_source=args.trigger_source,
        source_system=args.source_system,
        hubspot_workflow_id=args.hubspot_workflow_id,
        dry_run=dry_run,
        skip_bigquery=args.skip_bigquery,
        webhook_payload=_build_webhook_payload(args),
        persist=args.persist,
        stage_completion_webhook_url=args.stage_completion_webhook_url,
        send_stage_completion_on_success=not args.skip_stage_completion,
    )
    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    success_statuses = {
        "completed",
        HYDRATION_COMPLETE_STATUS,
        RESEARCH_COMPLETE_STATUS,
        "missing_required_company_context",
        "not_ready",
        "not_implemented",
    }
    return 0 if result["status"] in success_statuses else 1


def _build_webhook_payload(args: argparse.Namespace) -> dict:
    payload = {}
    if args.webhook_payload_json_file:
        payload.update(json.loads(Path(args.webhook_payload_json_file).read_text()))
    cli_payload = {
        "lead_id": args.lead_id,
        "lead_created_at": args.lead_created_at,
        "hubspot_owner_id": args.hubspot_owner_id,
        "lead_owner_id": args.lead_owner_id,
        "lead_source_detailed": args.lead_source_detailed,
        "contact_id": args.contact_id,
        "contact_email": args.contact_email,
        "contact_first_name": args.contact_first_name,
        "contact_last_name": args.contact_last_name,
        "contact_job_title": args.contact_job_title,
        "company_id": args.company_id,
        "company_name": args.company_name,
        "company_domain": args.company_domain,
        "company_website": args.company_website,
        "company_alternative_domain": args.company_alternative_domain,
        "company_industry": args.company_industry,
        "company_num_employees": args.company_num_employees,
        "company_icp_tier": args.company_icp_tier,
    }
    payload.update({key: value for key, value in cli_payload.items() if value is not None})
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
