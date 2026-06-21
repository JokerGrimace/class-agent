from typing import Optional

from app.core.tool.registry import tool
from app.core.tool.types import ToolResult
from app.core.websearch.fetch import fetch_web_page


@tool(
    name="web_fetch",
    description="Fetch a web page and return readable text with citation metadata.",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "HTTP or HTTPS URL to fetch"},
            "max_chars": {"type": "integer", "description": "Optional maximum output size"},
        },
        "required": ["url"],
    },
)
async def web_fetch(url: str, max_chars: Optional[int] = None) -> ToolResult:
    try:
        payload = await fetch_web_page(url, max_chars=max_chars)
    except Exception as exc:
        return ToolResult(success=False, error=str(exc))

    display_title = payload["title"] or payload["final_url"]
    content = f"Title: {display_title}\nURL: {payload['final_url']}\n\n{payload['content']}"
    return ToolResult(
        success=True,
        content=content,
        meta={
            "url": payload["url"],
            "final_url": payload["final_url"],
            "title": display_title,
            "citations": payload["citations"],
            "truncated": payload["truncated"],
        },
    )
