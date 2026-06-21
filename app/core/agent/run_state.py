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
    response_citations: list[dict[str, str]] = field(default_factory=list)
    pending_workflow_transition: Optional[dict[str, Any]] = None


@dataclass
class StandardTurnState:
    tools: list[dict[str, Any]]
    messages: list[Message]
    accumulated_text: str = ""
    accumulated_reasoning: str = ""
    pending_tool_calls: list[ToolCall] = field(default_factory=list)
    reasoning_ended: bool = False
