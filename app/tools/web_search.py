from typing import Optional

from app.core.tool.registry import tool
from app.core.tool.types import ToolResult
from app.core.websearch.service import WebSearchService


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

    if provider == "jina":
        lines.append(f"Content Preview: {_truncate_text(text)}")
    else:
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
        return ToolResult(success=False, error=str(exc))

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
