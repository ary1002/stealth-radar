"""
MockCrustdataClient — identical interface to CrustdataClient,
reads responses from tests/fixtures/<endpoint>.json.

Swap is a one-line constructor change:
    client = CrustdataClient(api_key=key)   # live
    client = MockCrustdataClient()          # mock
"""
from __future__ import annotations

import json
import os
from typing import AsyncIterator

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def _load(name: str) -> dict:
    path = os.path.join(FIXTURE_DIR, f"{name}.json")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Fixture not found: {path}\n"
            f"Run scripts/capture_fixtures.py to generate it."
        )
    with open(path) as f:
        return json.load(f)


class MockCrustdataClient:
    """Drop-in replacement for CrustdataClient. No network calls, no credits."""

    async def close(self):
        pass

    async def person_search(self, filters, fields, sorts, limit=1000) -> AsyncIterator:
        data = _load("person_search")
        profiles = data.get("profiles") or data.get("results") or []
        yield profiles[:limit]

    async def person_enrich(self, profile_urls: list[str], fields=None) -> dict:
        return _load("person_enrich")

    async def company_enrich(self, company_ids: list[int], fields: list[str]) -> dict:
        return _load("company_enrich")

    async def company_identify(self, names=None, domains=None,
                               linkedin_urls=None, company_ids=None) -> dict:
        return _load("company_identify")

    async def job_search(self, filters: dict, limit: int = 10) -> dict:
        return _load("job_search")

    async def web_search_live(self, query: str, sources=None, start_date=None) -> dict:
        return _load("web_search_live")

    async def person_search_autocomplete(self, field: str, value: str) -> dict:
        return _load("person_search_autocomplete")

    async def company_search_autocomplete(self, field: str, value: str) -> dict:
        return _load("company_search_autocomplete")
