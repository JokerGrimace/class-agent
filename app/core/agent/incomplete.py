from app.core.agent.context import AgentContext


INCOMPLETE_TURN_WARNING = "⚠️ Agent couldn't generate a response. Please try again."


def should_show_incomplete_turn_warning(context: AgentContext) -> bool:
    return context.last_tool_error is None


def get_incomplete_turn_warning() -> str:
    return INCOMPLETE_TURN_WARNING
