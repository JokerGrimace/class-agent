from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.tool.executor import executor
from app.core.tool.registry import registry
from app.core.tool.types import ToolCall

router = APIRouter(prefix="/tools", tags=["tools"])


class ExecuteToolRequest(BaseModel):
    tool_name: str
    arguments: dict


class ExecuteToolResponse(BaseModel):
    success: bool
    content: str = ""
    error: Optional[str] = None
    timed_out: bool = False


@router.get("")
async def list_tools():
    return {
        "tools": [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            }
            for tool in registry.get_tools()
        ]
    }


@router.post("/execute", response_model=ExecuteToolResponse)
async def execute_tool(req: ExecuteToolRequest):
    tool_call = ToolCall(
        id=f"call_{req.tool_name}",
        name=req.tool_name,
        arguments=req.arguments,
    )

    result = await executor.execute(tool_call)

    return ExecuteToolResponse(
        success=result.success,
        content=result.content,
        error=result.error,
        timed_out=result.timed_out,
    )
