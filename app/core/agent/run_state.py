from dataclasses import dataclass, field
from typing import Any, Optional

from app.core.agent.context import AgentContext
from app.core.llm.adapter import Message, ToolCall
from app.core.tool.types import ToolError, ToolResult


@dataclass
class AgentEvent:
    type: str
    content: str = ""
    tool_call_id: Optional[str] = None
    tool_name: Optional[str] = None
    tool_result: Optional[ToolResult] = None
    error: Optional[ToolError] = None
    warning: Optional[str] = None
    meta: Optional[dict[str, Any]] = None


@dataclass
class ToolCallResult:
    tool_call_id: str
    tool_name: str
    result: ToolResult
    error: Optional[ToolError] = None


@dataclass
class RunState:
    context: AgentContext
    runtime_context: dict[str, Any]
    file_content: str
    tool_type: Optional[str]
    current_workflow_page: Optional[str] = None
    available_tool_names: Optional[list[str]] = None
    response_citations: list[dict[str, str]] = field(default_factory=list)
    # 单页快照的归属校验使用服务端会话身份，不使用模型可见的用户字段。
    user_id: str = ""
    session_id: str = ""
    # 会话课程供工具向 Java 请求授权范围，不接受模型工具参数覆盖，也不直接作为权限证明。
    course_code: Optional[str] = None


@dataclass
class StandardTurnState:
    tools: list[dict[str, Any]]
    messages: list[Message]
    accumulated_text: str = ""
    accumulated_reasoning: str = ""
    pending_tool_calls: list[ToolCall] = field(default_factory=list)
    reasoning_ended: bool = False
