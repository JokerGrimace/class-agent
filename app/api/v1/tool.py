from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.auth import AuthenticatedUser, require_local_test_user
from app.core.tool.executor import executor
from app.core.tool.registry import registry
from app.core.tool.types import ToolCall

# 原始注册表与执行入口只用于本机调试；正式工具调用必须经过 Agent 的页面、会话及权限链路。
router = APIRouter(prefix="/tools", tags=["tools"], dependencies=[Depends(require_local_test_user)])


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
async def execute_tool(req: ExecuteToolRequest, current_user: AuthenticatedUser = Depends(require_local_test_user)):
    tool_call = ToolCall(
        id=f"call_{req.tool_name}",
        name=req.tool_name,
        arguments=req.arguments,
    )

    # 调试调用也保留已校验的身份，不再以匿名用户进入执行器。
    result = await executor.execute(tool_call, user_id=current_user.user_id)

    return ExecuteToolResponse(
        success=result.success,
        content=result.content,
        error=result.error,
        timed_out=result.timed_out,
    )
