"""Resources for the Dagster PLG HubSpot lead sync."""

from __future__ import annotations
import json
import os

from typing import Any
from urllib import error, request

from google.cloud import bigquery

from hubspot_agent.client import hubspot_request

APOLLO_API_BASE = "https://api.apollo.io/api/v1"
APOLLO_API_KEY_ENV_NAMES = (
    "APOLLO_API_ENRICHMENT_API_KEY",
    "Apollo_API_Key",
    "APOLLO_API_KEY",
)


class BigQueryScoreReader:
    def __init__(
        self,
        project: str = os.environ.get("GCP_PROJECT", "example-gcp-project"),
        dataset: str = os.environ.get("BQ_DATASET", "analytics"),
    ) -> None:
        self.project = project
        self.dataset = dataset
        self.client = bigquery.Client(project=project)

    def fetch_latest_accounts(self, min_score: int = 25) -> list[dict[str, Any]]:
        scores_ref = f"`{self.project}.{self.dataset}.plg_upsell_domain_scores_daily`"
        sql = f"""
        SELECT
            email_domain,
            company_name,
            pql_score AS pqa_score,
            avg_wau AS pqa_avg_wau,
            total_credits_30d AS pqa_ai_credits_30d,
            wow_growth_pct AS pqa_wow_growth,
            users_hitting_limits AS pqa_users_hitting_limits_14d,
            reload_dollars AS pqa_reload_spend_14d,
            users_upgraded AS pqa_free_to_paid_30d,
            new_domain_members AS pqa_new_members_14d,
            is_eligible,
            ineligibility_reason
        FROM {scores_ref}
        WHERE scored_date = (SELECT MAX(scored_date) FROM {scores_ref})
          AND (pql_score >= {min_score} OR is_eligible = FALSE)
        ORDER BY is_eligible DESC, pql_score DESC, email_domain
        """
        return [dict(row) for row in self.client.query(sql).result()]

    def fetch_champions(self, domains: list[str], max_rank: int = 3) -> dict[str, list[dict[str, Any]]]:
        if not domains:
            return {}
        champs_ref = f"`{self.project}.{self.dataset}.plg_upsell_domain_champions`"
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ArrayQueryParameter("domains", "STRING", domains),
                bigquery.ScalarQueryParameter("max_rank", "INT64", max_rank),
            ]
        )
        sql = f"""
        SELECT
            email_domain,
            user_email,
            champion_score AS pql_score,
            rank_in_domain AS pql_champion_rank,
            is_team_admin AS pql_is_team_admin,
            credits_used_t30d AS pql_ai_credit_usage_30d,
            days_active_in_last_30 AS pql_activity_frequency,
            CASE WHEN limit_hit_count > 0 THEN TRUE ELSE FALSE END AS pql_hit_credit_limit_14d
        FROM {champs_ref}
        WHERE email_domain IN UNNEST(@domains)
          AND rank_in_domain <= @max_rank
        ORDER BY email_domain, rank_in_domain
        """
        result: dict[str, list[dict[str, Any]]] = {}
        for row in self.client.query(sql, job_config=job_config).result():
            item = dict(row)
            domain = item.pop("email_domain")
            result.setdefault(domain, []).append(item)
        return result


