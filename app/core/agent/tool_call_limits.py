import json
from typing import Any


MAX_TOOL_CALLS_PER_TASK = 3
TOOL_UNAVAILABLE_MESSAGE = "Current tool is unavailable. Please continue with the existing information."
TOOL_CALL_LIMIT_ERROR = (
    f"Within one task, the same tool can be called at most "
    f"{MAX_TOOL_CALLS_PER_TASK} times consecutively with the same failing input."
)


def tool_input_signature(arguments: dict[str, Any]) -> str:
    try:
        return json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    except Exception:
        return str(arguments)
