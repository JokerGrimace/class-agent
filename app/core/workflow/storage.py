import json
from typing import Any, Optional
from urllib.parse import parse_qsl, urlparse

from app.config import settings
from app.core.workflow.models import Workflow


class WorkflowStorage:
    def initialize(self) -> None:
        return None

    def save_workflow(self, workflow: Workflow) -> None:
        raise NotImplementedError

    def get_workflow(self, workflow_id: str, user_id: str) -> Optional[Workflow]:
        raise NotImplementedError

    def get_active_workflow(self, session_id: str, user_id: str) -> Optional[Workflow]:
        raise NotImplementedError

    def delete_workflow(self, workflow_id: str, user_id: str) -> bool:
        raise NotImplementedError


class MySQLWorkflowStorage(WorkflowStorage):
    def __init__(self):
        self._mysql_conn: Optional[Any] = None
        self._initialized = False

    def initialize(self) -> None:
        self._ensure_initialized()

    def save_workflow(self, workflow: Workflow) -> None:
        self._ensure_initialized()
        with self._mysql_conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO workflows (
                    workflow_id,
                    session_id,
                    user_id,
                    agent_id,
                    workflow_name,
                    version,
                    status,
                    definition,
                    context,
                    created_at,
                    updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    session_id = VALUES(session_id),
                    user_id = VALUES(user_id),
                    agent_id = VALUES(agent_id),
                    workflow_name = VALUES(workflow_name),
                    version = VALUES(version),
                    status = VALUES(status),
                    definition = VALUES(definition),
                    context = VALUES(context),
                    created_at = VALUES(created_at),
                    updated_at = VALUES(updated_at)
                """,
                (
                    workflow.workflow_id,
                    workflow.session_id,
                    workflow.user_id,
                    workflow.agent_id,
                    workflow.workflow_name,
                    workflow.version,
                    workflow.status,
                    self._dump_json(workflow.definition.model_dump(mode="json")),
                    self._dump_json(workflow.context.model_dump(mode="json")),
                    workflow.created_at,
                    workflow.updated_at,
                ),
            )
        self._mysql_conn.commit()


    def get_workflow(self, workflow_id: str, user_id: str) -> Optional[Workflow]:
        self._ensure_initialized()
        self._mysql_conn.commit()
        with self._mysql_conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT workflow_id, session_id, user_id, agent_id, workflow_name,
                       version, status, definition, context, created_at, updated_at
                FROM workflows
                WHERE workflow_id = %s AND user_id = %s
                """,
                (workflow_id, user_id),
            )
            row = cursor.fetchone()
        if not row:
            return None
        return self._build_workflow(row)

    def get_active_workflow(self, session_id: str, user_id: str) -> Optional[Workflow]:
        self._ensure_initialized()
        with self._mysql_conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT workflow_id, session_id, user_id, agent_id, workflow_name,
                       version, status, definition, context, created_at, updated_at
                FROM workflows
                WHERE session_id = %s AND user_id = %s
                  AND status IN ('pending', 'running', 'waiting_user_input')
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (session_id, user_id),
            )
            row = cursor.fetchone()
        if not row:
            return None
        return self._build_workflow(row)

    def delete_workflow(self, workflow_id: str, user_id: str) -> bool:
        self._ensure_initialized()
        with self._mysql_conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM workflows WHERE workflow_id = %s AND user_id = %s",
                (workflow_id, user_id),
            )
            deleted = cursor.rowcount > 0
        self._mysql_conn.commit()
        return deleted

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
                CREATE TABLE IF NOT EXISTS workflows (
                    workflow_id VARCHAR(64) NOT NULL,
                    session_id VARCHAR(64) NOT NULL,
                    user_id VARCHAR(128) NOT NULL,
                    agent_id VARCHAR(128) NOT NULL,
                    workflow_name VARCHAR(128) NOT NULL,
                    version INT NOT NULL,
                    status VARCHAR(32) NOT NULL,
                    definition JSON NOT NULL,
                    context JSON NOT NULL,
                    created_at DATETIME(6) NOT NULL,
                    updated_at DATETIME(6) NOT NULL,
                    PRIMARY KEY (workflow_id),
                    KEY idx_workflows_session_user (session_id, user_id),
                    KEY idx_workflows_user_status (user_id, status)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
        self._mysql_conn.commit()

    def _build_workflow(self, row: dict[str, Any]) -> Workflow:
        return Workflow(
            workflow_id=row["workflow_id"],
            session_id=row["session_id"],
            user_id=row["user_id"],
            agent_id=row["agent_id"],
            workflow_name=row["workflow_name"],
            version=row["version"],
            status=row["status"],
            definition=self._load_json(row["definition"], {}),
            context=self._load_json(row["context"], {}),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
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


def create_workflow_storage() -> WorkflowStorage:
    return MySQLWorkflowStorage()
