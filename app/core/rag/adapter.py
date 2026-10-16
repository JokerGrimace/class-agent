from __future__ import annotations

import asyncio
import importlib
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .retrieval_scope import resolve_managed_retrieval_files, set_managed_retrieval_grants, reset_managed_retrieval_grants


def _normalize_list(value: list[str] | tuple[str, ...] | None) -> list[str] | None:
    if value is None:
        return None
    return list(value)


def _collapse_whitespace(text: str) -> str:
    return " ".join(text.split())


@dataclass
class _RAGRuntime:
    retriever: Any


_RAG_IMPORT_LOCK = threading.Lock()
_DEFAULT_DATABASE_NAMES = ["shanghai_policy", "iclass"]


class RAGAdapter:
    def __init__(self) -> None:
        self._runtime: _RAGRuntime | None = None
        self._runtime_lock = asyncio.Lock()
        self._llm_factory = None

    async def _get_runtime(self) -> _RAGRuntime:
        if self._runtime is not None:
            return self._runtime

        async with self._runtime_lock:
            if self._runtime is None:
                self._runtime = await asyncio.to_thread(self._build_runtime)
        return self._runtime

    def _resolve_rag_root(self) -> Path:
        # RAG 实现已并入当前包，先校验配置与检索器入口都存在，避免导入到其他同名模块。
        rag_root = Path(__file__).resolve().parent
        retriever_path = rag_root / "retriever.py"
        config_path = rag_root / "config.py"
        if retriever_path.is_file() and config_path.is_file():
            return rag_root

        raise RuntimeError(
            "Unable to locate the embedded RAG runtime. Expected "
            "app/core/rag/retriever.py and app/core/rag/config.py next to "
            f"{Path(__file__).resolve()}."
        )

    def _load_rag_modules(self) -> tuple[Any, Any]:
        rag_root = self._resolve_rag_root()
        # 检索器依赖 PyTorch 生态，导入前禁用 TensorFlow 路径以规避 Keras 版本冲突。
        os.environ.setdefault("USE_TF", "0")
        os.environ.setdefault("USE_TORCH", "1")
        with _RAG_IMPORT_LOCK:
            # 使用项目内的完整包路径加载模块，不再修改 sys.path 或依赖旧版 app/RAG 目录。
            try:
                rag_config = importlib.import_module("app.core.rag.config")
                retriever_module = importlib.import_module("app.core.rag.retriever")
            except Exception as exc:
                raise RuntimeError(
                    f"Failed to import embedded RAG modules from {rag_root}."
                ) from exc
        return rag_config, retriever_module

    def _build_runtime(self) -> _RAGRuntime:
        rag_config, retriever_module = self._load_rag_modules()

        if getattr(rag_config, "tavily_api_key", None):
            os.environ.setdefault("TAVILY_API_KEY", str(rag_config.tavily_api_key))

        retriever_cls = retriever_module.Retriever

        retriever = retriever_cls(
            vector_store_type=rag_config.vector_store_type,
            embeddings_name=rag_config.embeddings_name,
            embeddings_device=rag_config.embeddings_device,
            retriever_mode=rag_config.retriever_mode,
            is_online=rag_config.is_online,
            online_threshold=rag_config.online_threshold,
            max_online_results=rag_config.max_online_results,
            vector_store_name=rag_config.vector_store_name,
        )
        return _RAGRuntime(retriever=retriever)

    def _build_payload(
        self,
        *,
        query: str,
        database_names: list[str] | None = None,
        doc_ids: list[str] | None = None,
        search_mode: str | None = None,
        subject: str | None = None,
        course: str | None = None,
        teacher: str | None = None,
    ) -> dict[str, Any]:
        normalized_database_names = _normalize_list(database_names)
        if normalized_database_names is None:
            normalized_database_names = list(_DEFAULT_DATABASE_NAMES)

        metadata_filters = {
            key: value
            for key, value in {
                "subject": subject,
                "course": course,
                "teacher": teacher,
            }.items()
            if value not in (None, "", [])
        }
        return {
            "input": query,
            "database_names": normalized_database_names,
            "doc_ids": _normalize_list(doc_ids) or [],
            "search_mode": search_mode,
            "use_online": None,
            "metadata_filters": metadata_filters,
        }

    def _normalize_citations(
        self, knowledge_base_results: list[dict[str, Any]]
    ) -> list[dict[str, str]]:
        citations: list[dict[str, str]] = []
        seen_urls: set[str] = set()
        for item in knowledge_base_results:
            file_path = str(item.get("filePath") or "").strip()
            if not file_path or file_path in seen_urls:
                continue

            parsed = urlparse(file_path)
            if parsed.scheme not in {"http", "https"}:
                continue

            seen_urls.add(file_path)
            title = item.get("fileName") or item.get("sourceLabel") or file_path
            citations.append({"title": str(title), "url": file_path})
        return citations

    def _normalize_result_item(self, item: Any) -> dict[str, Any]:
        if isinstance(item, dict):
            normalized = dict(item)
            normalized["content"] = str(normalized.get("content", ""))
            return normalized
        return {"content": str(item)}

    def _normalize_search_result(
        self,
        *,
        query: str,
        payload: dict[str, Any],
        retrieval_payload: dict[str, Any],
    ) -> dict[str, Any]:
        knowledge_base_results = [
            self._normalize_result_item(item)
            for item in list(retrieval_payload.get("knowledgeBaseResults") or [])
        ]
        online_results = list(retrieval_payload.get("onlineResults") or [])
        combined_context = list(
            retrieval_payload.get("combinedContext")
            or (knowledge_base_results + online_results)
        )
        citations = self._normalize_citations(knowledge_base_results)

        if knowledge_base_results:
            lines = [f"Knowledge base search results for: {query}", ""]
            for idx, item in enumerate(knowledge_base_results[:5], start=1):
                source_label = (
                    item.get("sourceLabel")
                    or item.get("fileName")
                    or f"Result {idx}"
                )
                excerpt = _collapse_whitespace(str(item.get("content", "")))[:280]
                lines.append(f"{idx}. {source_label}")
                lines.append(f"Content: {excerpt}")
                lines.append("")
            content = "\n".join(lines).strip()
            answer_source = "knowledge_base"
        else:
            content = f"No knowledge base results found for: {query}"
            answer_source = "empty"

        return {
            "content": content,
            "answer_source": answer_source,
            "knowledge_base_results": knowledge_base_results,
            "online_results": online_results,
            "combined_context": combined_context,
            "citations": citations,
            "applied_database_names": payload.get("database_names") or [],
            "applied_doc_ids": payload.get("doc_ids") or [],
            "applied_metadata_filters": payload.get("metadata_filters") or {},
        }

    def _get_llm_factory(self):
        if self._llm_factory is not None:
            return self._llm_factory

        from app.core.llm.factory import create_llm_adapter

        return create_llm_adapter

    def _render_context(
        self,
        combined_context: list[Any],
        *,
        max_items: int = 8,
        max_chars: int = 6000,
        max_item_chars: int = 900,
    ) -> str:
        rendered_parts: list[str] = []
        total_chars = 0

        for idx, item in enumerate(combined_context[:max_items], start=1):
            if isinstance(item, dict):
                content = item.get("content", "")
                source_label = (
                    item.get("sourceLabel")
                    or item.get("fileName")
                    or item.get("title")
                    or f"Context {idx}"
                )
            elif isinstance(item, (list, tuple)) and item:
                content = item[0]
                source_label = item[1] if len(item) > 1 and item[1] else f"Context {idx}"
            else:
                content = item
                source_label = f"Context {idx}"

            text = str(content).strip()
            if text:
                text = _collapse_whitespace(text)
                trimmed = text[:max_item_chars].strip()
                part = f"[{source_label}]\n{trimmed}"
                remaining_chars = max_chars - total_chars
                if remaining_chars <= 0:
                    break
                if len(part) > remaining_chars:
                    part = part[:remaining_chars].rstrip()
                rendered_parts.append(part)
                total_chars += len(part) + 2

        if len(combined_context) > max_items or total_chars >= max_chars:
            rendered_parts.append("[Additional context omitted for brevity]")
        return "\n\n".join(rendered_parts)

    def _resolve_answer_source(
        self,
        *,
        knowledge_base_results: list[dict[str, Any]],
        online_results: list[Any],
        combined_context: list[Any],
    ) -> str:
        if not combined_context:
            return "empty"
        if knowledge_base_results and online_results:
            return "hybrid"
        if online_results and not knowledge_base_results:
            return "online_no_kb"
        return "knowledge_base"

    async def _generate_answer(
        self, *, query: str, combined_context: list[Any]
    ) -> str:
        context_text = self._render_context(combined_context)
        if not context_text:
            return "I could not find relevant knowledge base content for this question."

        from app.core.llm.adapter import Message

        llm = self._get_llm_factory()()
        messages = [
            Message(
                role="system",
                content=(
                    "You are answering with the help of retrieved knowledge base "
                    "context. Use the context when it is relevant, stay concise, "
                    "and do not invent citations."
                ),
            ),
            Message(
                role="user",
                content=(
                    f"Question:\n{query}\n\n"
                    f"Retrieved context:\n{context_text}\n\n"
                    "Write a direct answer grounded in the retrieved context."
                ),
            ),
        ]

        answer_parts: list[str] = []
        async for event in llm.chat(messages, tools=[], stream=False):
            if event.content:
                answer_parts.append(event.content)

        answer = "".join(answer_parts).strip()
        if answer:
            return answer
        return "I could not produce a grounded answer from the retrieved context."

    async def search_knowledge(
        self,
        *,
        query: str,
        database_names: list[str] | None = None,
        doc_ids: list[str] | None = None,
        search_mode: str | None = None,
        subject: str | None = None,
        course: str | None = None,
        teacher: str | None = None,
        course_code: str | None = None,
    ) -> dict[str, Any]:
        payload = self._build_payload(
            query=query,
            database_names=database_names,
            doc_ids=doc_ids,
            search_mode=search_mode,
            subject=subject,
            course=course,
            teacher=teacher,
        )
        runtime = await self._get_runtime()
        # 当前 Java 身份返回的授权独立绑定到线程上下文；模型 query/teacher/course/doc_ids 只能缩小查询。
        grants = await resolve_managed_retrieval_files(course_code=course_code)
        token = set_managed_retrieval_grants(grants)
        try:
            retrieval_payload = await asyncio.to_thread(runtime.retriever.search, payload)
        finally:
            reset_managed_retrieval_grants(token)
        return self._normalize_search_result(
            query=query,
            payload=payload,
            retrieval_payload=retrieval_payload,
        )

    async def answer_with_knowledge(
        self,
        *,
        query: str,
        database_names: list[str] | None = None,
        doc_ids: list[str] | None = None,
        search_mode: str | None = None,
        subject: str | None = None,
        course: str | None = None,
        teacher: str | None = None,
        course_code: str | None = None,
    ) -> dict[str, Any]:
        result = await self.search_knowledge(
            query=query,
            database_names=database_names,
            doc_ids=doc_ids,
            search_mode=search_mode,
            subject=subject,
            course=course,
            teacher=teacher,
            course_code=course_code,
        )
        answer_source = self._resolve_answer_source(
            knowledge_base_results=result["knowledge_base_results"],
            online_results=result["online_results"],
            combined_context=result["combined_context"],
        )
        if not result["combined_context"]:
            return {
                "content": (
                    "I could not find relevant knowledge base content for this question."
                ),
                "answer_source": "empty",
                "knowledge_base_results": result["knowledge_base_results"],
                "online_results": result["online_results"],
                "combined_context": result["combined_context"],
                "citations": result["citations"],
                "applied_database_names": result["applied_database_names"],
                "applied_doc_ids": result["applied_doc_ids"],
                "applied_metadata_filters": result["applied_metadata_filters"],
                "used_query": query,
            }

        try:
            answer = await self._generate_answer(
                query=query,
                combined_context=result["combined_context"],
            )
        except Exception:
            answer = (
                "I found relevant knowledge base content, but I could not generate "
                "a grounded answer from it just now."
            )
        return {
            "content": answer,
            "answer_source": answer_source,
            "knowledge_base_results": result["knowledge_base_results"],
            "online_results": result["online_results"],
            "combined_context": result["combined_context"],
            "citations": result["citations"],
            "applied_database_names": result["applied_database_names"],
            "applied_doc_ids": result["applied_doc_ids"],
            "applied_metadata_filters": result["applied_metadata_filters"],
            "used_query": query,
        }


_adapter: RAGAdapter | None = None
_adapter_lock = threading.Lock()


def get_rag_adapter() -> RAGAdapter:
    global _adapter
    if _adapter is not None:
        return _adapter

    with _adapter_lock:
        if _adapter is None:
            _adapter = RAGAdapter()
    return _adapter
