import json
from typing import Any, Optional

from app.core.tool.registry import tool
from app.core.tool.types import ToolResult
from app.core.workflow.catalog_db import WorkflowCatalogService
from app.core.workflow.runtime import get_workflow_runtime


_workflow_catalog_service_instance: Optional[WorkflowCatalogService] = None


def _workflow_catalog_service() -> WorkflowCatalogService:
    global _workflow_catalog_service_instance
    if _workflow_catalog_service_instance is None:
        _workflow_catalog_service_instance = WorkflowCatalogService()
    return _workflow_catalog_service_instance


@tool(
    name="create_workflow_run",
    description=(
        "Create a workflow run from a known workflow name. Runtime ownership "
        "fields and global_context are injected by the platform."
    ),
    parameters={
        "type": "object",
        "properties": {
            "workflow_name": {
                "type": "string",
                "description": "Workflow name listed in the system prompt.",
            },
        },
        "required": ["workflow_name"],
        "additionalProperties": False,
    },
)
async def create_workflow_run(
    workflow_name: str,
    user_id: str = "",
    session_id: str = "",
    agent_id: str = "",
    global_context: Optional[dict[str, Any]] = None,
) -> ToolResult:
    try:
        resolved_workflow_name = (workflow_name or "").strip()
        if not resolved_workflow_name:
            raise ValueError("workflow_name must not be blank")

        record = _workflow_catalog_service().get_by_name(resolved_workflow_name)
        if record is None:
            raise ValueError(f"Workflow not found: {resolved_workflow_name}")

        runtime = get_workflow_runtime()
        run = runtime.manager.create_workflow_run(
            workflow_name=record["workflow_name"],
            definition=record["definition_json"],
            user_id=user_id,
            session_id=session_id,
            coordinator_agent_id=agent_id,
            global_context=global_context or {},
        )

        payload = {
            "workflow_run_id": run.workflow_run_id,
            "workflow_name": run.workflow_name,
            "status": run.status,
        }
        return ToolResult(
            success=True,
            content=json.dumps(payload, ensure_ascii=False),
            meta=payload,
        )
    except Exception as exc:
        return ToolResult(success=False, error=str(exc))


@tool(
    name="dispatch_workflow_batch_and_wait",
    description=(
        "Dispatch the next ready workflow task batch and wait until the batch "
        "barrier completes."
    ),
    parameters={
        "type": "object",
        "properties": {
            "workflow_run_id": {
                "type": "string",
                "description": "Workflow run id returned by create_workflow_run.",
            },
        },
        "required": ["workflow_run_id"],
        "additionalProperties": False,
    },
)
async def dispatch_workflow_batch_and_wait(
    workflow_run_id: str,
    user_id: str = "",
    session_id: str = "",
    agent_id: str = "",
) -> ToolResult:
    try:
        result = await get_workflow_runtime().dispatch_owned(
            workflow_run_id=workflow_run_id,
            user_id=user_id,
            session_id=session_id,
            coordinator_agent_id=agent_id,
        )

        payload: dict[str, Any] = {
            "workflow_run_id": result.workflow_run_id,
            "status": result.status,
        }
        if result.status == "running":
            payload["completed_task_ids"] = result.completed_task_ids
        else:
            payload["summary_task_id"] = result.summary_task_id
            payload["result_json"] = result.result_json

        return ToolResult(
            success=True,
            content=json.dumps(payload, ensure_ascii=False),
            meta=payload,
        )
    except Exception as exc:
        return ToolResult(success=False, error=str(exc))


@tool(
    name="get_workflow_run",
    description="Read workflow run status and task statuses.",
    parameters={
        "type": "object",
        "properties": {
            "workflow_run_id": {
                "type": "string",
                "description": "Workflow run id returned by create_workflow_run.",
            },
        },
        "required": ["workflow_run_id"],
        "additionalProperties": False,
    },
)
async def get_workflow_run(
    workflow_run_id: str,
    user_id: str = "",
    session_id: str = "",
    agent_id: str = "",
) -> ToolResult:
    try:
        runtime = get_workflow_runtime()
        run = runtime.manager.get_workflow_run(
            workflow_run_id=workflow_run_id,
            user_id=user_id,
            session_id=session_id,
            coordinator_agent_id=agent_id,
        )
        tasks = runtime.manager.get_workflow_tasks(workflow_run_id=workflow_run_id)
        definitions_by_id = {
            task_definition.id: task_definition
            for task_definition in run.definition_snapshot_json.tasks
        }

        payload: dict[str, Any] = {
            "workflow_run_id": run.workflow_run_id,
            "workflow_name": run.workflow_name,
            "status": run.status,
            "tasks": [
                {
                    "task_id": task.task_id,
                    "show_name": definitions_by_id[task.task_id].show_name,
                    "status": task.status,
                }
                for task in tasks
            ],
        }

        if run.status == "completed":
            summary_task = next(
                task for task in tasks if task.task_id == run.summary_task_id
            )
            payload["summary_task_id"] = run.summary_task_id
            payload["result_json"] = summary_task.result_json

        return ToolResult(
            success=True,
            content=json.dumps(payload, ensure_ascii=False),
            meta=payload,
        )
    except Exception as exc:
        return ToolResult(success=False, error=str(exc))


