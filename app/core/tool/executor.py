import asyncio
from copy import deepcopy
from pathlib import Path
from typing import Any, Optional, Tuple

from app.config import settings
from app.core.governance.loader import GovernanceConfigLoader
from app.core.governance.policy import EvaluationContext, GovernancePolicyEngine
from app.core.hook.builtins import inject_workflow_tool_context
from app.core.hook.manager import HookManager
from app.core.hook.types import HookEventName, ToolHookContext
from app.core.permission.service import PermissionService, get_permission_service
from app.core.tool.registry import registry
from app.core.tool.types import ToolCall, ToolError, ToolResult


def _build_default_policy_engine() -> GovernancePolicyEngine:
    rules_path = (
        Path(__file__).resolve().parents[2] / "governance" / "rules" / "default.yaml"
    )
    config = GovernanceConfigLoader(str(rules_path)).load()
    return GovernancePolicyEngine(config)

def _build_default_permission_service() -> PermissionService:
    return get_permission_service()


def _extract_action_name(tool_call: ToolCall, action_source: Optional[str]) -> Optional[str]:
    if not action_source:
        return None
    action_value = tool_call.arguments.get(action_source)
    if isinstance(action_value, str):
        return action_value
    return None


def _build_evaluation_context(tool_def, tool_call: ToolCall) -> EvaluationContext:
    # 获取执行动作名称
    action_name = _extract_action_name(tool_call, tool_def.governance.action_source)
    action_tags = []
    if action_name:
        action_tags = list(tool_def.governance.action_tags.get(action_name, []))
    return EvaluationContext(
        tool_name=tool_call.name,
        tool_tags=list(tool_def.governance.tool_tags),
        action_name=action_name,
        action_tags=action_tags,
        arguments=dict(tool_call.arguments),
    )


def _resolve_tool_action_name(tool_def, tool_call: ToolCall) -> str:
    action_name = _extract_action_name(tool_call, tool_def.governance.action_source)
    if action_name:
        return action_name
    return tool_call.name


