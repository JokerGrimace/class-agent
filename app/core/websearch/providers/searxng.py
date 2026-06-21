import httpx
from typing import Optional

from app.core.websearch.base import SearchResponse, SearchResult


class SearXNGSearchProvider:
    def __init__(self, base_url: str, timeout_seconds: int):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def search(
        self,
        query: str,
        *,
        count: int,
        domains: Optional[list[str]] = None,
        freshness: Optional[str] = None,
    ) -> SearchResponse:
        final_query = query
        if domains:
            final_query += " " + " ".join(f"site:{domain}" for domain in domains)

        params = {"q": final_query, "format": "json"}
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(f"{self.base_url}/search", params=params)
            response.raise_for_status()
            payload = response.json()

        items = payload.get("results", [])[:count]
        results = [
            SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=item.get("content", ""),
                source="searxng",
            )
            for item in items
            if item.get("url")
        ]
        return SearchResponse(provider="searxng", query=query, results=results)
