from __future__ import annotations

import asyncio

from app.core.tool.registry import tool
from app.core.tool.types import ToolResult


# 文件摄取依赖文档解析、Embedding 和向量库后端，延迟创建可保证应用启动时只完成轻量 Tool 注册。
def get_rag_tool():
    from app.core.rag.tool import RAGTool

    return RAGTool()


# 目标知识库和向量后端属于部署配置，调用时再读取可避免把底层存储细节暴露为大模型参数。
def get_rag_upload_config() -> tuple[str, str]:
    from app.core.rag import config as rag_config

    return str(rag_config.vector_store_name), str(rag_config.vector_store_type)


# 上传接口负责接收二进制并生成 file_id；该 Tool 只消费可信 file_id，随后按服务端配置完成切分和入库。
@tool(
    name="rag_upload",
    description="Process an uploaded file, split its content, generate embeddings, and store the chunks in a vector database.",
    parameters={
        "type": "object",
        "properties": {
            "file_id": {
                "type": "string",
                "description": "The file ID returned by the upload API",
            }
        },
        "required": ["file_id"],
        "additionalProperties": False,
    },
    governance={
        "tool_tags": ["rag", "vector_store", "mutating"],
    },
)
async def rag_upload(file_id: str) -> ToolResult:
    # file_id 必须来自后续上传接口，空值不能进入服务器文件解析链路。
    normalized_file_id = str(file_id or "").strip()
    if not normalized_file_id:
        return ToolResult(success=False, error="file_id is required")

    # 入库目标统一从 RAG 配置读取，配置缺失时明确失败，不能由模型临时猜测数据库名称。
    database_name, vector_store_type = get_rag_upload_config()
    normalized_database_name = str(database_name or "").strip()
    if not normalized_database_name:
        return ToolResult(success=False, error="RAG vector_store_name is not configured")

    # 配置中的后端名称同样只接受核心明确支持的值，防止错误配置静默写入其他存储。
    normalized_vector_store_type = str(vector_store_type or "elastic").strip().lower()
    if normalized_vector_store_type not in {"elastic", "faiss", "chroma"}:
        return ToolResult(
            success=False,
            error="RAG vector_store_type must be 'elastic', 'faiss', or 'chroma'",
        )

    try:
        # 文件解析和向量写入是同步重计算，通过工作线程执行以保持 Agent 事件循环可响应。
        result = await asyncio.to_thread(
            get_rag_tool().import_persisted_upload,
            file_id=normalized_file_id,
            database_name=normalized_database_name,
            vector_store_type=normalized_vector_store_type,
            metadata={},
        )
    except Exception as exc:
        return ToolResult(success=False, error=str(exc))

    # 本地 filePath 只供摄取核心使用，Tool 结果仅暴露前端和模型需要的安全标识与目标库信息。
    file_metadata = dict(result.file_metadata or {})
    file_name = str(file_metadata.get("fileName") or "")
    return ToolResult(
        success=True,
        content=(
            f"Uploaded file {file_name or normalized_file_id} was processed and "
            f"stored in vector database {normalized_database_name}."
        ),
        meta={
            "file_id": normalized_file_id,
            "file_name": file_name,
            "database_name": normalized_database_name,
            "target_store": result.target_store,
        },
    )
