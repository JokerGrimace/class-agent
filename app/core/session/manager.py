import uuid
from typing import Optional

from app.core.session.models import Message, Session
from app.core.session.storage import SessionStorage, create_session_storage


class SessionManager:
    def __init__(self, storage: Optional[SessionStorage] = None):
        self.storage = storage or create_session_storage()

    def initialize(self) -> None:
        self.storage.initialize()

    def create_session(
        self,
        user_id: str = "anonymous",
        user_role: str = "",
        agent_id: str = "default",
        *,
        current_workflow_page: Optional[str] = None,
        course_code: Optional[str] = None,
    ) -> Session:
        session_id = f"sess_{uuid.uuid4().hex[:16]}"
        session = Session(
            session_id=session_id,
            user_id=user_id,
            user_role=user_role,
            agent_id=agent_id,
            current_workflow_page=current_workflow_page,
            course_code=course_code,
        )
        if hasattr(self.storage, "save_session"):
            self.storage.save_session(session)
        else:
            self.storage.save(session)
        return session

    def get_session(
        self,
        session_id: str,
        user_id: str = "anonymous",
        user_role: str = "",
    ) -> Optional[Session]:
        return self.storage.load(session_id, user_id, user_role=user_role)

    def get_session_with_scope(
        self,
        session_id: str,
        user_id: str = "anonymous",
        user_role: str = "",
        *,
        current_workflow_page: Optional[str] = None,
        course_code: Optional[str] = None,
    ) -> Optional[Session]:
        session = self.storage.load(session_id, user_id, user_role=user_role)
        if not session or not session.matches_scope(
            current_workflow_page=current_workflow_page,
            course_code=course_code,
        ):
            return None
        return session

    def update_session(self, session: Session) -> None:
        if hasattr(self.storage, "save_session"):
            self.storage.save_session(session)
            return
        self.storage.save(session)

    def delete_session(self, session_id: str, user_id: str = "anonymous", user_role: str = "") -> bool:
        return self.storage.delete(session_id, user_id, user_role=user_role)

    def delete_session_with_scope(
        self,
        session_id: str,
        user_id: str = "anonymous",
        user_role: str = "",
        *,
        current_workflow_page: Optional[str] = None,
        course_code: Optional[str] = None,
    ) -> bool:
        session = self.get_session_with_scope(
            session_id,
            user_id=user_id,
            user_role=user_role,
            current_workflow_page=current_workflow_page,
            course_code=course_code,
        )
        if not session:
            return False
        return self.delete_session(session_id, user_id, user_role=user_role)

    def list_sessions(self, user_id: str = "anonymous", user_role: str = "") -> list[str]:
        return self.storage.list_sessions(user_id, user_role=user_role)

    def list_sessions_with_scope(
        self,
        user_id: str = "anonymous",
        user_role: str = "",
        *,
        current_workflow_page: Optional[str] = None,
        course_code: Optional[str] = None,
    ) -> list[str]:
        session_ids = self.list_sessions(user_id, user_role=user_role)
        if not any((current_workflow_page, course_code)):
            return session_ids
        scoped_session_ids: list[str] = []
        for session_id in session_ids:
            session = self.get_session(session_id, user_id=user_id, user_role=user_role)
            if not session or not session.matches_scope(
                current_workflow_page=current_workflow_page,
                course_code=course_code,
            ):
                continue
            scoped_session_ids.append(session_id)
        return scoped_session_ids

    def list_session_summaries_with_scope(
        self,
        user_id: str = "anonymous",
        user_role: str = "",
        *,
        current_workflow_page: Optional[str] = None,
        course_code: Optional[str] = None,
    ) -> list[dict]:
        storage_method = getattr(self.storage, "list_session_summaries_with_scope", None)
        if callable(storage_method):
            return storage_method(
                user_id,
                user_role=user_role,
                current_workflow_page=current_workflow_page,
                course_code=course_code,
            )

        summaries: list[dict] = []
        for session_id in self.list_sessions_with_scope(
            user_id=user_id,
            user_role=user_role,
            current_workflow_page=current_workflow_page,
            course_code=course_code,
        ):
            session = self.get_session(session_id, user_id=user_id, user_role=user_role)
            if not session:
                continue
            visible = session.get_visible_messages()
            last_message = visible[-1] if visible else None
            summaries.append(
                {
                    "session_id": session.session_id,
                    "preview": last_message.content[:40] if last_message else "",
                    "updated_at": session.updated_at,
                    "current_workflow_page": session.current_workflow_page,
                    "course_code": session.course_code,
                }
            )
        return summaries

    def add_message(self, session: Session) -> None:
        self.storage.add_message(session)

    def add_messages(self, session: Session, messages: list[Message]) -> None:
        if not messages:
            return
        if hasattr(self.storage, "add_messages"):
            self.storage.add_messages(session, messages)
            return
        self.storage.save_session(session)

    def add_plan(self,sesssion:Session) -> None:
        self.storage.add_plan(sesssion)

session_manager = SessionManager()
