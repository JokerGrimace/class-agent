from typing import Optional
from urllib.parse import quote

import httpx

from app.core.websearch.base import SearchResponse, SearchResult


class JinaSearchProvider:
    def __init__(self, base_url: str, timeout_seconds: int, api_key: str = ""):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.api_key = api_key

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
        if freshness:
            final_query += f" freshness:{freshness}"

        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        encoded_query = quote(final_query, safe="")
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(f"{self.base_url}/{encoded_query}", headers=headers)
            response.raise_for_status()
            payload = response.json()

        items = payload if isinstance(payload, list) else payload.get("data", [])
        results = [
            SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=item.get("content", ""),
                source="jina",
            )
            for item in items[:count]
            if item.get("url")
        ]
        return SearchResponse(provider="jina", query=query, results=results)
