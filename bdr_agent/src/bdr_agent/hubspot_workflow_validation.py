"""Read-only validation for the BDR Agent HubSpot workflow contract."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any


DEFAULT_WORKFLOW_ID = "0000000000"
DEFAULT_OBJECT_TYPE_ID = "0-136"
DEFAULT_FLOW_TYPE = "PLATFORM_FLOW"
REQUIRED_BODY_KEY = "lead_id"
REQUIRED_PROPERTY_NAME = "hs_object_id"
REQUIRED_BODY_KEYS = ("lead_id",)
REQUIRED_DOMAIN_BODY_KEYS = (
    "company_domain",
    "company_website",
    "company_alternative_domain",
)
RECOMMENDED_BODY_KEYS = (
    "contact_id",
    "company_id",
    "company_domain",
    "company_website",
    "company_alternative_domain",
    "lead_owner_id",
    "lead_created_at",
    "contact_job_title",
    "lead_source_detailed",
    "contact_first_name",
    "contact_last_name",
    "contact_email",
)

BODY_FIELD_NAMES = {
    "body",
    "bodytemplate",
    "custombody",
    "customrequestbody",
    "jsonbody",
    "payload",
    "requestbody",
    "requestbodytemplate",
    "webhookbody",
}

TOKEN_ENV_VARS = (
    "HUBSPOT_API_KEY",
    "HUBSPOT_PRIVATE_APP_TOKEN",
    "GENERAL_HUBSPOT_APP_TOKEN",
    "HUBSPOT_ACCESS_TOKEN",
)


def validate_bdr_workflow(
    workflow: dict[str, Any],
    *,
    workflow_id: str = DEFAULT_WORKFLOW_ID,
    object_type_id: str = DEFAULT_OBJECT_TYPE_ID,
) -> dict[str, Any]:
    """Return a sanitized read-only validation report for the BDR workflow."""

    checks: list[dict[str, Any]] = []

    def add_check(name: str, passed: bool, message: str, severity: str = "error") -> None:
        checks.append(
            {
                "name": name,
                "passed": passed,
                "severity": severity,
                "message": message,
            }
        )

    workflow_id_value = workflow.get("id")
    add_check(
        "workflow_id",
        str(workflow_id_value) == str(workflow_id),
        f"Workflow id should be {workflow_id}.",
    )
    add_check(
        "workflow_disabled",
        workflow.get("isEnabled") is False,
        "Workflow must remain disabled for this validation.",
    )
    add_check(
        "lead_object_type",
        workflow.get("objectTypeId") == object_type_id,
        f"Workflow objectTypeId should be Lead ({object_type_id}).",
    )
    add_check(
        "platform_flow_type",
        workflow.get("type") == DEFAULT_FLOW_TYPE,
        f"Workflow type should be {DEFAULT_FLOW_TYPE}.",
        severity="warning",
    )

    webhook_actions = [_summarize_webhook_action(path, action) for path, action in find_webhook_actions(workflow)]
    add_check(
        "webhook_action_present",
        bool(webhook_actions),
        "Workflow should contain at least one native webhook action.",
    )
    add_check(
        "post_webhook_action_present",
        any(action["method"] == "POST" for action in webhook_actions),
        "At least one webhook action should use POST.",
    )
    add_check(
        "lead_id_json_body_mapping",
        any(action["has_lead_id_body_mapping"] for action in webhook_actions),
        "Webhook POST body should include lead_id mapped from Lead hs_object_id.",
    )
    add_check(
        "required_json_body_keys",
        any(not action["missing_required_body_keys"] for action in webhook_actions),
        "Webhook POST body should include lead_id. Other fields are recommended and can fall back to BigQuery.",
    )
    add_check(
        "company_domain_json_body_key",
        any(action["has_company_domain_body_key"] for action in webhook_actions),
        "Webhook POST body should include at least one company domain source when available: company_domain, company_website, or company_alternative_domain.",
        severity="warning",
    )

    query_param_mapping = any(action["has_lead_id_query_param_mapping"] for action in webhook_actions)
    if query_param_mapping:
        add_check(
            "lead_id_query_param_mapping",
            True,
            "lead_id query-param mapping is present, but the production contract requires JSON body mapping.",
            severity="warning",
        )

    blocking_failures = [check for check in checks if check["severity"] == "error" and not check["passed"]]
    return {
        "workflow_id": str(workflow_id_value) if workflow_id_value is not None else None,
        "expected_workflow_id": str(workflow_id),
        "workflow_name": workflow.get("name"),
        "is_enabled": workflow.get("isEnabled"),
        "object_type_id": workflow.get("objectTypeId"),
        "expected_object_type_id": object_type_id,
        "hubspot_api_key_present": any(bool(os.environ.get(name)) for name in TOKEN_ENV_VARS),
        "valid": not blocking_failures,
        "checks": checks,
        "webhook_actions": webhook_actions,
    }


def find_webhook_actions(value: Any, path: str = "$") -> list[tuple[str, dict[str, Any]]]:
    """Find likely webhook action dictionaries inside a HubSpot workflow JSON document."""

    found: list[tuple[str, dict[str, Any]]] = []
    if isinstance(value, dict):
        if _looks_like_webhook_action(value):
            found.append((path, value))
            return found
        for key, child in value.items():
            found.extend(find_webhook_actions(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(find_webhook_actions(child, f"{path}[{index}]"))
    return found


def fetch_property_metadata(
    *,
    object_type_id: str = DEFAULT_OBJECT_TYPE_ID,
    property_name: str = REQUIRED_PROPERTY_NAME,
) -> dict[str, Any]:
    """Fetch minimal property metadata with a read-only HubSpot GET."""
    from hubspot_agent.client import hubspot_request

    metadata = hubspot_request("GET", f"/crm/v3/properties/{object_type_id}/{property_name}")
    return {
        "object_type_id": object_type_id,
        "property_name": property_name,
        "found": bool(metadata),
        "name": metadata.get("name") if isinstance(metadata, dict) else None,
        "label": metadata.get("label") if isinstance(metadata, dict) else None,
        "type": metadata.get("type") if isinstance(metadata, dict) else None,
        "hubspot_defined": metadata.get("hubspotDefined") if isinstance(metadata, dict) else None,
    }


def _looks_like_webhook_action(action: dict[str, Any]) -> bool:
    action_type = str(action.get("type", "")).upper()
    if action_type == "WEBHOOK":
        return True
    if action.get("webhookUrl") and action.get("method"):
        return True
    fields = action.get("fields")
    return isinstance(fields, dict) and bool(fields.get("webhookUrl") and fields.get("method"))


def _summarize_webhook_action(path: str, action: dict[str, Any]) -> dict[str, Any]:
    method = _first_present(action, ("method",), nested_key="fields")
    method = str(method).upper() if method is not None else None
    body_candidates = _body_candidates(action)
    return {
        "path": path,
        "method": method,
        "webhook_url_present": bool(_first_present(action, ("webhookUrl",), nested_key="fields")),
        "body_candidate_count": len(body_candidates),
        "has_lead_id_body_key": any(_contains_key(candidate, REQUIRED_BODY_KEY) for candidate in body_candidates),
        "has_lead_id_body_mapping": any(_contains_lead_id_mapping(candidate) for candidate in body_candidates),
        "missing_required_body_keys": [
            key
            for key in REQUIRED_BODY_KEYS
            if not any(_contains_key(candidate, key) for candidate in body_candidates)
        ],
        "has_company_domain_body_key": any(
            _contains_key(candidate, key)
            for key in REQUIRED_DOMAIN_BODY_KEYS
            for candidate in body_candidates
        ),
        "present_recommended_body_keys": [
            key
            for key in RECOMMENDED_BODY_KEYS
            if any(_contains_key(candidate, key) for candidate in body_candidates)
        ],
        "has_lead_id_query_param_mapping": _has_query_param_mapping(action),
    }


def _first_present(action: dict[str, Any], keys: tuple[str, ...], *, nested_key: str) -> Any:
    for key in keys:
        if key in action:
            return action[key]
    nested = action.get(nested_key)
    if isinstance(nested, dict):
        for key in keys:
            if key in nested:
                return nested[key]
    return None


def _body_candidates(action: dict[str, Any]) -> list[Any]:
    candidates: list[Any] = []
    _collect_body_candidates(action, candidates)
    return candidates


def _collect_body_candidates(value: Any, candidates: list[Any]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized_key = str(key).replace("_", "").replace("-", "").lower()
            if normalized_key in BODY_FIELD_NAMES or "body" in normalized_key:
                candidates.append(child)
            _collect_body_candidates(child, candidates)
    elif isinstance(value, list):
        for child in value:
            _collect_body_candidates(child, candidates)


def _contains_key(value: Any, target_key: str) -> bool:
    if isinstance(value, dict):
        return target_key in value or any(_contains_key(child, target_key) for child in value.values())
    if isinstance(value, list):
        return any(_contains_key(child, target_key) for child in value)
    if isinstance(value, str):
        return target_key in value
    return False


def _contains_lead_id_mapping(value: Any) -> bool:
    if isinstance(value, dict):
        if REQUIRED_BODY_KEY in value and _references_hs_object_id(value[REQUIRED_BODY_KEY]):
            return True
        return any(_contains_lead_id_mapping(child) for child in value.values())
    if isinstance(value, list):
        return any(_contains_lead_id_mapping(child) for child in value)
    if isinstance(value, str):
        return REQUIRED_BODY_KEY in value and REQUIRED_PROPERTY_NAME in value
    return False


def _has_query_param_mapping(action: dict[str, Any]) -> bool:
    query_params = action.get("queryParams")
    if query_params is None and isinstance(action.get("fields"), dict):
        query_params = action["fields"].get("queryParams")
    if not isinstance(query_params, list):
        return False
    for param in query_params:
        if not isinstance(param, dict):
            continue
        if param.get("name") == REQUIRED_BODY_KEY and _references_hs_object_id(param.get("value")):
            return True
    return False


def _references_hs_object_id(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("propertyName") == REQUIRED_PROPERTY_NAME:
            return True
        return any(_references_hs_object_id(child) for child in value.values())
    if isinstance(value, list):
        return any(_references_hs_object_id(child) for child in value)
    if isinstance(value, str):
        return REQUIRED_PROPERTY_NAME in value
    return False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only validation for HubSpot workflow 0000000000 BDR webhook body shape."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--workflow-json", help="Path to a saved HubSpot workflow JSON document.")
    source.add_argument(
        "--fetch-hubspot",
        action="store_true",
        help="Fetch the workflow with a read-only HubSpot GET using HUBSPOT_API_KEY or compatible token env var.",
    )
    parser.add_argument("--workflow-id", default=DEFAULT_WORKFLOW_ID)
    parser.add_argument("--object-type-id", default=DEFAULT_OBJECT_TYPE_ID)
    parser.add_argument(
        "--fetch-property-metadata",
        action="store_true",
        help="Also perform a read-only GET for Lead hs_object_id property metadata.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.workflow_json:
        with open(args.workflow_json) as workflow_file:
            workflow = json.load(workflow_file)
    else:
        from hubspot_agent.workflows import get_workflow
        workflow = get_workflow(args.workflow_id)

    report = validate_bdr_workflow(
        workflow,
        workflow_id=args.workflow_id,
        object_type_id=args.object_type_id,
    )
    if args.fetch_property_metadata:
        report["property_metadata"] = fetch_property_metadata(
            object_type_id=args.object_type_id,
            property_name=REQUIRED_PROPERTY_NAME,
        )

    json.dump(report, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
