import json
from typing import Any, Optional

from app.core.session.models import ToolErrorInfo


class AgentContext:
    def __init__(self):
        self.last_tool_error: Optional[ToolErrorInfo] = None
        self.tool_error_count: int = 0
        self.session_parameter: Optional[dict[str, Any]] = None
        self.session_context: Optional[dict[str, Any]] = None
        self.task_continuity_notice: Optional[str] = None

    def set_tool_error(self, error: ToolErrorInfo) -> None:
        self.last_tool_error = error
        self.tool_error_count += 1

    def clear_tool_error(self) -> None:
        self.last_tool_error = None

    def has_tool_error(self) -> bool:
        return self.last_tool_error is not None

    def get_warning_text(self) -> Optional[str]:
        if not self.last_tool_error:
            return None
        error_suffix = f": {self.last_tool_error.error}" if self.last_tool_error.error else ""
        return f"⚠️ {self.last_tool_error.tool_name} failed{error_suffix}"

    def build_runtime_context_prompt(self) -> str:
        parts: list[str] = []
        if self.last_tool_error:
            error_suffix = f": {self.last_tool_error.error}" if self.last_tool_error.error else ""
            parts.append(f"⚠️ {self.last_tool_error.tool_name} failed{error_suffix}")
        if self.task_continuity_notice:
            parts.append(self.task_continuity_notice)
        return "\n".join(parts)

    def build_session_parameter_prompt(self):
        values = self.session_parameter
        if not values:
            values = self.session_context
        if not values:
            return ""
        return json.dumps(values, ensure_ascii=False, indent=2)
