import logging

from app.core.tool.types import ToolError, ToolResult

logger = logging.getLogger(__name__)
import asyncio
import inspect
from typing import AsyncGenerator, List, Optional, Any, Callable, Awaitable, Literal
from dataclasses import dataclass

from app.core.agent.tool_call_limits import (
    MAX_TOOL_CALLS_PER_TASK,
    TOOL_CALL_LIMIT_ERROR,
    tool_input_signature,
)
from app.core.llm.adapter import Message
from app.core.session.manager import SessionManager
from app.core.session.models import Session, ToolErrorInfo
from app.core.tool.executor import ToolExecutor

# ------------------------------
# 你项目已有的结构（保持完全一致）
# ------------------------------
@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]

@dataclass
class ToolCallResult:
    tool_call_id: str
    tool_name: str
    result: Any
    error: Optional[Any] = None

@dataclass
class AgentEvent:
    type: str
    content: str = ""
    tool_call_id: str = ""
    tool_name: str = ""
    tool_result: Optional[Any] = None
    error: Optional[Any] = None
    warning: Optional[str] = None
    meta:Optional[dict[str, Any]] = None

@dataclass
class LLMRunResult:
    content: str
    reasoning: str = ""
    tool_call_results: Optional[List[ToolCallResult]] = None




@dataclass
class LLMRunStreamEvent:
    type: Literal["runtime", "completed"]
    runtime_event: Optional[AgentEvent] = None
    result: Optional[LLMRunResult] = None

# ------------------------------
# 通用 LLM 工具调用执行器（最终抽象版）
# ------------------------------
@dataclass
class GuardedToolCallEvent:
    type: Literal["runtime", "completed"]
    runtime_event: Optional[AgentEvent] = None
    result: Optional[ToolResult] = None
    error: Optional[ToolError] = None


