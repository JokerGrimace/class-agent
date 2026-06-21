"""expand_tool — retrieve the full original content of a previously
truncated tool result from the cache, without re-executing the tool."""

from app.core.tool.registry import tool
from app.core.tool.types import ToolResult
from app.tools import cache


@tool(
    name="expand_tool",
    description="Retrieve full original content of a previously truncated tool result from cache. Use when a tool returned 'truncated' and you need more detail from its output.",
    parameters={
        "type": "object",
        "properties": {
            "tool_call_id": {
                "type": "string",
                "description": "The tool call ID of the truncated result (shown in the truncation notice).",
            },
            "offset": {
                "type": "integer",
                "description": "Character offset to start reading from (0-based).",
                "default": 0,
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of characters to return.",
                "default": 400,
            },
        },
        "required": ["tool_call_id"],
    },
)
async def expand_tool(tool_call_id: str, offset: int = 0, limit: int = 400) -> ToolResult:
    content = cache.get(tool_call_id, offset, limit)
    if content is None:
        return ToolResult(success=False, error=f"Tool result not cached: {tool_call_id}. The cache may have been cleared or the tool_call_id is incorrect.")
    return ToolResult(success=True, content=content)
