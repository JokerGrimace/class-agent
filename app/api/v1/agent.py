import json
import logging
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sse_starlette.sse import EventSourceResponse
from pydantic import BaseModel, Field

from app.config import settings
from app.core.agent.engine import AgentEngine
from app.core.auth import AuthenticatedUser, require_authenticated_user
from app.core.file_cache import get_file_content_cache
from app.core.llm.factory import create_llm_adapter
from app.core.permission.service import PermissionService, get_permission_service
from app.core.permission.types import PermissionReply
from app.core.session.manager import session_manager
from app.core.tool.executor import ToolExecutor
from app.core.workflow.manager import WorkflowManager
from app.tools.iclass_d_resource.prase_file import parse_temp_file_bytes

router = APIRouter(prefix="/agent", tags=["agent"])
logger = logging.getLogger(__name__)
ACTIVE_WORKFLOW_STATUSES = {"pending", "running", "waiting_user_input"}


class SendMessageRequest(BaseModel):
    user_id: str = "anonymous"
    session_id: Optional[str] = None
    message: str
    stream: bool = True
    tool_type: Optional[str] = "level1"
    runtime_context: Optional[dict[str, Any]] = None
    resume_workflow: bool = False
    workflow_id: Optional[str] = None


class SendMessageResponse(BaseModel):
    session_id: str
    response: str
    tool_results: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    assistant_payload: dict[str, Any] = Field(default_factory=dict)


class PermissionAskedInfo(BaseModel):
    request_id: str
    summary: str
    visible_arguments: dict[str, Any] = Field(default_factory=dict)
    editable_fields: list[str] = Field(default_factory=list)
    allowed_actions: list[str] = Field(default_factory=list)
    tool_name: str = ""
    tool_action: Optional[str] = None


class ParseTempFileResponse(BaseModel):
    success: bool
    file_name: str
    file_type: str
    file_id: str
    file_cache_key: str
    html_length: int
    html_content: str


class ReplyPermissionRequest(BaseModel):
    action: str
    edited_fields: dict[str, Any] = Field(default_factory=dict)
    comment: Optional[str] = None


class WorkflowCheckResponse(BaseModel):
    has_incomplete_workflow: bool
    workflow_id: Optional[str] = None
    workflow_name: Optional[str] = None
    status: Optional[str] = None
    current_step_id: Optional[str] = None


class WorkflowOperateRequest(BaseModel):
    action: Literal["resume", "cancel"]
    workflow_id: str


class WorkflowOperateResponse(BaseModel):
    success: bool
    action: Literal["resume", "cancel"]
    workflow_id: str


def _build_permission_service() -> PermissionService:
    return get_permission_service()


def _build_agent_engine() -> AgentEngine:
    llm = create_llm_adapter()
    tool_executor = ToolExecutor(
        permission_service=_build_permission_service(),
    )
    return AgentEngine(
        llm,
        tool_executor=tool_executor,
        workspace_dir=str(settings.workspace_dir),
    )


def _build_workflow_manager() -> WorkflowManager:
    return WorkflowManager()


def _build_permission_asked_info(event) -> dict[str, Any]:
    meta = event.meta or {}
    payload = PermissionAskedInfo(
        request_id=str(meta.get("request_id") or ""),
        summary=str(meta.get("summary") or ""),
        visible_arguments=meta.get("visible_arguments") or {},
        editable_fields=list(meta.get("editable_fields") or []),
        allowed_actions=list(meta.get("allowed_actions") or []),
        tool_name=str(event.tool_name or meta.get("tool_name") or ""),
        tool_action=meta.get("tool_action"),
    ).model_dump()
    compact_payload = {
        "request_id": payload["request_id"],
        "tool_name": payload["tool_name"],
        "allowed_actions": payload["allowed_actions"],
    }
    if payload["summary"]:
        compact_payload["summary"] = payload["summary"]
    if payload["visible_arguments"]:
        compact_payload["visible_arguments"] = payload["visible_arguments"]
    if payload["editable_fields"]:
        compact_payload["editable_fields"] = payload["editable_fields"]
    if payload["tool_action"] is not None:
        compact_payload["tool_action"] = payload["tool_action"]
    return compact_payload


def _serialize_tool_result_event(event) -> dict[str, Any]:
    return {
        "tool_call_id": event.tool_call_id,
        "tool_name": event.tool_name,
        "meta": (event.tool_result.meta if event.tool_result else {}),
        "error": None,
        "failed": event.error is not None,
    }


