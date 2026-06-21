from enum import Enum


class AgentState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    WAITING_TOOL = "waiting_tool"
    ERROR = "error"
    DONE = "done"
