from __future__ import annotations

import logging
from typing import Any


logger = logging.getLogger(__name__)


# RAG 摄取依赖重型解析和向量库组件，后台任务真正执行时才创建工具实例。
def get_rag_tool():
    from app.core.rag.tool import RAGTool

    return RAGTool()


# 索引名称和向量后端属于 Python 部署配置，上传接口和前端都不允许覆盖。
def get_rag_upload_config() -> tuple[str, str]:
    from app.core.rag import config as rag_config

    return str(rag_config.vector_store_name), str(rag_config.vector_store_type)


# HTTP 接口已经返回 202 后才执行该函数，因此所有异常必须转换成日志和 False，不能形成未处理后台异常。
def import_persisted_upload_in_background(
    *,
    file_id: str,
    metadata: dict[str, Any],
) -> bool:
    try:
        # 后台任务只允许读取上传接口刚写入的安全 ID，目标库继续使用服务端固定配置。
        normalized_file_id = str(file_id or "").strip()
        if not normalized_file_id:
            raise ValueError("file_id must not be blank")
        database_name, vector_store_type = get_rag_upload_config()
        normalized_database_name = str(database_name or "").strip()
        normalized_vector_store_type = str(vector_store_type or "").strip().lower()
        if not normalized_database_name:
            raise ValueError("RAG vector_store_name is not configured")
        if normalized_vector_store_type not in {"elastic", "faiss", "chroma"}:
            raise ValueError(
                "RAG vector_store_type must be 'elastic', 'faiss', or 'chroma'"
            )

        result = get_rag_tool().import_persisted_upload(
            file_id=normalized_file_id,
            database_name=normalized_database_name,
            vector_store_type=normalized_vector_store_type,
            metadata=dict(metadata or {}),
        )
        if result.result is False:
            raise RuntimeError("failed to process and store the uploaded file")

        logger.info(
            "Background RAG import completed. file_id=%s database_name=%s",
            normalized_file_id,
            normalized_database_name,
        )
        return True
    except Exception:
        logger.exception("Background RAG import failed. file_id=%s", file_id)
        return False
