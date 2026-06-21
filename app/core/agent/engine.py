import asyncio
import json
from typing import Any, AsyncGenerator, Optional, Union

from app.core.agent.context import AgentContext
from app.core.agent.run_state import AgentEvent, RunState, ToolCallResult
from app.core.agent.standard_runner import StandardAgentRunner
from app.core.agent.system_prompt import SystemPromptBuilder
from app.core.agent.task_continuity import TASK_CONTINUITY_REMINDER_BY_REASON
from app.core.agent.tool_call_limits import (
    MAX_TOOL_CALLS_PER_TASK,
    TOOL_CALL_LIMIT_ERROR,
    TOOL_UNAVAILABLE_MESSAGE,
    tool_input_signature,
)
from app.core.agent.tool_result_truncation import (
    DEFAULT_MAX_LIVE_TOOL_RESULT_CHARS,
    truncate_tool_result_text,
)
from app.core.llm.adapter import LLMAdapter, Message, ToolCall
from app.core.session.manager import SessionManager
from app.core.session.models import Message as SessionMessage, Session, ToolErrorInfo
from app.core.tool.executor import ToolExecutor
from app.core.tool.types import ToolError, ToolResult
from app.core.workflow.catalog_db import WorkflowCatalogService
from app.core.workflow.executor import WorkflowExecutor
from app.core.workflow.manager import WorkflowManager
from app.tools import cache as tool_cache
from app.core.workspace import Workspace

# DEFAULT_WORKFLOW_SUMMARY_LIMIT = 5
DEFAULT_RUNTIME_FILE_TEXT_LIMIT = 6000
MISSING_TOOL_RESPONSE_ERROR = "Error: Missing tool response reconstructed from stored session history."


def _extract_citations_from_tool_result(result: ToolResult) -> list[dict[str, str]]:
    meta = result.meta or {}
    citations = meta.get("citations")
    if isinstance(citations, list):
        extracted = []
        for item in citations:
            if not isinstance(item, dict):
                continue
            url = (item.get("url") or "").strip()
            if not url:
                continue
            extracted.append({
                "title": (item.get("title") or url).strip(),
                "url": url,
            })
        if extracted:
            return extracted

    fallback_url = (meta.get("final_url") or meta.get("url") or "").strip()
    if fallback_url:
        return [{
            "title": (meta.get("title") or fallback_url).strip(),
            "url": fallback_url,
        }]
    return []


def _dedupe_citations(citations: list[dict[str, str]]) -> list[dict[str, str]]:
    deduped: list[dict[str, str]] = []
    seen: set[str] = set()
    for citation in citations:
        url = citation["url"]
        if url in seen:
            continue
        seen.add(url)
        deduped.append(citation)
    return deduped


def _tool_input_signature(arguments: dict[str, Any]) -> str:
    return tool_input_signature(arguments)


def _format_runtime_file_content(file_content: Any) -> str:
    if isinstance(file_content, str):
        return file_content
    if isinstance(file_content, dict):
        files = file_content.get("files")
        if isinstance(files, list) and files:
            blocks: list[str] = []
            for index, item in enumerate(files, start=1):
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or f"temp_file_{index}")
                file_type = str(item.get("type") or "unknown")
                file_cache_key = str(item.get("file_cache_key") or "").strip()
                file_id = str(item.get("file_id") or "").strip()
                html_length = item.get("html_length")
                content = str(
                    item.get("html_content")
                    or item.get("content")
                    or item.get("text_content")
                    or ""
                ).strip()
                if not content and file_cache_key:
                    metadata_lines = []
                    if file_id:
                        metadata_lines.append(f"file_id: {file_id}")
                    metadata_lines.append(f"file_cache_key: {file_cache_key}")
                    metadata_lines.append("Use read_cached_file_content with this file_cache_key when full file content is needed.")
                    if html_length is not None:
                        metadata_lines.append(f"html_length: {html_length}")
                    content = "\n".join(metadata_lines)
                if not content:
                    continue
                if len(content) > DEFAULT_RUNTIME_FILE_TEXT_LIMIT:
                    content = content[:DEFAULT_RUNTIME_FILE_TEXT_LIMIT] + "\n...[content truncated]"
                blocks.append(
                    f"[Temporary File {index}]\n"
                    f"Name: {name}\n"
                    f"Type: {file_type}\n"
                    f"Content:\n{content}"
                )
            if blocks:
                return "\n\n".join(blocks)
    try:
        return json.dumps(file_content, ensure_ascii=False, indent=2)
    except Exception:
        return str(file_content)