class HubSpotWriter:
    def _apollo_api_key(self) -> str | None:
        for name in APOLLO_API_KEY_ENV_NAMES:
            value = os.environ.get(name)
            if value:
                return value
        return None

    def enrich_company_with_apollo(self, domain: str | None) -> dict[str, Any] | None:
        """Return Apollo org enrichment for *domain*, or None if unavailable."""
        if not domain:
            return None
        api_key = self._apollo_api_key()
        if not api_key:
            return None
        payload = json.dumps({"domains": [domain]}).encode("utf-8")
        req = request.Request(
            url=f"{APOLLO_API_BASE}/organizations/bulk_enrich",
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Cache-Control": "no-cache",
                "x-api-key": api_key,
            },
        )
        try:
            with request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except error.HTTPError as exc:
            raise RuntimeError(
                f"Apollo company enrichment failed for {domain}: HTTP {exc.code} "
                f"{exc.read().decode('utf-8', errors='replace')[:500]}"
            ) from exc
        orgs = body.get("organizations") or []
        return orgs[0] if orgs else None
    def read_company_properties(self, company_id: str | None) -> dict[str, Any]:
        if not company_id:
            return {}
        result = hubspot_request(
            "GET",
            f"/crm/v3/objects/companies/{company_id}"
            "?properties=hubspot_owner_id,eng_count_bucket,number_of_engineers_clay,"
            "account_ownership_type,company_clay_enrichment_queue,ready_for_enrichment,"
            "clay_enrichment_status,clay_last_enriched_at,enriched_by_clay,pqa_enriched_at",
        )
        return result.get("properties") or {}

    def read_company_owner(self, company_id: str | None) -> str | None:
        return self.read_company_properties(company_id).get("hubspot_owner_id")

    def update_company_properties(
        self,
        company_id: str | None,
        properties: dict[str, str],
        dry_run: bool = False,
    ) -> None:
        if not company_id or dry_run:
            return
        hubspot_request(
            "PATCH",
            f"/crm/v3/objects/companies/{company_id}",
            {"properties": properties},
        )

    def assign_company_owner(self, company_id: str, owner_id: str, dry_run: bool = False) -> None:
        if dry_run:
            return
        hubspot_request(
            "PATCH",
            f"/crm/v3/objects/companies/{company_id}",
            {
                "properties": {
                    "hubspot_owner_id": owner_id,
                    "account_ownership_type": "Routed",
                }
            },
        )
    def search_company_by_domain(self, domain: str) -> str | None:
        result = hubspot_request("POST", "/crm/v3/objects/companies/search", {
            "filterGroups": [{"filters": [
                {"propertyName": "domain", "operator": "EQ", "value": domain}
            ]}],
            "properties": ["domain", "name"],
            "limit": 1,
        })
        hits = result.get("results", [])
        return hits[0]["id"] if hits else None

    def upsert_company(self, domain: str, properties: dict[str, str], dry_run: bool = False) -> str | None:
        company_id = self.search_company_by_domain(domain)
        if dry_run:
            return company_id
        if company_id:
            hubspot_request("PATCH", f"/crm/v3/objects/companies/{company_id}", {"properties": properties})
            return company_id
        created = hubspot_request("POST", "/crm/v3/objects/companies", {
            "properties": {"domain": domain, **properties}
        })
        return created["id"]

    def search_contact_by_email(self, email: str) -> dict[str, Any] | None:
        result = hubspot_request("POST", "/crm/v3/objects/contacts/search", {
            "filterGroups": [{"filters": [
                {"propertyName": "email", "operator": "EQ", "value": email}
            ]}],
            "properties": ["email", "hubspot_owner_id"],
            "limit": 1,
        })
        hits = result.get("results", [])
        return hits[0] if hits else None

    def upsert_contact(self, email: str, properties: dict[str, str], dry_run: bool = False) -> dict[str, str | None]:
        contact = self.search_contact_by_email(email)
        contact_id = contact["id"] if contact else None
        contact_owner_id = (contact.get("properties") or {}).get("hubspot_owner_id") if contact else None
        if dry_run:
            return {"contact_id": contact_id, "hubspot_owner_id": contact_owner_id}
        if contact_id:
            hubspot_request("PATCH", f"/crm/v3/objects/contacts/{contact_id}", {"properties": properties})
            return {"contact_id": contact_id, "hubspot_owner_id": contact_owner_id}
        created = hubspot_request("POST", "/crm/v3/objects/contacts", {
            "properties": {"email": email, **properties}
        })
        return {
            "contact_id": created["id"],
            "hubspot_owner_id": (created.get("properties") or {}).get("hubspot_owner_id"),
        }

    def associate_contact_to_company(
        self,
        contact_id: str | None,
        company_id: str | None,
        dry_run: bool = False,
    ) -> None:
        if not contact_id or not company_id or dry_run:
            return
        hubspot_request(
            "POST",
            "/crm/v4/associations/contact/company/batch/create",
            {
                "inputs": [{
                    "from": {"id": contact_id},
                    "to": {"id": company_id},
                    "types": [{
                        "associationCategory": "HUBSPOT_DEFINED",
                        "associationTypeId": 1,
                    }],
                }]
            },
        )

    def upsert_lead(
        self,
        object_type: str,
        domain: str,
        champion_email: str,
        properties: dict[str, str],
        dry_run: bool = False,
    ) -> str | None:
        result = hubspot_request("POST", f"/crm/v3/objects/{object_type}/search", {
            "filterGroups": [{"filters": [
                {"propertyName": "plg_email_domain", "operator": "EQ", "value": domain},
                {"propertyName": "plg_champion_email", "operator": "EQ", "value": champion_email},
            ]}],
            "properties": ["plg_email_domain", "plg_champion_email"],
            "limit": 1,
        })
        hits = result.get("results", [])
        lead_id = hits[0]["id"] if hits else None
        if dry_run:
            return lead_id
        if lead_id:
            hubspot_request("PATCH", f"/crm/v3/objects/{object_type}/{lead_id}", {"properties": properties})
            return lead_id
        created = hubspot_request("POST", f"/crm/v3/objects/{object_type}", {"properties": properties})
        return created["id"]
