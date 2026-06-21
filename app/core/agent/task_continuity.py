from typing import Optional


TASK_CONTINUITY_REMINDER_BY_REASON = {
    "tool_call_limit": (
        "The previous task has already ended because one tool exceeded its per-task call limit. "
        "Do not continue that terminated task. Treat earlier conversation as background only, "
        "and focus on the latest user request."
    ),
    "max_turns": (
        "The previous task has already ended because it reached the maximum number of turns. "
        "Do not continue that terminated task. Treat earlier conversation as background only, "
        "and focus on the latest user request."
    ),
}

def get_task_continuity_reminder(reason: Optional[str]) -> Optional[str]:
    if not reason:
        return None
    return TASK_CONTINUITY_REMINDER_BY_REASON.get(reason)