class AgentEngine:
    TOOL_UNAVAILABLE_MESSAGE = TOOL_UNAVAILABLE_MESSAGE

    def __init__(
        self,
        llm: LLMAdapter,
        session_manager: Optional[SessionManager] = None,
        tool_executor: Optional[ToolExecutor] = None,
        workspace_dir: Optional[str] = None,
    ):
        self.llm = llm
        self.session_manager = session_manager or SessionManager()
        self.tool_executor = tool_executor or ToolExecutor()
        self.workflow_manager = WorkflowManager()
        from app.config import settings as app_settings
        self.prompt_builder = SystemPromptBuilder(
            workspace_dir=workspace_dir,
            workflow_summaries=self._load_default_workflow_summaries(),
            timezone_name=app_settings.timezone,
            docs_path=app_settings.docs_path or None,
        )
        self.max_turns = app_settings.max_turns
        self.strict_plan_max_turns = app_settings.strict_plan_max_turns
        self.context_token_budget = app_settings.context_tokens or app_settings.default_context_tokens
        self._workspace = Workspace(workspace_dir) if workspace_dir else None

    @staticmethod
    def _load_default_workflow_summaries(
        directory: Optional[Union[str, Any]] = None,
        page_code: Optional[str] = None,
    ) -> list[dict[str, str]]:
        return WorkflowCatalogService().list_prompt_summaries(
            page_code=page_code,
        )

    def _is_bootstrap_pending(self) -> bool:
        if not self._workspace:
            return False
        bootstrap_path = self._workspace.dir / "BOOTSTRAP.md"
        return bootstrap_path.exists()

    def _get_bootstrap_user_prefix(self) -> Optional[str]:
        if not self._is_bootstrap_pending():
            return None
        from app.core.agent.bootstrap import build_bootstrap_user_prefix
        bootstrap_mode = "limited" if self.prompt_builder.bootstrap_mode == "limited" else "full"
        return build_bootstrap_user_prefix(bootstrap_mode)

    async def run(
        self,
        session: Session,
        stream: bool = True,
        tool_type: Optional[str] = "level1",
        runtime_context: Optional[dict[str, Any]] = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        run_state = self._initialize_run_state(
            session=session,
            tool_type=tool_type,
            runtime_context=runtime_context,
        )
        incomplete_workflow = self._load_incomplete_workflow(session)
        if incomplete_workflow and self._should_resume_incomplete_workflow(
            session=session,
            run_state=run_state,
            workflow=incomplete_workflow,
        ):
            async for event in self._run_workflow_branch(
                session=session,
                run_state=run_state,
                workflow=incomplete_workflow,
                stream=stream,
            ):
                yield event
        else:
            async for event in self._run_standard_branch(
                session=session,
                run_state=run_state,
                stream=stream,
            ):
                yield event
            transition = run_state.pending_workflow_transition
            if transition:
                # 填充workflow所有需要的参数信息
                workflow = self._resolve_transition_workflow(
                    session=session,
                    transition=transition,
                )
                if workflow is not None:
                    async for event in self._run_workflow_branch(
                        session=session,
                        run_state=run_state,
                        workflow=workflow,
                        stream=stream,
                    ):
                        yield event
        yield AgentEvent(type="done")






    def _initialize_run_state(
        self,
        session: Session,
        tool_type: Optional[str],
        runtime_context: Optional[dict[str, Any]],
    ) -> RunState:
        tool_cache.clear_all()
        context = AgentContext()
        resolved_runtime_context = dict(runtime_context or {})
        current_workflow_page = resolved_runtime_context.get("current_workflow_page")
        self.prompt_builder.workflow_summaries = self._load_default_workflow_summaries(
            page_code=current_workflow_page,
        )

        if session.context.last_tool_error:
            context.set_tool_error(session.context.last_tool_error)
        if session.context.task_continuity_notice:
            context.task_continuity_notice = session.context.task_continuity_notice

        file_content = _format_runtime_file_content(resolved_runtime_context.get("file_content", ""))
        session_context = dict(resolved_runtime_context)
        session.context.session_context = session_context
        context.session_parameter = session_context
        self.prompt_builder.tool_type = tool_type
        return RunState(
            context=context,
            runtime_context=resolved_runtime_context,
            file_content=file_content,
            tool_type=tool_type,
        )

    def _load_incomplete_workflow(self, session: Session):
        workflow_manager = getattr(self, "workflow_manager", None)
        if workflow_manager is None:
            return None
        return workflow_manager.get_active_workflow(session.session_id, session.user_id)

    def _should_resume_incomplete_workflow(
        self,
        session: Session,
        run_state: RunState,
        workflow,
    ) -> bool:
        del session
        resume_workflow = run_state.runtime_context.get("resume_workflow")
        workflow_id = run_state.runtime_context.get("workflow_id")
        if resume_workflow is not True:
            return False
        if not isinstance(workflow_id, str) or not workflow_id:
            return False
        return workflow.workflow_id == workflow_id

    def _resolve_transition_workflow(
        self,
        session: Session,
        transition: dict[str, Any],
    ):
        workflow_manager = getattr(self, "workflow_manager", None)
        if workflow_manager is None:
            return None
        workflow_id = transition.get("workflow_id")
        if isinstance(workflow_id, str) and workflow_id:
            workflow = workflow_manager.get_workflow(workflow_id, session.user_id)
            if workflow is not None:
                return workflow
        return workflow_manager.get_active_workflow(session.session_id, session.user_id)

    def _extract_workflow_transition(
        self,
        tool_name: str,
        result: ToolResult,
    ) -> Optional[dict[str, Any]]:
        if tool_name != "start_workflow_tool":
            return None
        meta = result.meta or {}
        transition = meta.get("transition")
        workflow_id = meta.get("workflow_id")
        if transition != "enter_workflow":
            return None
        if not isinstance(workflow_id, str) or not workflow_id:
            return None
        return {
            "workflow_id": workflow_id,
            "workflow_name": meta.get("workflow_name"),
        }

    # 执行workflow流程
    def _build_workflow_status_payload(
        self,
        workflow,
        *,
        status: Optional[str] = None,
        current_step_id: Optional[str] = None,
        failed_step_id: Optional[str] = None,
    ) -> dict[str, Any]:
        resolved_status = status or workflow.status
        resolved_current_step_id = current_step_id
        if resolved_current_step_id is None:
            resolved_current_step_id = workflow.context.current_step_id

        steps = []
        for index, step in enumerate(workflow.definition.steps):
            step_status = workflow.context.step_status.get(step.id, "pending")
            if failed_step_id == step.id:
                step_status = "failed"
            elif resolved_status in {"pending", "running"} and resolved_current_step_id == step.id:
                step_status = "running"
            steps.append(
                {
                    "step_id": step.id,
                    "step_name": step.id,
                    "index": index,
                    "status": step_status,
                }
            )

        return {
            "workflow_id": workflow.workflow_id,
            "workflow_name": workflow.workflow_name,
            "status": resolved_status,
            "current_step_id": resolved_current_step_id,
            "steps": steps,
        }

    def _workflow_status_event(
        self,
        workflow,
        *,
        status: Optional[str] = None,
        current_step_id: Optional[str] = None,
        failed_step_id: Optional[str] = None,
    ) -> AgentEvent:
        return AgentEvent(
            type="workflow_status",
            meta=self._build_workflow_status_payload(
                workflow,
                status=status,
                current_step_id=current_step_id,
                failed_step_id=failed_step_id,
            ),
        )


    async def _run_workflow_branch(
        self,
        session: Session,
        workflow,
        run_state: Optional[RunState] = None,
        stream: bool = True,
    ) -> AsyncGenerator[AgentEvent, None]:
        # del run_state, stream

        workflow_manager = getattr(self, "workflow_manager", None)
        if workflow_manager is None:
            yield AgentEvent(type="warning", warning="Workflow manager is unavailable.")
            return

        # Temporary frontend render test path. The real workflow execution below is disabled for now.
        # async for event in self.run(session,workflow,runtime_context=run_state.runtime_context):
        #     yield event
        # return

        executor = WorkflowExecutor(
            definition=workflow.definition, # workflow的步骤
            session=session,
            llm=self.llm,
            context_parameters=dict(workflow.context.global_context or {}),
            tool_executor=self.tool_executor,
        )

        # 加载workflow需要的参数和前面步骤的状态信息
        executor.load_runtime_state(
            step_outputs=dict(workflow.context.step_results or {}),
            context_parameters=dict(workflow.context.global_context or {}),
        )

        yield self._workflow_status_event(workflow)

        while workflow.status in {"pending", "running"}:
            current_step_id = workflow.context.current_step_id
            if not current_step_id:
                try:
                    final_answer, _summary = await executor.generate_final_answer()
                except Exception as exc:
                    workflow = workflow_manager.mark_failed(workflow.workflow_id, session.user_id, str(exc))
                    yield self._workflow_status_event(workflow, status="failed")
                    yield AgentEvent(type="warning", warning=f"Workflow finalization failed: {exc}")
                    return

                final_text = (
                    final_answer
                    if isinstance(final_answer, str)
                    else json.dumps(final_answer, ensure_ascii=False)
                )
                workflow = workflow_manager.mark_completed(workflow.workflow_id, session.user_id, final_text)
                yield self._workflow_status_event(workflow)
                summary_text = ""
                async for summary_event in self._stream_workflow_final_summary(
                    session=session,
                    workflow=workflow,
                    final_text=final_text,
                    stream=stream,
                ):
                    summary_text += summary_event.content
                    yield summary_event
                if not summary_text:
                    summary_text = final_text
                    yield AgentEvent(type="content", content=summary_text)
                session.add_assistant_message(summary_text)
                self.session_manager.add_message(session)
                return

            step = next((item for item in workflow.definition.steps if item.id == current_step_id), None)
            if step is None:
                workflow = workflow_manager.mark_failed(
                    workflow.workflow_id,
                    session.user_id,
                    f"Workflow step not found: {current_step_id}",
                )
                yield self._workflow_status_event(workflow, status="failed", failed_step_id=current_step_id)
                yield AgentEvent(type="warning", warning=f"Workflow step not found: {current_step_id}")
                return

            step_messages = executor.build_workflow_messages()
            yield self._workflow_status_event(workflow, current_step_id=step.id)

            step_success = False
            step_error_message = ""
            try:
                async for step_event in executor.execute_step_events(step, step_messages, session):
                    if step_event.type == "runtime":
                        if step_event.runtime_event is not None:
                            yield step_event.runtime_event
                        continue

                    if step_event.type == "completed":
                        step_success = bool(step_event.step_success)
                        step_error_message = getattr(step_event, "error_message", "") or ""
                        break
            except Exception as exc:
                workflow = workflow_manager.mark_failed(workflow.workflow_id, session.user_id, str(exc))
                yield self._workflow_status_event(workflow, status="failed", failed_step_id=step.id)
                yield AgentEvent(type="warning", warning=f"Workflow step failed: {exc}")
                return



            if not step_success:
                failure_message = step_error_message or f"Workflow step failed: {step.id}"
                workflow = workflow_manager.mark_failed(
                    workflow.workflow_id,
                    session.user_id,
                    failure_message,
                )
                yield self._workflow_status_event(workflow, status="failed", failed_step_id=step.id)
                yield AgentEvent(type="warning", warning=f"Workflow step failed: {failure_message}")
                return

            workflow = workflow_manager.mark_step_completed(
                workflow.workflow_id,
                session.user_id,
                step_id=step.id,
                output_key=step.output_key,
                output_value=executor.step_outputs.get(step.output_key),
            )
            yield self._workflow_status_event(workflow)
            executor.load_runtime_state(
                step_outputs=dict(workflow.context.step_results or {}),
                context_parameters=dict(workflow.context.global_context or {}),
            )


        if workflow.status == "failed":
            error_message = ""
            if isinstance(workflow.context.last_error, dict):
                error_message = str(workflow.context.last_error.get("message") or "")
            yield self._workflow_status_event(workflow)
            yield AgentEvent(type="warning", warning=error_message or "Workflow failed.")

    async def _stream_workflow_final_summary(
        self,
        *,
        session: Session,
        workflow,
        final_text: str,
        stream: bool,
    ) -> AsyncGenerator[AgentEvent, None]:
        latest_user_message = self._get_latest_user_message(session) or ""
        step_results = getattr(workflow.context, "step_results", {}) or {}
        prompt = (
            "工作流已经完成。请根据工作流结果，给用户一段简洁、自然的任务结束总结。\n"
            "不要返回 JSON，不要继续调用工具，不要编造没有出现在结果里的数据。\n"
            f"用户原始需求：{latest_user_message}\n"
            f"工作流名称：{workflow.workflow_name}\n"
            f"工作流最终结果：{final_text}\n"
            f"工作流步骤结果：{json.dumps(step_results, ensure_ascii=False)}\n"
        )
        messages = [
            Message(role="system", content="You write concise final user-facing summaries after workflow completion."),
            Message(role="user", content=prompt),
        ]
        async for event in self.llm.chat(messages, tools=[], stream=stream):
            if event.type == "reasoning":
                chunk = event.reasoning_content or event.content
                if chunk:
                    yield AgentEvent(type="reasoning", content=chunk)
                continue
            if event.content:
                yield AgentEvent(type="content", content=event.content)

    @staticmethod
    def _get_latest_user_message(session: Session) -> Optional[str]:
        for message in reversed(session.messages):
            if message.role == "user":
                return message.content
        return None


    async def _run_standard_branch(
        self,
        session: Session,
        run_state: RunState,
        stream: bool,
    ) -> AsyncGenerator[AgentEvent, None]:
        runner = StandardAgentRunner(self)
        async for event in runner.run(session, run_state, stream):
            yield event

    def _resolve_max_turns(self, plan) -> int:
        return getattr(self, "max_turns", 10)

    @staticmethod
    def _extract_citations_from_tool_result(result: ToolResult) -> list[dict[str, str]]:
        return _extract_citations_from_tool_result(result)

    @staticmethod
    def _dedupe_citations(citations: list[dict[str, str]]) -> list[dict[str, str]]:
        return _dedupe_citations(citations)

    @staticmethod
    def _tool_input_signature(arguments: dict[str, Any]) -> str:
        return _tool_input_signature(arguments)

    def _apply_tool_error_state(
        self,
        session: Session,
        error: Optional[ToolError],
    ) -> None:
        if error:
            session.set_tool_error(
                ToolErrorInfo(
                    tool_name=error.tool_name,
                    error=error.error,
                    timed_out=error.timed_out,
                    meta=error.meta,
                )
            )
            return
        session.clear_tool_error()

    def _persist_tool_call_result(
        self,
        session: Session,
        tool_call_result: ToolCallResult,
    ) -> None:
        content = self._build_persisted_tool_result_content(tool_call_result)

        session.add_tool_message(
            tool_call_result.tool_call_id,
            tool_call_result.tool_name,
            content,
        )
        self.session_manager.add_message(session)

    def _persist_tool_call_interaction(
        self,
        session: Session,
        tool_calls_data: list[dict[str, Any]],
        tool_call_results: list[ToolCallResult],
        reasoning_content: Optional[str] = None,
    ) -> None:
        if not tool_calls_data or not tool_call_results:
            return

        session.add_assistant_message(
            "",
            tool_calls_data,
            reasoning_content=reasoning_content or None,
        )
        messages_to_persist = [session.messages[-1]]

        for tool_call_result in tool_call_results:
            content = self._build_persisted_tool_result_content(tool_call_result)
            session.add_tool_message(
                tool_call_result.tool_call_id,
                tool_call_result.tool_name,
                content,
            )
            messages_to_persist.append(session.messages[-1])

        self.session_manager.add_messages(session, messages_to_persist)

    @staticmethod
    def _build_persisted_tool_result_content(
        tool_call_result: ToolCallResult,
    ) -> str:
        raw_content = tool_call_result.result.content
        if tool_call_result.error:
            raw_content = f"Error: {tool_call_result.error.error}"
            if tool_call_result.error.timed_out:
                raw_content = f"Error: Tool timed out after {tool_call_result.result.error}"

        tool_cache.put(tool_call_result.tool_call_id, raw_content)
        return truncate_tool_result_text(raw_content, DEFAULT_MAX_LIVE_TOOL_RESULT_CHARS)

    def _handle_tool_call_limit(
        self,
        session: Session,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> Optional[ToolCallResult]:
        failure_streak = session.get_task_tool_failure_streak(
            tool_name,
            _tool_input_signature(arguments),
        )
        if failure_streak < MAX_TOOL_CALLS_PER_TASK:
            return None

        result = ToolResult(
            success=False,
            error=TOOL_CALL_LIMIT_ERROR,
        )
        error = ToolError(
            tool_name=tool_name,
            error=TOOL_CALL_LIMIT_ERROR,
        )
        return ToolCallResult(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            result=result,
            error=error,
        )

    @staticmethod
    def _prepare_tool_call(session: Session, tool_call: ToolCall) -> ToolCall:
        if tool_call.name != "start_workflow_tool":
            return tool_call

        arguments = dict(tool_call.arguments or {})
        arguments["session_id"] = session.session_id
        arguments["user_id"] = session.user_id
        arguments["agent_id"] = session.agent_id

        runtime_context = dict(session.context.session_context or {})
        explicit_runtime_context = arguments.get("runtime_context")
        if isinstance(explicit_runtime_context, dict):
            runtime_context.update(explicit_runtime_context)
        if runtime_context:
            arguments["context_parameters"] = runtime_context

        return ToolCall(
            id=tool_call.id,
            name=tool_call.name,
            arguments=arguments,
        )

    @staticmethod
    def _repair_session_history_tool_messages(
        messages: list[SessionMessage],
    ) -> list[SessionMessage]:
        repaired: list[SessionMessage] = []
        index = 0

        while index < len(messages):
            current = messages[index]
            repaired.append(current)
            index += 1

            if current.role != "assistant" or not current.tool_calls:
                continue

            expected_tool_calls: list[tuple[str, Optional[str]]] = []
            for tool_call in current.tool_calls:
                if not isinstance(tool_call, dict):
                    continue
                tool_call_id = tool_call.get("id")
                function = tool_call.get("function")
                tool_name = function.get("name") if isinstance(function, dict) else None
                if isinstance(tool_call_id, str) and tool_call_id:
                    expected_tool_calls.append((tool_call_id, tool_name))

            contiguous_tool_messages: list[SessionMessage] = []
            seen_tool_call_ids: set[str] = set()
            while index < len(messages) and messages[index].role == "tool":
                tool_message = messages[index]
                contiguous_tool_messages.append(tool_message)
                if tool_message.tool_call_id:
                    seen_tool_call_ids.add(tool_message.tool_call_id)
                index += 1

            repaired.extend(contiguous_tool_messages)

            for tool_call_id, tool_name in expected_tool_calls:
                if tool_call_id in seen_tool_call_ids:
                    continue
                repaired.append(
                    SessionMessage(
                        role="tool",
                        content=MISSING_TOOL_RESPONSE_ERROR,
                        tool_call_id=tool_call_id,
                        tool_name=tool_name,
                    )
                )

        return repaired

    def _build_messages(
        self,
        session: Session,
        context: AgentContext,
        file_content: Optional[str] = None,
    ) -> list[Message]:
        messages: list[Message] = []

        system_prompt = self.prompt_builder.build_with_context(context)
        messages.append(Message(role="system", content=system_prompt))

        boundary = session.get_boundary_index()
        start = boundary + 1 if boundary >= 0 else 0
        last_user_id = -1

        if session.context.task_continuity_notice:
            messages.append(Message(role="system", content=session.context.task_continuity_notice))

        repaired_session_messages = self._repair_session_history_tool_messages(
            session.messages[start:]
        )

        for msg in repaired_session_messages:
            tool_calls = None

            if msg.tool_calls:
                tool_calls = [
                    ToolCall(
                        id=tc.get("id", ""),
                        name=tc["function"]["name"],
                        arguments=tc["function"]["arguments"],
                    )
                    for tc in msg.tool_calls
                ]
            messages.append(Message(
                role=msg.role,
                content=msg.content,
                tool_call_id=msg.tool_call_id,
                tool_name=msg.tool_name,
                tool_calls=tool_calls,
                reasoning_content=msg.reasoning_content,
            ))
            if msg.role == "user":
                last_user_id = len(messages) - 1

        if file_content and last_user_id >= 0:
            messages[last_user_id].content = self.prompt_builder.build_with_readfile(
                file_content,
                messages[last_user_id].content,
            )
        return messages

    def _shrink_tool_results(self, messages: list[Message]) -> None:
        for msg in messages:
            if msg.role == "tool" and msg.content:
                msg.content = truncate_tool_result_text(
                    msg.content,
                    DEFAULT_MAX_LIVE_TOOL_RESULT_CHARS,
                )


async def run_agent(
    session_id: str,
    user_message: str,
    user_id: str = "anonymous",
    stream: bool = True,
    workspace_dir: Optional[str] = None,
) -> AsyncGenerator[AgentEvent, None]:
    from app.core.llm.factory import create_llm_adapter

    llm = create_llm_adapter()
    engine = AgentEngine(llm, workspace_dir=workspace_dir)

    session_manager = SessionManager()
    session = session_manager.get_session(session_id, user_id=user_id)
    if not session:
        session = session_manager.create_session(user_id=user_id)
        session_id = session.session_id

    session.add_user_message(user_message)
    session_manager.update_session(session)

    async for event in engine.run(session, stream=stream):
        yield event
