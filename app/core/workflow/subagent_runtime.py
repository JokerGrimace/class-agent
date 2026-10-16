from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from typing import Any, Callable, Literal, Optional
from uuid import uuid4

from jsonschema import Draft202012Validator

from app.config import settings
from app.core.agent.run_state import AgentEvent
from app.core.llm.adapter import LLMAdapter, Message
from app.core.llm.factory import create_llm_adapter
from app.core.llm.stream_parser import LLMToolCallExecutor
from app.core.permission.service import PermissionService, get_permission_service
from app.core.session.models import Session
from app.core.tool.executor import ToolExecutor
from app.core.tool.registry import ToolRegistry, registry
from app.core.tool.types import ToolResult
from app.core.workflow.models import WorkflowRun, WorkflowTask, WorkflowTaskDefinition
from app.core.workflow.runtime_events import WorkflowRuntimeEvent, workflow_runtime_events


MAX_SUBAGENT_ATTEMPTS = 3


COMPLETE_TASK_PARAMETERS = {
    "type": "object",
    "properties": {
        "result_json": {"type": "object"},
    },
    "required": ["result_json"],
    "additionalProperties": False,
}

FAIL_TASK_ERROR_SCHEMA = {
    "type": "object",
    "properties": {
        "error_type": {"type": "string", "minLength": 1},
        "message": {"type": "string", "minLength": 1},
    },
    "required": ["error_type", "message"],
    "additionalProperties": False,
}

FAIL_TASK_PARAMETERS = {
    "type": "object",
    "properties": {
        "error_json": FAIL_TASK_ERROR_SCHEMA,
    },
    "required": ["error_json"],
    "additionalProperties": False,
}


@dataclass
class SubagentExecutionResult:
    status: Literal["completed", "failed"]
    attempt: int
    result_json: Optional[dict[str, Any]] = None
    error_json: Optional[dict[str, Any]] = None
    permission_rejected: bool = False


@dataclass
class DispatchResult:
    workflow_run_id: str
    status: Literal["running", "completed"]
    completed_task_ids: list[str]
    started_task_ids: list[str]
    summary_task_id: Optional[str] = None
    result_json: Optional[dict[str, Any]] = None


class WorkflowTerminalFailure(RuntimeError):
    def __init__(self, task_id: str, error_json: dict[str, Any]):
        super().__init__(error_json.get("message") or "Workflow task failed")
        self.task_id = task_id
        self.error_json = error_json