@tool(
    name="cancel_workflow_run",
    description="Cancel an owned workflow run.",
    parameters={
        "type": "object",
        "properties": {
            "workflow_run_id": {
                "type": "string",
                "description": "Workflow run id returned by create_workflow_run.",
            },
        },
        "required": ["workflow_run_id"],
        "additionalProperties": False,
    },
)
async def cancel_workflow_run(
    workflow_run_id: str,
    user_id: str = "",
    session_id: str = "",
    agent_id: str = "",
) -> ToolResult:
    try:
        run = await get_workflow_runtime().cancel_owned(
            workflow_run_id=workflow_run_id,
            user_id=user_id,
            session_id=session_id,
            coordinator_agent_id=agent_id,
        )
        payload = {
            "workflow_run_id": run.workflow_run_id,
            "status": run.status,
        }
        return ToolResult(
            success=True,
            content=json.dumps(payload, ensure_ascii=False),
            meta=payload,
        )
    except Exception as exc:
        return ToolResult(success=False, error=str(exc))


@tool(
    name="save_workflow_catalog",
    description=(
        "Create or update a structured workflow definition in the "
        "workflow_catalog table."
    ),
    parameters={
        "type": "object",
        "properties": {
            "workflow_name": {"type": "string"},
            "file_name": {"type": "string"},
            "title": {"type": "string"},
            "description": {"type": "string"},
            "when_to_use": {"type": "string"},
            "content": {"type": "string"},
            "definition_json": {"type": "object"},
            "status": {"type": "string", "default": "active"},
            "is_active": {"type": "boolean", "default": True},
            "created_by": {"type": "string"},
            "updated_by": {"type": "string"},
        },
        "required": [
            "workflow_name",
            "file_name",
            "description",
            "when_to_use",
            "content",
            "definition_json",
        ],
        "additionalProperties": False,
    },
)
async def save_workflow_catalog(
    workflow_name: str,
    file_name: str,
    description: str,
    when_to_use: str,
    content: str,
    definition_json: dict[str, Any],
    title: Optional[str] = None,
    status: str = "active",
    is_active: bool = True,
    created_by: Optional[str] = None,
    updated_by: Optional[str] = None,
) -> ToolResult:
    try:
        saved = _workflow_catalog_service().save_workflow(
            workflow_name=workflow_name,
            file_name=file_name,
            title=title,
            description=description,
            when_to_use=when_to_use,
            content=content,
            definition_json=definition_json,
            status=status,
            is_active=is_active,
            created_by=created_by,
            updated_by=updated_by,
        )
        payload = {
            "workflow_name": saved["workflow_name"],
            "file_name": saved["file_name"],
            "status": saved["status"],
            "is_active": saved["is_active"],
        }
        return ToolResult(
            success=True,
            content=json.dumps(payload, ensure_ascii=False),
            meta=payload,
        )
    except Exception as exc:
        return ToolResult(success=False, error=str(exc))


@tool(
    name="read_workflow_content",
    description="Read the full instructions for a known workflow.",
    parameters={
        "type": "object",
        "properties": {
            "workflow_name": {
                "type": "string",
                "description": "Workflow name listed in the system prompt.",
            }
        },
        "required": ["workflow_name"],
        "additionalProperties": False,
    },
)
async def read_workflow_content(workflow_name: str) -> ToolResult:
    record = _workflow_catalog_service().get_by_name(workflow_name)
    if record is None:
        return ToolResult(
            success=False,
            error=f"Workflow not found: {workflow_name}",
        )
    payload = {
        "workflow_name": record["workflow_name"],
        "content": record["content"],
    }
    return ToolResult(
        success=True,
        content=json.dumps(payload, ensure_ascii=False),
        meta=payload,
    )
