import asyncio
import json
import logging
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
from app.core.tool.openapi_mapping_db import OpenApiToolMappingService
from app.core.tool.python_tool_catalog_db import PythonToolCatalogService
from app.core.tool.types import ToolError, ToolResult
from app.core.agent_page_prompt.catalog_db import AgentPagePromptCatalogService
from app.core.workflow.catalog_db import WorkflowCatalogService
from app.tools import cache as tool_cache
from app.tools.iclass_openapi import load_iclass_openapi_tools
from app.tools.python_loader import load_python_tools
from app.core.workspace import Workspace
from app.core.file_cache import get_file_content_cache

# DEFAULT_WORKFLOW_SUMMARY_LIMIT = 5
DEFAULT_RUNTIME_FILE_TEXT_LIMIT = 6000
RUNTIME_FILE_TOOL_NAMES = frozenset({"read_cached_file_content"})
MISSING_TOOL_RESPONSE_ERROR = "Error: Missing tool response reconstructed from stored session history."
logger = logging.getLogger(__name__)

_openapi_tool_mapping_service_instance: Optional[OpenApiToolMappingService] = None
_python_tool_catalog_service_instance: Optional[PythonToolCatalogService] = None


def _openapi_tool_mapping_service() -> OpenApiToolMappingService:
    global _openapi_tool_mapping_service_instance
    if _openapi_tool_mapping_service_instance is None:
        _openapi_tool_mapping_service_instance = OpenApiToolMappingService()
    return _openapi_tool_mapping_service_instance


def _python_tool_catalog_service() -> PythonToolCatalogService:
    global _python_tool_catalog_service_instance
    if _python_tool_catalog_service_instance is None:
        _python_tool_catalog_service_instance = PythonToolCatalogService()
    return _python_tool_catalog_service_instance


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


def _runtime_file_tool_names(runtime_context: dict[str, Any]) -> set[str]:
    """为当前请求中的临时附件补齐读取正文所需的运行时工具。"""
    file_content = runtime_context.get("file_content")
    if not isinstance(file_content, dict):
        return set()

    files = file_content.get("files")
    if not isinstance(files, list):
        return set()

    # 附件解析接口只把缓存键返回给前端，正文通过缓存读取工具按需获取；
    # 因此只要当前请求包含带缓存键的文件，就必须把读取工具加入本轮工具目录。
    has_cached_file = any(
        isinstance(item, dict)
        and str(item.get("file_cache_key") or "").strip()
        for item in files
    )
    return set(RUNTIME_FILE_TOOL_NAMES) if has_cached_file else set()


# 只删除当前会话上一次使用、且本次请求已经切换掉的文件缓存。
def _cleanup_switched_file_cache(
    session: Session,
    runtime_context: dict[str, Any],
    *,
    cache=None,
) -> None:
    file_cache = cache if cache is not None else get_file_content_cache()
    current_file_path = str(runtime_context.get("file_path") or "").strip()
    file_content = runtime_context.get("file_content")
    current_attachment_cache_keys: set[str] = set()
    if isinstance(file_content, dict):
        files = file_content.get("files")
        if isinstance(files, list):
            current_attachment_cache_keys = {
                str(item.get("file_cache_key") or "").strip()
                for item in files
                if isinstance(item, dict) and str(item.get("file_cache_key") or "").strip()
            }

    previous_file_path = str(session.context.active_file_path or "").strip()
    previous_file_cache_key = str(session.context.active_file_cache_key or "").strip()
    if previous_file_path != current_file_path:
        _delete_file_cache_safely(file_cache, previous_file_cache_key)
        session.context.active_file_cache_key = None

    previous_attachment_cache_keys = {
        str(key).strip()
        for key in session.context.attachment_file_cache_keys
        if str(key).strip()
    }
    for file_cache_key in previous_attachment_cache_keys - current_attachment_cache_keys:
        _delete_file_cache_safely(file_cache, file_cache_key)

    session.context.active_file_path = current_file_path or None
    session.context.attachment_file_cache_keys = sorted(current_attachment_cache_keys)


# 解析同一路径文件得到新缓存时，及时替换该路径之前的缓存。
def _remember_read_course_material_cache(
    session: Session,
    tool_call_result,
    *,
    cache=None,
) -> bool:
    meta = getattr(tool_call_result, "meta", None) or {}
    source_file_path = str(meta.get("source_file_path") or "").strip()
    file_cache_key = str(meta.get("file_cache_key") or "").strip()
    if not source_file_path or not file_cache_key:
        return False

    file_cache = cache if cache is not None else get_file_content_cache()
    previous_file_path = str(session.context.active_file_path or "").strip()
    previous_file_cache_key = str(session.context.active_file_cache_key or "").strip()
    if (
        previous_file_path == source_file_path
        and previous_file_cache_key
        and previous_file_cache_key != file_cache_key
    ):
        _delete_file_cache_safely(file_cache, previous_file_cache_key)

    session.context.active_file_path = source_file_path
    session.context.active_file_cache_key = file_cache_key
    return True


