from collections.abc import Iterable
from typing import Any, Optional

from app.core.tool.types import (
    ToolDefinition,
    ToolGovernanceMetadata,
    ToolMetadata,
    ToolVisibilityMetadata,
)


TOOL_TYPE_TO_NAMES: dict[str, set[str]] = {
    "mcp": {"exec", "read_file", "write_file", "expand_tool"},
    "native": {"web_search", "web_fetch", "rag_search", "rag_answer"},
    "level1": {
        # 默认 send 请求必须向模型暴露问题重写与 RAG 检索，模型才能在标准对话循环中自主选择并串联调用。
        "rewrite_query",
        "rag_search",
        "web_search",
        "web_fetch",
        "read_workflow_content",
        "create_workflow_run",
        "dispatch_workflow_batch_and_wait",
        "get_workflow_run",
        "cancel_workflow_run",
        "read_course_material_content",
        "read_cached_file_content",
    },
}


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}

    def register(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        func,
        governance: Optional[dict[str, Any]] = None,
        visibility: Optional[dict[str, Any]] = None,
    ):
        tool_def = ToolDefinition(
            name=name,
            description=description,
            parameters=parameters,
            func=func,
            governance=ToolGovernanceMetadata(**(governance or {})),
            visibility=ToolVisibilityMetadata(**(visibility or {})),
        )
        self._tools[name] = tool_def

    def get(self, name: str) -> Optional[ToolDefinition]:
        return self._tools.get(name)

    @staticmethod
    def _matches_tool_type(tool: ToolDefinition, tool_type: Optional[str]) -> bool:
        if not tool_type:
            return True

        normalized_tool_type = tool_type.lower()
        if tool.visibility.tool_types:
            return normalized_tool_type in {
                value.lower()
                for value in tool.visibility.tool_types
                if isinstance(value, str)
            }

        allowed_names = TOOL_TYPE_TO_NAMES.get(normalized_tool_type)
        if not allowed_names:
            return False
        return tool.name in allowed_names

    @staticmethod
    def _matches_current_workflow_page(
        tool: ToolDefinition,
        current_workflow_page: Optional[str],
    ) -> bool:
        normalized_page = (current_workflow_page or "").strip()
        if tool.visibility.requires_page_match:
            if not normalized_page:
                return False
            if not tool.visibility.page_names:
                return False
            return normalized_page in tool.visibility.page_names

        if tool.visibility.page_names:
            return normalized_page in tool.visibility.page_names

        return True

    def _filter_tools(
        self,
        tool_type: Optional[str],
        current_workflow_page: Optional[str] = None,
    ) -> list[ToolDefinition]:
        return [
            tool
            for tool in self._tools.values()
            if self._matches_tool_type(tool, tool_type)
            and self._matches_current_workflow_page(tool, current_workflow_page)
        ]

    def filter_tools_by_list(self, tool_list: Iterable[str]) -> list[ToolDefinition]:
        return self.filter_tools_by_list_and_page(tool_list)

    def filter_tools_by_list_and_page(
        self,
        tool_list: Iterable[str],
        current_workflow_page: Optional[str] = None,
    ) -> list[ToolDefinition]:
        allowed_names = set(tool_list)
        tools = list(self._tools.values())
        return [
            tool
            for tool in tools
            if tool.name in allowed_names
            and self._matches_current_workflow_page(tool, current_workflow_page)
        ]

    def get_tools(
        self,
        tool_type: Optional[str] = None,
        current_workflow_page: Optional[str] = None,
    ) -> list[ToolDefinition]:
        return self._filter_tools(
            tool_type,
            current_workflow_page=current_workflow_page,
        )

    def get_tools_schema(
        self,
        tool_type: Optional[str] = None,
        current_workflow_page: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in self._filter_tools(
                tool_type,
                current_workflow_page=current_workflow_page,
            )
        ]

    def get_tools_metadata(
        self,
        tool_type: Optional[str] = None,
        current_workflow_page: Optional[str] = None,
    ) -> list[ToolMetadata]:
        return [
            tool.metadata
            for tool in self._filter_tools(
                tool_type,
                current_workflow_page=current_workflow_page,
            )
        ]

    def get_tools_schema_by_list(
        self,
        tool_list: Iterable[str],
        current_workflow_page: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in self.filter_tools_by_list_and_page(
                tool_list,
                current_workflow_page=current_workflow_page,
            )
        ]

    def clear(self) -> None:
        self._tools.clear()


def tool(
    name: str,
    description: str,
    parameters: dict[str, Any],
    governance: Optional[dict[str, Any]] = None,
    visibility: Optional[dict[str, Any]] = None,
):
    def decorator(func):
        registry.register(
            name,
            description,
            parameters,
            func,
            governance=governance,
            visibility=visibility,
        )
        return func
    return decorator


registry = ToolRegistry()
