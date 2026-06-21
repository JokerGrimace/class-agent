"""update_plan — track multi-step work progress across turns.

Pure declarative tool: validates the plan structure and persists it
to session state. The engine injects session.plan into the system
prompt so the LLM always sees its current progress.
"""

from app.core.tool.registry import tool
from app.core.tool.types import ToolResult
from app.core.agent.plan import validate_plan_payload


@tool(
    name="update_plan",
    description="Update the structured work plan for this run. Use for non-trivial multi-step work. Keep steps short. At most one step may be in_progress. Skip for simple one-step tasks.",
    parameters={
        "type": "object",
        "properties": {
            "explanation": {
                "type": "string",
                "description": "Optional short note explaining what changed in the plan.",
            },
            "plan": {
                "description": "Either a legacy ordered step list or a strict plan object injected by a skill.",
                "anyOf": [
                    {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {
                                "step": {"type": "string", "description": "Short plan step."},
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "in_progress", "completed"],
                                    "description": "One of pending, in_progress, completed.",
                                },
                            },
                            "required": ["step", "status"],
                        },
                    },
                    {
                        "type": "object",
                        "properties": {
                            "strict": {"type": "boolean"},
                            "steps": {
                                "type": "array",
                                "minItems": 1,
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "id": {"type": "string"},
                                        "title": {"type": "string"},
                                        "status": {
                                            "type": "string",
                                            "enum": ["pending", "in_progress", "completed"],
                                        },
                                        "allowed_tools": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                        },
                                        "completion_mode": {
                                            "type": "string",
                                            "enum": ["explicit"],
                                        },
                                    },
                                    "required": ["id", "title", "status", "allowed_tools", "completion_mode"],
                                },
                            },
                        },
                        "required": ["strict", "steps"],
                    },
                ],
            },
        },
        "required": ["plan"],
    },
)
async def update_plan(plan, explanation: str = "") -> ToolResult:
    error = validate_plan_payload(plan)
    if error:
        return ToolResult(success=False, error=error)

    return ToolResult(
        success=True,
        content="",
        meta={"plan": plan, "explanation": explanation},
    )
