import uuid
from typing import Optional

from app.core.session.models import Message, Session
from app.core.session.storage import SessionStorage, create_session_storage


class SessionManager:
    def __init__(self, storage: Optional[SessionStorage] = None):
        self.storage = storage or create_session_storage()

    def initialize(self) -> None:
        self.storage.initialize()

    def create_session(self, user_id: str = "anonymous", agent_id: str = "default") -> Session:
        session_id = f"sess_{uuid.uuid4().hex[:16]}"
        session = Session(session_id=session_id, user_id=user_id, agent_id=agent_id)
        if hasattr(self.storage, "save_session"):
            self.storage.save_session(session)
        else:
            self.storage.save(session)
        return session

    def get_session(self, session_id: str, user_id: str = "anonymous") -> Optional[Session]:
        return self.storage.load(session_id, user_id)

    def update_session(self, session: Session) -> None:
        self.storage.save_session(session)

    def delete_session(self, session_id: str, user_id: str = "anonymous") -> bool:
        return self.storage.delete(session_id, user_id)

    def list_sessions(self, user_id: str = "anonymous") -> list[str]:
        return self.storage.list_sessions(user_id)

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