def _delete_file_cache_safely(cache, file_cache_key: str) -> None:
    if not file_cache_key:
        return
    try:
        cache.delete(file_cache_key)
    except Exception as exc:
        logger.warning(
            "Failed to delete stale file content cache key=%s: %s",
            file_cache_key,
            exc,
        )


# 测试替身可能只实现消息保存，真实会话管理器则额外持久化文件缓存状态。
def _persist_session_context(session_manager: SessionManager, session: Session) -> None:
    update_session = getattr(session_manager, "update_session", None)
    if callable(update_session):
        update_session(session)


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

    @staticmethod
    def _load_active_page_agents_md(
        web_page_name: Optional[str] = None,
    ) -> str:
        prompt = AgentPagePromptCatalogService().get_active_prompt(
            web_page_name or ""
        )
        if not prompt:
            return ""
        return str(prompt.get("content") or "")

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
        resolved_runtime_context = dict(runtime_context or {})
        _cleanup_switched_file_cache(session, resolved_runtime_context)
        _persist_session_context(self.session_manager, session)
        runtime_file_tool_names = _runtime_file_tool_names(resolved_runtime_context)
        current_workflow_page = resolved_runtime_context.get("current_workflow_page")
        try:
            registered_tool_names = await load_iclass_openapi_tools(page_name=current_workflow_page)
        except Exception as exc:
            registered_tool_names = []
            logger.warning(
                "Failed to load OpenAPI tools for current_workflow_page=%r: %s",
                current_workflow_page,
                exc,
            )
        logger.info(
            "Agent runtime page=%r registered_openapi_tools=%s",
            current_workflow_page,
            registered_tool_names,
        )
        try:
            python_tool_rows = _python_tool_catalog_service().list_tool_rows_for_page(
                current_workflow_page or "",
            )
            python_tool_names = [
                str(row.get("tool_name") or "").strip()
                for row in python_tool_rows
                if str(row.get("tool_name") or "").strip()
            ]
            # 数据库目录控制页面工具权限，但临时附件的缓存读取工具属于本轮上下文的必要能力；
            # 即使旧目录尚未登记它，也要在导入工具模块前纳入本轮工具集合。
            python_tool_names = sorted(set(python_tool_names) | runtime_file_tool_names)
            python_tool_modules = {
                str(row.get("tool_name") or "").strip(): str(row.get("module_path") or "").strip()
                for row in python_tool_rows
                if str(row.get("tool_name") or "").strip()
                and str(row.get("module_path") or "").strip()
            }
            registered_python_tool_names = load_python_tools(
                python_tool_names,
                module_by_tool_name=python_tool_modules,
            )
        except Exception as exc:
            registered_python_tool_names = []
            logger.warning(
                "Failed to load Python tools for current_workflow_page=%r: %s",
                current_workflow_page,
                exc,
            )
        logger.info(
            "Agent runtime page=%r registered_python_tools=%s",
            current_workflow_page,
            registered_python_tool_names,
        )
        run_state = self._initialize_run_state(
            session=session,
            tool_type=tool_type,
            runtime_context=resolved_runtime_context,
        )
        async for event in self._run_standard_branch(
                session=session,
                run_state=run_state,
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
        runtime_file_tool_names = _runtime_file_tool_names(resolved_runtime_context)
        current_workflow_page = resolved_runtime_context.get("current_workflow_page")
        try:
            openapi_tool_names = _openapi_tool_mapping_service().list_tool_names_for_page(
                current_workflow_page or "",
            )
            python_tool_names = _python_tool_catalog_service().list_tool_names_for_page(
                current_workflow_page or "",
            )
            available_tool_names = sorted(
                set(openapi_tool_names) | set(python_tool_names) | runtime_file_tool_names
            )
        except Exception as exc:
            available_tool_names = None
            logger.warning(
                "Failed to build available tool names for current_workflow_page=%r: %s",
                current_workflow_page,
                exc,
            )
        logger.info(
            "Agent available tools page=%r names=%s",
            current_workflow_page,
            available_tool_names,
        )
        self.prompt_builder.workflow_summaries = self._load_default_workflow_summaries(
            page_code=current_workflow_page,
        )
        self.prompt_builder.page_agents_md = self._load_active_page_agents_md(
            web_page_name=current_workflow_page,
        )
        self.prompt_builder.current_workflow_page = current_workflow_page
        self.prompt_builder.available_tool_names = available_tool_names

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
            current_workflow_page=current_workflow_page,
            available_tool_names=available_tool_names,
            # 快照读取需要会话真实身份，避免其他会话引用同一个缓存键。
            user_id=session.user_id,
            session_id=session.session_id,
            # 保留请求已选定的会话课程；工具将该值传给 Java 进行选课与资源权限核验。
            course_code=session.course_code,
        )





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
            assistant_payload=self._extract_assistant_payload(tool_call_result),
        )
        self.session_manager.add_message(session)
        if _remember_read_course_material_cache(session, tool_call_result):
            _persist_session_context(self.session_manager, session)

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
        file_cache_state_changed = False

        for tool_call_result in tool_call_results:
            content = self._build_persisted_tool_result_content(tool_call_result)
            file_cache_state_changed = (
                _remember_read_course_material_cache(session, tool_call_result)
                or file_cache_state_changed
            )
            session.add_tool_message(
                tool_call_result.tool_call_id,
                tool_call_result.tool_name,
                content,
                assistant_payload=self._extract_assistant_payload(tool_call_result),
            )
            messages_to_persist.append(session.messages[-1])

        self.session_manager.add_messages(session, messages_to_persist)
        if file_cache_state_changed:
            _persist_session_context(self.session_manager, session)

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

    @staticmethod
    def _extract_assistant_payload(
        tool_call_result: ToolCallResult,
    ) -> Optional[dict[str, Any]]:
        assistant_payload = (tool_call_result.result.meta or {}).get("assistant_payload")
        return assistant_payload if isinstance(assistant_payload, dict) else None

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
        workflow_context_injected_tools = {
            "create_workflow_run",
            "dispatch_workflow_batch_and_wait",
            "get_workflow_run",
            "cancel_workflow_run",
        }
        if tool_call.name not in workflow_context_injected_tools:
            return tool_call

        arguments = dict(tool_call.arguments or {})
        arguments["session_id"] = session.session_id
        arguments["user_id"] = session.user_id
        arguments["agent_id"] = session.agent_id

        if tool_call.name == "create_workflow_run":
            arguments["global_context"] = dict(session.context.session_context or {})

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

        plan_prompt = self._format_plan(session.plan)
        if plan_prompt:
            messages.append(Message(role="system", content=plan_prompt))

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

    @staticmethod
    def _format_plan(plan) -> str:
        if not plan:
            return ""

        if isinstance(plan, dict):
            steps = plan.get("steps")
            if not isinstance(steps, list) or not steps:
                return ""
            lines = ["Current Plan", f"Strict: {'enabled' if plan.get('strict') else 'disabled'}"]
            for index, step in enumerate(steps, start=1):
                if not isinstance(step, dict):
                    continue
                title = step.get("title") or step.get("step") or step.get("id") or f"Step {index}"
                status = step.get("status", "pending")
                lines.append(f"{index}. [{status}] {title}")
                allowed_tools = step.get("allowed_tools")
                if allowed_tools:
                    lines.append(f"   allowed_tools: {', '.join(str(item) for item in allowed_tools)}")
                completion_mode = step.get("completion_mode")
                if completion_mode:
                    lines.append(f"   completion_mode: {completion_mode}")
            return "\n".join(lines)

        if isinstance(plan, list):
            lines = ["Current Plan"]
            for index, step in enumerate(plan, start=1):
                if not isinstance(step, dict):
                    continue
                title = step.get("step") or step.get("title") or f"Step {index}"
                status = step.get("status", "pending")
                lines.append(f"{index}. [{status}] {title}")
            return "\n".join(lines) if len(lines) > 1 else ""

        return ""

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
    user_role: str = "",
    stream: bool = True,
    workspace_dir: Optional[str] = None,
) -> AsyncGenerator[AgentEvent, None]:
    from app.core.llm.factory import create_llm_adapter
    from app.core.llm.user_context import reset_current_model_user, set_current_model_user

    llm = create_llm_adapter()
    engine = AgentEngine(llm, workspace_dir=workspace_dir)

    session_manager = SessionManager()
    session = session_manager.get_session(session_id, user_id=user_id, user_role=user_role)
    if not session:
        session = session_manager.create_session(user_id=user_id, user_role=user_role)
        session_id = session.session_id

    session.add_user_message(user_message)
    session_manager.update_session(session)

    # 直接调用入口没有 HTTP 路由可绑定身份，因此使用其显式用户参数建立模型上下文。
    model_user_context = set_current_model_user(user_id, user_role)
    try:
        async for event in engine.run(session, stream=stream):
            yield event
    finally:
        reset_current_model_user(model_user_context)
