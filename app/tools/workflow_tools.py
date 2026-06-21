from datetime import time
from pathlib import Path
import json
from typing import Any, Optional

from app.core.session.manager import session_manager
from app.core.tool.registry import tool
from app.core.tool.types import ToolResult
from app.core.workflow.catalog import WorkflowCatalog
from app.core.workflow.catalog_db import WorkflowCatalogService
from app.core.workflow.manager import WorkflowManager


def _workflow_catalog() -> WorkflowCatalog:
    workflows_dir = Path(__file__).resolve().parents[1] / "workflows"
    return WorkflowCatalog.from_directory(workflows_dir, ignore_errors=True)


_workflow_catalog_service_instance: Optional[WorkflowCatalogService] = None


def _workflow_catalog_service() -> WorkflowCatalogService:
    global _workflow_catalog_service_instance
    if _workflow_catalog_service_instance is None:
        _workflow_catalog_service_instance = WorkflowCatalogService()
    return _workflow_catalog_service_instance


def _workflow_summary(
    spec_name: str,
    context_parameters: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    return {
        "status": "not_implemented",
        "message": "Workflow execution is not implemented yet.",
        "workflow_name": spec_name,
        "context_parameters": context_parameters or {},
    }


def _invalid_input_result(error: str, meta: dict[str, Any]) -> ToolResult:
    return ToolResult(
        success=False,
        error=error,
        meta=meta,
    )


@tool(
    name="start_workflow_tool",
    description=(
        """
        When you need to use a workflow, this serves as the common entry point for invoking all workflows.
        This tool loads all information required for the workflow from MySQL, creates the workflow run, and returns the workflow transition metadata.
        """
    ),
    parameters={
        "type": "object",
        "properties": {
            "workflow_name": {
                "type": "string",
                "description": (
                    "Workflow name listed in the system prompt. "
                    "Example: 'chapter_build'."
                ),
            },
            "session_id": {
                "type": "string",
                "description": (
                    "Optional existing chat session id that owns this workflow run. "
                    "The agent runtime injects the current session id automatically when omitted."
                ),
            },
            "user_id": {
                "type": "string",
                "description": (
                    "User id that owns the session and workflow. "
                ),
                "default": "anonymous",
            },
            "agent_id": {
                "type": "string",
                "description": (
                    "Optional agent identifier for workflow ownership and tracing. "
                    "If omitted, defaults to 'default'."
                ),
                "default": "default",
            },
            "context_parameters": {
                "type": "object",
                "description": (
                    "Global workflow context injected from outside the workflow. "
                    "Use this for browser page parameters or external runtime state. "
                    "Example keys: course_code, teacher_code, page_id, selected_node_id, "
                    "resource_id, auth_token, origin_url, dom_context."
                ),
                "default": {},
            },
        },
        "required": ["workflow_name", "context_parameters"],
        "additionalProperties": False,
    },
)
async def start_workflow_tool(
    session_id: str,
    workflow_name: Optional[str] = None,
    user_id: str = "anonymous",
    agent_id: str = "default",
    context_parameters: Optional[dict[str, Any]] = None,
) -> ToolResult:
    # breakpoint()
    try:
        resolved_workflow_name = workflow_name
        if not resolved_workflow_name or not resolved_workflow_name.strip():
            raise ValueError("workflow_name must not be blank")
        if not session_id.strip():
            raise ValueError("session_id must not be blank")

        session = session_manager.get_session(session_id, user_id=user_id)
        if session is None:
            raise ValueError(f"Session not found: session_id={session_id}, user_id={user_id}")

        workflow_record = _workflow_catalog_service().get_by_name(resolved_workflow_name)
        if workflow_record is None:
            raise ValueError(f"Workflow not found in workflow_catalog: {resolved_workflow_name}")
        workflow_def = workflow_record["definition_json"]
        workflow_manager = WorkflowManager()

        user_input = ""
        for message in reversed(session.messages):
            if message.role == "user":
                user_input = message.content
                break
        # TODO:需要去细化workflow表的粒度
        workflow = workflow_manager.start_workflow(
            session_id=session_id,
            user_id=user_id,
            agent_id=agent_id,
            workflow_name=workflow_record["workflow_name"],
            definition=workflow_def,
            user_input=user_input,
            global_context=context_parameters or {},
        )

        result_meta = {
            "status": "created",
            "transition": "enter_workflow",
            "workflow_id": workflow.workflow_id,
            "workflow_name": workflow_record["workflow_name"],
            "session_id": session_id,
            "user_id": user_id,
            "agent_id": agent_id,
            "context_parameters": context_parameters or {},
        }

        return ToolResult(
            success=True,
            content=json.dumps(
                {
                    "workflow_id": workflow.workflow_id,
                    "workflow_name": workflow.workflow_name,
                    "status": workflow.status,
                },
                ensure_ascii=False,
            ),
            meta=result_meta,
        )

    except Exception as exc:
        return ToolResult(success=False, error=str(exc))

@tool(
    name="save_workflow_catalog",
    description=(
        "Create or update a structured workflow definition in the workflow_catalog table. "
        "Use this when you need to register a workflow so the runtime can discover and execute it."
    ),
    parameters={
        "type": "object",
        "properties": {
            "workflow_name": {
                "type": "string",
                "description": "Logical workflow name, for example 'chapter_build'.",
            },
            "file_name": {
                "type": "string",
                "description": "Workflow file identifier, for example 'chapter-build.md'.",
            },
            "title": {
                "type": "string",
                "description": "Optional workflow display title.",
            },
            "description": {
                "type": "string",
                "description": "Workflow description shown in the system prompt.",
            },
            "when_to_use": {
                "type": "string",
                "description": "Workflow usage guidance shown in the system prompt.",
            },
            "markdown_content": {
                "type": "string",
                "description": "Optional original workflow markdown content for archival.",
                "default": "",
            },
            "definition_json": {
                "type": "object",
                "description": "Full structured workflow definition JSON.",
            },
            "required_inputs_json": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional explicit required input keys. Derived from definition_json when omitted.",
            },
            "allowed_tools_json": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional explicit allowed tool list. Derived from definition_json when omitted.",
            },
            "status": {
                "type": "string",
                "description": "Workflow status, for example active, inactive, draft, or deprecated.",
                "default": "active",
            },
            "is_active": {
                "type": "boolean",
                "description": "Whether the workflow is active for prompt discovery.",
                "default": True,
            },
            "sort_order": {
                "type": "integer",
                "description": "Prompt ordering priority. Lower values appear first.",
                "default": 0,
            },
            "tags_json": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional workflow tags.",
            },
            "notes": {
                "type": "string",
                "description": "Optional internal notes.",
            },
            "created_by": {
                "type": "string",
                "description": "Optional creator identifier.",
            },
            "updated_by": {
                "type": "string",
                "description": "Optional updater identifier.",
            },
        },
        "required": [
            "workflow_name",
            "file_name",
            "description",
            "when_to_use",
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
    definition_json: dict[str, Any],
    title: Optional[str] = None,
    markdown_content: str = "",
    required_inputs_json: Optional[list[str]] = None,
    allowed_tools_json: Optional[list[str]] = None,
    status: str = "active",
    is_active: bool = True,
    sort_order: int = 0,
    tags_json: Optional[list[str]] = None,
    notes: Optional[str] = None,
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
            markdown_content=markdown_content,
            definition_json=definition_json,
            required_inputs_json=required_inputs_json,
            allowed_tools_json=allowed_tools_json,
            status=status,
            is_active=is_active,
            sort_order=sort_order,
            tags_json=tags_json,
            notes=notes,
            created_by=created_by,
            updated_by=updated_by,
        )
        payload = {
            "workflow_name": saved["workflow_name"],
            "file_name": saved["file_name"],
            "definition_version": saved["definition_version"],
            "step_count": saved["step_count"],
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
