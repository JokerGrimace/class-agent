import json
from typing import Any, Optional
from urllib.parse import parse_qsl, urlparse

from app.config import settings
from app.core.session.models import Message, Session


class SessionStorage:
    def add_message(self, session: Session) -> None:
        raise NotImplementedError

    def add_messages(self, session: Session, messages: list[Message]) -> None:
        raise NotImplementedError

    def load(self, session_id: str, user_id: str) -> Optional[Session]:
        raise NotImplementedError

    def delete(self, session_id: str, user_id: str) -> bool:
        raise NotImplementedError

    def save_session(self, session: Session) -> None:
        raise NotImplementedError

    def list_sessions(self, user_id: str) -> list[str]:
        raise NotImplementedError

    def initialize(self) -> None:
        return None

    def add_plan(self,session:Session)  -> None:
        raise NotImplementedError


class MySQLSessionStorage(SessionStorage):
    def __init__(self):
        self._mysql_conn: Optional[Any] = None
        self._initialized = False

    def initialize(self) -> None:
        self._ensure_initialized()

    def save_session(self, session: Session) -> None:
        self._ensure_initialized()
        with self._mysql_conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO sessions (session_id, user_id, agent_id, plan, context, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s) ON DUPLICATE KEY
                UPDATE
                    user_id =
                VALUES (user_id), agent_id =
                VALUES (agent_id), plan =
                VALUES (plan), context =
                VALUES (context), created_at =
                VALUES (created_at), updated_at =
                VALUES (updated_at)
                """,
                (
                    session.session_id,
                    session.user_id,
                    session.agent_id,
                    self._dump_json(session.plan),
                    self._dump_json(session.context.model_dump(mode="json")),
                    session.created_at,
                    session.updated_at,
                ),
            )
        self._mysql_conn.commit()


    def add_plan(self,session:Session) -> None:
        self._ensure_initialized()
        with self._mysql_conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO seesion_plan(
                session_id, user_id, agent_id, plan,plan_status)
                VALUES (%s, %s, %s, %s,%s) ON DUPLICATE KEY
                """,
                (
                    session.session_id,
                    session.user_id,
                    session.agent_id,
                    self._dump_json(session.plan),
                    1 if session.plan_is_completed else 0

                )

            )

            self._mysql_conn.commit()



    def add_message(self, session: Session) -> None:
        self._ensure_initialized()

        with self._mysql_conn.cursor() as cursor:

            message = session.messages[-1]

            cursor.execute(
                    """
                    INSERT INTO session_messages (
                        session_id,
                        user_id,
                        agent_id,
                        role,
                        content,
                        tool_calls,
                        citations,
                        tool_call_id,
                        tool_name,
                        reasoning_content,
                        created_at
                    ) VALUES (%s,%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,

                        (
                            session.session_id,
                            session.user_id,
                            session.agent_id,
                            message.role,
                            message.content,
                            self._dump_json(message.tool_calls),
                            self._dump_json(message.citations),
                            message.tool_call_id,
                            message.tool_name,
                            message.reasoning_content,
                            session.updated_at,
                        )

                    ,
                )
        self._mysql_conn.commit()

    def add_messages(self, session: Session, messages: list[Message]) -> None:
        if not messages:
            return
        self._ensure_initialized()

        with self._mysql_conn.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO session_messages (
                    session_id,
                    user_id,
                    agent_id,
                    role,
                    content,
                    tool_calls,
                    citations,
                    tool_call_id,
                    tool_name,
                    reasoning_content,
                    created_at
                ) VALUES (%s,%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    (
                        session.session_id,
                        session.user_id,
                        session.agent_id,
                        message.role,
                        message.content,
                        self._dump_json(message.tool_calls),
                        self._dump_json(message.citations),
                        message.tool_call_id,
                        message.tool_name,
                        message.reasoning_content,
                        session.updated_at,
                    )
                    for message in messages
                ],
            )
        self._mysql_conn.commit()

    def load(self, session_id: str, user_id: str) -> Optional[Session]:
        self._ensure_initialized()

        with self._mysql_conn.cursor() as cursor:
            cursor.execute(
                """
                select
                t1.session_id,
                t1.user_id,
                t1.agent_id,
                t2.plan,
                t1.context,
                t1.created_at,
                t1.updated_at
                from
                (SELECT session_id, user_id, agent_id, plan, context, created_at, updated_at
                FROM sessions
                WHERE session_id = %s AND user_id = %s) t1
                left join
                (select session_id,user_id,plan from session_plan where plan_status = 0
                ) t2
                on t1.session_id = t2.session_id and t1.user_id = t2.user_id
                """,
                (session_id, user_id),
            )
            session_row = cursor.fetchone()
            if not session_row:
                return None

            cursor.execute(
                """
                SELECT role, content, tool_calls, citations, tool_call_id, tool_name, reasoning_content
                FROM session_messages
                WHERE session_id = %s AND user_id = %s
                ORDER BY id ASC
                """,
                (session_id, user_id),
            )
            message_rows = cursor.fetchall()

        return self._build_session(session_row, message_rows)

    def delete(self, session_id: str, user_id: str) -> bool:
        self._ensure_initialized()

        with self._mysql_conn.cursor() as cursor:
            # 删除session
            cursor.execute(
                "DELETE FROM sessions WHERE session_id = %s AND user_id = %s",
                (session_id, user_id),
            )
            deleted = cursor.rowcount > 0

            # 删除对应的message
            cursor.execute(
                "DELETE FROM session_messages WHERE session_id = %s AND user_id = %s",
                (session_id, user_id),
            )

            # 删除对应的plan
            cursor.execute(
                "DELETE FROM session_plan WHERE session_id = %s AND user_id = %s",
                (session_id, user_id),
            )
        self._mysql_conn.commit()
        return deleted

    def list_sessions(self, user_id: str) -> list[str]:
        self._ensure_initialized()

        with self._mysql_conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT session_id
                FROM sessions
                WHERE user_id = %s
                ORDER BY updated_at DESC
                """,
                (user_id,),
            )
            rows = cursor.fetchall()
        return [row["session_id"] for row in rows]

    def _ensure_initialized(self) -> None:
        if self._initialized:
            self._mysql_conn.ping(reconnect=True)
            return

        self._mysql_conn = self._build_mysql_connection()
        self._create_tables()
        self._initialized = True

    def _build_mysql_connection(self) -> Any:
        import pymysql

        parsed = urlparse(settings.mysql_url.removeprefix("jdbc:"))
        query_params = dict(parse_qsl(parsed.query, keep_blank_values=True))
        charset = self._normalize_mysql_charset(query_params.get("characterEncoding", "utf8mb4"))
        database = parsed.path.lstrip("/")

        return pymysql.connect(
            host=parsed.hostname or "localhost",
            port=parsed.port or 3306,
            user=settings.mysql_username,
            password=settings.mysql_password,
            database=database,
            charset=charset,
            autocommit=False,
            cursorclass=pymysql.cursors.DictCursor,
        )

    def _create_tables(self) -> None:
        with self._mysql_conn.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id VARCHAR(64) NOT NULL,
                    user_id VARCHAR(128) NOT NULL,
                    agent_id VARCHAR(128) NOT NULL,
                    plan JSON NOT NULL,
                    context JSON NOT NULL,
                    created_at DATETIME(6) NOT NULL,
                    updated_at DATETIME(6) NOT NULL,
                    PRIMARY KEY (session_id),
                    KEY idx_sessions_user_id (user_id),
                    KEY idx_sessions_user_updated_at (user_id, updated_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS session_messages (
                    id BIGINT NOT NULL AUTO_INCREMENT,
                    session_id VARCHAR(64) NOT NULL,
                    user_id VARCHAR(128) NOT NULL,
                    role VARCHAR(32) NOT NULL,
                    content LONGTEXT NOT NULL,
                    tool_calls JSON NOT NULL,
                    citations JSON NOT NULL,
                    tool_call_id VARCHAR(128) NULL,
                    tool_name VARCHAR(128) NULL,
                    reasoning_content LONGTEXT NULL,
                    created_at DATETIME(6) NOT NULL,
                    PRIMARY KEY (id),
                    KEY idx_session_messages_session_id (session_id, id),
                    KEY idx_session_messages_user_session (user_id, session_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
        self._mysql_conn.commit()

    def _build_session(self, session_row: dict, message_rows: list[dict]) -> Session:
        messages = [
            Message(
                role=row["role"],
                content=row["content"],
                tool_calls=self._load_json(row["tool_calls"], []),
                citations=self._load_json(row["citations"], []),
                tool_call_id=row["tool_call_id"],
                tool_name=row["tool_name"],
                reasoning_content=row["reasoning_content"],
            )
            for row in message_rows
        ]
        context = self._load_json(session_row["context"], {})
        if not isinstance(context, dict):
            context = {}
        session_context = context.get("session_context")
        if not isinstance(session_context, dict):
            context["session_context"] = {}
        return Session(
            session_id=session_row["session_id"],
            user_id=session_row["user_id"],
            agent_id=session_row["agent_id"],
            messages=messages,
            plan=self._load_json(session_row["plan"], []),
            context=context,
            created_at=session_row["created_at"],
            updated_at=session_row["updated_at"],
        )

    @staticmethod
    def _dump_json(value) -> str:
        return json.dumps(value, ensure_ascii=False)

    @staticmethod
    def _load_json(value, default):
        if value in (None, ""):
            return default
        if isinstance(value, (dict, list)):
            return value
        return json.loads(value)

    @staticmethod
    def _normalize_mysql_charset(charset: str) -> str:
        normalized = (charset or "utf8mb4").strip().lower().replace("-", "")
        if normalized in {"utf8", "utf8mb3"}:
            return "utf8mb4"
        return normalized


def create_session_storage() -> SessionStorage:
    return MySQLSessionStorage()
