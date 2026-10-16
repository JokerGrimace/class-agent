from __future__ import annotations

from typing import Callable, Optional

from app.core.llm.adapter import LLMAdapter
from app.core.llm.factory import create_llm_adapter
from app.core.permission.service import PermissionService, get_permission_service
from app.core.tool.registry import registry
from app.core.workflow.manager import WorkflowManager
from app.core.workflow.models import WorkflowRun
from app.core.workflow.runtime_events import workflow_runtime_events
from app.core.workflow.scheduler import WorkflowScheduler
from app.core.workflow.storage import WorkflowStorage
from app.core.workflow.subagent_runtime import DispatchResult, WorkflowSubagentRuntime


class WorkflowRuntime:
    def __init__(
        self,
        *,
        manager: WorkflowManager,
        scheduler: WorkflowScheduler,
        subagents: WorkflowSubagentRuntime,
    ):
        self.manager = manager
        self.scheduler = scheduler
        self.subagents = subagents

    async def dispatch_owned(
        self,
        *,
        workflow_run_id: str,
        user_id: str,
        session_id: str,
        coordinator_agent_id: str,
    ) -> DispatchResult:
        run = self.manager.get_workflow_run(
            workflow_run_id=workflow_run_id,
            user_id=user_id,
            session_id=session_id,
            coordinator_agent_id=coordinator_agent_id,
        )
        tasks = self.manager.get_workflow_tasks(workflow_run_id=workflow_run_id)
        return await self.subagents.dispatch_batch(run=run, tasks=tasks)

    async def cancel_owned(
        self,
        *,
        workflow_run_id: str,
        user_id: str,
        session_id: str,
        coordinator_agent_id: str,
    ) -> WorkflowRun:
        self.manager.get_workflow_run(
            workflow_run_id=workflow_run_id,
            user_id=user_id,
            session_id=session_id,
            coordinator_agent_id=coordinator_agent_id,
        )
        await self.subagents._cancel_active_attempts(workflow_run_id)
        return self.manager.cancel_workflow_run(
            workflow_run_id=workflow_run_id,
            user_id=user_id,
            session_id=session_id,
            coordinator_agent_id=coordinator_agent_id,
        )

    async def cancel_for_user(
            self,
            *,
            workflow_run_id: str,
            user_id: str,
    ) -> WorkflowRun:
        run = self.manager.get_workflow_run_for_user(
            workflow_run_id=workflow_run_id,
            user_id=user_id,
        )
        await self.subagents._cancel_active_attempts(workflow_run_id)
        return self.manager.cancel_workflow_run_for_user(
            workflow_run_id=run.workflow_run_id,
            user_id=user_id,
        )

    async def cancel_active_run(
            self,
            *,
            session_id: str,
            user_id: str,
            coordinator_agent_id: str,
    ) -> Optional[WorkflowRun]:
        run = self.manager.get_active_workflow_run(
            session_id=session_id,
            user_id=user_id,
            coordinator_agent_id=coordinator_agent_id,
        )
        if run is None:
            return None
        await self.subagents._cancel_active_attempts(run.workflow_run_id)
        return self.manager.cancel_workflow_run(
            workflow_run_id=run.workflow_run_id,
            user_id=user_id,
            session_id=session_id,
            coordinator_agent_id=coordinator_agent_id,
        )

_workflow_runtime: Optional[WorkflowRuntime] = None


def configure_workflow_runtime(
    *,
    llm_factory: Callable[[], LLMAdapter] = create_llm_adapter,
    permission_service: Optional[PermissionService] = None,
    storage: Optional[WorkflowStorage] = None,
) -> WorkflowRuntime:
    global _workflow_runtime

    manager = WorkflowManager(storage=storage)
    scheduler = WorkflowScheduler()
    subagents = WorkflowSubagentRuntime(
        manager=manager,
        scheduler=scheduler,
        global_registry=registry,
        permission_service=permission_service or get_permission_service(),
        llm_factory=llm_factory,
        events=workflow_runtime_events,
    )
    _workflow_runtime = WorkflowRuntime(
        manager=manager,
        scheduler=scheduler,
        subagents=subagents,
    )
    return _workflow_runtime


def get_workflow_runtime() -> WorkflowRuntime:
    if _workflow_runtime is None:
        return configure_workflow_runtime()
    return _workflow_runtime
