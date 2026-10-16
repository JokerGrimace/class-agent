from __future__ import annotations

from typing import Optional

from app.core.rag import get_rag_adapter
from app.core.tool.registry import tool
from app.core.tool.types import ToolResult


# 回答工具仍保留高级过滤参数，因此在自身模块维护校验规则，不再依赖已精简的检索 Tool 实现细节。
_VALID_SEARCH_MODES = {"local", "hybrid"}
_DEFAULT_SEARCH_MODE = "local"


# 高级回答参数中的列表必须来自 JSON 字符串数组，统一清理空白值后再交给 RAG 适配器。
def _normalize_string_list(value: object, *, field_name: str) -> tuple[list[str] | None, str | None]:
    if value is None:
        return None, None
    if not isinstance(value, list):
        return None, f"{field_name} must be a list of strings"

    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str):
            return None, f"{field_name} must be a list of strings"
        trimmed = item.strip()
        if trimmed:
            normalized.append(trimmed)
    return normalized, None


# 可选文本过滤值统一转成去除首尾空白的字符串，空值继续交由服务端默认策略处理。
def _normalize_optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return str(value).strip() or None
    return value.strip() or None


@tool(
    name="rag_answer",
    description="Search the configured knowledge base and return a grounded final answer with source metadata.",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The knowledge-base question to answer",
            },
            "database_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional knowledge-base names to scope the search",
            },
            "doc_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional document IDs to scope the search",
            },
            "search_mode": {
                "type": "string",
                "enum": ["local", "hybrid"],
                "description": "Whether to use local-only or hybrid retrieval",
            },
            "subject": {"type": "string"},
            "course": {"type": "string"},
            "teacher": {"type": "string"},
        },
        "required": ["query"],
    },
)
async def rag_answer(
    query: str,
    database_names: Optional[list[str]] = None,
    doc_ids: Optional[list[str]] = None,
    search_mode: Optional[str] = None,
    subject: Optional[str] = None,
    course: Optional[str] = None,
    teacher: Optional[str] = None,
    __run_state=None,
) -> ToolResult:
    normalized_query = " ".join((query or "").split()).strip()
    if not normalized_query:
        return ToolResult(success=False, error="query is required")

    if search_mode not in (None, *_VALID_SEARCH_MODES):
        return ToolResult(
            success=False,
            error="search_mode must be 'local' or 'hybrid'",
        )

    normalized_database_names, database_names_error = _normalize_string_list(
        database_names,
        field_name="database_names",
    )
    if database_names_error:
        return ToolResult(success=False, error=database_names_error)

    normalized_doc_ids, doc_ids_error = _normalize_string_list(
        doc_ids,
        field_name="doc_ids",
    )
    if doc_ids_error:
        return ToolResult(success=False, error=doc_ids_error)

    normalized_subject = _normalize_optional_text(subject)
    normalized_course = _normalize_optional_text(course)
    normalized_teacher = _normalize_optional_text(teacher)

    effective_search_mode = search_mode or _DEFAULT_SEARCH_MODE

    try:
        # 会话课程与模型可见 course 分离；学生的授权选择只采用执行器隐藏的当前会话课程。
        session_scope = {"course_code": __run_state.course_code} if __run_state is not None else {}
        result = await get_rag_adapter().answer_with_knowledge(
            query=normalized_query,
            database_names=normalized_database_names,
            doc_ids=normalized_doc_ids,
            search_mode=effective_search_mode,
            subject=normalized_subject,
            course=normalized_course,
            teacher=normalized_teacher,
            **session_scope,
        )
    except RuntimeError as exc:
        return ToolResult(success=False, error=f"{type(exc).__name__}: {exc}")

    return ToolResult(
        success=True,
        content=result["content"],
        meta={
            "answer_source": result["answer_source"],
            "knowledge_base_results": result["knowledge_base_results"],
            "online_results": result["online_results"],
            "combined_context": result["combined_context"],
            "citations": result["citations"],
            "applied_database_names": result.get("applied_database_names", []),
            "applied_doc_ids": result.get("applied_doc_ids", []),
            "applied_metadata_filters": result.get("applied_metadata_filters", {}),
            "used_query": result["used_query"],
        },
    )
