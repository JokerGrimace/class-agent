import json
import logging
import mimetypes
import tempfile
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sse_starlette.sse import EventSourceResponse
from pydantic import BaseModel, Field, model_validator

from app.config import settings
from app.core.agent.engine import AgentEngine
from app.core.auth import AuthenticatedUser, require_authenticated_user
from app.core.file_cache import get_file_content_cache
from app.core.display_snapshot import DisplaySnapshot, MessageDisplaySnapshot, bind_message_display
from app.core.iclass.auth_context import (
    reset_current_iclass_api_token,
    set_current_iclass_api_token,
)
from app.core.llm.factory import create_llm_adapter
from app.core.llm.user_context import (
    reset_current_model_user,
    set_current_model_user,
)
from app.core.permission.service import PermissionService, get_permission_service
from app.core.permission.types import PermissionReply
from app.core.session.manager import session_manager
from app.core.tool.executor import ToolExecutor
from app.tools.iclass_d_resource.prase_file import parse_temp_file_bytes
from app.core.workflow.runtime import get_workflow_runtime
router = APIRouter(prefix="/agent", tags=["agent"])
logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DOWNLOAD_ALLOWED_SUFFIXES = {".doc", ".docx", ".html", ".htm", ".md", ".pdf", ".txt"}


class SendMessageRequest(BaseModel):
    session_id: Optional[str] = None
    message: str
    stream: bool = True
    tool_type: Optional[str] = "level1"
    current_workflow_page: Optional[str] = None
    course_code: Optional[str] = None
    runtime_context: Optional[dict[str, Any]] = None
    # 当前页在发送时附带；采集失败仍允许普通对话，只有读取工具会消费失败原因。
    display_snapshot: Optional[DisplaySnapshot] = None
    display_snapshot_error: Optional[str] = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_result(self):
        # 不允许同一条消息同时声明读取成功和失败；没有打开文件时二者均可缺省。
        if self.display_snapshot is not None and self.display_snapshot_error is not None:
            raise ValueError("display_snapshot 与 display_snapshot_error 不能同时提供")
        return self


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
    tool_display_name: Optional[str] = None
    tool_action: Optional[str] = None


class ParseTempFileResponse(BaseModel):
    success: bool
    file_name: str
    file_type: str
    file_id: str
    file_cache_key: str
    html_length: int


class ReplyPermissionRequest(BaseModel):
    action: str
    edited_fields: dict[str, Any] = Field(default_factory=dict)
    comment: Optional[str] = None


class WorkflowCancelResponse(BaseModel):
    workflow_run_id: str
    status: Literal["cancelled"]


def _normalize_optional_text(value: Any) -> Optional[str]:
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return None


def _build_session_scope(
    runtime_context: dict[str, Any],
    *,
    current_workflow_page: Optional[str] = None,
    course_code: Optional[str] = None,
) -> dict[str, Optional[str]]:
    return {
        "current_workflow_page": current_workflow_page
        if current_workflow_page is not None
        else _normalize_optional_text(runtime_context.get("current_workflow_page")),
        "course_code": course_code
        if course_code is not None
        else _normalize_optional_text(runtime_context.get("course_code")),
    }




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


def _resolve_download_file_path(file_path: str) -> Path:
    normalized_file_path = (file_path or "").strip()
    if not normalized_file_path:
        raise HTTPException(status_code=400, detail="file_path is required")

    resolved_path = Path(normalized_file_path).expanduser().resolve()
    allowed_roots = [
        PROJECT_ROOT.resolve(),
        settings.data_dir.resolve(),
        settings.workspace_dir.resolve(),
        Path(tempfile.gettempdir()).resolve(),
    ]
    is_allowed = any(
        resolved_path == allowed_root or allowed_root in resolved_path.parents
        for allowed_root in allowed_roots
    )
    if not is_allowed:
        raise HTTPException(status_code=403, detail="File is outside the downloadable directories")
    if resolved_path.suffix.lower() not in DOWNLOAD_ALLOWED_SUFFIXES:
        raise HTTPException(status_code=400, detail="File type is not downloadable")
    if not resolved_path.exists() or not resolved_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return resolved_path



