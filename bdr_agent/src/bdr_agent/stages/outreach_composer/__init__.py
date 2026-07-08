"""Outreach composer stage for the BDR Agent."""

from bdr_agent.stages.outreach_composer.config import CANONICAL_STAGE, SCHEMA_VERSION, STAGE
from bdr_agent.stages.outreach_composer.run import run_lead_brief

run_outreach_composer = run_lead_brief

__all__ = [
    "CANONICAL_STAGE",
    "SCHEMA_VERSION",
    "STAGE",
    "run_lead_brief",
    "run_outreach_composer",
]
