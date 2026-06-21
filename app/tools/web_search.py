import logging
from typing import Optional

from app.core.tool.registry import tool
from app.core.tool.types import ToolResult
from app.core.websearch.service import WebSearchService

logger = logging.getLogger(__name__)

# 搜索不可用时把联网核验边界交还给 Agent，只允许基于已有资料继续，不构造搜索结果或引用。
_SEARCH_FALLBACK_NOTICE = (
    "联网搜索未取得可用结果。可基于已有课件或知识库继续回答，并明确说明未完成联网核验；"
    "需要最新信息时请用户提供资料或稍后重试，不得编造搜索结果或引用。"
)


def _collapse_whitespace(text: str) -> str:
    return " ".join(text.split())


def _truncate_text(text: str, limit: int = 280) -> str:
    text = _collapse_whitespace(text)
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _format_result_lines(idx: int, item, provider: str) -> list[str]:
    lines = [
        f"{idx}. {item.title}",
        f"URL: {item.url}",
    ]

    text = (item.snippet or "").strip()
    if not text:
        return lines + [""]

    lines.append(f"Snippet: {_truncate_text(text)}")
    lines.append("")
    return lines


@tool(
    name="web_search",
    description="Search the web and return compact results with citation metadata.",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "count": {"type": "integer", "description": "Maximum number of results", "default": 5},
            "domains": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional domain filters",
            },
            "freshness": {
                "type": "string",
                "description": "Optional freshness filter: day, week, or month",
            },
        },
        "required": ["query"],
    },
)
async def web_search(
    query: str,
    count: int = 5,
    domains: Optional[list[str]] = None,
    freshness: Optional[str] = None,
) -> ToolResult:
    try:
        service = WebSearchService()
        response = await service.search(query, count=count, domains=domains, freshness=freshness)
    except Exception as exc:
        # ConnectTimeout 等异常的消息可能为空，必须保留类型，避免执行器把真实故障记为 Unknown error。
        exception_type = type(exc).__name__
        detail = f"{exception_type}: {str(exc).strip()}".rstrip(": ")
        logger.warning("Web search failed: exception_type=%s", exception_type)
        return ToolResult(
            success=False,
            error=f"{detail}. {_SEARCH_FALLBACK_NOTICE}",
            meta={"exception_type": exception_type},
        )

    # HTTP 请求成功但没有可引用条目，也不能向 Agent 宣称搜索成功。
    if not response.results:
        return ToolResult(
            success=False,
            error=f"No search results. {_SEARCH_FALLBACK_NOTICE}",
            meta={"provider": response.provider, "results": [], "citations": []},
        )

    lines = [f"Search results for: {response.query}", ""]
    for idx, item in enumerate(response.results, start=1):
        lines.extend(_format_result_lines(idx, item, response.provider))
    content = "\n".join(lines).strip()
    return ToolResult(
        success=True,
        content=content,
        meta={
            "provider": response.provider,
            "query": response.query,
            "results": [item.to_citation() for item in response.results],
            "citations": response.citations,
            "applied_domains": domains or [],
            "applied_count": min(max(count, 1), len(response.results) or max(count, 1)),
        },
    )
