"""Central constants for BDR company research."""

import os

SCHEMA_VERSION = "bdr_agent_company_research.v1"
STAGE = "company_research"
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT", "example-gcp-project")
BIGQUERY_DATASET = os.environ.get("BQ_DATASET", "gtm_agents")
RUNS_TABLE = "bdr_agent_runs"
OUTPUTS_TABLE = "bdr_agent_outputs"
COMPANY_RESEARCH_OUTPUTS_TABLE = "bdr_agent_company_research_outputs"
HOOKS_TABLE = "bdr_agent_hooks"
BDR_AGENT_OZ_DEV_ENVIRONMENT_ID = os.environ.get(
    "BDR_AGENT_OZ_DEV_ENVIRONMENT_ID", "example-oz-environment-id"
)
GCS_ARTIFACT_BUCKET = os.environ.get("GCS_ARTIFACT_BUCKET", "example-artifacts-bucket")
GCS_ARTIFACT_PREFIX = "bdr-agent"
GCS_ARTIFACT_URI_PREFIX = f"gs://{GCS_ARTIFACT_BUCKET}/{GCS_ARTIFACT_PREFIX}"
STAGE_COMPLETION_WEBHOOK_URL_ENV_VAR = "BDR_AGENT_STAGE_COMPLETION_WEBHOOK_URL"
STAGE_COMPLETION_WEBHOOK_SECRET_ENV_VAR = "BDR_AGENT_STAGE_COMPLETION_WEBHOOK_SECRET"
STAGE_COMPLETION_HEADER_NAME = "X-BDR-Agent-Stage-Completion-Token"

HYDRATION_HYDRATED = "hydrated"
HYDRATION_NOT_READY = "not_ready"
HYDRATION_MISSING_REQUIRED_COMPANY_CONTEXT = "missing_required_company_context"

TIER_2_STRATEGY = "exa_positioning_guided_company_owned_search_v1"
POSITIONING_TAXONOMY_VERSION = "product_positioning_research_v1"
TIER_2_FRESHNESS_DAYS = 14
EXA_API_KEY_ENV_VAR = "BDR_AGENT_EXA_API_KEY"
EXA_SEARCH_URL = "https://api.exa.ai/search"
DEFAULT_TIER_2_EXA_MAX_QUERIES = 2
DEFAULT_TIER_2_EXA_NUM_RESULTS = 3

VALID_HYDRATION_STATUSES = {
    HYDRATION_HYDRATED,
    HYDRATION_NOT_READY,
    HYDRATION_MISSING_REQUIRED_COMPANY_CONTEXT,
}

VALID_TIER_2_REUSE_STATUSES = {"found", "partial"}
COMPANY_RESEARCH_BIGQUERY_TABLES = (
    RUNS_TABLE,
    OUTPUTS_TABLE,
    COMPANY_RESEARCH_OUTPUTS_TABLE,
)
BDR_AGENT_BIGQUERY_TABLES = (
    RUNS_TABLE,
    OUTPUTS_TABLE,
    COMPANY_RESEARCH_OUTPUTS_TABLE,
    HOOKS_TABLE,
)


def bigquery_table_id(table_name: str) -> str:
    return f"{GCP_PROJECT_ID}.{BIGQUERY_DATASET}.{table_name}"
