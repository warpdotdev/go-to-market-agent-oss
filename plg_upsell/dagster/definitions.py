"""Dagster definitions for the PLG HubSpot sync."""

from dagster import Definitions, define_asset_job

from .assets import hubspot_plg_lead_sync, pqa_accounts, pql_champions

plg_hubspot_sync_job = define_asset_job(
    name="plg_hubspot_sync",
    selection=[pqa_accounts, pql_champions, hubspot_plg_lead_sync],
)

defs = Definitions(
    assets=[pqa_accounts, pql_champions, hubspot_plg_lead_sync],
    jobs=[plg_hubspot_sync_job],
)
