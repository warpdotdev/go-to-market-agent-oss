"""Safe smoke checks for BDR Agent Oz Dev stage validation.

The default run is read-only: it checks local skill files, dependency imports,
BDR_AGENT_EXA_API_KEY boolean availability, BigQuery read authentication, and
optionally Step 2 dry-run execution. GCS and BigQuery writes require explicit
flags plus a test ID.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import sys
import traceback
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
for path in (SRC_ROOT, REPO_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from bdr_agent.stages.company_research.config import (  # noqa: E402
    BDR_AGENT_OZ_DEV_ENVIRONMENT_ID,
    EXA_API_KEY_ENV_VAR,
    GCP_PROJECT_ID,
    GCS_ARTIFACT_BUCKET,
    GCS_ARTIFACT_PREFIX,
)
from bdr_agent.stages.company_research.run import run_company_research  # noqa: E402

DEFAULT_TRIGGER_SOURCE = "bdr_oz_dev_smoke"
DEFAULT_SOURCE_SYSTEM = "oz_dev_smoke"
DEFAULT_HUBSPOT_WORKFLOW_ID = "0000000000"
SKILL_PATHS = (
    Path("skills/company-research/SKILL.md"),
    Path("skills/outreach-composer/SKILL.md"),
)
SAFE_TEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,80}$")


@dataclass
class SmokeCheck:
    name: str
    status: str
    details: dict[str, Any]


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _check_result(name: str, func: Callable[[], dict[str, Any]]) -> SmokeCheck:
    try:
        return SmokeCheck(name=name, status="passed", details=func())
    except Exception as exc:  # pragma: no cover - exercised by real smoke failures.
        return SmokeCheck(
            name=name,
            status="failed",
            details={
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback": traceback.format_exc(limit=3),
            },
        )


def validate_test_id(test_id: str | None, *, required_for: str) -> str:
    if not test_id:
        raise ValueError(f"--test-id is required for {required_for}")
    if not SAFE_TEST_ID_RE.fullmatch(test_id):
        raise ValueError(
            "--test-id must be 3-81 chars and contain only letters, digits, '_', '.', or '-'"
        )
    return test_id

def validate_step2_payload_args(args: argparse.Namespace, *, required_for: str) -> None:
    if not args.lead_id:
        raise ValueError(f"--lead-id required for {required_for}")


def _load_skill_frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"{path} is missing YAML frontmatter")
    try:
        end_index = lines[1:].index("---") + 1
    except ValueError as exc:
        raise ValueError(f"{path} is missing closing YAML frontmatter marker") from exc

    frontmatter: dict[str, str] = {}
    for line in lines[1:end_index]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        frontmatter[key.strip()] = value.strip()
    return frontmatter


def check_skill_load_paths(repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    skills = []
    for relative_path in SKILL_PATHS:
        path = repo_root / relative_path
        if not path.exists():
            raise FileNotFoundError(f"Missing skill file: {relative_path}")
        frontmatter = _load_skill_frontmatter(path)
        missing = [key for key in ("name", "description") if not frontmatter.get(key)]
        if missing:
            raise ValueError(f"{relative_path} missing frontmatter keys: {missing}")
        skills.append(
            {
                "path": str(relative_path),
                "name": frontmatter["name"],
                "description_present": bool(frontmatter["description"]),
            }
        )
    return {"skill_count": len(skills), "skills": skills}


def check_dependency_imports() -> dict[str, Any]:
    imports = {
        "google.cloud.bigquery": False,
        "google.cloud.storage": False,
        "httpx": False,
    }
    for module_name in imports:
        __import__(module_name)
        imports[module_name] = True
    return {"imports": imports}


def check_exa_key_boolean() -> dict[str, Any]:
    return {
        "env_var": EXA_API_KEY_ENV_VAR,
        "available": bool(os.getenv(EXA_API_KEY_ENV_VAR)),
        "value_printed": False,
    }


def check_bigquery_read_auth(project_id: str = GCP_PROJECT_ID) -> dict[str, Any]:
    from google.cloud import bigquery

    client = bigquery.Client(project=project_id)
    rows = list(client.query("select 1 as ok").result(max_results=1))
    if not rows or rows[0]["ok"] != 1:
        raise RuntimeError("BigQuery read-auth query did not return expected row")
    return {"project_id": project_id, "query": "select 1 as ok", "row_count": len(rows)}


def check_gcs_write_read_permissions(*, test_id: str, project_id: str = GCP_PROJECT_ID) -> dict[str, Any]:
    from google.cloud import storage

    safe_test_id = validate_test_id(test_id, required_for="GCS write/read checks")
    object_name = f"{GCS_ARTIFACT_PREFIX}/smoke/{safe_test_id}/gcs_write_read_check.json"
    payload = {
        "check": "gcs_write_read_permissions",
        "created_at": _utc_now_iso(),
        "environment_id": BDR_AGENT_OZ_DEV_ENVIRONMENT_ID,
        "test_id": safe_test_id,
    }

    client = storage.Client(project=project_id)
    bucket = client.bucket(GCS_ARTIFACT_BUCKET)
    blob = bucket.blob(object_name)
    blob.upload_from_string(json.dumps(payload, sort_keys=True), content_type="application/json")
    downloaded = json.loads(blob.download_as_text())
    if downloaded != payload:
        raise RuntimeError("Downloaded GCS smoke payload did not match uploaded payload")
    return {
        "bucket": GCS_ARTIFACT_BUCKET,
        "object_name": object_name,
        "gcs_uri": f"gs://{GCS_ARTIFACT_BUCKET}/{object_name}",
        "write_performed": True,
        "read_performed": True,
    }


def run_step2_smoke(
    *,
    lead_id: str,
    contact_id: str,
    company_id: str,
    company_domain: str | None,
    company_website: str | None,
    company_alternative_domain: str | None,
    contact_email: str | None,
    company_name: str | None,
    trigger_source: str,
    source_system: str,
    hubspot_workflow_id: str,
    persist: bool,
    allow_live_exa: bool,
) -> dict[str, Any]:
    webhook_payload = {
        "lead_id": lead_id,
        "contact_id": contact_id,
        "company_id": company_id,
        "company_domain": company_domain,
        "company_website": company_website,
        "company_alternative_domain": company_alternative_domain,
        "contact_email": contact_email,
        "company_name": company_name,
    }
    result = run_company_research(
        lead_id=lead_id,
        trigger_source=trigger_source,
        source_system=source_system,
        hubspot_workflow_id=hubspot_workflow_id,
        webhook_payload={key: value for key, value in webhook_payload.items() if value is not None},
        dry_run=not persist,
        persist=persist,
        skip_tier_2_public_research=not allow_live_exa,
        send_stage_completion_on_success=False,
    )
    return {
        "status": result["status"],
        "lead_id": result["lead_id"],
        "run_id": result["run_id"],
        "output_id": result["output_id"],
        "dry_run": result["dry_run"],
        "storage": result["output"].get("storage"),
        "stage_completion": result.get("stage_completion"),
        "stage_completion_disabled_equivalent_cli_flag": "--skip-stage-completion",
        "failure_reason": result.get("failure_reason"),
        "tier_2_public_research_status": result["output"]
        .get("tier_2_public_company_research", {})
        .get("status"),
        "live_exa_allowed": allow_live_exa,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run safe BDR Agent Oz Dev smoke checks. Defaults are read-only; writes require "
            "explicit allow flags and --test-id."
        )
    )
    parser.add_argument("--environment-id", default=BDR_AGENT_OZ_DEV_ENVIRONMENT_ID)
    parser.add_argument("--lead-id", help="Lead ID for Step 2 dry-run or persist checks.")
    parser.add_argument("--contact-id", help="Associated Contact ID for Step 2 smoke checks.")
    parser.add_argument("--company-id", help="Associated Company ID for Step 2 smoke checks.")
    parser.add_argument("--company-domain", help="Associated Company domain for Step 2 smoke checks.")
    parser.add_argument("--company-website", help="Associated Company website for Step 2 smoke checks.")
    parser.add_argument("--company-alternative-domain", help="Associated Company alternative domain for Step 2 smoke checks.")
    parser.add_argument("--contact-email", help="Associated Contact email for Step 2 smoke checks.")
    parser.add_argument("--company-name", help="Associated Company name for Step 2 smoke checks.")
    parser.add_argument("--trigger-source", default=DEFAULT_TRIGGER_SOURCE)
    parser.add_argument("--source-system", default=DEFAULT_SOURCE_SYSTEM)
    parser.add_argument("--hubspot-workflow-id", default=DEFAULT_HUBSPOT_WORKFLOW_ID)
    parser.add_argument("--test-id", help="Required for checks that write GCS/BigQuery artifacts.")
    parser.add_argument("--skip-dependency-imports", action="store_true")
    parser.add_argument("--skip-bigquery-read", action="store_true")
    parser.add_argument("--skip-exa-key-check", action="store_true")
    parser.add_argument("--skip-step2-dry-run", action="store_true")
    parser.add_argument(
        "--allow-live-exa",
        action="store_true",
        help="Allow Step 2 smoke runs to call Exa if hydration requires fresh Tier 2 research.",
    )
    parser.add_argument(
        "--allow-gcs-write-read",
        action="store_true",
        help="Write and read a smoke object under gs://example-artifacts-bucket/bdr-agent/smoke/<test-id>/.",
    )
    parser.add_argument(
        "--allow-step2-persist",
        action="store_true",
        help=(
            "Run Step 2 with persistence enabled and stage completion disabled. Requires "
            "--lead-id and --test-id. This is equivalent to the Step 2 CLI's "
            "--skip-stage-completion safety flag."
        ),
    )
    return parser


def run_smoke(args: argparse.Namespace) -> dict[str, Any]:
    if args.environment_id != BDR_AGENT_OZ_DEV_ENVIRONMENT_ID:
        raise ValueError(
            f"Expected Oz Dev environment {BDR_AGENT_OZ_DEV_ENVIRONMENT_ID}, got {args.environment_id}"
        )
    if args.allow_gcs_write_read:
        validate_test_id(args.test_id, required_for="GCS write/read checks")
    if args.allow_step2_persist:
        validate_test_id(args.test_id, required_for="Step 2 persistence smoke")
    if args.lead_id and not args.skip_step2_dry_run:
        validate_step2_payload_args(args, required_for="Step 2 dry-run smoke")
    if args.allow_step2_persist:
        validate_step2_payload_args(args, required_for="Step 2 persistence smoke")

    checks: list[SmokeCheck] = []
    checks.append(_check_result("skill_load_paths", check_skill_load_paths))
    if not args.skip_dependency_imports:
        checks.append(_check_result("dependency_imports", check_dependency_imports))
    if not args.skip_exa_key_check:
        checks.append(_check_result("exa_key_boolean", check_exa_key_boolean))
    if not args.skip_bigquery_read:
        checks.append(_check_result("bigquery_read_auth", check_bigquery_read_auth))
    if args.allow_gcs_write_read:
        checks.append(
            _check_result(
                "gcs_write_read_permissions",
                lambda: check_gcs_write_read_permissions(test_id=args.test_id),
            )
        )
    elif args.test_id:
        checks.append(
            SmokeCheck(
                name="gcs_write_read_permissions",
                status="skipped",
                details={"reason": "--allow-gcs-write-read not provided", "write_performed": False},
            )
        )

    if args.lead_id and not args.skip_step2_dry_run:
        checks.append(
            _check_result(
                "step2_dry_run",
                lambda: run_step2_smoke(
                    lead_id=args.lead_id,
                    contact_id=args.contact_id,
                    company_id=args.company_id,
                    company_domain=args.company_domain,
                    company_website=args.company_website,
                    company_alternative_domain=args.company_alternative_domain,
                    contact_email=args.contact_email,
                    company_name=args.company_name,
                    trigger_source=args.trigger_source,
                    source_system=args.source_system,
                    hubspot_workflow_id=args.hubspot_workflow_id,
                    persist=False,
                    allow_live_exa=args.allow_live_exa,
                ),
            )
        )
    else:
        checks.append(
            SmokeCheck(
                name="step2_dry_run",
                status="skipped",
                details={
                    "reason": "no --lead-id provided" if not args.lead_id else "--skip-step2-dry-run provided",
                    "write_performed": False,
                },
            )
        )

    if args.allow_step2_persist:
        persist_trigger = f"{args.trigger_source}:{args.test_id}"
        checks.append(
            _check_result(
                "step2_persist_skip_stage_completion",
                lambda: run_step2_smoke(
                    lead_id=args.lead_id,
                    contact_id=args.contact_id,
                    company_id=args.company_id,
                    company_domain=args.company_domain,
                    company_website=args.company_website,
                    company_alternative_domain=args.company_alternative_domain,
                    contact_email=args.contact_email,
                    company_name=args.company_name,
                    trigger_source=persist_trigger,
                    source_system=args.source_system,
                    hubspot_workflow_id=args.hubspot_workflow_id,
                    persist=True,
                    allow_live_exa=args.allow_live_exa,
                ),
            )
        )
    else:
        checks.append(
            SmokeCheck(
                name="step2_persist_skip_stage_completion",
                status="skipped",
                details={"reason": "--allow-step2-persist not provided", "write_performed": False},
            )
        )

    failed = [check.name for check in checks if check.status == "failed"]
    return {
        "status": "failed" if failed else "passed",
        "failed_checks": failed,
        "environment_id": args.environment_id,
        "gcp_project_id": GCP_PROJECT_ID,
        "artifact_prefix": f"gs://{GCS_ARTIFACT_BUCKET}/{GCS_ARTIFACT_PREFIX}/",
        "stage_completion_webhook_called": False,
        "hubspot_writes_performed": False,
        "secret_values_printed": False,
        "oz_run_link_reminder": (
            "If this smoke tooling is run by an Oz cloud agent, report the Oz run link in the handoff."
        ),
        "checks": [asdict(check) for check in checks],
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = run_smoke(args)
    json.dump(report, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
