from typing import Any, Mapping, Optional

from app.core.workflow.models import WorkflowDefinition, WorkflowRun, WorkflowTask
from app.core.workflow.storage import WorkflowStorage, create_workflow_storage


class WorkflowManager:
    def __init__(self, storage: Optional[WorkflowStorage] = None):
        self.storage = storage or create_workflow_storage()

    def initialize(self) -> None:
        self.storage.initialize()
    #创建一个新的 WorkflowRun，并一次性创建所有初始 Task。
    def create_workflow_run(
        self,
        *,
        workflow_name: str,
        definition: WorkflowDefinition,
        user_id: str,
        session_id: str,
        coordinator_agent_id: str,
        global_context: dict[str, Any],
    ) -> WorkflowRun:
        definition_model = WorkflowDefinition.model_validate(definition)

        run = WorkflowRun(
            workflow_name=workflow_name,
            summary_task_id=definition_model.summary_task_id,
            user_id=user_id,
            session_id=session_id,
            coordinator_agent_id=coordinator_agent_id,
            status="pending",
            global_context_json=dict(global_context or {}),
            definition_snapshot_json=definition_model.model_copy(deep=True),
        )

        tasks = [
            WorkflowTask(
                workflow_run_id=run.workflow_run_id,
                task_id=task_definition.id,
                status="pending",
            )
            for task_definition in definition_model.tasks
        ]

        return self.storage.create_run_with_tasks(run, tasks)
    # 查询某个 WorkflowRun 下所有 Task。
    def get_workflow_run(
        self,
        *,
        workflow_run_id: str,
        user_id: str,
        session_id: str,
        coordinator_agent_id: str,
    ) -> WorkflowRun:
        run = self.storage.get_run(workflow_run_id)
        if run is None:
            raise ValueError(f"WorkflowRun not found: {workflow_run_id}")

        if (
            run.user_id != user_id
            or run.session_id != session_id
            or run.coordinator_agent_id != coordinator_agent_id
        ):
            raise ValueError("WorkflowRun ownership mismatch")

        return run

    # 查询某个 WorkflowRun 下所有 Task。
    def get_workflow_tasks(self, *, workflow_run_id: str) -> list[WorkflowTask]:
        return self.storage.list_tasks(workflow_run_id)

    # 把某个 Task 标记为 completed，并写入 result_json
    def complete_task(
        self,
        *,
        workflow_run_id: str,
        task_id: str,
        result_json: dict[str, Any],
    ) -> WorkflowTask:
        return self.storage.update_task_status(
            workflow_run_id=workflow_run_id,
            task_id=task_id,
            status="completed",
            result_json=result_json,
            error_json=None,
        )

    # 把某个 Task 标记为 failed，并写入 error_json。
    def fail_task(
        self,
        *,
        workflow_run_id: str,
        task_id: str,
        error_json: dict[str, Any],
    ) -> WorkflowTask:
        return self.storage.update_task_status(
            workflow_run_id=workflow_run_id,
            task_id=task_id,
            status="failed",
            result_json=None,
            error_json=error_json,
        )

    # 校验归属
    # 把所有未终态 Task 改成 cancelled
    # 把 WorkflowRun 改成 cancelled
    def cancel_workflow_run(
        self,
        *,
        workflow_run_id: str,
        user_id: str,
        session_id: str,
        coordinator_agent_id: str,
    ) -> WorkflowRun:
        run = self.get_workflow_run(
            workflow_run_id=workflow_run_id,
            user_id=user_id,
            session_id=session_id,
            coordinator_agent_id=coordinator_agent_id,
        )

        tasks = self.storage.list_tasks(workflow_run_id)
        for task in tasks:
            if task.status not in {"completed", "failed", "cancelled"}:
                self.storage.update_task_status(
                    workflow_run_id=workflow_run_id,
                    task_id=task.task_id,
                    status="cancelled",
                    result_json=task.result_json,
                    error_json=task.error_json,
                )

        return self.storage.update_run_status(
            workflow_run_id=run.workflow_run_id,
            status="cancelled",
            error_json=None,
        )

    def get_workflow_run_for_user(
            self,
            *,
            workflow_run_id: str,
            user_id: str,
    ) -> WorkflowRun:
        run = self.storage.get_run(workflow_run_id)
        if run is None:
            raise ValueError(f"WorkflowRun not found: {workflow_run_id}")
        if run.user_id != user_id:
            raise ValueError("WorkflowRun ownership mismatch")
        return run

    def cancel_workflow_run_for_user(
            self,
            *,
            workflow_run_id: str,
            user_id: str,
    ) -> WorkflowRun:
        run = self.get_workflow_run_for_user(
            workflow_run_id=workflow_run_id,
            user_id=user_id,
        )

        tasks = self.storage.list_tasks(workflow_run_id)
        for task in tasks:
            if task.status not in {"completed", "failed", "cancelled"}:
                self.storage.update_task_status(
                    workflow_run_id=workflow_run_id,
                    task_id=task.task_id,
                    status="cancelled",
                    result_json=task.result_json,
                    error_json=task.error_json,
                )

        return self.storage.update_run_status(
            workflow_run_id=run.workflow_run_id,
            status="cancelled",
            error_json=None,
        )

    def get_active_workflow_run(
            self,
            *,
            session_id: str,
            user_id: str,
            coordinator_agent_id: str,
    ) -> Optional[WorkflowRun]:
        return self.storage.get_active_run_for_owner(
            session_id=session_id,
            user_id=user_id,
            coordinator_agent_id=coordinator_agent_id,
        )

    def fail_incomplete_runs_on_startup(self) -> None:
        self.storage.fail_stale_active_runs()