from __future__ import annotations

import json
from typing import Any

from jsonschema import Draft202012Validator

from app.config import settings
from app.core.workflow.models import WorkflowRun, WorkflowTask, WorkflowTaskDefinition

# result_json最大字符上限
# RESULT_JSON_MAX_CHARS = 6000


class WorkflowScheduler:
    # 只选择 pending Task
    # 检查 depends_on
    # summary Task 最后执行
    # 控制并发上限
    def select_ready_batch(
        self,
        run: WorkflowRun,
        tasks: list[WorkflowTask],
    ) -> list[WorkflowTaskDefinition]:
        definition = run.definition_snapshot_json
        task_state = {task.task_id: task for task in tasks}
        summary_task_id = definition.summary_task_id

        ready: list[WorkflowTaskDefinition] = []

        non_summary_complete = all(
            task_state[task_definition.id].status == "completed"
            for task_definition in definition.tasks
            if task_definition.id != summary_task_id
        )

        for task_definition in definition.tasks:
            task = task_state.get(task_definition.id)
            if task is None:
                continue

            if task.status != "pending":
                continue

            if task_definition.id == summary_task_id:
                if non_summary_complete:
                    ready.append(task_definition)
                continue

            dependencies_ready = all(
                task_state[dependency_id].status == "completed"
                for dependency_id in task_definition.depends_on
            )
            if dependencies_ready:
                ready.append(task_definition)

        max_parallel_tasks = (
            definition.max_parallel_tasks or settings.workflow_run_max_parallel_tasks
        )

        return ready[:max_parallel_tasks]

    # 构造task_input
    def build_task_input(
        self,
        run: WorkflowRun,
        task_definition: WorkflowTaskDefinition,
        tasks: list[WorkflowTask],
    ) -> dict[str, Any]:
        task_state = {task.task_id: task for task in tasks}

        task_results: dict[str, Any] = {}
        for dependency_id in task_definition.depends_on:
            dependency_task = task_state.get(dependency_id)
            if dependency_task is None:
                raise ValueError(f"Missing dependency task state: {dependency_id}")
            if dependency_task.result_json is None:
                raise ValueError(f"Missing dependency result_json: {dependency_id}")
            task_results[dependency_id] = dependency_task.result_json

        return {
            "global_context": run.global_context_json,
            "task_results": task_results,
            "constants": task_definition.constants,
        }

    # 校验input格式
    def validate_task_input(
        self,
        task_definition: WorkflowTaskDefinition,
        input_json: dict[str, Any],
    ) -> None:
        self._validate_json_schema(
            schema=task_definition.input_schema,
            payload=input_json,
            task_id=task_definition.id,
            label="input",
        )

    # 校验output格式
    def validate_task_output(
        self,
        task_definition: WorkflowTaskDefinition,
        result_json: dict[str, Any],
    ) -> None:

        self._validate_json_schema(
            schema=task_definition.output_schema,
            payload=result_json,
            task_id=task_definition.id,
            label="output",
        )

    def _validate_json_schema(
        self,
        *,
        schema: dict[str, Any],
        payload: dict[str, Any],
        task_id: str,
        label: str,
    ) -> None:
        validator = Draft202012Validator(schema)
        errors = sorted(
            validator.iter_errors(payload),
            key=lambda item: list(item.path),
        )
        if not errors:
            return

        first_error = errors[0]
        error_path = ".".join(str(part) for part in first_error.path)
        if error_path:
            raise ValueError(
                f"Task {label} validation failed for {task_id} at {error_path}: "
                f"{first_error.message}"
            )

        raise ValueError(
            f"Task {label} validation failed for {task_id}: "
            f"{first_error.message}"
        )