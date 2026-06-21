from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.auth import AuthenticatedUser, require_authenticated_user
from app.core.session.manager import session_manager

router = APIRouter(prefix="/sessions", tags=["sessions"])


class CreateSessionRequest(BaseModel):
    user_id: str = "anonymous"
    agent_id: str = "default"


class CreateSessionResponse(BaseModel):
    session_id: str


class SessionResponse(BaseModel):
    session_id: str
    user_id: str
    agent_id: str
    messages: list[dict]
    created_at: str
    updated_at: str


@router.post("", response_model=CreateSessionResponse)
async def create_session(
    req: CreateSessionRequest = CreateSessionRequest(),
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
):
    session = session_manager.create_session(user_id=current_user.user_id, agent_id=req.agent_id)
    return CreateSessionResponse(session_id=session.session_id)


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: str,
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
):
    session = session_manager.get_session(session_id, user_id=current_user.user_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return SessionResponse(
        session_id=session.session_id,
        user_id=session.user_id,
        agent_id=session.agent_id,
        messages=[m.model_dump() for m in session.get_visible_messages()],
        created_at=session.created_at.isoformat(),
        updated_at=session.updated_at.isoformat(),
    )


@router.delete("/{session_id}")
async def delete_session(
    session_id: str,
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
):
    if not session_manager.delete_session(session_id, user_id=current_user.user_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "ok"}


@router.get("")
async def list_sessions(current_user: AuthenticatedUser = Depends(require_authenticated_user)):
    sessions = []
    for sid in session_manager.list_sessions(user_id=current_user.user_id):
        s = session_manager.get_session(sid, user_id=current_user.user_id)
        if s:
            visible = s.get_visible_messages()
            last_msg = visible[-1] if visible else None
            sessions.append({
                "session_id": s.session_id,
                "preview": last_msg.content[:40] if last_msg else "",
                "updated_at": s.updated_at.isoformat(),
            })
    return {"sessions": sessions}