def _build_default_assistant_payload(
    response_text: str,
) -> dict[str, Any]:
    return {
        "reply_text": response_text,
        "action_type": None,
        "needs_confirmation": False,
        "pending_action": None,
        "ui_refresh_hint": None,
        "result_payload": None,
    }


def _persist_cancelled_permission_messages(
    session,
    pending_requests,
    *,
    reason: str,
    session_manager_instance=None,
) -> None:
    if not pending_requests:
        return

    existing_tool_call_ids = {
        message.tool_call_id
        for message in session.messages
        if message.role == "tool" and message.tool_call_id
    }
    assistant_tool_call_ids = set()
    for message in session.messages:
        if message.role != "assistant" or not message.tool_calls:
            continue
        for tool_call in message.tool_calls:
            if isinstance(tool_call, dict) and tool_call.get("id"):
                assistant_tool_call_ids.add(tool_call["id"])

    appended = False
    for request in pending_requests:
        if request.tool_call_id in existing_tool_call_ids:
            continue
        if request.tool_call_id not in assistant_tool_call_ids:
            continue
        session.add_tool_message(
            request.tool_call_id,
            request.tool_name,
            f"Error: {reason}",
        )
        appended = True
    if appended:
        manager = session_manager_instance or session_manager
        manager.add_message(session)


async def _collect_non_stream_events(events) -> dict[str, Any]:
    response_text = ""
    tool_results: list[dict[str, Any]] = []
    warnings: list[str] = []
    assistant_payload: Optional[dict[str, Any]] = None

    async for event in events:
        if event.type == "content":
            response_text += event.content
        elif event.type == "tool_result":
            payload = _serialize_tool_result_event(event)
            tool_results.append(payload)
            meta = payload.get("meta") or {}
            if assistant_payload is None and isinstance(meta, dict):
                tool_assistant_payload = meta.get("assistant_payload")
                if isinstance(tool_assistant_payload, dict):
                    assistant_payload = tool_assistant_payload
        elif event.type == "warning" and event.warning:
            warnings.append(event.warning)
        elif event.type == "done":
            done_assistant_payload = getattr(event, "assistant_payload", None)
            if isinstance(done_assistant_payload, dict):
                assistant_payload = done_assistant_payload

    if assistant_payload is None:
        assistant_payload = _build_default_assistant_payload(response_text)

    return {
        "response_text": response_text,
        "tool_results": tool_results,
        "warnings": warnings,
        "assistant_payload": assistant_payload,
    }

