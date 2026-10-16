import importlib
import logging
import sys
from collections.abc import Iterable

from app.core.tool.registry import ToolRegistry, registry


logger = logging.getLogger(__name__)


PYTHON_TOOL_MODULE_BY_NAME: dict[str, str] = {
    # 当前展示页工具由页面目录控制可见性，模块映射用于按需加载。
    "read_current_display_content": "app.tools.current_display",
    "cancel_workflow_run": "app.tools.workflow_tools",
    "convert_upload_to_html_file": "app.tools.iclass_d_resource.prase_file",
    "create_workflow_run": "app.tools.workflow_tools",
    "dispatch_workflow_batch_and_wait": "app.tools.workflow_tools",
    "exec": "app.tools.builtin",
    "expand_tool": "app.tools.expand",
    "get_workflow_run": "app.tools.workflow_tools",
    # RAG 工具继续复用目标分支现有的按需加载机制，避免恢复旧版全局启动导入并影响其他工具。
    "rag_answer": "app.tools.rag_answer",
    "rag_search": "app.tools.rag_search",
    "rag_upload": "app.tools.rag_upload",
    "rewrite_query": "app.tools.rewrite_query",
    "parse_bytes_to_html": "app.tools.iclass_d_resource.prase_file",
    "read_cached_file_content": "app.tools.iclass_d_resource.prase_file",
    "read_file": "app.tools.builtin",
    "read_skill": "app.tools.skill_reader",
    "read_workflow_content": "app.tools.workflow_tools",
    "readonly_course_sql_query": "app.tools.readonly_course_sql",
    "render_dashboard_panel": "app.tools.readonly_course_sql",
    "render_query_result_chart": "app.tools.readonly_course_sql",
    "save_workflow_catalog": "app.tools.workflow_tools",
    "web_fetch": "app.tools.web_fetch",
    "web_search": "app.tools.web_search",
    "write_file": "app.tools.builtin",
}


def load_python_tools(
    tool_names: Iterable[str],
    *,
    tool_registry: ToolRegistry | None = None,
    module_by_tool_name: dict[str, str] | None = None,
) -> list[str]:
    target_registry = tool_registry or registry
    module_mapping = dict(PYTHON_TOOL_MODULE_BY_NAME)
    for tool_name, module_path in (module_by_tool_name or {}).items():
        normalized_tool_name = str(tool_name or "").strip()
        normalized_module_path = str(module_path or "").strip()
        if normalized_tool_name and normalized_module_path:
            module_mapping[normalized_tool_name] = normalized_module_path
    normalized_tool_names = sorted(
        {
            str(tool_name).strip()
            for tool_name in tool_names
            if str(tool_name).strip()
        }
    )
    module_names = sorted(
        {
            module_name
            for tool_name in normalized_tool_names
            if (module_name := module_mapping.get(tool_name))
        }
    )
    missing_mappings = [
        tool_name
        for tool_name in normalized_tool_names
        if tool_name not in module_mapping
    ]
    if missing_mappings:
        logger.warning(
            "Python tool catalog contains tool names without module mapping: %s",
            missing_mappings,
        )

    for module_name in module_names:
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            logger.warning("Failed to import Python tool module %s: %s", module_name, exc)
            continue

        module_tool_names = [
            tool_name
            for tool_name in normalized_tool_names
            if module_mapping.get(tool_name) == module_name
        ]
        if all(target_registry.get(tool_name) is not None for tool_name in module_tool_names):
            continue
        if module_name in sys.modules:
            importlib.reload(module)

    return [
        tool_name
        for tool_name in normalized_tool_names
        if target_registry.get(tool_name) is not None
    ]
