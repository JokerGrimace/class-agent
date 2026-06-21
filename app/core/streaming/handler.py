import json
from typing import Any, AsyncGenerator

from app.core.agent.engine import AgentEvent


class StreamHandler:
    @staticmethod
    def format_sse_event(event: AgentEvent) -> str:
        if event.type == "content":
            return f"event: text\ndata: {json.dumps({'content': event.content})}\n\n"
        elif event.type == "tool_call":
            return f"event: tool_call\ndata: {json.dumps({'tool_name': event.tool_name, 'tool_call_id': event.tool_call_id})}\n\n"
        elif event.type == "tool_result":
            payload = {
                "tool_call_id": event.tool_call_id,
                "tool_name": event.tool_name,
                "meta": (event.tool_result.meta if event.tool_result else {}),
                "error": str(event.error) if event.error else None,
            }
            return f"event: tool_result\ndata: {json.dumps(payload)}\n\n"
        elif event.type == "warning":
            return f"event: warning\ndata: {json.dumps({'message': event.warning})}\n\n"
        elif event.type == "done":
            return "event: done\ndata: {}\n\n"
        return ""

    @staticmethod
    def format_ws_message(event: AgentEvent) -> dict[str, Any]:
        if event.type == "content":
            return {"type": "text", "content": event.content}
        elif event.type == "tool_call":
            return {"type": "tool_call", "tool": event.tool_name, "tool_call_id": event.tool_call_id}
        elif event.type == "tool_result":
            return {
                "type": "tool_result",
                "tool_call_id": event.tool_call_id,
                "tool": event.tool_name,
                "meta": (event.tool_result.meta if event.tool_result else {}),
            }
        elif event.type == "warning":
            return {"type": "warning", "message": event.warning}
        elif event.type == "done":
            return {"type": "done"}
        return {"type": "unknown"}
