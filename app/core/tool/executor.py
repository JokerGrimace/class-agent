import asyncio
import contextlib
import inspect
from copy import deepcopy
from pathlib import Path
from typing import Any, Optional, Tuple

from app.config import settings
from app.core.governance.loader import GovernanceConfigLoader
from app.core.governance.policy import EvaluationContext, GovernancePolicyEngine
from app.core.hook.builtins import (
    inject_workflow_tool_context,
    subscribe_workflow_runtime_events,
    unsubscribe_workflow_runtime_events,
)
from app.core.hook.manager import HookManager
from app.core.hook.types import HookEventName, ToolHookContext
from app.core.permission.service import PermissionService, get_permission_service
from app.core.tool.openapi_mapping_db import OpenApiToolMappingService
from app.core.tool.python_tool_catalog_db import PythonToolCatalogService
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


def _build_default_openapi_mapping_service() -> OpenApiToolMappingService:
    return OpenApiToolMappingService()


def _build_default_python_tool_catalog_service() -> PythonToolCatalogService:
    return PythonToolCatalogService()


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


def _find_missing_required_arguments(parameters: dict[str, Any], arguments: dict[str, Any]) -> list[str]:
    required = parameters.get("required")
    if not isinstance(required, list):
        return []
    return [
        name
        for name in required
        if isinstance(name, str)
        and (name not in arguments or arguments.get(name) is None)
    ]


def _build_missing_required_arguments_result(missing_arguments: list[str]) -> ToolResult:
    missing_text = ", ".join(missing_arguments)
    return ToolResult(
        success=False,
        error=f"Missing required tool argument(s): {missing_text}",
        meta={"missing_required_arguments": missing_arguments},
    )


def _snake_to_camel(value: str) -> str:
    parts = [part for part in value.split("_") if part]
    if not parts:
        return ""
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])


def _read_permission_argument(arguments: dict[str, Any], field: str) -> tuple[bool, Any]:
    if field in arguments:
        return True, deepcopy(arguments[field])

    body = arguments.get("body")
    if not isinstance(body, dict):
        return False, None

    if field in body:
        return True, deepcopy(body[field])

    body_field = _snake_to_camel(field)
    if body_field and body_field in body:
        return True, deepcopy(body[body_field])

    return False, None


def _build_visible_permission_arguments(
    arguments: dict[str, Any],
    visible_fields: list[str],
) -> dict[str, Any]:
    visible_arguments: dict[str, Any] = {}
    for field in visible_fields:
        found, value = _read_permission_argument(arguments, field)
        if found:
            visible_arguments[field] = value
    return visible_arguments


def _apply_permission_edited_fields(
    arguments: dict[str, Any],
    editable_fields: list[str],
    edited_fields: dict[str, Any],
) -> dict[str, Any]:
    effective_arguments = deepcopy(arguments)
    body = effective_arguments.get("body")

    for key, value in edited_fields.items():
        if key not in editable_fields:
            continue
        if key in effective_arguments:
            effective_arguments[key] = deepcopy(value)
            continue
        if not isinstance(body, dict):
            continue
        if key in body:
            body[key] = deepcopy(value)
            continue
        body_key = _snake_to_camel(key)
        if body_key and body_key in body:
            body[body_key] = deepcopy(value)

    return effective_arguments


