from __future__ import annotations

import asyncio

from app.core.tool.registry import tool
from app.core.tool.types import ToolResult


# 问题重写依赖模型运行时，只有 Tool 真正被调用时才导入，避免启动注册阶段加载重型 RAG 依赖。
def get_query_rewriter():
    from app.core.rag.query_rewrite import _get_query_rewriter

    return _get_query_rewriter()


# 大模型只提交待优化的问题；会话历史属于 Agent 运行态，不要求模型把历史消息重新组装成 Tool 参数。
@tool(
    name="rewrite_query",
    description="Rewrite a question into a clear standalone query for knowledge-base retrieval.",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The question to rewrite for knowledge-base retrieval",
            }
        },
        "required": ["query"],
        "additionalProperties": False,
    },
)
async def rewrite_query(query: str) -> ToolResult:
    # 空问题无法形成独立检索语句，因此在调用模型前直接返回明确失败。
    normalized_query = " ".join((query or "").split()).strip()
    if not normalized_query:
        return ToolResult(success=False, error="query is required")

    try:
        # 当前核心重写器使用同步 LLM invoke，通过工作线程执行，并由核心层处理无历史时的重写策略。
        rewritten_query = await asyncio.to_thread(
            get_query_rewriter().rewrite,
            normalized_query,
        )
    except Exception as exc:
        return ToolResult(success=False, error=str(exc))

    # 核心层异常时通常回退原问题；包装层仍保证 content 始终是可供后续检索使用的文本。
    final_query = str(rewritten_query or normalized_query).strip() or normalized_query
    return ToolResult(
        success=True,
        content=final_query,
        meta={
            "original_query": normalized_query,
            "rewrite_query": final_query,
        },
    )
