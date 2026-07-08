#!/usr/bin/env python3
"""Enrich PLG-scored domains and champions missing from Apollo.

Reads the gap between plg_upsell_domain_scores / plg_upsell_domain_champions
and your_analytics_dataset.apollo_enriched_companies / apollo_enriched_people,
then calls the Apollo bulk enrichment API to fill the gap and writes results
back to BigQuery.

Usage:
    # Dry run — show what would be enriched, no API calls
    .venv/bin/python plg_upsell/scripts/enrich_missing_apollo.py --dry-run

    # Enrich companies only
    .venv/bin/python plg_upsell/scripts/enrich_missing_apollo.py --companies-only

    # Enrich people only
    .venv/bin/python plg_upsell/scripts/enrich_missing_apollo.py --people-only

    # Enrich both, limit to top 50 domains by PQL
    .venv/bin/python plg_upsell/scripts/enrich_missing_apollo.py --top-n 50

    # Full run (all missing)
    .venv/bin/python plg_upsell/scripts/enrich_missing_apollo.py

Requires:
    - APOLLO_API_ENRICHMENT_API_KEY env var (or fetched from GCP secrets)
    - BigQuery credentials (ADC or GCP_SERVICE_ACCOUNT_JSON)
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any
from urllib import error, request

from google.cloud import bigquery
from google.oauth2 import service_account


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PROJECT = os.environ.get("GCP_PROJECT", "example-gcp-project")
SCORING_DATASET = "analytics"
APOLLO_DATASET = "your_analytics_dataset"

APOLLO_API_BASE = "https://api.apollo.io/api/v1"
BULK_ORG_BATCH_SIZE = 10  # Apollo max per request
BULK_PEOPLE_BATCH_SIZE = 10
API_DELAY_SECONDS = 1.0  # polite rate-limiting between batches


# ---------------------------------------------------------------------------
# BigQuery helpers
# ---------------------------------------------------------------------------

def build_bq_client() -> bigquery.Client:
    sa_json = os.environ.get("GCP_SERVICE_ACCOUNT_JSON")
    if sa_json:
        creds = service_account.Credentials.from_service_account_info(json.loads(sa_json))
        return bigquery.Client(project=PROJECT, credentials=creds)
    return bigquery.Client(project=PROJECT)


def fetch_missing_domains(client: bigquery.Client, top_n: int | None) -> list[dict]:
    """Return scored domains not yet in apollo_enriched_companies."""
    top_clause = f"LIMIT {top_n}" if top_n else ""
    sql = f"""
    WITH scored AS (
        SELECT email_domain, company_name, pql_score
        FROM `{PROJECT}.{SCORING_DATASET}.plg_upsell_domain_scores`
        ORDER BY pql_score DESC
        {top_clause}
    ),
    already_enriched AS (
        SELECT DISTINCT domain
        FROM `{PROJECT}.{APOLLO_DATASET}.apollo_enriched_companies`
        WHERE domain IS NOT NULL
    )
    SELECT s.email_domain, s.company_name, s.pql_score
    FROM scored s
    LEFT JOIN already_enriched ae ON s.email_domain = ae.domain
    WHERE ae.domain IS NULL
    ORDER BY s.pql_score DESC
    """
    return [dict(row) for row in client.query(sql).result()]


def fetch_missing_champions(client: bigquery.Client, top_n: int | None) -> list[dict]:
    """Return rank-1 champions not yet in apollo_enriched_people."""
    top_clause = f"LIMIT {top_n}" if top_n else ""
    sql = f"""
    WITH scored_domains AS (
        SELECT email_domain, pql_score
        FROM `{PROJECT}.{SCORING_DATASET}.plg_upsell_domain_scores`
        ORDER BY pql_score DESC
        {top_clause}
    ),
    champions AS (
        SELECT c.email_domain, c.user_email, c.champion_score,
               sd.pql_score
        FROM `{PROJECT}.{SCORING_DATASET}.plg_upsell_domain_champions` c
        INNER JOIN scored_domains sd USING (email_domain)
        WHERE c.rank_in_domain = 1
    ),
    already_enriched AS (
        SELECT DISTINCT user_email
        FROM `{PROJECT}.{APOLLO_DATASET}.apollo_enriched_people`
        WHERE user_email IS NOT NULL
    )
    SELECT ch.email_domain, ch.user_email, ch.champion_score, ch.pql_score
    FROM champions ch
    LEFT JOIN already_enriched ae ON ch.user_email = ae.user_email
    WHERE ae.user_email IS NULL
    ORDER BY ch.pql_score DESC
    """
    return [dict(row) for row in client.query(sql).result()]


# ---------------------------------------------------------------------------
# Apollo API helpers
# ---------------------------------------------------------------------------

def apollo_request(api_key: str, endpoint: str, payload: dict) -> dict:
    """Make a POST request to the Apollo API."""
    url = f"{APOLLO_API_BASE}/{endpoint}"
    req = request.Request(
        url=url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
            "x-api-key": api_key,
        },
    )
    try:
        with request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Apollo API HTTP {exc.code}: {body}") from exc


def enrich_companies_batch(api_key: str, domains: list[str]) -> list[dict]:
    """Call bulk org enrichment for up to 10 domains. Returns org dicts."""
    resp = apollo_request(api_key, "organizations/bulk_enrich", {
        "domains": domains,
    })
    return resp.get("organizations") or []


def enrich_people_batch(api_key: str, emails: list[str]) -> list[dict]:
    """Call bulk people enrichment for up to 10 emails. Returns person dicts."""
    details = [{"email": e} for e in emails]
    resp = apollo_request(api_key, "people/bulk_match", {
        "details": details,
        "reveal_personal_emails": False,
        "reveal_phone_number": False,
    })
    matches = resp.get("matches") or []
    return [m for m in matches if m is not None]


# ---------------------------------------------------------------------------
# Transform Apollo responses → BigQuery row dicts
# ---------------------------------------------------------------------------

def _json_or_none(val: Any) -> str | None:
    """Serialize a value to JSON string for BigQuery JSON columns, or None."""
    if val is None:
        return None
    return json.dumps(val)


def org_to_bq_row(org: dict) -> dict:
    """Map an Apollo org response to the apollo_enriched_companies schema."""
    now = datetime.now(timezone.utc).isoformat()
    return {
        "domain": org.get("primary_domain") or org.get("domain"),
        "apollo_org_id": org.get("id"),
        "company_name": org.get("name"),
        "website_url": org.get("website_url"),
        "linkedin_url": org.get("linkedin_url"),
        "twitter_url": org.get("twitter_url"),
        "facebook_url": org.get("facebook_url"),
        "logo_url": org.get("logo_url"),
        "crunchbase_url": org.get("crunchbase_url"),
        "industry": org.get("industry"),
        "industries": _json_or_none(org.get("industries")),
        "keywords": _json_or_none(org.get("keywords")),
        "estimated_num_employees": org.get("estimated_num_employees"),
        "annual_revenue": org.get("annual_revenue"),
        "annual_revenue_printed": org.get("annual_revenue_printed"),
        "total_funding": org.get("total_funding"),
        "total_funding_printed": org.get("total_funding_printed"),
        "latest_funding_round_date": org.get("latest_funding_round_date"),
        "latest_funding_stage": org.get("latest_funding_stage"),
        "funding_events": _json_or_none(org.get("funding_events")),
        "departmental_head_count": _json_or_none(org.get("departmental_head_count")),
        "short_description": org.get("short_description"),
        "seo_description": org.get("seo_description"),
        "founded_year": org.get("founded_year"),
        "phone": org.get("phone"),
        "raw_address": org.get("raw_address"),
        "street_address": org.get("street_address"),
        "city": org.get("city"),
        "state": org.get("state"),
        "postal_code": org.get("postal_code"),
        "country": org.get("country"),
        "technology_names": _json_or_none(org.get("technology_names")),
        "publicly_traded_symbol": org.get("publicly_traded_symbol"),
        "publicly_traded_exchange": org.get("publicly_traded_exchange"),
        "alexa_ranking": org.get("alexa_ranking"),
        "sic_codes": _json_or_none(org.get("sic_codes")),
        "naics_codes": _json_or_none(org.get("naics_codes")),
        "enriched_at": now,
    }


def person_to_bq_row(person: dict, user_email: str) -> dict:
    """Map an Apollo person response to the apollo_enriched_people schema."""
    now = datetime.now(timezone.utc).isoformat()
    emp_history = person.get("employment_history") or []
    org = person.get("organization") or {}
    return {
        "user_id": None,  # we don't have a product user_id here; can be joined later
        "user_email": user_email,
        "apollo_person_id": person.get("id"),
        "first_name": person.get("first_name"),
        "last_name": person.get("last_name"),
        "name": person.get("name"),
        "title": person.get("title"),
        "headline": person.get("headline"),
        "seniority": person.get("seniority"),
        "linkedin_url": person.get("linkedin_url"),
        "twitter_url": person.get("twitter_url"),
        "github_url": person.get("github_url"),
        "photo_url": person.get("photo_url"),
        "city": person.get("city"),
        "state": person.get("state"),
        "country": person.get("country"),
        "email_status": person.get("email_status"),
        "departments": _json_or_none(person.get("departments")),
        "subdepartments": _json_or_none(person.get("subdepartments")),
        "functions": _json_or_none(person.get("functions")),
        "employment_history": _json_or_none(emp_history),
        "current_organization_name": org.get("name") or person.get("organization_name"),
        "current_organization_id": person.get("organization_id"),
        "num_past_jobs": len(emp_history),
        "enriched_at": now,
    }


# ---------------------------------------------------------------------------
# BigQuery write
# ---------------------------------------------------------------------------

def write_rows(client: bigquery.Client, table_id: str, rows: list[dict]) -> int:
    """Append rows to a BigQuery table. Returns number of rows written."""
    if not rows:
        return 0
    errors = client.insert_rows_json(table_id, rows)
    if errors:
        raise RuntimeError(f"BigQuery insert errors: {errors}")
    return len(rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Enrich missing PLG domains/champions via Apollo API")
    p.add_argument("--dry-run", action="store_true", help="Show what would be enriched; no API calls")
    p.add_argument("--companies-only", action="store_true", help="Only enrich companies")
    p.add_argument("--people-only", action="store_true", help="Only enrich people")
    p.add_argument("--top-n", type=int, default=None,
                   help="Limit to top N domains by PQL score (default: all)")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    # --- API key ---
    api_key = os.environ.get("APOLLO_API_ENRICHMENT_API_KEY")
    if not api_key and not args.dry_run:
        print("Error: APOLLO_API_ENRICHMENT_API_KEY not set.", file=sys.stderr)
        print("Fetch it with: export APOLLO_API_ENRICHMENT_API_KEY=$(gcloud "
              "--project=example-gcp-project secrets versions access 1 "
              "--secret=YOUR_APOLLO_ENRICHMENT_SECRET)", file=sys.stderr)
        return 1

    bq = build_bq_client()
    do_companies = not args.people_only
    do_people = not args.companies_only

    # ------------------------------------------------------------------
    # Companies
    # ------------------------------------------------------------------
    if do_companies:
        missing_domains = fetch_missing_domains(bq, args.top_n)
        print(f"\nCompanies: {len(missing_domains)} scored domains missing Apollo enrichment")

        if args.dry_run:
            for d in missing_domains[:20]:
                print(f"  {d['email_domain']:40s} PQL {float(d['pql_score']):5.1f}  {d['company_name']}")
            if len(missing_domains) > 20:
                print(f"  ... and {len(missing_domains) - 20} more")
        else:
            company_rows: list[dict] = []
            domains_list = [d["email_domain"] for d in missing_domains]
            total_batches = (len(domains_list) + BULK_ORG_BATCH_SIZE - 1) // BULK_ORG_BATCH_SIZE

            for i in range(0, len(domains_list), BULK_ORG_BATCH_SIZE):
                batch = domains_list[i : i + BULK_ORG_BATCH_SIZE]
                batch_num = i // BULK_ORG_BATCH_SIZE + 1
                print(f"  Enriching companies batch {batch_num}/{total_batches} "
                      f"({len(batch)} domains)...", end=" ", flush=True)

                try:
                    orgs = enrich_companies_batch(api_key, batch)
                    rows = [org_to_bq_row(o) for o in orgs]
                    company_rows.extend(rows)
                    print(f"got {len(orgs)} results")
                except Exception as exc:
                    print(f"ERROR: {exc}", file=sys.stderr)

                if i + BULK_ORG_BATCH_SIZE < len(domains_list):
                    time.sleep(API_DELAY_SECONDS)

            table_id = f"{PROJECT}.{APOLLO_DATASET}.apollo_enriched_companies"
            written = write_rows(bq, table_id, company_rows)
            print(f"  Wrote {written} company rows to {table_id}")

    # ------------------------------------------------------------------
    # People
    # ------------------------------------------------------------------
    if do_people:
        missing_champs = fetch_missing_champions(bq, args.top_n)
        print(f"\nChampions: {len(missing_champs)} rank-1 champions missing Apollo enrichment")

        if args.dry_run:
            for c in missing_champs[:20]:
                print(f"  {c['user_email']:45s} PQL {float(c['pql_score']):5.1f}  "
                      f"champ {float(c['champion_score']):5.1f}")
            if len(missing_champs) > 20:
                print(f"  ... and {len(missing_champs) - 20} more")
        else:
            people_rows: list[dict] = []
            emails_list = [c["user_email"] for c in missing_champs]
            total_batches = (len(emails_list) + BULK_PEOPLE_BATCH_SIZE - 1) // BULK_PEOPLE_BATCH_SIZE

            for i in range(0, len(emails_list), BULK_PEOPLE_BATCH_SIZE):
                batch = emails_list[i : i + BULK_PEOPLE_BATCH_SIZE]
                batch_num = i // BULK_PEOPLE_BATCH_SIZE + 1
                print(f"  Enriching people batch {batch_num}/{total_batches} "
                      f"({len(batch)} emails)...", end=" ", flush=True)

                try:
                    people = enrich_people_batch(api_key, batch)
                    rows = [person_to_bq_row(p, p.get("email") or batch[j])
                            for j, p in enumerate(people)]
                    people_rows.extend(rows)
                    print(f"got {len(people)} results")
                except Exception as exc:
                    print(f"ERROR: {exc}", file=sys.stderr)

                if i + BULK_PEOPLE_BATCH_SIZE < len(emails_list):
                    time.sleep(API_DELAY_SECONDS)

            table_id = f"{PROJECT}.{APOLLO_DATASET}.apollo_enriched_people"
            written = write_rows(bq, table_id, people_rows)
            print(f"  Wrote {written} people rows to {table_id}")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