class ToolExecutor:
    def __init__(
        self,
        timeout_ms: Optional[int] = None,
        registry=None,
        policy_engine=None,
        permission_service=None,
        hook_manager=None,
    ):
        self.timeout_ms = timeout_ms or settings.tool_timeout_ms
        self.registry = registry or globals()["registry"]
        self._policy_engine = policy_engine
        self._permission_service = permission_service
        self.hook_manager = hook_manager or HookManager()
        # 平台钩子首先运行，因此权限门看到的是标准化的参数。钩子允许操作永远不会跳过下面的治理环节。
        self.hook_manager.register(
            HookEventName.BEFORE_TOOL_USE,
            inject_workflow_tool_context,
            prepend=True,
        )

    @property
    def policy_engine(self) -> GovernancePolicyEngine:
        if self._policy_engine is None:
            self._policy_engine = _build_default_policy_engine()
        return self._policy_engine

    @property
    def permission_service(self) -> PermissionService:
        if self._permission_service is None:
            self._permission_service = _build_default_permission_service()
        return self._permission_service

    async def execute(
        self,
        tool_call: ToolCall,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> ToolResult:
        # 检查工具是否被注册
        tool_def = self.registry.get(tool_call.name)
        if not tool_def:
            return ToolResult(
                success=False,
                error=f"Tool not found: {tool_call.name}",
            )

        try:
            # Execute-phase BeforeToolUse is the last extension point before
            # governance. Any updated arguments are intentionally evaluated by
            # GovernancePolicyEngine and PermissionService below.
            before_hook = await self.hook_manager.trigger(
                ToolHookContext(
                    event_name=HookEventName.BEFORE_TOOL_USE,
                    session_id=session_id,
                    user_id=user_id,
                    tool_call_id=tool_call.id,
                    tool_name=tool_call.name,
                    arguments=deepcopy(tool_call.arguments),
                    meta={"phase": "execute"},
                )
            )
            if not before_hook.continue_execution:
                # A blocking hook denies this tool call only. It does not create
                # a session grant and does not replace the permission system.
                return ToolResult(
                    success=False,
                    error=before_hook.blocking_error or "Tool blocked by hook",
                    meta={
                        "hook_event": HookEventName.BEFORE_TOOL_USE.value,
                        **before_hook.meta,
                    },
                )
            if before_hook.updated_arguments is not None:
                tool_call = ToolCall(
                    id=tool_call.id,
                    name=tool_call.name,
                    arguments=deepcopy(before_hook.updated_arguments),
                )

            tool_action_name = _resolve_tool_action_name(tool_def, tool_call)

            # Session grants are still checked after hooks. This keeps the
            # current OpenCode-style permission gate as the source of truth.
            if self.permission_service.is_session_granted(
                session_id=session_id or "unknown_session",
                tool_name=tool_call.name,
                tool_action=tool_action_name,
            ):
                return await self._execute_tool_definition(
                    tool_def=tool_def,
                    arguments=tool_call.arguments,
                )

            decision = self.policy_engine.evaluate(
                _build_evaluation_context(tool_def, tool_call)
            )
            if decision.decision == "deny":
                return ToolResult(
                    success=False,
                    error=decision.deny_message or "Permission denied",
                    meta={
                        "decision": decision.decision,
                    },
                )

            if decision.decision == "ask":
                visible_arguments = {
                    key: deepcopy(value)
                    for key, value in tool_call.arguments.items()
                    if key in decision.visible_fields
                }
                editable_fields = [
                    field for field in decision.editable_fields if field in visible_arguments
                ]
                # ask waits in the same streaming run. Do not reintroduce the
                # old approval_required / pending_approval_id resume path here.
                reply = await self.permission_service.ask(
                    session_id=session_id or "unknown_session",
                    user_id=user_id or "unknown_user",
                    tool_call_id=tool_call.id,
                    tool_name=tool_call.name,
                    tool_action=tool_action_name,
                    summary=decision.summary,
                    original_arguments=deepcopy(tool_call.arguments),
                    visible_arguments=visible_arguments,
                    editable_fields=editable_fields,
                )
                if reply.action == "reject":
                    return ToolResult(
                        success=False,
                        error=decision.deny_message or "User rejected the operation",
                        meta={"decision": decision.decision},
                    )
                if reply.action == "always":
                    self.permission_service.add_session_grant(
                        session_id=session_id or "unknown_session",
                        tool_name=tool_call.name,
                        tool_action=tool_action_name,
                    )
                    effective_arguments = deepcopy(tool_call.arguments)
                elif reply.action == "once_with_changes":
                    effective_arguments = deepcopy(tool_call.arguments)
                    for key, value in reply.edited_fields.items():
                        if key in editable_fields:
                            effective_arguments[key] = deepcopy(value)
                else:
                    effective_arguments = deepcopy(tool_call.arguments)
                return await self._execute_tool_definition(
                    tool_def=tool_def,
                    arguments=effective_arguments,
                )

            return await self._execute_tool_definition(
                tool_def=tool_def,
                arguments=tool_call.arguments,
            )
        except asyncio.TimeoutError:
            return ToolResult(
                success=False,
                error=f"Tool timed out after {self.timeout_ms}ms",
                timed_out=True,
            )
        except Exception as exc:
            exception_type = type(exc).__name__
            return ToolResult(
                success=False,
                error=f"{exception_type}: {exc}",
                meta={"exception_type": exception_type},
            )

    async def _execute_tool_definition(
        self,
        tool_def,
        arguments: dict[str, Any],
    ) -> ToolResult:
        result = await asyncio.wait_for(
            tool_def.func(**deepcopy(arguments)),
            timeout=self.timeout_ms / 1000,
        )
        if isinstance(result, ToolResult):
            return result
        return ToolResult(success=True, content=str(result))

    async def execute_tool_call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        tool_call_id: Optional[str] = None,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Tuple[ToolResult, Optional[ToolError]]:

        tool_call = ToolCall(
            id=tool_call_id or f"call_{tool_name}",
            name=tool_name,
            arguments=arguments,
        )

        result = await self.execute(
            tool_call,
            session_id=session_id,
            user_id=user_id,
        )

        error: Optional[ToolError] = None
        if not result.success:
            error = ToolError(
                tool_name=tool_name,
                error=result.error or "Unknown error",
                timed_out=result.timed_out,
            )

        # AfterToolUse is observational for now. Callers persist results and
        # update session state in the existing runner path.
        await self.hook_manager.trigger(
            ToolHookContext(
                event_name=HookEventName.AFTER_TOOL_USE,
                session_id=session_id,
                user_id=user_id,
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                arguments=deepcopy(arguments),
                result=result,
                error=error,
            )
        )
        return result, error


executor = ToolExecutor()
