from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Union

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python 3.8 fallback
    ZoneInfo = None

from pydantic import BaseModel, Field, field_validator

from app.core.agent.compaction_marker import COMPACTION_MARKER_PREFIX
from app.core.agent.task_continuity import get_task_continuity_reminder
from app.config import settings


def _now_local() -> datetime:
    timezone_name = (settings.timezone or "").strip().lower()
    if timezone_name in {"asia/shanghai", "utc+8", "utc+08:00"}:
        if ZoneInfo is not None:
            return datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
        return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=8)
    if timezone_name == "utc":
        return datetime.now(timezone.utc).replace(tzinfo=None)
    return datetime.now()


class Message(BaseModel):
    role: str
    content: str
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    citations: list[dict[str, str]] = Field(default_factory=list)
    tool_call_id: Optional[str] = None
    tool_name: Optional[str] = None
    reasoning_content: Optional[str] = None
    assistant_payload: Optional[dict[str, Any]] = None


class ToolErrorInfo(BaseModel):
    tool_name: str
    error: str
    timed_out: bool = False
    meta: Optional[str] = None


class SessionContext(BaseModel):
    last_tool_error: Optional[ToolErrorInfo] = None
    tool_error_count: int = 0
    session_context: dict[str, Any] = Field(default_factory=dict)
    # 记录当前会话正在使用的文件缓存，避免不同会话共享文件切换状态。
    active_file_path: Optional[str] = None
    active_file_cache_key: Optional[str] = None
    attachment_file_cache_keys: list[str] = Field(default_factory=list)
    task_tool_call_counts: dict[str, int] = Field(default_factory=dict)
    task_blocked_tools: list[str] = Field(default_factory=list)
    task_last_failed_tool_name: Optional[str] = None
    task_last_failed_tool_signature: Optional[str] = None
    task_consecutive_failed_tool_calls: int = 0
    task_ended: bool = False
    task_end_reason: Optional[str] = None
    task_continuity_notice: Optional[str] = None

    @field_validator("session_context", mode="before")
    @classmethod
    def normalize_session_context(cls, value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        return {}

class Session(BaseModel):
    session_id: str
    user_id: str = "anonymous"
    user_role: str = ""
    agent_id: str = "default"
    current_workflow_page: Optional[str] = None
    course_code: Optional[str] = None
    messages: list[Message] = Field(default_factory=list)
    plan: Union[list[dict], dict[str, Any]] = Field(default_factory=list)
    plan_is_completed: bool = True
    context: SessionContext = Field(default_factory=SessionContext)
    created_at: datetime = Field(default_factory=_now_local)
    updated_at: datetime = Field(default_factory=_now_local)


    def add_user_message(self, content: str) -> None:
        if not (content or "").startswith(COMPACTION_MARKER_PREFIX):
            previous_task_end_reason = self.context.task_end_reason
            self.context.last_tool_error = None
            self.context.task_tool_call_counts = {}
            self.context.task_blocked_tools = []
            self.context.task_last_failed_tool_name = None
            self.context.task_last_failed_tool_signature = None
            self.context.task_consecutive_failed_tool_calls = 0
            self.context.task_ended = False
            self.context.task_end_reason = None
            self.context.task_continuity_notice = get_task_continuity_reminder(previous_task_end_reason)
        self.messages.append(Message(role="user", content=content))
        self.updated_at = _now_local()

    def add_boundary_marker(self, summary: str) -> None:
        self.messages.append(
            Message(
                role="user",
                content=f"{COMPACTION_MARKER_PREFIX}{summary}",
            )
        )
        self.updated_at = _now_local()

    @staticmethod
    def _normalize_scope_value(value: Any) -> str:
        if isinstance(value, str):
            return value.strip()
        return ""

    def matches_scope(
        self,
        *,
        current_workflow_page: Optional[str] = None,
        course_code: Optional[str] = None,
    ) -> bool:
        if current_workflow_page is not None:
            stored_page = self._normalize_scope_value(self.current_workflow_page)
            if stored_page != self._normalize_scope_value(current_workflow_page):
                return False

        if course_code is not None:
            stored_course_code = self._normalize_scope_value(self.course_code)
            if stored_course_code != self._normalize_scope_value(course_code):
                return False

        return True



    def add_assistant_message(
        self,
        content: str,
        tool_calls: Optional[list[dict[str, Any]]] = None,
        reasoning_content: Optional[str] = None,
        citations: Optional[list[dict[str, str]]] = None,
    ) -> None:
        self.messages.append(Message(
            role="assistant",
            content=content,
            tool_calls=tool_calls or [],
            reasoning_content=reasoning_content,
            citations=citations or [],
        ))
        self.updated_at = _now_local()

    # 根据不同的模型去适配不同的tool message
    def add_tool_message(
        self,
        tool_call_id: str,
        tool_name: str,
        content: str,
        assistant_payload: Optional[dict[str, Any]] = None,
    ) -> None:
        self.messages.append(Message(
            role="tool",
            content=content,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            assistant_payload=assistant_payload,
        ))
        self.updated_at = _now_local()

    def set_tool_error(self, error: ToolErrorInfo) -> None:
        self.context.last_tool_error = error
        self.context.tool_error_count += 1
        self.updated_at = _now_local()

    def clear_tool_error(self) -> None:
        self.context.last_tool_error = None
        self.updated_at = _now_local()

    def set_task_tool_call_count(self, tool_name: str, count: int) -> None:
        self.context.task_tool_call_counts[tool_name] = count
        self.updated_at = _now_local()

    def get_task_tool_call_count(self, tool_name: str) -> int:
        return int(self.context.task_tool_call_counts.get(tool_name, 0))

    def block_task_tool(self, tool_name: str) -> None:
        if tool_name not in self.context.task_blocked_tools:
            self.context.task_blocked_tools.append(tool_name)
            self.updated_at = _now_local()

    def is_task_tool_blocked(self, tool_name: str) -> bool:
        return tool_name in self.context.task_blocked_tools

    def record_task_tool_failure(self, tool_name: str, input_signature: str) -> None:
        if (
            self.context.task_last_failed_tool_name == tool_name
            and self.context.task_last_failed_tool_signature == input_signature
        ):
            self.context.task_consecutive_failed_tool_calls += 1
        else:
            self.context.task_last_failed_tool_name = tool_name
            self.context.task_last_failed_tool_signature = input_signature
            self.context.task_consecutive_failed_tool_calls = 1
        self.updated_at = _now_local()

    def clear_task_tool_failure_streak(self) -> None:
        self.context.task_last_failed_tool_name = None
        self.context.task_last_failed_tool_signature = None
        self.context.task_consecutive_failed_tool_calls = 0
        self.updated_at = _now_local()

    def get_task_tool_failure_streak(self, tool_name: str, input_signature: str) -> int:
        if (
            self.context.task_last_failed_tool_name == tool_name
            and self.context.task_last_failed_tool_signature == input_signature
        ):
            return int(self.context.task_consecutive_failed_tool_calls)
        return 0

    def mark_task_ended(self, reason: str) -> None:
        self.context.task_ended = True
        self.context.task_end_reason = reason
        self.updated_at = _now_local()

    def get_boundary_index(self) -> int:
        """Return the index of the last compaction boundary marker.

        Returns -1 if no boundary exists.
        """
        for i in range(len(self.messages) - 1, -1, -1):
            m = self.messages[i]
            if m.role == "user" and m.content and m.content.startswith(COMPACTION_MARKER_PREFIX):
                return i
        return -1

    def get_visible_messages(self) -> list[Message]:
        """Return messages visible to API consumers.

        Compaction boundary markers are filtered out, but all other
        messages (including pre-compaction history) are included.
        """
        visible_messages: list[Message] = []
        for m in self.messages:
            if m.role == "user" and m.content and m.content.startswith(COMPACTION_MARKER_PREFIX):
                continue
            if m.role == "tool":
                continue
            if m.role == "assistant" and not (m.content or "").strip():
                continue
            visible_messages.append(m)
        return visible_messages