def _build_permission_asked_info(event) -> dict[str, Any]:
    meta = event.meta or {}
    payload = PermissionAskedInfo(
        request_id=str(meta.get("request_id") or ""),
        summary=str(meta.get("summary") or ""),
        visible_arguments=meta.get("visible_arguments") or {},
        editable_fields=list(meta.get("editable_fields") or []),
        allowed_actions=list(meta.get("allowed_actions") or []),
        tool_name=str(event.tool_name or meta.get("tool_name") or ""),
        tool_display_name=str(meta.get("tool_display_name") or "").strip() or None,
        tool_action=meta.get("tool_action"),
    ).model_dump()
    compact_payload = {
        "request_id": payload["request_id"],
        "tool_name": payload["tool_name"],
        "allowed_actions": payload["allowed_actions"],
    }
    if payload["tool_display_name"]:
        compact_payload["tool_display_name"] = payload["tool_display_name"]
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
    meta = event.tool_result.meta if event.tool_result else {}
    payload = {
        "tool_call_id": event.tool_call_id,
        "tool_name": event.tool_name,
        "meta": meta,
        "error": None,
        "failed": event.error is not None,
    }
    if isinstance(meta, dict):
        tool_display_name = str(meta.get("tool_display_name") or "").strip()
        if tool_display_name:
            payload["tool_display_name"] = tool_display_name
    return payload


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
    request: Request,
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
):
    permission_service = _build_permission_service()
    runtime_context = dict(req.runtime_context or {})
    session_scope = _build_session_scope(
        runtime_context,
        current_workflow_page=req.current_workflow_page,
        course_code=req.course_code,
    )
    if req.session_id:
        session = session_manager.get_session_with_scope(
            req.session_id,
            user_id=current_user.user_id,
            user_role=current_user.user_role,
            **session_scope,
        )
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
    else:
        session = session_manager.create_session(
            user_id=current_user.user_id,
            user_role=current_user.user_role,
        )
        if any(session_scope.values()):
            session.current_workflow_page = session_scope["current_workflow_page"]
            session.course_code = session_scope["course_code"]
            session_manager.update_session(session)
        req.session_id = session.session_id

    # 不信任上下文中的快照引用；正文只从独立入站字段绑定到本条消息。
    runtime_context.pop("display_snapshot", None)
    runtime_context.pop("display_snapshot_error", None)
    runtime_context.pop("frontend_capabilities", None)

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
    message_display = MessageDisplaySnapshot(
        current_user.user_id, session.session_id, snapshot=req.display_snapshot, error=req.display_snapshot_error,
    )

    engine = _build_agent_engine()
    # 工具凭证必须与已认证身份一致；本机测试身份的空 token 不得回退为共享凭证。
    iclass_api_token = current_user.token

    if not req.stream:
        token_context = set_current_iclass_api_token(iclass_api_token)
        # 模型凭证解析只信任认证依赖返回的教师编码与角色，并限制在本次请求内。
        model_user_context = set_current_model_user(
            current_user.user_id,
            current_user.user_role,
        )
        try:
            with bind_message_display(message_display):
                aggregated = await _collect_non_stream_events(
                    engine.run(session, stream=False, runtime_context=runtime_context)
                )
        finally:
            reset_current_model_user(model_user_context)
            reset_current_iclass_api_token(token_context)

        return SendMessageResponse(
            session_id=session.session_id,
            response=aggregated["response_text"],
            tool_results=aggregated["tool_results"],
            warnings=aggregated["warnings"],
            assistant_payload=aggregated["assistant_payload"],
        )

    return EventSourceResponse(
        _generate_events(
            session,
            engine,
            runtime_context=runtime_context,
            iclass_api_token=iclass_api_token,
            teacher_code=current_user.user_id,
            user_role=current_user.user_role,
            message_display=message_display,
        ),
        media_type="text/event-stream",
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
        payload.pop("html_content", None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to parse temporary file: {exc}") from exc
    return ParseTempFileResponse(**payload)


@router.get("/download-generated-file")
async def download_generated_file(
    file_path: str,
    file_name: Optional[str] = None,
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
):
    del current_user
    resolved_path = _resolve_download_file_path(file_path)
    download_name = (file_name or "").strip() or resolved_path.name
    media_type = mimetypes.guess_type(resolved_path.name)[0] or "application/octet-stream"
    return FileResponse(
        path=resolved_path,
        media_type=media_type,
        filename=download_name,
    )

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

@router.post(
    "/workflow-runs/{workflow_run_id}/cancel",
    response_model=WorkflowCancelResponse,
)
async def cancel_workflow_run(
    workflow_run_id: str,
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
):
    run = await get_workflow_runtime().cancel_for_user(
        workflow_run_id=workflow_run_id,
        user_id=current_user.user_id,
    )
    return WorkflowCancelResponse(
        workflow_run_id=run.workflow_run_id,
        status="cancelled",
    )


async def _generate_events(
    session,
    engine,
    runtime_context,
    iclass_api_token: Optional[str] = None,
    teacher_code: str = "",
    user_role: str = "",
    message_display: Optional[MessageDisplaySnapshot] = None,
):
    permission_service = getattr(getattr(engine, "tool_executor", None), "permission_service", None)
    engine_session_manager = getattr(engine, "session_manager", session_manager)
    token_context = set_current_iclass_api_token(iclass_api_token)
    # SSE 生成器可能晚于路由函数执行，身份必须在生成器自己的生命周期中绑定。
    model_user_context = set_current_model_user(teacher_code, user_role)
    # 独立快照只在此 SSE 执行作用域内可见，恢复直接消费引擎事件。
    with bind_message_display(message_display):
        engine_events = engine.run(session, stream=True, runtime_context=runtime_context)
        try:
            #TODO: tool_type没有回传
            async for event in engine_events:
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
            reset_current_model_user(model_user_context)
            reset_current_iclass_api_token(token_context)
            await engine_events.aclose()
            await get_workflow_runtime().cancel_active_run(
                session_id=session.session_id,
                user_id=session.user_id,
                coordinator_agent_id=session.agent_id,
            )
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
