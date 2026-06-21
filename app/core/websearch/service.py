from typing import Optional

from app.config import settings
from app.core.websearch.base import SearchResponse
from app.core.websearch.providers.brave import BraveSearchProvider
from app.core.websearch.providers.jina import JinaSearchProvider
from app.core.websearch.providers.searxng import SearXNGSearchProvider

VALID_FRESHNESS = {"day", "week", "month"}


class WebSearchService:
    def __init__(
        self,
        *,
        provider_mode: Optional[str] = None,
        brave_provider=None,
        searxng_provider=None,
        jina_provider=None,
        brave_configured: Optional[bool] = None,
        searxng_configured: Optional[bool] = None,
        jina_configured: Optional[bool] = None,
        max_results: Optional[int] = None,
    ):
        self.provider_mode = provider_mode or settings.web_search_provider
        self.max_results = max_results or settings.web_search_max_results
        self.brave_configured = (
            bool(settings.brave_api_key) if brave_configured is None else brave_configured
        )
        self.searxng_configured = (
            bool(settings.searxng_base_url) if searxng_configured is None else searxng_configured
        )
        self.jina_configured = (
            bool(settings.jina_search_base_url) if jina_configured is None else jina_configured
        )
        self.brave_provider = brave_provider
        self.searxng_provider = searxng_provider
        self.jina_provider = jina_provider

        if self.brave_provider is None and self.brave_configured:
            self.brave_provider = BraveSearchProvider(
                api_key=settings.brave_api_key,
                timeout_seconds=settings.web_search_timeout_seconds,
            )
        if self.searxng_provider is None and self.searxng_configured:
            self.searxng_provider = SearXNGSearchProvider(
                base_url=settings.searxng_base_url,
                timeout_seconds=settings.web_search_timeout_seconds,
            )
        if self.jina_provider is None and self.jina_configured:
            self.jina_provider = JinaSearchProvider(
                base_url=settings.jina_search_base_url,
                timeout_seconds=settings.web_search_timeout_seconds,
                api_key=settings.jina_api_key,
            )

    def _resolve_provider(self):
        if self.provider_mode == "disabled":
            raise RuntimeError("Web search is disabled")
        if self.provider_mode == "brave":
            if not self.brave_provider:
                raise RuntimeError("Brave provider is not configured")
            return self.brave_provider
        if self.provider_mode == "searxng":
            if not self.searxng_provider:
                raise RuntimeError("SearXNG provider is not configured")
            return self.searxng_provider
        if self.provider_mode == "jina":
            if not self.jina_provider:
                raise RuntimeError("Jina provider is not configured")
            return self.jina_provider
        if self.brave_provider:
            return self.brave_provider
        if self.searxng_provider:
            return self.searxng_provider
        if self.jina_provider:
            return self.jina_provider
        raise RuntimeError(
            "No web search provider is configured. Set OPENCLAW_BRAVE_API_KEY, "
            "OPENCLAW_SEARXNG_BASE_URL, or use the default Jina endpoint."
        )

    async def search(
        self,
        query: str,
        *,
        count: int = 5,
        domains: Optional[list[str]] = None,
        freshness: Optional[str] = None,
    ) -> SearchResponse:
        query = " ".join(query.split())
        if not query:
            raise ValueError("Query cannot be empty")
        if freshness and freshness not in VALID_FRESHNESS:
            raise ValueError(f"Invalid freshness: {freshness}")

        clamped_count = max(1, min(count, self.max_results))
        provider = self._resolve_provider()
        return await provider.search(
            query,
            count=clamped_count,
            domains=domains,
            freshness=freshness,
        )