def build_task_error(
    *,
    error_type: str,
    task_id: str,
    attempt: int,
    message: str,
    validation_errors: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    return {
        "error_type": error_type,
        "task_id": task_id,
        "attempt": attempt,
        "message": message,
        "validation_errors": list(validation_errors or []),
    }


class WorkflowSubagentRuntime:
    def __init__(
        self,
        *,
        manager=None,
        scheduler=None,
        global_registry: Optional[ToolRegistry] = None,
        permission_service: Optional[PermissionService] = None,
        llm_factory: Optional[Callable[[], LLMAdapter]] = None,
        events=None,
    ):
        self.manager = manager
        self.scheduler = scheduler
        self.global_registry = global_registry or registry
        self.permission_service = permission_service or get_permission_service()
        self.llm_factory = llm_factory or (
            lambda: create_llm_adapter(settings.llm_provider)
        )
        self.events = events or workflow_runtime_events
        self._active_subagents: dict[tuple[str, str], str] = {}
        self._cancellation_events: dict[tuple[str, str], asyncio.Event] = {}
        self._last_attempts: dict[tuple[str, str], int] = {}

    def _require_manager(self):
        if self.manager is None:
            raise RuntimeError(
                "WorkflowSubagentRuntime.manager is required for batch execution"
            )
        return self.manager

    def _require_scheduler(self):
        if self.scheduler is None:
            raise RuntimeError(
                "WorkflowSubagentRuntime.scheduler is required for batch execution"
            )
        return self.scheduler

    @staticmethod
    def _task_key(workflow_run_id: str, task_id: str) -> tuple[str, str]:
        return workflow_run_id, task_id

    def _set_active_subagent(
        self,
        workflow_run_id: str,
        task_id: str,
        subagent_id: str,
        cancellation_event: asyncio.Event,
        attempt: int,
    ) -> None:
        key = self._task_key(workflow_run_id, task_id)
        self._active_subagents[key] = subagent_id
        self._cancellation_events[key] = cancellation_event
        self._last_attempts[key] = attempt

    def _clear_active_subagent(
        self,
        workflow_run_id: str,
        task_id: str,
        subagent_id: str,
    ) -> None:
        key = self._task_key(workflow_run_id, task_id)
        if self._active_subagents.get(key) == subagent_id:
            self._active_subagents.pop(key, None)
            self._cancellation_events.pop(key, None)

    def _is_active_subagent(
        self,
        workflow_run_id: str,
        task_id: str,
        subagent_id: str,
    ) -> bool:
        return (
            self._active_subagents.get(self._task_key(workflow_run_id, task_id))
            == subagent_id
        )

    def _build_subagent_system_prompt(
        self,
        task_definition: WorkflowTaskDefinition,
    ) -> str:
        return (
            "You are an isolated workflow subagent.\n"
            "You execute exactly one workflow task.\n"
            "Use only the provided task package and approved tools.\n"
            "When the task is successfully finished, call complete_task with result_json.\n"
            "If the task cannot be completed, call fail_task with error_json.\n"
            "Do not ask for additional user input.\n"
            f"Current task id: {task_definition.id}\n"
            f"Current task show name: {task_definition.show_name}\n"
        )

    def _build_validation_errors(
        self,
        errors,
    ) -> list[dict[str, Any]]:
        return [
            {
                "path": list(error.absolute_path),
                "schema_path": list(error.absolute_schema_path),
                "validator": str(error.validator),
                "message": error.message,
            }
            for error in errors
        ]

    def _copy_allowed_tools(
        self,
        task_definition: WorkflowTaskDefinition,
    ) -> ToolRegistry:
        subagent_registry = ToolRegistry()

        for tool_name in task_definition.allowed_tools:
            tool_definition = self.global_registry.get(tool_name)
            if tool_definition is None:
                raise ValueError(
                    f"Workflow task allowed_tools contains unknown tool: {tool_name}"
                )

            subagent_registry.register(
                name=tool_definition.name,
                description=tool_definition.description,
                parameters=tool_definition.parameters,
                func=tool_definition.func,
                governance=asdict(tool_definition.governance),
            )

        return subagent_registry

    def _build_tool_executor(
        self,
        subagent_registry: ToolRegistry,
    ) -> ToolExecutor:
        return ToolExecutor(
            registry=subagent_registry,
            permission_service=self.permission_service,
        )

    def _get_task_definition(
        self,
        run: WorkflowRun,
        task_id: str,
    ) -> WorkflowTaskDefinition:
        for task_definition in run.definition_snapshot_json.tasks:
            if task_definition.id == task_id:
                return task_definition
        raise ValueError(f"Workflow task definition not found: {task_id}")

    def _get_run(self, workflow_run_id: str) -> WorkflowRun:
        run = self._require_manager().storage.get_run(workflow_run_id)
        if run is None:
            raise ValueError(f"WorkflowRun not found: {workflow_run_id}")
        return run

    def _get_tasks(self, workflow_run_id: str) -> list[WorkflowTask]:
        return self._require_manager().get_workflow_tasks(
            workflow_run_id=workflow_run_id
        )

    def _set_run_status(
        self,
        workflow_run_id: str,
        status: Literal["pending", "running", "completed", "failed", "cancelled"],
        error_json: Optional[dict[str, Any]] = None,
    ) -> WorkflowRun:
        return self._require_manager().storage.update_run_status(
            workflow_run_id=workflow_run_id,
            status=status,
            error_json=error_json,
        )

    def _set_task_status(
        self,
        workflow_run_id: str,
        task_id: str,
        status: Literal["pending", "running", "completed", "failed", "cancelled"],
        result_json: Optional[dict[str, Any]] = None,
        error_json: Optional[dict[str, Any]] = None,
    ) -> WorkflowTask:
        return self._require_manager().storage.update_task_status(
            workflow_run_id=workflow_run_id,
            task_id=task_id,
            status=status,
            result_json=result_json,
            error_json=error_json,
        )

    def _complete_run_if_all_tasks_completed(self, workflow_run_id: str) -> WorkflowRun:
        tasks = self._get_tasks(workflow_run_id)
        if all(task.status == "completed" for task in tasks):
            return self._set_run_status(
                workflow_run_id=workflow_run_id,
                status="completed",
                error_json=None,
            )

        run = self._get_run(workflow_run_id)
        if run.status != "running":
            return self._set_run_status(
                workflow_run_id=workflow_run_id,
                status="running",
                error_json=None,
            )
        return run

    async def _publish_status(
        self,
        run: WorkflowRun,
        task_definition: WorkflowTaskDefinition,
        status: Literal["running", "retrying", "completed", "failed", "cancelled"],
        attempt: int,
    ) -> None:
        await self.events.publish(
            run.workflow_run_id,
            WorkflowRuntimeEvent(
                workflow_run_id=run.workflow_run_id,
                workflow_name=run.workflow_name,
                task_id=task_definition.id,
                show_name=task_definition.show_name,
                status=status,
                attempt=attempt,
                max_attempts=MAX_SUBAGENT_ATTEMPTS,
            ),
        )

    async def _publish_permission_asked(
        self,
        run: WorkflowRun,
        runtime_event,
    ) -> None:
        await self.events.publish(
            run.workflow_run_id,
            AgentEvent(
                type="permission_asked",
                content="",
                tool_call_id=runtime_event.tool_call_id,
                tool_name=runtime_event.tool_name,
                meta=dict(runtime_event.meta or {}),
            ),
        )

    async def _cancel_active_attempts(
        self,
        workflow_run_id: str,
        exclude_task_id: Optional[str] = None,
    ) -> None:
        for (active_run_id, active_task_id), cancellation_event in list(
            self._cancellation_events.items()
        ):
            if active_run_id != workflow_run_id:
                continue
            if exclude_task_id is not None and active_task_id == exclude_task_id:
                continue
            cancellation_event.set()

    async def _cancel_unfinished_tasks(
        self,
        run: WorkflowRun,
        failed_task_id: str,
    ) -> None:
        for task in self._get_tasks(run.workflow_run_id):
            if task.task_id == failed_task_id:
                continue
            if task.status in {"completed", "failed", "cancelled"}:
                continue

            self._set_task_status(
                workflow_run_id=run.workflow_run_id,
                task_id=task.task_id,
                status="cancelled",
                result_json=task.result_json,
                error_json=task.error_json,
            )
            await self._publish_status(
                run,
                self._get_task_definition(run, task.task_id),
                "cancelled",
                self._last_attempts.get(
                    self._task_key(run.workflow_run_id, task.task_id),
                    0,
                ),
            )

    async def dispatch_batch(
        self,
        *,
        run: WorkflowRun,
        tasks: list[WorkflowTask],
    ) -> DispatchResult:
        manager = self._require_manager()
        scheduler = self._require_scheduler()

        ready = scheduler.select_ready_batch(run, tasks)
        if not ready:
            raise RuntimeError("Workflow has no ready tasks")

        self._set_run_status(run.workflow_run_id, "running", error_json=None)

        executions = {
            task_definition.id: asyncio.create_task(
                self._execute_with_retries(run, task_definition, tasks)
            )
            for task_definition in ready
        }

        try:
            results = await asyncio.gather(*executions.values())
        except WorkflowTerminalFailure as exc:
            await self._cancel_active_attempts(
                run.workflow_run_id,
                exclude_task_id=exc.task_id,
            )

            for task_id, execution in executions.items():
                if task_id == exc.task_id:
                    continue
                if not execution.done():
                    execution.cancel()

            await asyncio.gather(*executions.values(), return_exceptions=True)

            manager.fail_task(
                workflow_run_id=run.workflow_run_id,
                task_id=exc.task_id,
                error_json=exc.error_json,
            )
            await self._publish_status(
                run,
                self._get_task_definition(run, exc.task_id),
                "failed",
                int(
                    exc.error_json.get(
                        "attempt",
                        self._last_attempts.get(
                            self._task_key(run.workflow_run_id, exc.task_id),
                            0,
                        ),
                    )
                ),
            )
            await self._cancel_unfinished_tasks(run, exc.task_id)
            self._set_run_status(run.workflow_run_id, "failed", exc.error_json)
            raise

        completed_task_ids: list[str] = []
        started_task_ids = [task_definition.id for task_definition in ready]

        for task_definition, result in zip(ready, results):
            manager.complete_task(
                workflow_run_id=run.workflow_run_id,
                task_id=task_definition.id,
                result_json=result.result_json or {},
            )
            completed_task_ids.append(task_definition.id)
            await self._publish_status(
                run,
                task_definition,
                "completed",
                result.attempt,
            )

        updated_run = self._complete_run_if_all_tasks_completed(run.workflow_run_id)
        if updated_run.status == "completed":
            summary_task = next(
                task
                for task in self._get_tasks(run.workflow_run_id)
                if task.task_id == updated_run.summary_task_id
            )
            return DispatchResult(
                workflow_run_id=updated_run.workflow_run_id,
                status="completed",
                completed_task_ids=completed_task_ids,
                started_task_ids=started_task_ids,
                summary_task_id=updated_run.summary_task_id,
                result_json=summary_task.result_json,
            )

        return DispatchResult(
            workflow_run_id=updated_run.workflow_run_id,
            status="running",
            completed_task_ids=completed_task_ids,
            started_task_ids=started_task_ids,
        )

    async def _execute_with_retries(
        self,
        run: WorkflowRun,
        task_definition: WorkflowTaskDefinition,
        tasks: list[WorkflowTask],
    ) -> SubagentExecutionResult:
        scheduler = self._require_scheduler()

        try:
            input_json = scheduler.build_task_input(run, task_definition, tasks)
            scheduler.validate_task_input(task_definition, input_json)
        except ValueError as exc:
            raise WorkflowTerminalFailure(
                task_definition.id,
                build_task_error(
                    error_type="INPUT_SCHEMA_VALIDATION",
                    task_id=task_definition.id,
                    attempt=0,
                    message=str(exc),
                ),
            ) from exc

        previous_error: Optional[dict[str, Any]] = None

        for attempt in range(1, MAX_SUBAGENT_ATTEMPTS + 1):
            subagent_id = f"sub_{uuid4().hex[:16]}"
            cancellation_event = asyncio.Event()
            self._set_active_subagent(
                run.workflow_run_id,
                task_definition.id,
                subagent_id,
                cancellation_event,
                attempt,
            )

            if attempt > 1:
                await self._publish_status(run, task_definition, "retrying", attempt)

            self._set_task_status(
                workflow_run_id=run.workflow_run_id,
                task_id=task_definition.id,
                status="running",
                result_json=None,
                error_json=None,
            )
            await self._publish_status(run, task_definition, "running", attempt)

            try:
                result = await asyncio.wait_for(
                    self.execute_task(
                        run,
                        task_definition,
                        input_json,
                        subagent_id=subagent_id,
                        attempt=attempt,
                        previous_error=previous_error,
                        cancellation_event=cancellation_event,
                    ),
                    timeout=settings.workflow_subagent_timeout_seconds,
                )
            except asyncio.TimeoutError:
                result = SubagentExecutionResult(
                    status="failed",
                    attempt=attempt,
                    error_json=build_task_error(
                        error_type="SUBAGENT_TIMEOUT",
                        task_id=task_definition.id,
                        attempt=attempt,
                        message=(
                            "Subagent timed out after "
                            f"{settings.workflow_subagent_timeout_seconds} seconds"
                        ),
                    ),
                )
            except asyncio.CancelledError:
                raise
            finally:
                self._clear_active_subagent(
                    run.workflow_run_id,
                    task_definition.id,
                    subagent_id,
                )

            if result.status == "completed":
                return result

            if result.permission_rejected:
                raise WorkflowTerminalFailure(
                    task_definition.id,
                    result.error_json
                    or build_task_error(
                        error_type="PERMISSION_REJECTED",
                        task_id=task_definition.id,
                        attempt=attempt,
                        message="User rejected the operation",
                    ),
                )

            previous_error = result.error_json

        raise WorkflowTerminalFailure(
            task_definition.id,
            previous_error
            or build_task_error(
                error_type="TASK_EXECUTION_FAILED",
                task_id=task_definition.id,
                attempt=MAX_SUBAGENT_ATTEMPTS,
                message="Task failed after maximum retry attempts",
            ),
        )

    async def execute_task(
        self,
        run: WorkflowRun,
        task_definition: WorkflowTaskDefinition,
        input_json: dict[str, Any],
        *,
        subagent_id: str = "sub_1",
        attempt: int = 1,
        previous_error: Optional[dict[str, Any]] = None,
        cancellation_event: Optional[asyncio.Event] = None,
    ) -> SubagentExecutionResult:
        subagent_session = Session(
            session_id=run.session_id,
            user_id=run.user_id,
            agent_id=subagent_id,
        )

        subagent_registry = self._copy_allowed_tools(task_definition)
        terminal_result: dict[str, Optional[SubagentExecutionResult]] = {"value": None}

        output_validator = Draft202012Validator(task_definition.output_schema)
        fail_validator = Draft202012Validator(FAIL_TASK_ERROR_SCHEMA)

        async def complete_task(result_json: dict[str, Any]) -> ToolResult:
            if not self._is_active_subagent(
                run.workflow_run_id,
                task_definition.id,
                subagent_id,
            ):
                return ToolResult(
                    success=False,
                    error="Stale subagent submission",
                    meta={"error_type": "STALE_SUBAGENT_SUBMISSION"},
                )

            if terminal_result["value"] is not None:
                return ToolResult(
                    success=False,
                    error="Terminal submission already received",
                )

            errors = list(output_validator.iter_errors(result_json))
            if errors:
                validation_errors = self._build_validation_errors(errors)
                error_json = build_task_error(
                    error_type="OUTPUT_SCHEMA_VALIDATION",
                    task_id=task_definition.id,
                    attempt=attempt,
                    message=validation_errors[0]["message"],
                    validation_errors=validation_errors,
                )
                return ToolResult(
                    success=False,
                    error=error_json["message"],
                    meta={"error_json": error_json},
                )

            terminal_result["value"] = SubagentExecutionResult(
                status="completed",
                attempt=attempt,
                result_json=result_json,
            )
            return ToolResult(
                success=True,
                content=json.dumps({"status": "completed"}, ensure_ascii=False),
                meta={
                    "workflow_run_id": run.workflow_run_id,
                    "task_id": task_definition.id,
                    "subagent_id": subagent_id,
                    "result_json": result_json,
                },
            )

        async def fail_task(error_json: dict[str, Any]) -> ToolResult:
            if not self._is_active_subagent(
                run.workflow_run_id,
                task_definition.id,
                subagent_id,
            ):
                return ToolResult(
                    success=False,
                    error="Stale subagent submission",
                    meta={"error_type": "STALE_SUBAGENT_SUBMISSION"},
                )

            if terminal_result["value"] is not None:
                return ToolResult(
                    success=False,
                    error="Terminal submission already received",
                )

            errors = list(fail_validator.iter_errors(error_json))
            if errors:
                normalized_error = build_task_error(
                    error_type="INVALID_FAIL_TASK_ERROR",
                    task_id=task_definition.id,
                    attempt=attempt,
                    message="fail_task.error_json must contain only error_type and message",
                )
            else:
                normalized_error = build_task_error(
                    error_type=error_json["error_type"],
                    task_id=task_definition.id,
                    attempt=attempt,
                    message=error_json["message"],
                )

            terminal_result["value"] = SubagentExecutionResult(
                status="failed",
                attempt=attempt,
                error_json=normalized_error,
            )
            return ToolResult(
                success=True,
                content=json.dumps({"status": "failed"}, ensure_ascii=False),
                meta={
                    "workflow_run_id": run.workflow_run_id,
                    "task_id": task_definition.id,
                    "subagent_id": subagent_id,
                    "error_json": normalized_error,
                },
            )

        subagent_registry.register(
            name="complete_task",
            description="Submit the final successful task result.",
            parameters=COMPLETE_TASK_PARAMETERS,
            func=complete_task,
            governance={"tool_tags": []},
        )
        subagent_registry.register(
            name="fail_task",
            description="Submit the final failed task result.",
            parameters=FAIL_TASK_PARAMETERS,
            func=fail_task,
            governance={"tool_tags": []},
        )

        tool_schemas = subagent_registry.get_tools_schema()
        tool_executor = self._build_tool_executor(subagent_registry)
        subagent_llm = self.llm_factory()

        messages = [
            Message(
                role="system",
                content=self._build_subagent_system_prompt(task_definition),
            ),
            Message(
                role="user",
                content=json.dumps(
                    {
                        "instruction": task_definition.instruction,
                        "input_json": input_json,
                        "output_schema": task_definition.output_schema,
                        "attempt": attempt,
                        "previous_error": previous_error,
                    },
                    ensure_ascii=False,
                ),
            ),
        ]

        loop = LLMToolCallExecutor(
            subagent_llm,
            subagent_session,
            tool_executor=tool_executor,
            persist_messages=False,
            cancellation_event=cancellation_event,
        )

        async for stream_event in loop.run_until_done(
            session=subagent_session,
            messages=messages,
            tools=tool_schemas,
            stream=True,
            max_turns=settings.workflow_subagent_max_turns,
        ):
            if stream_event.type != "runtime":
                continue

            runtime_event = stream_event.runtime_event
            if runtime_event is None:
                continue

            if runtime_event.type == "permission_asked":
                await self._publish_permission_asked(run, runtime_event)
                continue

            if runtime_event.type != "tool_result":
                continue

            if runtime_event.tool_name not in {"complete_task", "fail_task"}:
                tool_result = runtime_event.tool_result
                if (
                    tool_result is not None
                    and tool_result.meta.get("permission_reply_action") == "reject"
                ):
                    return SubagentExecutionResult(
                        status="failed",
                        attempt=attempt,
                        permission_rejected=True,
                        error_json=build_task_error(
                            error_type="PERMISSION_REJECTED",
                            task_id=task_definition.id,
                            attempt=attempt,
                            message=tool_result.error
                            or (
                                runtime_event.error.error
                                if runtime_event.error
                                else "User rejected the operation"
                            ),
                        ),
                    )
                continue

            if terminal_result["value"] is not None:
                return terminal_result["value"]

        if cancellation_event is not None and cancellation_event.is_set():
            raise asyncio.CancelledError()

        if terminal_result["value"] is not None:
            return terminal_result["value"]

        return SubagentExecutionResult(
            status="failed",
            attempt=attempt,
            error_json=build_task_error(
                error_type="SUBAGENT_NO_TERMINAL_SUBMISSION",
                task_id=task_definition.id,
                attempt=attempt,
                message="Subagent ended without complete_task or fail_task",
            ),
        )
