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

    async def person_enrich(self, profile_urls: list[str]):
        return await self._post("/person/enrich", {"professional_network_profile_urls": profile_urls})

    async def company_enrich(self, company_ids: list[int], fields: list[str]):
        return await self._post("/company/enrich", {"crustdata_company_ids": company_ids, "fields": fields})
