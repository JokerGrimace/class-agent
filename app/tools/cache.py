"""Tool result cache — stores full raw content for truncated tool outputs,
allowing expand_tool to retrieve specific sections without re-executing."""

from typing import Optional

_tool_results: dict[str, str] = {}


def put(tool_call_id: str, content: str) -> None:
    _tool_results[tool_call_id] = content


def get(tool_call_id: str, offset: int = 0, limit: int = 400) -> Optional[str]:
    content = _tool_results.get(tool_call_id)
    if content is None:
        return None
    return content[offset : offset + limit]


def clear_all() -> None:
    _tool_results.clear()
