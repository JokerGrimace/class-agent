from __future__ import annotations

from app.core.rag import get_rag_adapter
from app.core.tool.registry import tool
from app.core.tool.types import ToolResult


# 大模型只负责表达“查什么”；知识库范围、检索模式和元数据过滤由 RAG 服务端配置统一决定。
@tool(
    name="rag_search",
    description="Search the knowledge base for information relevant to the user's question.",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The question or keywords to search in the knowledge base",
            }
        },
        "required": ["query"],
        "additionalProperties": False,
    },
)
async def rag_search(query: str, __run_state=None) -> ToolResult:
    # 空字符串无法产生有效向量查询，进入 RAG 核心前先快速失败，避免无意义的模型和数据库调用。
    normalized_query = " ".join((query or "").split()).strip()
    if not normalized_query:
        return ToolResult(success=False, error="query is required")

    try:
        # 隐藏参数由通用执行器注入；课程只用于 Java 核验，不能由大模型选择别人的课程扩大权限。
        session_scope = {"course_code": __run_state.course_code} if __run_state is not None else {}
        result = await get_rag_adapter().search_knowledge(query=normalized_query, **session_scope)
    except Exception as exc:
        return ToolResult(success=False, error=str(exc))

    # content 提供给大模型继续回答，meta 保留引用和诊断信息供 Agent 引擎及前端使用。
    return ToolResult(
        success=True,
        content=result["content"],
        meta={
            "answer_source": result["answer_source"],
            "knowledge_base_results": result["knowledge_base_results"],
            "online_results": result["online_results"],
            "combined_context": result["combined_context"],
            "citations": result["citations"],
            "applied_database_names": result["applied_database_names"],
            "applied_doc_ids": result["applied_doc_ids"],
            "applied_metadata_filters": result["applied_metadata_filters"],
        },
    )
