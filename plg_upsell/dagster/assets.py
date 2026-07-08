"""Dagster assets for PLG PQA/PQL → HubSpot Lead sync."""

from __future__ import annotations

import time

from dagster import Config, asset

from .core import (
    LEAD_OBJECT_TYPE,
    build_company_apollo_enrichment_properties,
    build_company_enrichment_properties,
    build_company_properties,
    build_contact_properties,
    build_lead_properties,
    choose_lead_champions_by_domain,
    ae_owner_for_company_routing,
    company_enrichment_queued,
    company_enrichment_ready,
    lead_role_for_champion,
    pql_owner_for_crm_owner,
    sequence_intent_for_tier,
    should_request_company_enrichment,
    should_trigger_bdr_workflow,
)
from .resources import BigQueryScoreReader, HubSpotWriter


class PlgHubSpotSyncConfig(Config):
    dry_run: bool = True
    min_score: int = 25
    lead_object_type: str = LEAD_OBJECT_TYPE


@asset(group_name="plg_hubspot")
def pqa_accounts(config: PlgHubSpotSyncConfig) -> list[dict]:
    return BigQueryScoreReader().fetch_latest_accounts(min_score=config.min_score)


@asset(group_name="plg_hubspot")
def pql_champions(pqa_accounts: list[dict]) -> dict[str, list[dict]]:
    domains = [a["email_domain"] for a in pqa_accounts if a.get("is_eligible") is not False]
    return BigQueryScoreReader().fetch_champions(domains=domains)


@asset(group_name="plg_hubspot")
def hubspot_plg_lead_sync(
    config: PlgHubSpotSyncConfig,
    pqa_accounts: list[dict],
    pql_champions: dict[str, list[dict]],
) -> dict:
    now_ms = int(time.time() * 1000)
    run_id = str(now_ms)
    hubspot = HubSpotWriter()
    lead_champions_by_domain = choose_lead_champions_by_domain(pql_champions)
    summary = {
        "companies_processed": 0,
        "contacts_processed": 0,
        "leads_processed": 0,
        "admin_leads_processed": 0,
        "tier_1_leads": 0,
        "tier_2_leads": 0,
        "tier_2_held": 0,
        "company_enrichment_requested": 0,
        "company_enrichment_waiting": 0,
        "tier_3_marketing_touch": 0,
        "dry_run": config.dry_run,
    }

    for account in pqa_accounts:
        domain = account.get("email_domain")
        if not domain or account.get("is_eligible") is False:
            continue

        company_props = build_company_properties(account, now_ms)
        company_id = hubspot.upsert_company(domain, company_props, dry_run=config.dry_run)
        hubspot_company_props = hubspot.read_company_properties(company_id)
        company_owner_id = hubspot_company_props.get("hubspot_owner_id")
        summary["companies_processed"] += 1

        tier = company_props["pqa_tier"]
        if tier == "tier_1" and not company_enrichment_ready(hubspot_company_props):
            apollo_enriched = False
            apollo_org = hubspot.enrich_company_with_apollo(domain)
            if apollo_org:
                apollo_props = build_company_apollo_enrichment_properties(apollo_org, now_ms)
                if "number_of_engineers_clay" in apollo_props:
                    hubspot.update_company_properties(
                        company_id,
                        apollo_props,
                        dry_run=config.dry_run,
                    )
                    hubspot_company_props = {**hubspot_company_props, **apollo_props}
                    summary["company_enrichment_requested"] += 1
                    apollo_enriched = True
                else:
                    apollo_org = None
            if apollo_enriched:
                pass
            elif should_request_company_enrichment(tier, hubspot_company_props):
                hubspot.update_company_properties(
                    company_id,
                    build_company_enrichment_properties(now_ms),
                    dry_run=config.dry_run,
                )
                summary["company_enrichment_requested"] += 1
                continue
            elif company_enrichment_queued(hubspot_company_props):
                summary["company_enrichment_waiting"] += 1
                continue
            else:
                summary["company_enrichment_waiting"] += 1
                continue

        if tier == "tier_1" and company_id and not company_owner_id:
            company_owner_id = ae_owner_for_company_routing(hubspot_company_props)
            hubspot.assign_company_owner(company_id, company_owner_id, dry_run=config.dry_run)
        lead_champions = lead_champions_by_domain.get(domain, [])
        if not lead_champions:
            if tier == "tier_3":
                summary["tier_3_marketing_touch"] += 1
            continue

        primary_champion = lead_champions[0]
        for champion in lead_champions:
            email = champion.get("user_email")
            if not email:
                continue

            contact_props = build_contact_properties(
                champion,
                now_ms,
                request_enrichment=should_trigger_bdr_workflow(tier),
                sequence_intent=sequence_intent_for_tier(tier),
                run_id=run_id,
            )
            contact_result = hubspot.upsert_contact(email, contact_props, dry_run=config.dry_run)
            contact_id = contact_result.get("contact_id")
            contact_owner_id = contact_result.get("hubspot_owner_id")
            lead_owner_id = pql_owner_for_crm_owner(contact_owner_id) or pql_owner_for_crm_owner(company_owner_id)
            hubspot.associate_contact_to_company(contact_id, company_id, dry_run=config.dry_run)
            summary["contacts_processed"] += 1

            lead_role = lead_role_for_champion(champion, primary=primary_champion)
            lead_props = build_lead_properties(
                account,
                champion,
                company_id=company_id,
                contact_id=contact_id,
                now_ms=now_ms,
                run_id=run_id,
                lead_role=lead_role,
                hubspot_owner_id=lead_owner_id,
            )
            if tier == "tier_1":
                hubspot.upsert_lead(
                    config.lead_object_type,
                    domain,
                    str(email or ""),
                    lead_props,
                    dry_run=config.dry_run,
                )
                summary["leads_processed"] += 1
                if lead_role in {"admin_champion", "primary_admin_champion"}:
                    summary["admin_leads_processed"] += 1
                summary["tier_1_leads"] += 1
            elif tier == "tier_2":
                summary["tier_2_held"] += 1
            else:
                summary["tier_3_marketing_touch"] += 1

    return summary
