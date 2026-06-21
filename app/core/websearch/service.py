from typing import Optional

from app.config import settings
from app.core.websearch.base import SearchResponse
from app.core.websearch.providers.open_websearch import OpenWebSearchProvider

VALID_FRESHNESS = {"day", "week", "month"}


class WebSearchService:
    def __init__(
        self,
        *,
        provider_mode: Optional[str] = None,
        open_websearch_provider=None,
        open_websearch_configured: Optional[bool] = None,
        max_results: Optional[int] = None,
    ):
        self.provider_mode = provider_mode or settings.web_search_provider
        self.max_results = max_results or settings.web_search_max_results
        self.open_websearch_configured = (
            bool(settings.open_websearch_engines.strip())
            if open_websearch_configured is None
            else open_websearch_configured
        )
        self.open_websearch_provider = open_websearch_provider

        # 搜索服务只创建 open-webSearch；具体引擎顺序和失败接续由该 provider 负责。
        if self.open_websearch_provider is None and self.open_websearch_configured:
            self.open_websearch_provider = OpenWebSearchProvider(
                engines=settings.open_websearch_engines,
                timeout_seconds=settings.web_search_timeout_seconds,
            )

    def _resolve_provider(self):
        # 显式禁用和不再支持的模式必须直接拒绝，不能静默切换成其他配置语义。
        if self.provider_mode == "disabled":
            raise RuntimeError("Web search is disabled")
        if self.provider_mode not in {"auto", "open_websearch"}:
            raise ValueError(f"Unsupported web search provider: {self.provider_mode}")

        # auto 与显式模式共用唯一 provider，同时保留缺失配置时的明确诊断。
        if self.open_websearch_provider is not None:
            return self.open_websearch_provider
        if self.provider_mode == "open_websearch":
            raise RuntimeError("open-webSearch provider is not configured")
        raise RuntimeError(
            "No web search provider is configured. Set OPENCLAW_OPEN_WEBSEARCH_ENGINES."
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
        # 参数校验完成后只委托一次，直接保留引擎聚合错误与取消信号供上层处理。
        provider = self._resolve_provider()
        return await provider.search(
            query,
            count=clamped_count,
            domains=domains,
            freshness=freshness,
        )