class ToolExecutor:
    def __init__(
        self,
        timeout_ms: Optional[int] = None,
        registry=None,
        policy_engine=None,
        permission_service=None,
        openapi_mapping_service=None,
        python_tool_catalog_service=None,
        hook_manager=None,
    ):
        self.timeout_ms = timeout_ms or settings.tool_timeout_ms
        self.registry = registry or globals()["registry"]
        self._policy_engine = policy_engine
        self._permission_service = permission_service
        self._openapi_mapping_service = openapi_mapping_service
        self._python_tool_catalog_service = python_tool_catalog_service
        self.hook_manager = hook_manager or HookManager()
        # 平台钩子首先运行，因此权限门看到的是标准化的参数。钩子允许操作永远不会跳过下面的治理环节。
        self.hook_manager.register(
            HookEventName.BEFORE_TOOL_USE,
            inject_workflow_tool_context,
            prepend=True,
        )
        self.hook_manager.register(
            HookEventName.BEFORE_TOOL_USE,
            subscribe_workflow_runtime_events,
        )
        self.hook_manager.register(
            HookEventName.AFTER_TOOL_USE,
            unsubscribe_workflow_runtime_events,
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

    @property
    def openapi_mapping_service(self) -> OpenApiToolMappingService:
        if self._openapi_mapping_service is None:
            self._openapi_mapping_service = _build_default_openapi_mapping_service()
        return self._openapi_mapping_service

    @property
    def python_tool_catalog_service(self) -> PythonToolCatalogService:
        if self._python_tool_catalog_service is None:
            self._python_tool_catalog_service = _build_default_python_tool_catalog_service()
        return self._python_tool_catalog_service

    async def execute(
        self,
        tool_call: ToolCall,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        run_state: Optional[Any] = None
    ) -> ToolResult:
        # 检查工具是否被注册
        tool_def = self.registry.get(tool_call.name)
        if not tool_def:
            return ToolResult(
                success=False,
                error=f"Tool not found: {tool_call.name}",
            )
        if not self._is_tool_available_on_current_page(tool_call.name, run_state):
            return ToolResult(
                success=False,
                error=f"Tool not available on current page: {tool_call.name}",
                meta={"decision": "deny", "reason": "tool_not_available_on_page"},
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
                    meta={"phase": "execute","run_state": run_state},
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

            # Tool schemas are exposed to the model, but the model can still
            # emit incomplete arguments. Validate required top-level arguments
            # before governance or Python invocation so the agent can retry with
            # a structured tool error instead of a raw TypeError.
            missing_arguments = _find_missing_required_arguments(
                tool_def.parameters,
                tool_call.arguments,
            )
            if missing_arguments:
                return _build_missing_required_arguments_result(missing_arguments)

            tool_action_name = _resolve_tool_action_name(tool_def, tool_call)
            current_workflow_page = getattr(run_state, "current_workflow_page", None)

            # Session grants are still checked after hooks. This keeps the
            # current OpenCode-style permission gate as the source of truth.
            if self.permission_service.is_session_granted(
                session_id=session_id or "unknown_session",
                tool_name=tool_call.name,
                tool_action=tool_action_name,
                page_name=current_workflow_page,
            ):
                return await self._execute_tool_definition(
                    tool_def=tool_def,
                    arguments=tool_call.arguments,
                    run_state=run_state,
                )

            page_permission = self._get_page_permission(
                tool_name=tool_call.name,
                current_workflow_page=current_workflow_page,
            )
            if page_permission is not None:
                return await self._execute_with_page_permission(
                    page_permission=page_permission,
                    tool_def=tool_def,
                    tool_call=tool_call,
                    tool_action_name=tool_action_name,
                    session_id=session_id,
                    user_id=user_id,
                    current_workflow_page=current_workflow_page,
                    run_state=run_state,
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
                visible_arguments = _build_visible_permission_arguments(
                    tool_call.arguments,
                    decision.visible_fields,
                )
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
                    tool_display_name=None,
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
                        meta={"decision": decision.decision,"permission_reply_action": "reject",},
                    )
                if reply.action == "always":
                    self.permission_service.add_session_grant(
                        session_id=session_id or "unknown_session",
                        tool_name=tool_call.name,
                        tool_action=tool_action_name,
                        page_name=current_workflow_page,
                    )
                    effective_arguments = deepcopy(tool_call.arguments)
                elif reply.action == "once_with_changes":
                    effective_arguments = _apply_permission_edited_fields(
                        tool_call.arguments,
                        editable_fields,
                        reply.edited_fields,
                    )
                else:
                    effective_arguments = deepcopy(tool_call.arguments)
                return await self._execute_tool_definition(
                    tool_def=tool_def,
                    arguments=effective_arguments,
                    run_state=run_state,
                )

            return await self._execute_tool_definition(
                tool_def=tool_def,
                arguments=tool_call.arguments,
                run_state=run_state,
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
        run_state: Optional[Any] = None,
    ) -> ToolResult:
        effective_arguments = deepcopy(arguments)
        signature = inspect.signature(tool_def.func)
        if "__run_state" in signature.parameters:
            effective_arguments["__run_state"] = run_state
        result = await asyncio.wait_for(
            tool_def.func(**effective_arguments),
            timeout=self.timeout_ms / 1000,
        )
        if isinstance(result, ToolResult):
            return result
        return ToolResult(success=True, content=str(result))

    @staticmethod
    def _is_tool_available_on_current_page(
        tool_name: str,
        run_state: Optional[Any],
    ) -> bool:
        if run_state is None:
            return True
        available_tool_names = getattr(run_state, "available_tool_names", None)
        if available_tool_names is None:
            return True
        return tool_name in set(available_tool_names)

    def _get_page_permission(
        self,
        *,
        tool_name: str,
        current_workflow_page: Optional[str],
    ) -> Optional[dict[str, Any]]:
        normalized_page = (current_workflow_page or "").strip()

        page_permissions: list[dict[str, Any]] = []

        openapi_permission = self.openapi_mapping_service.get_page_permission(
            tool_name,
            normalized_page,
        )
        if openapi_permission is not None:
            page_permissions.append(openapi_permission)

        python_permission = self.python_tool_catalog_service.get_page_permission(
            tool_name,
            normalized_page,
        )
        if python_permission is not None:
            page_permissions.append(python_permission)

        return page_permissions[0] if page_permissions else None

    async def _execute_with_page_permission(
        self,
        *,
        page_permission: dict[str, Any],
        tool_def,
        tool_call: ToolCall,
        tool_action_name: str,
        session_id: Optional[str],
        user_id: Optional[str],
        current_workflow_page: Optional[str],
        run_state: Optional[Any],
    ) -> ToolResult:
        decision = str(page_permission.get("permission_decision") or "allow").strip().lower()
        if decision == "deny":
            return ToolResult(
                success=False,
                error=str(page_permission.get("permission_deny_message") or "Permission denied"),
                meta={"decision": "deny"},
            )
        if decision == "allow":
            return await self._execute_tool_definition_with_page_permission(
                tool_def,
                tool_call.arguments,
                run_state,
                page_permission,
            )

        visible_fields = list(page_permission.get("visible_fields_json") or [])
        editable_fields = list(page_permission.get("editable_fields_json") or [])
        visible_arguments = _build_visible_permission_arguments(
            tool_call.arguments,
            visible_fields,
        )
        editable_fields = [
            field for field in editable_fields if field in visible_arguments
        ]
        reply = await self.permission_service.ask(
            session_id=session_id or "unknown_session",
            user_id=user_id or "unknown_user",
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            tool_display_name=str(page_permission.get("tool_display_name") or tool_call.name),
            tool_action=tool_action_name,
            summary=str(page_permission.get("permission_summary") or ""),
            original_arguments=deepcopy(tool_call.arguments),
            visible_arguments=visible_arguments,
            editable_fields=editable_fields,
        )
        if reply.action == "reject":
            return ToolResult(
                success=False,
                error=str(
                    page_permission.get("permission_deny_message")
                    or "User rejected the operation"
                ),
                meta={"decision": "ask", "permission_reply_action": "reject"},
            )
        if reply.action == "always":
            self.permission_service.add_session_grant(
                session_id=session_id or "unknown_session",
                tool_name=tool_call.name,
                tool_action=tool_action_name,
                page_name=current_workflow_page,
            )
            effective_arguments = deepcopy(tool_call.arguments)
        elif reply.action == "once_with_changes":
            effective_arguments = _apply_permission_edited_fields(
                tool_call.arguments,
                editable_fields,
                reply.edited_fields,
            )
        else:
            effective_arguments = deepcopy(tool_call.arguments)
        return await self._execute_tool_definition_with_page_permission(
            tool_def,
            effective_arguments,
            run_state,
            page_permission,
        )

    async def _execute_tool_definition_with_page_permission(
        self,
        tool_def,
        arguments: dict[str, Any],
        run_state: Optional[Any],
        page_permission: dict[str, Any],
    ) -> ToolResult:
        if run_state is None:
            result = await self._execute_tool_definition(
                tool_def=tool_def,
                arguments=arguments,
                run_state=None,
            )
            return self._append_page_permission_result_meta(result, page_permission)
        previous_permission = getattr(run_state, "current_tool_page_permission", None)
        setattr(run_state, "current_tool_page_permission", page_permission)
        try:
            result = await self._execute_tool_definition(
                tool_def=tool_def,
                arguments=arguments,
                run_state=run_state,
            )
            return self._append_page_permission_result_meta(result, page_permission)
        finally:
            if previous_permission is None:
                with contextlib.suppress(AttributeError):
                    delattr(run_state, "current_tool_page_permission")
            else:
                setattr(run_state, "current_tool_page_permission", previous_permission)

    @staticmethod
    def _append_page_permission_result_meta(
        result: ToolResult,
        page_permission: dict[str, Any],
    ) -> ToolResult:
        tool_display_name = str(page_permission.get("tool_display_name") or "").strip()
        if tool_display_name and not result.meta.get("tool_display_name"):
            result.meta = {**result.meta, "tool_display_name": tool_display_name}
        return result

    async def execute_tool_call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        tool_call_id: Optional[str] = None,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        run_state: Optional[Any] = None
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
            run_state = run_state
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
                meta={"phase": "observe","run_state": run_state},
            )
        )
        return result, error


executor = ToolExecutor()
