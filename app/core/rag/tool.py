"""Public facade for using RAG ingestion and retrieval as one tool."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .upload import (
    DEFAULT_UPLOAD_DIR,
    persist_uploaded_file,
    validate_rag_upload_file_type,
)


@dataclass
class DatabaseRequest:
    inputDatabase: str
    fileParam: List[Dict[str, Any]]


@dataclass
class UploadImportResult:
    result: Any
    file_metadata: Dict[str, Any]
    target_store: str


def resolve_input_database_name(vector_store_type: str) -> str:
    mapping = {
        "elastic": "ElasticsearchStore",
        "faiss": "Faiss",
        "chroma": "Chroma",
    }
    return mapping.get(str(vector_store_type or "").lower(), "ElasticsearchStore")


class RAGTool:
    """Single entry point for file ingestion and retrieval.

    ``retriever`` is optional so ingestion workers can use the tool without
    constructing a query model. A chat service can inject its existing
    Retriever instance and use :meth:`search` through the same facade.
    """

    def __init__(self, retriever=None, upload_dir: Path | str = DEFAULT_UPLOAD_DIR):
        self.retriever = retriever
        self.upload_dir = Path(upload_dir)

    def add(self, request) -> Any:
        # 入库依赖较重且不属于检索启动路径，调用时再加载以保持主服务可启动。
        from .data_process.database_process import database_add_process

        return database_add_process(file_param=request)

    def replace(self, request) -> Any:
        # 替换操作延迟加载具体向量库实现，未安装的可选后端不会影响纯检索使用。
        from .data_process.database_process import database_replace_process

        return database_replace_process(file_param=request)

    def delete(self, file_param: Dict[str, Any], input_database: str) -> Any:
        # 删除操作只在实际调用时解析后端依赖，隔离摄取能力与查询能力。
        from .data_process.database_process import database_delete_process

        return database_delete_process(file_param, input_database)

    def import_upload(
        self,
        upload,
        *,
        database_name: str,
        vector_store_type: str,
        file_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> UploadImportResult:
        if upload is None or not getattr(upload, "filename", None):
            raise ValueError("upload file is required")

        final_file_id = file_id or uuid.uuid4().hex
        file_name = Path(upload.filename).name
        target_store = resolve_input_database_name(vector_store_type)
        file_metadata: Dict[str, Any] = {
            "id": final_file_id,
            "fileName": file_name,
            "rangeType": "1",
            "databaseName": database_name,
        }
        file_metadata.update({
            key: value
            for key, value in dict(metadata or {}).items()
            if value not in (None, "", [])
        })

        saved_file = persist_uploaded_file(upload, final_file_id, self.upload_dir)
        file_metadata["filePath"] = str(saved_file)
        result = self.add(DatabaseRequest(
            inputDatabase=target_store,
            fileParam=[file_metadata],
        ))
        return UploadImportResult(
            result=result,
            file_metadata=file_metadata,
            target_store=target_store,
        )

    def import_persisted_upload(
        self,
        *,
        file_id: str,
        database_name: str,
        vector_store_type: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> UploadImportResult:
        # Agent Tool 只能传 JSON file_id；这里将它限制为上传目录中的单个安全路径段，禁止任意路径读取。
        safe_file_id = Path(str(file_id or "")).name
        if not safe_file_id or safe_file_id in {".", ".."} or safe_file_id != str(file_id):
            raise ValueError("file_id must be a single safe path segment")
        # 内部管理的逻辑文件和版本原件只允许可信管理任务处理，普通 Tool 不得绕过绑定与版本。
        if safe_file_id.startswith(("pk_", "ok_", "managed_")):
            raise ValueError("managed RAG files require the internal management interface")

        # 每个 file_id 目录应只包含上传接口保存的一个原始文件，歧义目录不能自动选择文件。
        upload_root = self.upload_dir.resolve()
        target_dir = (upload_root / safe_file_id).resolve()
        if target_dir.parent != upload_root or not target_dir.is_dir():
            raise ValueError(f"uploaded file not found for file_id: {safe_file_id}")
        uploaded_files = sorted(
            (path for path in target_dir.iterdir() if path.is_file()),
            key=lambda path: path.name,
        )
        if len(uploaded_files) != 1:
            raise ValueError(
                f"file_id must resolve to exactly one uploaded file: {safe_file_id}"
            )
        saved_file = uploaded_files[0].resolve()
        if upload_root not in saved_file.parents:
            raise ValueError("uploaded file must stay inside the RAG upload directory")

        # file_id 可能来自其他持久化入口，入库前必须再次校验，确保不支持的文件不会加载重型解析依赖。
        validate_rag_upload_file_type(saved_file.name)

        # 系统字段由可信上传记录生成，调用方 metadata 不能覆盖文件标识、路径或目标向量库。
        target_store = resolve_input_database_name(vector_store_type)
        file_metadata: Dict[str, Any] = {
            "id": safe_file_id,
            "fileName": saved_file.name,
            "filePath": str(saved_file),
            "rangeType": "1",
            "databaseName": database_name,
        }
        reserved_fields = {"id", "fileName", "filePath", "rangeType", "databaseName"}
        file_metadata.update({
            key: value
            for key, value in dict(metadata or {}).items()
            if key not in reserved_fields and value not in (None, "", [])
        })

        # 复用现有数据库入口完成解析、切分、Embedding 和写入；False 必须提升为明确失败。
        result = self.add(DatabaseRequest(
            inputDatabase=target_store,
            fileParam=[file_metadata],
        ))
        if result is False:
            raise RuntimeError("failed to process and store the uploaded file")
        return UploadImportResult(
            result=result,
            file_metadata=file_metadata,
            target_store=target_store,
        )

    def search(self, query):
        if self.retriever is None:
            raise RuntimeError("RAGTool.search requires a retriever")
        return self.retriever.search(query)
