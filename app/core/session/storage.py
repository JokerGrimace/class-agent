import json
from contextlib import contextmanager
from typing import Any, Optional
from urllib.parse import parse_qsl, urlparse

from app.config import settings
from app.core.agent.compaction_marker import COMPACTION_MARKER_PREFIX
from app.core.session.models import Message, Session


class SessionStorage:
    def add_message(self, session: Session) -> None:
        raise NotImplementedError

    def add_messages(self, session: Session, messages: list[Message]) -> None:
        raise NotImplementedError

    def load(self, session_id: str, user_id: str, user_role: str = "") -> Optional[Session]:
        raise NotImplementedError

    def delete(self, session_id: str, user_id: str, user_role: str = "") -> bool:
        raise NotImplementedError

    def save_session(self, session: Session) -> None:
        raise NotImplementedError

    def list_sessions(self, user_id: str, user_role: str = "") -> list[str]:
        raise NotImplementedError

    def load_with_scope(
        self,
        session_id: str,
        user_id: str,
        user_role: str = "",
        *,
        current_workflow_page: Optional[str] = None,
        course_code: Optional[str] = None,
    ) -> Optional[Session]:
        session = self.load(session_id, user_id, user_role=user_role)
        if not session or not session.matches_scope(
            current_workflow_page=current_workflow_page,
            course_code=course_code,
        ):
            return None
        return session

    def delete_with_scope(
        self,
        session_id: str,
        user_id: str,
        user_role: str = "",
        *,
        current_workflow_page: Optional[str] = None,
        course_code: Optional[str] = None,
    ) -> bool:
        session = self.load_with_scope(
            session_id,
            user_id,
            user_role=user_role,
            current_workflow_page=current_workflow_page,
            course_code=course_code,
        )
        if not session:
            return False
        return self.delete(session_id, user_id, user_role=user_role)

    def list_sessions_with_scope(
        self,
        user_id: str,
        user_role: str = "",
        *,
        current_workflow_page: Optional[str] = None,
        course_code: Optional[str] = None,
    ) -> list[str]:
        session_ids: list[str] = []
        for session_id in self.list_sessions(user_id, user_role=user_role):
            session = self.load(session_id, user_id, user_role=user_role)
            if not session or not session.matches_scope(
                current_workflow_page=current_workflow_page,
                course_code=course_code,
            ):
                continue
            session_ids.append(session_id)
        return session_ids

    def list_session_summaries_with_scope(
        self,
        user_id: str,
        user_role: str = "",
        *,
        current_workflow_page: Optional[str] = None,
        course_code: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Return list-page data while retaining compatibility with old stores."""
        summaries: list[dict[str, Any]] = []
        for session_id in self.list_sessions_with_scope(
            user_id,
            user_role=user_role,
            current_workflow_page=current_workflow_page,
            course_code=course_code,
        ):
            session = self.load(session_id, user_id, user_role=user_role)
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
                INSERT INTO sessions (session_id, user_id, user_role, agent_id, current_workflow_page, course_code, plan, context, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON DUPLICATE KEY
                UPDATE
                    user_id = VALUES(user_id), user_role = VALUES(user_role), agent_id = VALUES(agent_id),
                    current_workflow_page = VALUES(current_workflow_page),
                    course_code = VALUES(course_code),
                    plan = VALUES(plan), context = VALUES(context),
                    created_at = VALUES(created_at), updated_at = VALUES(updated_at)
                """,
                (
                    session.session_id,
                    session.user_id,
                    session.user_role,
                    session.agent_id,
                    session.current_workflow_page,
                    session.course_code,
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
                        assistant_payload,
                        created_at
                    ) VALUES (%s,%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                            self._dump_json(message.assistant_payload),
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
                    assistant_payload,
                    created_at
                ) VALUES (%s,%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                        self._dump_json(message.assistant_payload),
                        session.updated_at,
                    )
                    for message in messages
                ],
            )
        self._mysql_conn.commit()

    def load(self, session_id: str, user_id: str, user_role: str = "") -> Optional[Session]:
        self._ensure_initialized()

        with self._read_cursor() as cursor:
            cursor.execute(
                """
                select 
                t1.session_id,
                t1.user_id,
                t1.user_role,
                t1.agent_id,
                t1.current_workflow_page,
                t1.course_code,
                t2.plan,
                t1.context, 
                t1.created_at, 
                t1.updated_at
                from 
                (SELECT session_id, user_id, user_role, agent_id, current_workflow_page, course_code, plan, context, created_at, updated_at
                FROM sessions
                WHERE session_id = %s AND user_id = %s AND (%s = '' OR user_role = %s)) t1 
                left join 
                (select session_id,user_id,plan from session_plan where plan_status = 0 
                ) t2 
                on t1.session_id = t2.session_id and t1.user_id = t2.user_id
                """,
                (session_id, user_id, user_role, user_role),
            )
            session_row = cursor.fetchone()
            if not session_row:
                return None

            cursor.execute(
                """
                SELECT role, content, tool_calls, citations, tool_call_id, tool_name, reasoning_content, assistant_payload
                FROM session_messages
                WHERE session_id = %s AND user_id = %s
                ORDER BY id ASC
                """,
                (session_id, user_id),
            )
            message_rows = cursor.fetchall()

        return self._build_session(session_row, message_rows)

    def delete(self, session_id: str, user_id: str, user_role: str = "") -> bool:
        self._ensure_initialized()

        with self._mysql_conn.cursor() as cursor:
            # 删除session
            cursor.execute(
                "DELETE FROM sessions WHERE session_id = %s AND user_id = %s AND (%s = '' OR user_role = %s)",
                (session_id, user_id, user_role, user_role),
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

    def list_sessions(self, user_id: str, user_role: str = "") -> list[str]:
        self._ensure_initialized()

        with self._read_cursor() as cursor:
            cursor.execute(
                """
                SELECT session_id
                FROM sessions
                WHERE user_id = %s AND (%s = '' OR user_role = %s)
                ORDER BY updated_at DESC
                """,
                (user_id, user_role, user_role),
            )
            rows = cursor.fetchall()
        return [row["session_id"] for row in rows]

    def load_with_scope(
        self,
        session_id: str,
        user_id: str,
        user_role: str = "",
        *,
        current_workflow_page: Optional[str] = None,
        course_code: Optional[str] = None,
    ) -> Optional[Session]:
        self._ensure_initialized()
        where_clauses = ["session_id = %s", "user_id = %s"]
        params: list[str] = [session_id, user_id]
        if user_role:
            where_clauses.append("user_role = %s")
            params.append(user_role)
        self._append_scope_filter(
            where_clauses,
            params,
            current_workflow_page=current_workflow_page,
            course_code=course_code,
        )

        with self._read_cursor() as cursor:
            cursor.execute(
                f"""
                SELECT session_id
                FROM sessions
                WHERE {' AND '.join(where_clauses)}
                """,
                tuple(params),
            )
            row = cursor.fetchone()
        if not row:
            return None
        return self.load(session_id, user_id, user_role=user_role)

    def delete_with_scope(
        self,
        session_id: str,
        user_id: str,
        user_role: str = "",
        *,
        current_workflow_page: Optional[str] = None,
        course_code: Optional[str] = None,
    ) -> bool:
        session = self.load_with_scope(
            session_id,
            user_id,
            user_role=user_role,
            current_workflow_page=current_workflow_page,
            course_code=course_code,
        )
        if not session:
            return False
        return self.delete(session_id, user_id, user_role=user_role)

    def list_sessions_with_scope(
        self,
        user_id: str,
        user_role: str = "",
        *,
        current_workflow_page: Optional[str] = None,
        course_code: Optional[str] = None,
    ) -> list[str]:
        self._ensure_initialized()
        where_clauses = ["user_id = %s"]
        params: list[str] = [user_id]
        if user_role:
            where_clauses.append("user_role = %s")
            params.append(user_role)
        self._append_scope_filter(
            where_clauses,
            params,
            current_workflow_page=current_workflow_page,
            course_code=course_code,
        )

        with self._read_cursor() as cursor:
            cursor.execute(
                f"""
                SELECT session_id
                FROM sessions
                WHERE {' AND '.join(where_clauses)}
                ORDER BY updated_at DESC
                """,
                tuple(params),
            )
            rows = cursor.fetchall()
        return [row["session_id"] for row in rows]

    def list_session_summaries_with_scope(
        self,
        user_id: str,
        user_role: str = "",
        *,
        current_workflow_page: Optional[str] = None,
        course_code: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Fetch scoped list data without loading every session transcript."""
        self._ensure_initialized()
        where_clauses = ["s.user_id = %s"]
        scope_params: list[str] = [user_id]
        if user_role:
            where_clauses.append("s.user_role = %s")
            scope_params.append(user_role)
        self._append_scope_filter(
            where_clauses,
            scope_params,
            current_workflow_page=current_workflow_page,
            course_code=course_code,
            table_alias="s",
        )

        with self._read_cursor() as cursor:
            cursor.execute(
                f"""
                SELECT
                    s.session_id,
                    LEFT(COALESCE((
                        SELECT m.content
                        FROM session_messages AS m
                        WHERE m.session_id = s.session_id
                          AND m.user_id = s.user_id
                          AND m.role <> 'tool'
                          AND NOT (
                              m.role = 'user'
                              AND LEFT(m.content, CHAR_LENGTH(%s)) = %s
                          )
                          AND NOT (
                              m.role = 'assistant'
                              AND m.content NOT REGEXP '[^[:space:]]'
                          )
                        ORDER BY m.id DESC
                        LIMIT 1
                    ), ''), 40) AS preview,
                    s.updated_at,
                    s.current_workflow_page,
                    s.course_code
                FROM sessions AS s
                WHERE {' AND '.join(where_clauses)}
                ORDER BY s.updated_at DESC
                """,
                tuple([COMPACTION_MARKER_PREFIX, COMPACTION_MARKER_PREFIX, *scope_params]),
            )
            rows = cursor.fetchall()

        return [
            {
                "session_id": row["session_id"],
                "preview": row["preview"] or "",
                "updated_at": row["updated_at"],
                "current_workflow_page": row.get("current_workflow_page"),
                "course_code": row.get("course_code"),
            }
            for row in rows
        ]

    @staticmethod
    def _append_scope_filter(
        where_clauses: list[str],
        params: list[str],
        *,
        current_workflow_page: Optional[str] = None,
        course_code: Optional[str] = None,
        table_alias: Optional[str] = None,
    ) -> None:
        for column_name, value in (
            ("current_workflow_page", current_workflow_page),
            ("course_code", course_code),
        ):
            if value is None:
                continue
            qualified_column = f"{table_alias}.{column_name}" if table_alias else column_name
            where_clauses.append(f"{qualified_column} = %s")
            params.append(value)

    @contextmanager
    def _read_cursor(self):
        # 历史读取复用连接，但不能跨请求保留可重复读快照；
        # 成功、空结果和异常均结束只读事务，让下次查询看到其他连接刚保存的回复。
        try:
            with self._mysql_conn.cursor() as cursor:
                yield cursor
        finally:
            self._mysql_conn.rollback()

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
                    user_role VARCHAR(32) NOT NULL DEFAULT '',
                    agent_id VARCHAR(128) NOT NULL,
                    current_workflow_page VARCHAR(128) NULL,
                    course_code VARCHAR(128) NULL,
                    plan JSON NOT NULL,
                    context JSON NOT NULL,
                    created_at DATETIME(6) NOT NULL,
                    updated_at DATETIME(6) NOT NULL,
                    PRIMARY KEY (session_id),
                    KEY idx_sessions_user_id (user_id),
                    KEY idx_sessions_user_role (user_role),
                    KEY idx_sessions_user_updated_at (user_id, updated_at),
                    KEY idx_sessions_scope (user_id, user_role, current_workflow_page, course_code)
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
                    assistant_payload JSON NULL,
                    created_at DATETIME(6) NOT NULL,
                    PRIMARY KEY (id),
                    KEY idx_session_messages_session_id (session_id, id),
                    KEY idx_session_messages_user_session (user_id, session_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            self._ensure_sessions_scope_columns(cursor)
            self._ensure_sessions_scope_index(cursor)
            self._ensure_session_messages_assistant_payload_column(cursor)
        self._mysql_conn.commit()

    def _ensure_session_messages_assistant_payload_column(self, cursor: Any) -> None:
        cursor.execute("SHOW COLUMNS FROM session_messages LIKE %s", ("assistant_payload",))
        if cursor.fetchone():
            return
        cursor.execute(
            """
            ALTER TABLE session_messages
            ADD COLUMN assistant_payload JSON NULL AFTER reasoning_content
            """
        )

    def _ensure_sessions_scope_columns(self, cursor: Any) -> None:
        for column_name, column_definition in (
            ("user_role", "VARCHAR(32) NOT NULL DEFAULT ''"),
            ("current_workflow_page", "VARCHAR(128) NULL"),
            ("course_code", "VARCHAR(128) NULL"),
        ):
            cursor.execute("SHOW COLUMNS FROM sessions LIKE %s", (column_name,))
            if cursor.fetchone():
                continue
            cursor.execute(
                f"ALTER TABLE sessions ADD COLUMN {column_name} {column_definition}"
            )

    def _ensure_sessions_scope_index(self, cursor: Any) -> None:
        cursor.execute("SHOW INDEX FROM sessions")
        if any(row.get("Key_name") == "idx_sessions_scope" for row in cursor.fetchall()):
            return
        cursor.execute(
            """
            ALTER TABLE sessions
            ADD INDEX idx_sessions_scope (user_id, user_role, current_workflow_page, course_code)
            """
        )

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
                assistant_payload=self._load_json(row.get("assistant_payload"), None),
            )
            for row in message_rows
        ]
        context = self._load_json(session_row["context"], {})
        if not isinstance(context, dict):
            context = {}
        session_context = context.get("session_context")
        if not isinstance(session_context, dict):
            session_context = {}
            context["session_context"] = session_context
        return Session(
            session_id=session_row["session_id"],
            user_id=session_row["user_id"],
            user_role=str(session_row.get("user_role") or ""),
            agent_id=session_row["agent_id"],
            current_workflow_page=self._resolve_scope_column(
                session_row.get("current_workflow_page"),
                session_context,
                "current_workflow_page",
            ),
            course_code=self._resolve_scope_column(
                session_row.get("course_code"),
                session_context,
                "course_code",
            ),
            messages=messages,
            plan=self._load_json(session_row["plan"], []),
            context=context,
            created_at=session_row["created_at"],
            updated_at=session_row["updated_at"],
        )

    @staticmethod
    def _resolve_scope_column(
        stored_value: Any,
        session_context: dict[str, Any],
        key: str,
    ) -> Optional[str]:
        if isinstance(stored_value, str) and stored_value.strip():
            return stored_value
        fallback_value = session_context.get(key)
        if isinstance(fallback_value, str) and fallback_value.strip():
            return fallback_value
        return None

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