class LLMToolCallExecutor:
    def __init__(
        self,
        llm,
        session,
        tool_executor: Optional[ToolExecutor] = None,
        session_manager: Optional[SessionManager] = None,
        persist_messages: bool = True,
    ):
        self.llm = llm
        self.tool_executor = tool_executor or ToolExecutor()
        self.session_manager = session_manager or SessionManager()
        self.persist_messages = persist_messages
        self.session = session

    def _add_message(self, session: Session) -> None:
        if self.persist_messages:
            self.session_manager.add_message(session)

    @staticmethod
    def _tool_error_info(error: ToolError) -> ToolErrorInfo:
        return ToolErrorInfo(
            tool_name=error.tool_name,
            error=error.error,
            timed_out=error.timed_out,
            meta=error.meta,
        )

    @staticmethod
    def _tool_call_limit_result(tool_call: ToolCall) -> ToolCallResult:
        result = ToolResult(success=False, error=TOOL_CALL_LIMIT_ERROR)
        error = ToolError(tool_name=tool_call.name, error=TOOL_CALL_LIMIT_ERROR)
        return ToolCallResult(
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            result=result,
            error=error,
        )

    @staticmethod
    def _is_tool_call_limit_reached(session: Session, tool_call: ToolCall) -> bool:
        failure_streak = session.get_task_tool_failure_streak(
            tool_call.name,
            tool_input_signature(tool_call.arguments),
        )
        return failure_streak >= MAX_TOOL_CALLS_PER_TASK

    @staticmethod
    def _record_tool_call_outcome(
        session: Session,
        tool_call: ToolCall,
        error: Optional[ToolError],
    ) -> None:
        if error:
            session.record_task_tool_failure(
                tool_call.name,
                tool_input_signature(tool_call.arguments),
            )
            session.set_tool_error(LLMToolCallExecutor._tool_error_info(error))
            return
        session.clear_task_tool_failure_streak()
        session.clear_tool_error()

    async def _execute_tool_call_with_permission_events(
        self,
        session: Session,
        tool_call: ToolCall,
    ) -> AsyncGenerator[LLMRunStreamEvent, None]:
        execution_task = asyncio.create_task(
            self.tool_executor.execute_tool_call(
                tool_call.name,
                tool_call.arguments,
                tool_call.id,
                session_id=session.session_id,
                user_id=session.user_id,
            )
        )

        emitted_permission_request_id: Optional[str] = None
        while not execution_task.done():
            permission_service = getattr(self.tool_executor, "permission_service", None)
            if permission_service is not None:
                request = permission_service.find_pending_by_tool_call_id(
                    session_id=session.session_id,
                    user_id=session.user_id,
                    tool_call_id=tool_call.id,
                )
                if request is not None and request.request_id != emitted_permission_request_id:
                    emitted_permission_request_id = request.request_id
                    logger.info(
                        "Emitting permission_asked for session_id=%s tool_call_id=%s request_id=%s tool_name=%s",
                        session.session_id,
                        tool_call.id,
                        request.request_id,
                        tool_call.name,
                    )
                    yield LLMRunStreamEvent(
                        type="runtime",
                        runtime_event=AgentEvent(
                            type="permission_asked",
                            tool_call_id=tool_call.id,
                            tool_name=tool_call.name,
                            meta={
                                "request_id": request.request_id,
                                "summary": request.summary,
                                "visible_arguments": request.visible_arguments,
                                "editable_fields": request.editable_fields,
                                "allowed_actions": request.allowed_actions,
                                "tool_name": request.tool_name,
                                "tool_action": request.tool_action,
                            },
                        ),
                    )
            await asyncio.sleep(0.05)

        result, error = await execution_task
        yield LLMRunStreamEvent(
            type="completed",
            result=ToolCallResult(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                result=result,
                error=error,
            ),
        )

    async def _emit_runtime_event(
        self,
        on_runtime_event: Optional[Callable[[AgentEvent], Optional[Awaitable[None]]]],
        event: AgentEvent,
    ) -> None:
        if on_runtime_event is None:
            return
        maybe_awaitable = on_runtime_event(event)
        if inspect.isawaitable(maybe_awaitable):
            await maybe_awaitable

    async def execute(
        self,
        session: Session,
        messages: list[Any],
        tools: list[Any],
        stream: bool = True,
    ) -> AsyncGenerator[AgentEvent, None]:
        """
        通用 LLM 流式执行 + 工具调用
        给 Agent 和 Workflow 共同使用
        """
        accumulated_reasoning = ""
        accumulated_text = ""
        pending_tool_calls: List[ToolCall] = []
        reasoning_ended = False
        response_citations = []

        # --------------------------------------------------------------------
        # 1. 流式接收 LLM
        # --------------------------------------------------------------------
        try:
            async for event in self.llm.chat(messages, tools, stream=stream):
                # 思考过程
                if event.type == "reasoning":
                    chunk = event.reasoning_content or event.content
                    if chunk:
                        accumulated_reasoning += chunk
                        reasoning_ended = True
                        yield AgentEvent(type="reasoning", content=chunk)
                    continue

                if event.reasoning_content and not accumulated_reasoning:
                    accumulated_reasoning = event.reasoning_content

                # 工具调用
                if event.tool_calls:
                    if reasoning_ended:
                        # yield AgentEvent(type="reasoning_end", content="")
                        reasoning_ended = False
                    for tc in event.tool_calls:
                        pending_tool_calls.append(tc)

                # 正常内容
                if event.content:
                    if reasoning_ended:
                        # yield AgentEvent(type="reasoning_end", content="")
                        reasoning_ended = False
                    accumulated_text += event.content
                    yield AgentEvent(type="content", content=event.content)

        except Exception as e:
            yield AgentEvent(type="warning", warning=f"LLM 调用失败: {str(e)}")
            return

        # --------------------------------------------------------------------
        # 2. 执行工具调用（完全复用你原来的逻辑）
        # --------------------------------------------------------------------
        if pending_tool_calls:
            # 保存 assistant tool_calls 消息
            tool_calls_data = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.arguments},
                } for tc in pending_tool_calls
            ]
            if self.persist_messages:
                session.add_assistant_message(
                    "",
                    tool_calls_data,
                    reasoning_content=accumulated_reasoning or None
                )
                self._add_message(session)

            tool_call_results: List[ToolCallResult] = []

            # 执行每个工具
            for tc in pending_tool_calls:
                yield AgentEvent(
                    type="tool_call",
                    content="",
                    tool_call_id=tc.id,
                    tool_name=tc.name,
                )

                # 执行工具（你项目真实方法）
                result, error = await self._execute_guarded_tool_call(tc,session)

                # 包装结果
                tcr = ToolCallResult(
                    tool_call_id=tc.id,
                    tool_name=tc.name,
                    result=result,
                    error=error
                )
                tool_call_results.append(tcr)

                # 错误存入上下文
                if error:
                    session.set_tool_error(error)
                else:
                    session.clear_tool_error()

                # 推送事件
                yield AgentEvent(
                    type="tool_result",
                    content=result.content if result else str(error),
                    tool_call_id=tc.id,
                    tool_name=tc.name,
                    tool_result=result,
                    error=error,
                )

            # 保存 tool 消息
            for tcr in tool_call_results:
                raw = tcr.result.content if tcr.result else f"Error: {tcr.error}"
                if self.persist_messages:
                    session.add_tool_message(
                        tcr.tool_call_id,
                        tcr.tool_name,
                        raw
                    )
                    self._add_message(session)

            return

        # --------------------------------------------------------------------
        # 3. 纯文本回答
        # --------------------------------------------------------------------
        if accumulated_text:
            if self.persist_messages:
                session.add_assistant_message(
                    accumulated_text,
                    reasoning_content=accumulated_reasoning or None
                )
                self._add_message(session)
            return

    async def run_until_done(
        self,
        session: Session,
        messages: list[Message],
        tools: list[Any],
        stream: bool = True,
        max_turns: int = 8,
    ) ->  AsyncGenerator[LLMRunStreamEvent, None]:
        tool_call_results: list[ToolCallResult] = []
        turn = 0
        # breakpoint()
        while turn < max_turns:
            turn += 1
            accumulated_reasoning = ""
            accumulated_text = ""
            pending_tool_calls: List[ToolCall] = []

            try:
                async for event in self.llm.chat(messages, tools, stream=stream):
                    if event.type == "reasoning":
                        chunk = event.reasoning_content or event.content
                        if chunk:
                            accumulated_reasoning += chunk
                        continue

                    if event.reasoning_content and not accumulated_reasoning:
                        accumulated_reasoning = event.reasoning_content

                    if event.tool_calls:
                        pending_tool_calls.extend(event.tool_calls)

                    if event.content:
                        accumulated_text += event.content
            except Exception as e:
                raise RuntimeError(f"LLM 调用失败: {e}") from e

            if pending_tool_calls:
                assistant_message = Message(
                    role="assistant",
                    content="",
                    tool_calls=pending_tool_calls,
                    reasoning_content=accumulated_reasoning or None,
                )
                messages.append(assistant_message)
                if self.persist_messages:
                    session.add_assistant_message(
                        "",
                        [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {"name": tc.name, "arguments": tc.arguments},
                            }
                            for tc in pending_tool_calls
                        ],
                        reasoning_content=accumulated_reasoning or None,
                    )
                    self._add_message(session)

                for tc in pending_tool_calls:
                    yield LLMRunStreamEvent(
                        type="runtime",
                        runtime_event=AgentEvent(
                            type="tool_call",
                            tool_call_id=tc.id,
                            tool_name=tc.name,
                        ),
                    )

                    if self._is_tool_call_limit_reached(session, tc):
                        tcr = self._tool_call_limit_result(tc)
                        session.mark_task_ended("tool_call_limit")
                    else:
                        tcr = None
                        async for tool_event in self._execute_tool_call_with_permission_events(session, tc):
                            if tool_event.type == "runtime":
                                yield tool_event
                                continue
                            tcr = tool_event.result
                        if tcr is None:
                            raise RuntimeError("Tool execution finished without a result")

                    tool_call_results.append(tcr)

                    self._record_tool_call_outcome(session, tc, tcr.error)
                    if tcr.error:
                        tool_content = f"Error: {tcr.error.error}"
                    else:
                        tool_content = tcr.result.content if tcr.result else ""

                    messages.append(
                        Message(
                            role="tool",
                            content=tool_content,
                            tool_call_id=tc.id,
                            tool_name=tc.name,
                        )
                    )
                    if self.persist_messages:
                        session.add_tool_message(tc.id, tc.name, tool_content)
                        self._add_message(session)

                    yield LLMRunStreamEvent(
                        type="runtime",
                        runtime_event=AgentEvent(
                            type="tool_result",
                            tool_call_id=tc.id,
                            tool_name=tc.name,
                            content=tool_content,
                            tool_result=tcr.result,
                            error=tcr.error,
                        ),
                    )

                    if tcr.error and tcr.error.error == TOOL_CALL_LIMIT_ERROR:
                        yield LLMRunStreamEvent(
                            type="completed",
                            result=LLMRunResult(
                                content=tool_content,
                                tool_call_results=tool_call_results,
                            ),
                        )
                        return

                continue

            if accumulated_text:
                if self.persist_messages:
                    session.add_assistant_message(
                        accumulated_text,
                        reasoning_content=accumulated_reasoning or None,
                    )
                    self._add_message(session)
                messages.append(
                    Message(
                        role="assistant",
                        content=accumulated_text,
                        reasoning_content=accumulated_reasoning or None,
                    )
                )

            yield LLMRunStreamEvent(
                type="completed",
                result=LLMRunResult(
                    content=accumulated_text,
                    tool_call_results=tool_call_results,
                ),
            )
            return

        yield LLMRunStreamEvent(
            type="completed",
            result=LLMRunResult(
                content="",
                tool_call_results=tool_call_results,
            ),
        )
