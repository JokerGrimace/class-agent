from __future__ import annotations

import asyncio
import json
import logging
import re
from functools import lru_cache  # TODO：缓存重写器实例，供 RAG 和后续其他模块复用同一个工具。
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool  # TODO：将问题重写模块暴露为标准 LangChain 工具。

from . import config
from .llm import LLM

logger = logging.getLogger(__name__)


class QueryRewriter:
    """TODO：使用当前主模型将多轮追问改写为可独立检索的问题。"""

    def __init__(self, llm=None, max_rounds=None):
        self.llm = llm or LLM(
            config.llm_model,
            config.llm_model_name,
            config.temperature,
        ).llm
        configured_max_rounds = int(
            max_rounds
            if max_rounds is not None
            else getattr(config, "rewrite_history_max_rounds", 4)
        )
        self.max_rounds = min(max(configured_max_rounds, 0), 4)  # TODO：无论配置如何，重写最多读取最近 4 轮历史。

    @staticmethod
    def _message_role_and_content(message: Any) -> tuple[str, str]:
        if isinstance(message, dict):
            role = str(message.get("role") or message.get("type") or "").lower()
            return role, str(message.get("content") or "").strip()
        if isinstance(message, HumanMessage):
            return "user", str(message.content or "").strip()
        if isinstance(message, AIMessage):
            return "assistant", str(message.content or "").strip()
        role = str(
            getattr(message, "role", "")
            or getattr(message, "type", "")
            or ""
        ).lower()
        return role, str(getattr(message, "content", "") or "").strip()

    def select_recent_rounds(self, history_messages) -> list[tuple[str, str]]:
        """TODO：只保留最近的完整问答轮次，避免将孤立消息传给重写模型。"""
        rounds = []
        pending_user_message = None
        for message in list(history_messages or []):
            role, content = self._message_role_and_content(message)
            if not content:
                continue
            if role in {"human", "user"}:
                pending_user_message = content
                continue
            if role in {"ai", "assistant"} and pending_user_message:
                rounds.append((pending_user_message, content))
                pending_user_message = None
        return rounds[-self.max_rounds:]

    @staticmethod
    def _response_text(response: Any) -> str:
        content = getattr(response, "content", response)
        if isinstance(content, list):
            return "".join(
                item.get("text", "") if isinstance(item, dict) else str(item)
                for item in content
            ).strip()
        return str(content or "").strip()

    @staticmethod
    def _parse_rewrite_query(response_text: str) -> str:
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", response_text.strip(), flags=re.IGNORECASE)
        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            return str(payload.get("rewriteQuery") or "").strip()
        match = re.search(r'"rewriteQuery"\s*:\s*"([^"]+)"', cleaned)
        if match:
            return match.group(1).strip()
        return ""

    def _build_prompts(
        self,
        original_query: str,
        history_messages=None,
        *,
        subject: str | None = None,
        course: str | None = None,
        teacher: str | None = None,
    ) -> tuple[str, str]:
        """TODO：统一构建同步与异步模型使用的问题重写提示词。"""
        history_rounds = self.select_recent_rounds(history_messages)
        logger.info("Starting query rewrite. history_rounds=%s", len(history_rounds))

        history_text = "\n".join(
            f"第{index}轮\n用户：{user_message}\n助手：{assistant_message}"
            for index, (user_message, assistant_message) in enumerate(history_rounds, start=1)
        )
        system_prompt = (
            "你是 RAG 检索问题重写器。请根据最近最多 4 轮对话，将当前问题重写为不依赖历史也能独立检索的完整问题。"
            "只补全指代、省略的对象和已经出现的条件；不要回答问题，不要增加历史中没有的事实，不要改变用户意图。"
            "即使没有历史记录也必须执行重写；当原问题已经完整时可以原样返回。"
            "必须严格输出 JSON：{\"rewriteQuery\": \"...\"}"
        )
        # 学科、课程和教师是调用方明确提供的检索范围，提示词只纳入非空值，避免制造不存在的业务条件。
        context_text = "\n".join(
            f"{key}：{value}"
            for key, value in {
                "subject": subject,
                "course": course,
                "teacher": teacher,
            }.items()
            if str(value or "").strip()
        )
        user_prompt = (
            f"可用历史轮次：{len(history_rounds)}\n"
            f"历史记录：\n{history_text or '无'}\n\n"
            f"业务上下文：\n{context_text or '无'}\n\n"
            f"当前原问题：{original_query}"
        )
        return system_prompt, user_prompt

    async def _rewrite_with_async_llm(
        self,
        original_query: str,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        """TODO：使用主项目异步 LLMAdapter 完成问题重写。"""
        from app.core.llm.adapter import Message

        try:
            response = await self.llm.chat_complete(
                [
                    Message(role="system", content=system_prompt),
                    Message(role="user", content=user_prompt),
                ],
                tools=None,
            )
            rewrite_query = self._parse_rewrite_query(self._response_text(response))
        except Exception:
            logger.exception("Query rewrite failed, falling back to original query.")
            return original_query
        return self._finalize_rewrite(original_query, rewrite_query)

    def _rewrite_with_sync_llm(
        self,
        original_query: str,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        """TODO：使用嵌入式 RAG 的同步 LangChain 模型完成问题重写。"""
        try:
            response = self.llm.invoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
            ])
            rewrite_query = self._parse_rewrite_query(self._response_text(response))
        except Exception:
            logger.exception("Query rewrite failed, falling back to original query.")
            return original_query
        return self._finalize_rewrite(original_query, rewrite_query)

    @staticmethod
    def _finalize_rewrite(original_query: str, rewrite_query: str) -> str:
        """TODO：无效模型输出统一回退原问题，保证检索始终获得可用文本。"""
        if not rewrite_query:
            logger.warning("Query rewrite returned an invalid payload, falling back to original query.")
            return original_query
        logger.info("Query rewrite completed. original=%r rewrite=%r", original_query, rewrite_query)
        return rewrite_query

    def rewrite(
        self,
        original_query: str,
        history_messages=None,
        *,
        subject: str | None = None,
        course: str | None = None,
        teacher: str | None = None,
    ):
        """TODO：根据调用方模型接口选择异步主模型或同步嵌入式 RAG 模型。"""
        original_query = str(original_query or "").strip()
        if not original_query:
            return ""

        system_prompt, user_prompt = self._build_prompts(
            original_query,
            history_messages,
            subject=subject,
            course=course,
            teacher=teacher,
        )
        # 目标分支主 LLM 使用 chat_complete；嵌入式 RAG 保留同步 invoke，两条路径只影响调用方式。
        if callable(getattr(self.llm, "chat_complete", None)):
            # Retriever 在线程中同步执行；线程内创建事件循环，异步调用方则直接返回协程。
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return asyncio.run(
                    self._rewrite_with_async_llm(
                        original_query,
                        system_prompt,
                        user_prompt,
                    )
                )
            return self._rewrite_with_async_llm(original_query, system_prompt, user_prompt)
        return self._rewrite_with_sync_llm(original_query, system_prompt, user_prompt)


@lru_cache(maxsize=1)  # TODO：延迟初始化主模型，避免仅导入工具时就加载模型。
def _get_query_rewriter() -> QueryRewriter:
    """TODO：返回供各调用方共享的问题重写器实例。"""
    return QueryRewriter()


@tool("rewrite_query")  # TODO：注册为可通过 invoke 调用的标准问题重写工具。
def rewrite_query_tool(
    original_query: str,
    history_messages: list[Any] | None = None,
) -> str:
    """TODO：结合最多 4 轮历史，将原问题改写为可独立检索的问题。"""
    return _get_query_rewriter().rewrite(
        original_query,
        history_messages,
    )


# TODO：以上整个文件为本次新增的 RAG 独立问题重写模块。