@router.post("/send")
async def send_message(
    req: SendMessageRequest,
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
):
    permission_service = _build_permission_service()
    if req.session_id:
        session = session_manager.get_session(req.session_id, user_id=current_user.user_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
    else:
        session = session_manager.create_session(user_id=current_user.user_id)
        req.session_id = session.session_id

    pending_requests = permission_service.list_pending(
        session_id=session.session_id,
        user_id=current_user.user_id,
    )
    permission_service.cancel_session_requests(
        session_id=session.session_id,
        user_id=current_user.user_id,
        reason="Superseded by a new user message.",
    )
    _persist_cancelled_permission_messages(
        session,
        pending_requests,
        reason="Superseded by a new user message.",
        session_manager_instance=session_manager,
    )

    session.add_user_message(req.message)
    session_manager.add_message(session)

    engine = _build_agent_engine()
    runtime_context = dict(req.runtime_context or {})
    if req.resume_workflow:
        runtime_context["resume_workflow"] = True
    if req.workflow_id is not None:
        runtime_context["workflow_id"] = req.workflow_id

    if not req.stream:
        aggregated = await _collect_non_stream_events(
            engine.run(session, stream=False, runtime_context=runtime_context)
        )

        return SendMessageResponse(
            session_id=session.session_id,
            response=aggregated["response_text"],
            tool_results=aggregated["tool_results"],
            warnings=aggregated["warnings"],
            assistant_payload=aggregated["assistant_payload"],
        )

    return EventSourceResponse(
        _generate_events(session, engine, runtime_context=runtime_context),
        media_type="text/event-stream",
    )


@router.get("/workflow/check", response_model=WorkflowCheckResponse)
async def workflow_check(
    session_id: str,
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
):
    session = session_manager.get_session(session_id, user_id=current_user.user_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    workflow_manager = _build_workflow_manager()
    workflow = workflow_manager.get_active_workflow(session_id, current_user.user_id)
    if not workflow:
        return WorkflowCheckResponse(has_incomplete_workflow=False)

    return WorkflowCheckResponse(
        has_incomplete_workflow=True,
        workflow_id=workflow.workflow_id,
        workflow_name=workflow.workflow_name,
        status=workflow.status,
        current_step_id=workflow.context.current_step_id,
    )


@router.post("/workflow/operate", response_model=WorkflowOperateResponse)
async def workflow_operate(
    req: WorkflowOperateRequest,
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
):
    workflow_manager = _build_workflow_manager()
    workflow = workflow_manager.get_workflow(req.workflow_id, current_user.user_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    if workflow.status not in ACTIVE_WORKFLOW_STATUSES:
        raise HTTPException(status_code=409, detail="Workflow is not resumable")

    if req.action == "cancel":
        workflow_manager.mark_cancelled(req.workflow_id, current_user.user_id)

    return WorkflowOperateResponse(
        success=True,
        action=req.action,
        workflow_id=req.workflow_id,
    )


@router.post("/parse-temp-file", response_model=ParseTempFileResponse)
async def parse_temp_file(
    file: UploadFile = File(...),
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
):
    del current_user
    file_name = (file.filename or "").strip() or "temp-upload"
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    try:
        payload = parse_temp_file_bytes(file_name, file_bytes)
        html_content = str(payload.get("html_content") or "")
        cached_file = get_file_content_cache().put(
            file_name=str(payload.get("file_name") or file_name),
            file_type=str(payload.get("file_type") or ""),
            html_content=html_content,
        )
        payload.update(
            {
                "file_id": cached_file.file_id,
                "file_cache_key": cached_file.file_cache_key,
                "html_length": len(html_content),
            }
        )
        # payload.pop("html_content", None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to parse temporary file: {exc}") from exc
    return ParseTempFileResponse(**payload)

# 用户授权接口
@router.post("/permissions/{request_id}/reply")
async def reply_permission(
    request_id: str,
    req: ReplyPermissionRequest,
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
):
    # 获取全局的权限缓存变量
    permission_service = _build_permission_service()
    try:
        permission_service.reply(
            request_id,
            user_id=current_user.user_id,
            reply=PermissionReply(
                action=req.action,
                edited_fields=req.edited_fields,
                comment=req.comment,
            ),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"request_id": request_id, "accepted": True}


@router.get("/permissions/pending")
async def list_pending_permissions(
    session_id: Optional[str] = None,
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
):
    permission_service = _build_permission_service()
    items = [
        item.model_dump()
        for item in permission_service.list_pending(
            session_id=session_id,
            user_id=current_user.user_id,
        )
    ]
    return {"items": items}


async def _generate_events(session, engine, runtime_context):
    permission_service = getattr(getattr(engine, "tool_executor", None), "permission_service", None)
    engine_session_manager = getattr(engine, "session_manager", session_manager)
    try:
        async for event in engine.run(session, stream=True, runtime_context=runtime_context):
            if event.type == "reasoning":
                yield {"event": "reasoning", "data": event.content}
            elif event.type == "reasoning_end":
                yield {"event": "reasoning_end", "data": ""}
            elif event.type == "content":
                yield {"event": "text", "data": event.content}
            elif event.type == "tool_call":
                payload = {
                    "tool_name": event.tool_name,
                    "tool_call_id": event.tool_call_id,
                }
                yield {"event": "tool_call", "data": json.dumps(payload)}
            elif event.type == "tool_result":
                yield {"event": "tool_result", "data": json.dumps(_serialize_tool_result_event(event))}
                # 返回SSE流 ==> 前端会将请求发给/permissions/{request_id}/reply
            elif event.type == "workflow_status":
                yield {"event": "workflow_status", "data": json.dumps(event.meta or {})}
            elif event.type == "permission_asked":
                yield {"event": "permission_asked", "data": json.dumps(_build_permission_asked_info(event))}
            elif event.type == "warning":
                yield {"event": "warning", "data": event.warning}
            elif event.type == "done":
                yield {"event": "done", "data": ""}
    finally:
        if permission_service is not None:
            pending_requests = permission_service.list_pending(
                session_id=session.session_id,
                user_id=session.user_id,
            )
            cancelled = permission_service.cancel_session_requests(
                session_id=session.session_id,
                user_id=session.user_id,
                reason="Permission request cancelled because the agent stream closed.",
            )
            _persist_cancelled_permission_messages(
                session,
                pending_requests,
                reason="Permission request cancelled because the agent stream closed.",
                session_manager_instance=engine_session_manager,
            )
            if cancelled:
                logger.warning(
                    "Closed agent stream cancelled %s permission request(s) for session_id=%s",
                    cancelled,
                    session.session_id,
                )
