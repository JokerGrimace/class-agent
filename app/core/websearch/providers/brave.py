import httpx
from typing import Optional

from app.core.websearch.base import SearchResponse, SearchResult


class BraveSearchProvider:
    def __init__(self, api_key: str, timeout_seconds: int):
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    async def search(
        self,
        query: str,
        *,
        count: int,
        domains: Optional[list[str]] = None,
        freshness: Optional[str] = None,
    ) -> SearchResponse:
        params: dict[str, str | int] = {"q": query, "count": count}
        if domains:
            params["site"] = ",".join(domains)
        if freshness:
            params["freshness"] = freshness

        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": self.api_key,
        }

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params=params,
                headers=headers,
            )
            response.raise_for_status()
            payload = response.json()

        items = payload.get("web", {}).get("results", [])
        results = [
            SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=item.get("description", ""),
                source="brave",
            )
            for item in items
            if item.get("url")
        ]
        return SearchResponse(provider="brave", query=query, results=results)
