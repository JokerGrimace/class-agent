from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from app.core.tool.types import ToolError, ToolResult


class HookEventName(str, Enum):
    # 事件名称有意与代理工具包使用的钩子词汇保持一致。请保持这些字符串值的稳定性，因为测试和未来的配置可能会直接引用它们。
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    BEFORE_MODEL_TURN = "BeforeModelTurn"
    BEFORE_TOOL_USE = "BeforeToolUse"
    AFTER_TOOL_USE = "AfterToolUse"
    STOP = "Stop"


@dataclass
class HookResult:
    # continue_execution=False 会阻止当前操作。在工具路径中，这会产生一个失败的 ToolResult；它不会授予权限。
    continue_execution: bool = True
    blocking_error: Optional[str] = None
    # updated_arguments 会在评估治理机制之前应用于当前的工具调用。治理机制仍然会看到最终生效的参数。
    updated_arguments: Optional[dict[str, Any]] = None
    additional_context: Optional[str] = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class HookContext:
    event_name: HookEventName
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolHookContext(HookContext):
    tool_call_id: Optional[str] = None
    tool_name: Optional[str] = None
    arguments: dict[str, Any] = field(default_factory=dict)
    result: Optional[ToolResult] = None
    error: Optional[ToolError] = None


@dataclass
class ModelTurnHookContext(HookContext):
    session: Any = None
    run_state: Any = None
    turn_state: Any = None
