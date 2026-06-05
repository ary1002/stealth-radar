import asyncio
import httpx
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type

from config import CRUSTDATA_BASE, CRUSTDATA_API_KEY, API_VERSION, RATE_LIMIT_RPS


class CrustdataClient:
    def __init__(self, rps=RATE_LIMIT_RPS, api_key: str | None = None):
        _key = api_key or CRUSTDATA_API_KEY
        if not _key:
            raise ValueError("Crustdata API key is required. Enter it in the sidebar.")
        self._sem = asyncio.Semaphore(rps)
        self._http = httpx.AsyncClient(
            base_url=CRUSTDATA_BASE,
            headers={
                "Authorization": f"Bearer {_key}",
                "x-api-version": API_VERSION,
                "content-type": "application/json",
            },
            timeout=60.0,
        )

    async def close(self):
        await self._http.aclose()

    async def _post(self, path: str, payload: dict):
        @retry(
            wait=wait_exponential(min=2, max=30),
            stop=stop_after_attempt(5),
            retry=retry_if_exception_type(httpx.HTTPStatusError),
        )
        async def _call():
            async with self._sem:
                resp = await self._http.post(path, json=payload)
                resp.raise_for_status()
                return resp.json()

        return await _call()

    async def person_search(self, filters: dict, fields: str, sorts: list, limit: int = 1000):
        """Async generator that yields pages (lists of person dicts), following next_cursor pagination."""
        cursor = None
        while True:
            payload = {
                "filters": filters,
                "fields": fields,
                "sorts": sorts,
                "limit": limit,
            }
            if cursor is not None:
                payload["cursor"] = cursor

            data = await self._post("/person/search", payload)
            results = data.get("profiles") or data.get("results") or []
            yield results

            cursor = data.get("next_cursor")
            if not cursor:
                break

    async def _get(self, path: str, params: dict | None = None):
        @retry(
            wait=wait_exponential(min=2, max=30),
            stop=stop_after_attempt(5),
            retry=retry_if_exception_type(httpx.HTTPStatusError),
        )
        async def _call():
            async with self._sem:
                resp = await self._http.get(path, params=params)
                resp.raise_for_status()
                return resp.json()

        return await _call()

    async def person_enrich(self, profile_urls: list[str], fields: list[str] | None = None):
        """1–7 cr/record depending on add-ons. Omit fields for base profile (1 cr)."""
        payload: dict = {"professional_network_profile_urls": profile_urls}
        if fields:
            payload["fields"] = fields
        return await self._post("/person/enrich", payload)

    async def company_enrich(self, company_ids: list[int], fields: list[str]):
        return await self._post("/company/enrich", {"crustdata_company_ids": company_ids, "fields": fields})

    # ── v2 additions ───────────────────────────────────────────────────────────

    async def company_identify(
        self,
        names: list[str] | None = None,
        domains: list[str] | None = None,
        linkedin_urls: list[str] | None = None,
        company_ids: list[int] | None = None,
    ) -> dict:
        """FREE. Resolve company identifiers to Crustdata company_id."""
        payload: dict = {}
        if names:        payload["names"]                           = names
        if domains:      payload["domains"]                         = domains
        if linkedin_urls: payload["professional_network_urls"]      = linkedin_urls
        if company_ids:  payload["crustdata_company_ids"]           = company_ids
        return await self._post("/company/identify", payload)

    async def job_search(self, filters: dict, limit: int = 10) -> dict:
        """0.03 cr/result. Use limit=0 for a zero-cost count aggregation."""
        return await self._post("/job/search", {"filters": filters, "limit": limit})

    async def web_search_live(
        self,
        query: str,
        sources: list[str] | None = None,
        start_date: int | None = None,   # unix seconds
    ) -> dict:
        """1 cr/query. Response timestamps are in milliseconds."""
        payload: dict = {"query": query}
        if sources:    payload["sources"]     = sources
        if start_date: payload["start_date"]  = start_date
        return await self._post("/web/search/live", payload)

    async def person_search_autocomplete(self, field: str, query: str) -> dict:
        """FREE. Snap valid values for a person-search filter field."""
        return await self._post(
            "/person/search/autocomplete",
            {"field": field, "query": query},
        )

    async def company_search_autocomplete(self, field: str, query: str) -> dict:
        """FREE. Snap valid values for a company-search filter field."""
        return await self._post(
            "/company/search/autocomplete",
            {"field": field, "query": query},
        )
