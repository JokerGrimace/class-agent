from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.auth import AuthenticatedUser, require_authenticated_user
from app.core.session.manager import session_manager

router = APIRouter(prefix="/sessions", tags=["sessions"])


class CreateSessionRequest(BaseModel):
    agent_id: str = "default"
    current_workflow_page: Optional[str] = None
    course_code: Optional[str] = None


class CreateSessionResponse(BaseModel):
    session_id: str


class SessionResponse(BaseModel):
    session_id: str
    user_id: str
    agent_id: str
    current_workflow_page: Optional[str] = None
    course_code: Optional[str] = None
    messages: list[dict]
    created_at: str
    updated_at: str


class AgentSessionSummary(BaseModel):
    session_id: str
    preview: str
    updated_at: str
    current_workflow_page: Optional[str] = None
    course_code: Optional[str] = None


class AgentSessionListResponse(BaseModel):
    sessions: list[AgentSessionSummary]


@router.post("", response_model=CreateSessionResponse)
async def create_session(
    req: CreateSessionRequest = CreateSessionRequest(),
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
):
    session = session_manager.create_session(
        user_id=current_user.user_id,
        user_role=current_user.user_role,
        agent_id=req.agent_id,
    )
    if any((req.current_workflow_page, req.course_code)):
        session.current_workflow_page = req.current_workflow_page
        session.course_code = req.course_code
        session_manager.update_session(session)
    return CreateSessionResponse(session_id=session.session_id)


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: str,
    current_workflow_page: Optional[str] = None,
    course_code: Optional[str] = None,
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
):
    session = session_manager.get_session_with_scope(
        session_id,
        user_id=current_user.user_id,
        user_role=current_user.user_role,
        current_workflow_page=current_workflow_page,
        course_code=course_code,
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return SessionResponse(
        session_id=session.session_id,
        user_id=session.user_id,
        agent_id=session.agent_id,
        current_workflow_page=session.current_workflow_page,
        course_code=session.course_code,
        messages=[m.model_dump() for m in session.messages],
        created_at=session.created_at.isoformat(),
        updated_at=session.updated_at.isoformat(),
    )


@router.delete("/{session_id}")
async def delete_session(
    session_id: str,
    current_workflow_page: Optional[str] = None,
    course_code: Optional[str] = None,
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
):
    deleted = session_manager.delete_session_with_scope(
        session_id,
        user_id=current_user.user_id,
        user_role=current_user.user_role,
        current_workflow_page=current_workflow_page,
        course_code=course_code,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "ok"}


@router.get("", response_model=AgentSessionListResponse)
async def list_sessions(
    current_workflow_page: Optional[str] = None,
    course_code: Optional[str] = None,
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
):
    summaries = session_manager.list_session_summaries_with_scope(
        user_id=current_user.user_id,
        user_role=current_user.user_role,
        current_workflow_page=current_workflow_page,
        course_code=course_code,
    )
    return {
        "sessions": [
            {
                **summary,
                "updated_at": summary["updated_at"].isoformat(),
            }
            for summary in summaries
        ]
    }
