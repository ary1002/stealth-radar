"""
Capture one real API response per new endpoint and save to tests/fixtures/.
Run ONCE. All subsequent development uses MockCrustdataClient.

Estimated cost: ~8 credits (see breakdown below).
"""
import asyncio
import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from ingestion.client import CrustdataClient

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures")
LOG_PATH    = os.path.join(os.path.dirname(__file__), "..", "logs", "credits.log")

os.makedirs(FIXTURE_DIR, exist_ok=True)
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

THESIS_ID = "fixture-capture"
total_credits = 0.0


def save(name: str, data: dict):
    path = os.path.join(FIXTURE_DIR, f"{name}.json")
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"  saved {name}.json  ({len(json.dumps(data))} bytes)")


def log_credit(endpoint: str, credits: float):
    global total_credits
    total_credits += credits
    line = f"{datetime.utcnow().isoformat()}Z  {endpoint}  {credits:.2f}cr  {THESIS_ID}"
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")
    print(f"  [{credits:.2f} cr] {endpoint}   running total: {total_credits:.2f}")


async def main():
    client = CrustdataClient()
    try:
        # ── 1. person/search — recently_changed_jobs=true, 10 results (0.30 cr) ──
        print("\n[1] POST /person/search")
        filt = {
            "op": "and",
            "conditions": [
                {
                    "field": "experience.employment_details.past.company_professional_network_profile_url",
                    "type": "=",
                    "value": "https://www.linkedin.com/company/stripe",
                },
                {"field": "recently_changed_jobs", "type": "=", "value": True},
            ],
        }
        from ingestion.cohort import COHORT_FIELDS, SORTS
        results = []
        async for page in client.person_search(filters=filt, fields=COHORT_FIELDS, sorts=SORTS, limit=10):
            results = page
            break
        data = {"profiles": results, "total_count": len(results)}
        save("person_search", data)
        log_credit("/person/search", len(results) * 0.03)

        # ── 2. company/identify — FREE ─────────────────────────────────────────
        print("\n[2] POST /company/identify")
        resp = await client.company_identify(names=["Stealth AI"])
        save("company_identify", resp)
        log_credit("/company/identify", 0.0)

        # ── 3. company/enrich — 2 cr ───────────────────────────────────────────
        # Use Stripe's company_id (631394) — known from earlier work
        print("\n[3] POST /company/enrich")
        resp = await client.company_enrich(
            company_ids=[631394],
            fields=["basic_info", "headcount", "funding", "hiring", "web_traffic", "news"],
        )
        save("company_enrich", resp)
        log_credit("/company/enrich", 2.0)

        # ── 4. job/search — limit 5 (0.15 cr max) ─────────────────────────────
        print("\n[4] POST /job/search")
        resp = await client.job_search(
            filters={"field": "company.basic_info.company_id", "type": "=", "value": 631394},
            limit=5,
        )
        n_jobs = len(resp.get("jobs") or resp.get("data") or [])
        save("job_search", resp)
        log_credit("/job/search", n_jobs * 0.03)

        # ── 5. web/search/live — 1 cr ──────────────────────────────────────────
        print("\n[5] POST /web/search/live")
        resp = await client.web_search_live(
            query="Patrick Monaghan Hireline co-founder",
            sources=["web", "news", "social"],
        )
        save("web_search_live", resp)
        log_credit("/web/search/live", 1.0)

        # ── 6. person/enrich — base profile only, 1 cr ────────────────────────
        print("\n[6] POST /person/enrich")
        resp = await client.person_enrich(
            profile_urls=["https://www.linkedin.com/in/tejasmanohar"],
            fields=["basic_profile", "experience", "education", "professional_network"],
        )
        save("person_enrich", resp)
        log_credit("/person/enrich", 1.0)

        # ── 7. person/search/autocomplete — FREE ──────────────────────────────
        print("\n[7] POST /person/search/autocomplete")
        resp = await client.person_search_autocomplete(
            field="basic_profile.location.country",
            value="uni",
        )
        save("person_search_autocomplete", resp)
        log_credit("/person/search/autocomplete", 0.0)

        # ── 8. company/search/autocomplete — FREE ─────────────────────────────
        print("\n[8] POST /company/search/autocomplete")
        resp = await client.company_search_autocomplete(
            field="basic_info.industries",
            value="data",
        )
        save("company_search_autocomplete", resp)
        log_credit("/company/search/autocomplete", 0.0)

    finally:
        await client.close()

    print(f"\n✅ All fixtures captured. Total credits spent: {total_credits:.2f}")
    print(f"   Log: {LOG_PATH}")
    print(f"   Fixtures: {FIXTURE_DIR}")


if __name__ == "__main__":
    asyncio.run(main())
