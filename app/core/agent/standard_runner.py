import asyncio
import logging
from typing import TYPE_CHECKING, AsyncGenerator, Optional

from app.core.agent.compaction import compact_history
from app.core.agent.compaction_marker import COMPACTION_MARKER_PREFIX
from app.core.agent.context_guard import precheck_context
from app.core.agent.incomplete import get_incomplete_turn_warning, should_show_incomplete_turn_warning
from app.core.agent.run_state import AgentEvent, RunState, StandardTurnState, ToolCallResult
from app.core.hook.types import HookEventName, ModelTurnHookContext, ToolHookContext
from app.core.llm.adapter import ToolCall
from app.core.session.models import Session, ToolErrorInfo
from app.core.tool.registry import registry

if TYPE_CHECKING:
    from app.core.agent.engine import AgentEngine

logger = logging.getLogger(__name__)


class StandardAgentRunner:
    def __init__(self, engine: "AgentEngine"):
        self.engine = engine
        self._aborted = False

    async def run(
        self,
        session: Session,
        run_state: RunState,
        stream: bool,
    ) -> AsyncGenerator[AgentEvent, None]:
        turn = 0
        while True:
            max_turns = self.engine._resolve_max_turns(session.plan)
            if turn >= max_turns:
                async for event in self._emit_turn_limit_warning(session, run_state):
                    yield event
                break
            turn += 1
            turn_state = self._prepare_turn_state(session, run_state)
            # 在模型执行轮次之前触发，该事件发生在消息 / 工具构建完成之后，且在上下文预检查或调用大语言模型之前。钩子函数可以在不深入循环主体的情况下检查本轮的情况。
            await self._trigger_model_turn_hook(
                event_name=HookEventName.BEFORE_MODEL_TURN,
                session=session,
                run_state=run_state,
                turn_state=turn_state,
            )
            can_continue, context_events = await self._ensure_context_fits(
                session=session,
                run_state=run_state,
                turn_state=turn_state,
            )
            for event in context_events:
                yield event
            if not can_continue:
                break

            llm_failed = False
            try:
                async for event in self._stream_llm_response(turn_state, stream):
                    yield event
            except Exception as exc:
                llm_failed = True
                yield AgentEvent(
                    type="warning",
                    warning=f"LLM call failed: {exc}",
                )
            if llm_failed:
                break

            if turn_state.pending_tool_calls:
                async for event in self._execute_pending_tool_calls(
                    session=session,
                    run_state=run_state,
                    pending_tool_calls=turn_state.pending_tool_calls,
                    accumulated_reasoning=turn_state.accumulated_reasoning,
                ):
                    yield event
                if self._aborted:
                    return
                if run_state.pending_workflow_transition is not None:
                    return
                continue

            if turn_state.accumulated_text:
                self._finalize_text_response(
                    session=session,
                    run_state=run_state,
                    turn_state=turn_state,
                )
                # Stop is emitted only when the standard branch is about to
                # leave normally. It does not force continuation.
                await self._trigger_model_turn_hook(
                    event_name=HookEventName.STOP,
                    session=session,
                    run_state=run_state,
                    turn_state=turn_state,
                )
                break

            if not turn_state.pending_tool_calls and not turn_state.accumulated_text:
                warning = self._build_empty_turn_warning(
                    run_state=run_state,
                    turn=turn,
                    max_turns=max_turns,
                )
                if warning is not None:
                    yield warning
                await self._trigger_model_turn_hook(
                    event_name=HookEventName.STOP,
                    session=session,
                    run_state=run_state,
                    turn_state=turn_state,
                )
                break

    async def _emit_turn_limit_warning(
        self,
        session: Session,
        run_state: RunState,
    ) -> AsyncGenerator[AgentEvent, None]:
        if should_show_incomplete_turn_warning(run_state.context):
            yield AgentEvent(type="warning", warning=get_incomplete_turn_warning())
        session.mark_task_ended("max_turns")
        self.engine.session_manager.update_session(session)

    def _prepare_turn_state(
        self,
        session: Session,
        run_state: RunState,
    ) -> StandardTurnState:
        tools = registry.get_tools_schema(tool_type=run_state.tool_type)
        messages = self.engine._build_messages(
            session,
            run_state.context,
            run_state.file_content,
        )
        return StandardTurnState(
            tools=tools,
            messages=messages,
        )

    async def _trigger_model_turn_hook(
        self,
        *,
        event_name: HookEventName,
        session: Session,
        run_state: RunState,
        turn_state: StandardTurnState,
    ) -> None:
        hook_manager = getattr(self.engine.tool_executor, "hook_manager", None)
        if hook_manager is None:
            return
        # 模型转向钩子与工具执行器共享管理器，因此外部扩展对于标准循环有一个注册点。
        await hook_manager.trigger(
            ModelTurnHookContext(
                event_name=event_name,
                session_id=session.session_id,
                user_id=session.user_id,
                session=session,
                run_state=run_state,
                turn_state=turn_state,
            )
        )

    async def _ensure_context_fits(
        self,
        session: Session,
        run_state: RunState,
        turn_state: StandardTurnState,
    ) -> tuple[bool, list[AgentEvent]]:
        events: list[AgentEvent] = []
        precheck = precheck_context(
            messages=turn_state.messages,
            system_prompt="",
            tools=turn_state.tools,
            context_token_budget=self.engine.context_token_budget,
        )
        if precheck.route == "fits":
            return True, events

        self.engine._shrink_tool_results(turn_state.messages)
        precheck = precheck_context(
            messages=turn_state.messages,
            system_prompt="",
            tools=turn_state.tools,
            context_token_budget=self.engine.context_token_budget,
        )
        if precheck.route == "fits":
            return True, events

        result = await compact_history(
            turn_state.messages,
            self.engine.llm,
            self.engine.context_token_budget,
        )
        if result.compacted:
            session.add_user_message(f"{COMPACTION_MARKER_PREFIX}{result.summary}")
            self.engine.session_manager.add_message(session)
            turn_state.messages = self.engine._build_messages(
                session,
                run_state.context,
                run_state.file_content,
            )
            events.append(
                AgentEvent(
                    type="warning",
                    warning=f"Context compacted ({result.tokens_saved} tokens freed).",
                )
            )
            return True, events

        events.append(
            AgentEvent(
                type="warning",
                warning=(
                    f"Context overflow: {precheck.estimated_tokens} tokens estimated "
                    f"(budget={self.engine.context_token_budget}). Try /reset to start a fresh session."
                ),
            )
        )
        return False, events

    async def _stream_llm_response(
        self,
        turn_state: StandardTurnState,
        stream: bool,
    ) -> AsyncGenerator[AgentEvent, None]:
        async for event in self.engine.llm.chat(
            turn_state.messages,
            turn_state.tools,
            stream=stream,
        ):
            if event.type == "reasoning":
                chunk = event.reasoning_content or event.content
                if chunk:
                    turn_state.accumulated_reasoning += chunk
                    turn_state.reasoning_ended = True
                    yield AgentEvent(type="reasoning", content=chunk)
                continue

            if event.reasoning_content and not turn_state.accumulated_reasoning:
                turn_state.accumulated_reasoning = event.reasoning_content

            if event.tool_calls:
                if turn_state.reasoning_ended:
                    yield AgentEvent(type="reasoning_end", content="")
                    turn_state.reasoning_ended = False
                for tool_call in event.tool_calls:
                    turn_state.pending_tool_calls.append(tool_call)

            if event.content:
                if turn_state.reasoning_ended:
                    yield AgentEvent(type="reasoning_end", content="")
                    turn_state.reasoning_ended = False
                turn_state.accumulated_text += event.content
                yield AgentEvent(type="content", content=event.content)

    async def _execute_pending_tool_calls(
        self,
        session: Session,
        run_state: RunState,
        pending_tool_calls: list[ToolCall],
        accumulated_reasoning: str,
    ) -> AsyncGenerator[AgentEvent, None]:
        prepared_tool_calls = await self._prepare_tool_calls(
            session=session,
            run_state=run_state,
            pending_tool_calls=pending_tool_calls,
        )
        prepared_tool_calls = self._prioritize_workflow_transition_tool_calls(
            prepared_tool_calls
        )
        tool_calls_data = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": tc.arguments},
            }
            for tc in prepared_tool_calls
        ]

        for index, tc in enumerate(prepared_tool_calls):
            current_tool_calls_data = [tool_calls_data[index]]
            yield AgentEvent(
                type="tool_call",
                content="",
                tool_call_id=tc.id,
                tool_name=tc.name,
            )

            limit_result = self.engine._handle_tool_call_limit(
                session,
                tc.id,
                tc.name,
                tc.arguments,
            )
            if limit_result is not None:
                self.engine._persist_tool_call_interaction(
                    session=session,
                    tool_calls_data=current_tool_calls_data,
                    tool_call_results=[limit_result],
                    reasoning_content=accumulated_reasoning,
                )
                yield AgentEvent(
                    type="tool_result",
                    content=limit_result.result.content,
                    tool_call_id=tc.id,
                    tool_name=tc.name,
                    tool_result=limit_result.result,
                    error=limit_result.error,
                )
                self.engine._apply_tool_error_state(session, limit_result.error)
                session.add_assistant_message(self.engine.TOOL_UNAVAILABLE_MESSAGE)
                self.engine.session_manager.add_message(session)
                session.mark_task_ended("tool_call_limit")
                self.engine.session_manager.update_session(session)
                yield AgentEvent(type="content", content=self.engine.TOOL_UNAVAILABLE_MESSAGE)
                self._aborted = True
                return

            execution_task = asyncio.create_task(
                self.engine.tool_executor.execute_tool_call(
                    tc.name,
                    tc.arguments,
                    tc.id,
                    session_id=session.session_id,
                    user_id=session.user_id,
                )
            )
            emitted_permission_request_id: Optional[str] = None
            # ToolExecutor waits inside PermissionService.ask for ask decisions.
            # While it is waiting, the runner translates the pending request
            # into a permission_asked runtime event for the API stream.
            while not execution_task.done():
                permission_service = getattr(self.engine.tool_executor, "permission_service", None)
                if permission_service is not None:
                    request = permission_service.find_pending_by_tool_call_id(
                        session_id=session.session_id,
                        user_id=session.user_id,
                        tool_call_id=tc.id,
                    )
                    if request is not None and request.request_id != emitted_permission_request_id:
                        logger.info(
                            "Emitting permission_asked for session_id=%s tool_call_id=%s request_id=%s tool_name=%s",
                            session.session_id,
                            tc.id,
                            request.request_id,
                            tc.name,
                        )
                        emitted_permission_request_id = request.request_id
                        yield AgentEvent(
                            type="permission_asked",
                            content="",
                            tool_call_id=tc.id,
                            tool_name=tc.name,
                            meta=request.model_dump(),
                        )
                await asyncio.sleep(0.05)

            result, error = await execution_task
            tool_call_result = ToolCallResult(
                tool_call_id=tc.id,
                tool_name=tc.name,
                result=result,
                error=error,
            )
            run_state.response_citations.extend(
                self.engine._extract_citations_from_tool_result(result)
            )

            if error:
                session.record_task_tool_failure(
                    tc.name,
                    self.engine._tool_input_signature(tc.arguments),
                )
                tool_error_info = ToolErrorInfo(
                    tool_name=error.tool_name,
                    error=error.error,
                    timed_out=error.timed_out,
                    meta=error.meta,
                )
                run_state.context.set_tool_error(tool_error_info)
                session.set_tool_error(tool_error_info)
            else:
                session.clear_task_tool_failure_streak()
                run_state.context.clear_tool_error()
                session.clear_tool_error()

            self.engine._persist_tool_call_interaction(
                session=session,
                tool_calls_data=current_tool_calls_data,
                tool_call_results=[tool_call_result],
                reasoning_content=accumulated_reasoning,
            )
            yield AgentEvent(
                type="tool_result",
                content=result.content,
                tool_call_id=tc.id,
                tool_name=tc.name,
                tool_result=result,
                error=error,
            )

            workflow_transition = self.engine._extract_workflow_transition(tc.name, result)
            if workflow_transition is not None:
                run_state.pending_workflow_transition = workflow_transition
                return

    @staticmethod
    def _prioritize_workflow_transition_tool_calls(
        tool_calls: list[ToolCall],
    ) -> list[ToolCall]:
        # next()拿到生成器中的第一个元素就立即返回
        # 找到workflow_tool就返回，准备走workflow分支，否则就走普通分支
        workflow_tool_call = next(
            (tool_call for tool_call in tool_calls if tool_call.name == "start_workflow_tool"),
            None,
        )
        if workflow_tool_call is None:
            return tool_calls
        return [workflow_tool_call]

    async def _prepare_tool_calls(
        self,
        session: Session,
        run_state: RunState,
        pending_tool_calls: list[ToolCall],
    ) -> list[ToolCall]:
        hook_manager = getattr(self.engine.tool_executor, "hook_manager", None)
        if hook_manager is None:
            return [
                self.engine._prepare_tool_call(session, tool_call)
                for tool_call in pending_tool_calls
            ]

        prepared_tool_calls: list[ToolCall] = []
        for tool_call in pending_tool_calls:
            # Prepare-phase BeforeToolUse handles argument normalization before
            # the assistant/tool message pair is persisted. Permission checks
            # still happen later in ToolExecutor during the execute phase.
            hook_result = await hook_manager.trigger(
                ToolHookContext(
                    event_name=HookEventName.BEFORE_TOOL_USE,
                    session_id=session.session_id,
                    user_id=session.user_id,
                    tool_call_id=tool_call.id,
                    tool_name=tool_call.name,
                    arguments=dict(tool_call.arguments or {}),
                    meta={
                        "phase": "prepare",
                        "session": session,
                        "run_state": run_state,
                    },
                )
            )
            arguments = (
                hook_result.updated_arguments
                if hook_result.updated_arguments is not None
                else dict(tool_call.arguments or {})
            )
            prepared_tool_calls.append(
                ToolCall(
                    id=tool_call.id,
                    name=tool_call.name,
                    arguments=arguments,
                )
            )
        return prepared_tool_calls

    def _finalize_text_response(
        self,
        session: Session,
        run_state: RunState,
        turn_state: StandardTurnState,
    ) -> None:
        session.add_assistant_message(
            turn_state.accumulated_text,
            reasoning_content=turn_state.accumulated_reasoning or None,
            citations=self.engine._dedupe_citations(run_state.response_citations),
        )
        self.engine.session_manager.add_message(session)

    def _build_empty_turn_warning(
        self,
        run_state: RunState,
        turn: int,
        max_turns: int,
    ) -> Optional[AgentEvent]:
        if turn >= max_turns or not run_state.context.has_tool_error():
            if should_show_incomplete_turn_warning(run_state.context):
                return AgentEvent(type="warning", warning=get_incomplete_turn_warning())
        return None
