from collections.abc import Iterable
from typing import Any, Optional

from app.core.tool.types import ToolDefinition, ToolGovernanceMetadata, ToolMetadata


TOOL_TYPE_TO_NAMES: dict[str, set[str]] = {
    "mcp": {"exec", "read_file", "write_file", "expand_tool"},
    "native": {"web_search", "web_fetch", "iclass_api"},
    "level1": {
        "web_search",
        "web_fetch",
        "iclass_api",
        "list_prepare_courses",
        "list_material_directory",
        "preview_material",
        "download_material",
        "rename_material",
        "move_material",
        "delete_material",
        "create_folder",
        "rename_folder",
        "move_folder",
        "copy_folder",
        "delete_folder",
        # "write_file",
        # "exec",
        # "read_file",
        "start_workflow_tool",
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
    ):
        tool_def = ToolDefinition(
            name=name,
            description=description,
            parameters=parameters,
            func=func,
            governance=ToolGovernanceMetadata(**(governance or {})),
        )
        self._tools[name] = tool_def

    def get(self, name: str) -> Optional[ToolDefinition]:
        return self._tools.get(name)

    def _filter_tools_by_type(self, tool_type: Optional[str]) -> list[ToolDefinition]:
        tools = list(self._tools.values())
        if not tool_type:
            return tools

        allowed_names = TOOL_TYPE_TO_NAMES.get(tool_type.lower())
        if not allowed_names:
            return []

        return [tool for tool in tools if tool.name in allowed_names]

    def filter_tools_by_list(self, tool_list: Iterable[str]) -> list[ToolDefinition]:
        tools = list(self._tools.values())
        return [tool for tool in tools if tool.name in tool_list]

    def get_tools(self, tool_type: Optional[str] = None) -> list[ToolDefinition]:
        return self._filter_tools_by_type(tool_type)

    def get_tools_schema(self, tool_type: Optional[str] = None) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in self._filter_tools_by_type(tool_type)
        ]

    def get_tools_metadata(self, tool_type: Optional[str] = None) -> list[ToolMetadata]:
        return [tool.metadata for tool in self._filter_tools_by_type(tool_type)]

    def clear(self) -> None:
        self._tools.clear()


def tool(
    name: str,
    description: str,
    parameters: dict[str, Any],
    governance: Optional[dict[str, Any]] = None,
):
    def decorator(func):
        registry.register(name, description, parameters, func, governance=governance)
        return func
    return decorator


registry = ToolRegistry()
