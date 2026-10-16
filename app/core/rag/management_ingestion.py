from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import threading

from elasticsearch import BadRequestError
from elasticsearch.helpers import bulk

from .management_es import KnowledgeES
from .schemas import KnowledgeTask
from .upload import DEFAULT_UPLOAD_DIR, validate_rag_upload_file_type


@dataclass
class PreparedVersion:
    chunks: list
    vectors: list[list[float]]


_embedding = None
_embedding_lock = threading.Lock()


# 只有真实摄取才加载 embedding，模型配置沿用现有 RAG 配置且只在进程内构造一次。
def get_ingestion_embedding():
    global _embedding
    with _embedding_lock:
        if _embedding is None:
            os.environ.setdefault("USE_TF", "0")
            os.environ.setdefault("USE_TORCH", "1")
            from langchain_community.embeddings import HuggingFaceEmbeddings
            from . import config
            _embedding = HuggingFaceEmbeddings(model_name=config.embeddings_name,
                            model_kwargs={"device": config.embeddings_device},
                            encode_kwargs={"normalize_embeddings": True, "batch_size": 32})
        return _embedding


# 技术任务只保存受控上传 ID；管理逻辑 ID 与物理版本原件分离，不接收服务器任意路径。
def prepare_version(task: KnowledgeTask, upload_id: str) -> PreparedVersion:
    if not upload_id or Path(upload_id).name != upload_id or not upload_id.startswith("managed_"):
        raise ValueError("invalid managed upload_id")
    root = DEFAULT_UPLOAD_DIR.resolve()
    directory = (root / upload_id).resolve()
    if directory.parent != root or not directory.is_dir():
        raise ValueError("managed source file is not available")
    files = [path for path in directory.iterdir() if path.is_file()]
    if len(files) != 1 or root not in files[0].resolve().parents:
        raise ValueError("managed source must contain one original file")
    source = files[0].resolve()
    validate_rag_upload_file_type(source.name)
    embedding = get_ingestion_embedding()
    from .file_process.document_processor import SmartDocumentProcessor
    metadata = {"id": task.file_id, "fileName": source.name, "filePath": str(source),
                "rangeType": "1", "subject": task.subject_code, "task_version": task.task_version}
    if task.binding_type == "PERSONAL":
        metadata["teacher"] = task.teacher_code
    else:
        metadata["official_library_id"] = task.library_id
        metadata["department_id"] = task.department_id
    chunks, _ = SmartDocumentProcessor(embedding).process_documents(metadata)
    if not chunks or any(not chunk.page_content.strip() for chunk in chunks):
        raise ValueError("managed source contains no usable knowledge fragments")
    vectors = embedding.embed_documents([chunk.page_content for chunk in chunks])
    if len(vectors) != len(chunks) or not vectors or not vectors[0]:
        raise RuntimeError("embedding result does not match knowledge fragments")
    return PreparedVersion(chunks, vectors)


# 使用 ES 原生 bulk 写入现有 Retriever 的 text/vector/metadata 结构，避免纯读依赖向量库封装。
class ManagedKnowledgeES(KnowledgeES):
    @staticmethod
    def mapping(dimensions):
        text_keyword = {"type": "text", "fields": {"keyword": {"type": "keyword"}}}
        return {"properties": {
            "text": {"type": "text"},
            "vector": {"type": "dense_vector", "dims": dimensions, "index": True, "similarity": "cosine"},
            "metadata": {"type": "object", "dynamic": False, "properties": {
                "id": text_keyword, "subject": text_keyword, "teacher": text_keyword,
                "chunk_id": {"type": "keyword"}, "chunk_order": {"type": "long"},
                "task_version": {"type": "long"}, "official_library_id": {"type": "long"},
                "department_id": {"type": "long"},
                "input_timestamp": {"type": "long"}, "parent_id": {"type": "keyword"},
                "fileName": {"type": "keyword"}, "rangeType": {"type": "keyword"},
                "related_images": {"type": "text", "index": False}
            }}
        }}

    # 空库首次建立明确 mapping；已有 mapping 必须与真实维度及精确字段契约一致。
    def ensure_index(self, dimensions):
        if not self.client.indices.exists(index=self.index):
            try:
                self.client.indices.create(index=self.index, mappings=self.mapping(dimensions))
                return
            except BadRequestError as exc:
                if exc.error != "resource_already_exists_exception":
                    raise
        properties = self.client.indices.get_mapping(index=self.index)[self.index]["mappings"]["properties"]
        expected = self.mapping(dimensions)["properties"]
        vector = properties.get("vector", {})
        if vector.get("type") != "dense_vector" or vector.get("dims") != dimensions:
            raise RuntimeError("RAG mapping vector dimensions do not match current embedding")
        metadata = properties.get("metadata", {}).get("properties", {})
        for name, specification in expected["metadata"]["properties"].items():
            # 只允许已验证管理 mapping 缺少此次明确新增的学院 long 字段；其他缺项或类型冲突继续拒绝。
            if name == "department_id" and name not in metadata:
                continue
            actual = metadata.get(name, {})
            if actual.get("type") != specification["type"] or actual.get("fields", {}) != specification.get("fields", {}):
                raise RuntimeError("RAG metadata mapping does not match management contract")
        if "department_id" not in metadata:
            # 精确追加后重新读取核验，不覆盖旧字段、不重建索引，重试及多进程同时追加仍为相同类型。
            self.client.indices.put_mapping(index=self.index, properties={"metadata": {"properties": {"department_id": {"type": "long"}}}})
            updated = self.client.indices.get_mapping(index=self.index)[self.index]["mappings"]["properties"]["metadata"]["properties"]
            if updated.get("department_id") != {"type": "long"}:
                raise RuntimeError("RAG department mapping was not applied")

    def write(self, task: KnowledgeTask, prepared: PreparedVersion):
        dimensions = len(prepared.vectors[0])
        if any(len(vector) != dimensions for vector in prepared.vectors):
            raise RuntimeError("embedding dimensions differ between fragments")
        self.ensure_index(dimensions)
        permitted = set(self.mapping(dimensions)["properties"]["metadata"]["properties"])
        operations = []
        for order, (chunk, vector) in enumerate(zip(prepared.chunks, prepared.vectors)):
            # 入库只保留精确白名单；物理 filePath 和向量不会成为详情响应的 metadata。
            metadata = {key: value for key, value in chunk.metadata.items() if key in permitted}
            metadata.update({"id": task.file_id, "subject": task.subject_code,
                             "task_version": task.task_version, "chunk_order": order})
            if task.binding_type == "PERSONAL":
                metadata["teacher"] = task.teacher_code
                metadata.pop("official_library_id", None)
                metadata.pop("department_id", None)
            else:
                metadata["official_library_id"] = task.library_id
                metadata["department_id"] = task.department_id
                metadata.pop("teacher", None)
            document_id = hashlib.sha256(f"{task.file_id}:{task.task_version}:{order}".encode()).hexdigest()
            operations.append({"_op_type": "index", "_index": self.index, "_id": document_id,
                               "_source": {"text": chunk.page_content, "vector": vector, "metadata": metadata}})
        written, errors = bulk(self.client, operations, refresh="wait_for", raise_on_error=True)
        if errors or written != len(prepared.chunks):
            raise RuntimeError("RAG bulk write was incomplete")
        return len(prepared.chunks)

    def close(self):
        self.client.close()
