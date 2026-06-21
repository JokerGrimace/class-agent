from datetime import datetime
import json
from typing import Any, Callable, Optional
from urllib.parse import parse_qsl, urlparse

from app.config import settings
from app.core.tool.registry import registry
from app.core.workflow.models import WorkflowDefinition, WorkflowRun, WorkflowTask, WorkflowStatus, WorkflowTaskStatus

ACTIVE_WORKFLOW_STATUSES = ("pending", "running")
TERMINAL_WORKFLOW_STATUSES = ("completed", "failed", "cancelled")
TERMINAL_TASK_STATUSES = ("completed", "failed", "cancelled")

class WorkflowStorage:
    def initialize(self) -> None:
        raise NotImplementedError

    # 同时创建一条工作流实例 + 批量关联任务（事务原子操作）
    def create_run_with_tasks(
        self,
        run: WorkflowRun,
        tasks: list[WorkflowTask],
    ) -> WorkflowRun:
        raise NotImplementedError

    # 根据run_id查询整条工作流实例
    def get_run(self, workflow_run_id: str) -> Optional[WorkflowRun]:
        raise NotImplementedError

    # 查询某条工作流下所有任务
    def list_tasks(self, workflow_run_id: str) -> list[WorkflowTask]:
        raise NotImplementedError

    # 更新工作流整体状态（完成/失败/取消）
    def update_run_status(
        self,
        workflow_run_id: str,
        status: WorkflowStatus,
        error_json: Optional[dict[str, Any]] = None,
    ) -> WorkflowRun:
        raise NotImplementedError

    # 更新单个任务状态、结果、报错信息
    def update_task_status(
        self,
        workflow_run_id: str,
        task_id: str,
        status: WorkflowTaskStatus,
        result_json: Optional[dict[str, Any]] = None,
        error_json: Optional[dict[str, Any]] = None,
    ) -> WorkflowTask:
        raise NotImplementedError

    # 服务重启时，把残留未结束的脏流程标记失败
    def fail_stale_active_runs(self) -> None:
        raise NotImplementedError
    # 扫描还在运行的workflow
    def get_active_run_for_owner(
        self,
        *,
        session_id: str,
        user_id: str,
        coordinator_agent_id: str,
    ) -> Optional[WorkflowRun]:
        raise NotImplementedError

class MySQLWorkflowStorage(WorkflowStorage):
    def __init__(self):
        self._mysql_conn: Optional[Any] = None
        self._initialized = False

    def initialize(self) -> None:
        self._ensure_initialized()
        self._create_tables()

    def create_run_with_tasks(
        self,
        run: WorkflowRun,
        tasks: list[WorkflowTask],
    ) -> WorkflowRun:
        self._ensure_initialized()

        try:
            with self._mysql_conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT workflow_run_id
                    FROM workflow_runs
                    WHERE session_id = %s
                      AND user_id = %s
                      AND status IN ('pending', 'running')
                    FOR UPDATE
                    """,
                    (run.session_id, run.user_id),
                )
                active_row = cursor.fetchone()
                if active_row:
                    raise ValueError(
                        "active workflow run already exists for this session and user"
                    )

                cursor.execute(
                    """
                    INSERT INTO workflow_runs (
                        workflow_run_id,
                        workflow_name,
                        summary_task_id,
                        user_id,
                        session_id,
                        coordinator_agent_id,
                        status,
                        global_context_json,
                        definition_snapshot_json,
                        error_json,
                        created_at,
                        updated_at,
                        finished_at,
                        expires_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        run.workflow_run_id,
                        run.workflow_name,
                        run.summary_task_id,
                        run.user_id,
                        run.session_id,
                        run.coordinator_agent_id,
                        run.status,
                        self._dump_json(run.global_context_json),
                        self._dump_json(
                            run.definition_snapshot_json.model_dump(mode="json")
                        ),
                        self._dump_json(run.error_json),
                        run.created_at,
                        run.updated_at,
                        run.finished_at,
                        run.expires_at,
                    ),
                )

                for task in tasks:
                    cursor.execute(
                        """
                        INSERT INTO workflow_tasks (
                            workflow_run_id,
                            task_id,
                            status,
                            result_json,
                            error_json,
                            created_at,
                            updated_at,
                            finished_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            task.workflow_run_id,
                            task.task_id,
                            task.status,
                            self._dump_json(task.result_json),
                            self._dump_json(task.error_json),
                            task.created_at,
                            task.updated_at,
                            task.finished_at,
                        ),
                    )

            self._mysql_conn.commit()
        except Exception:
            self._mysql_conn.rollback()
            raise

        return self.get_run(run.workflow_run_id) or run

    def get_run(self, workflow_run_id: str) -> Optional[WorkflowRun]:
        self._ensure_initialized()

        with self._mysql_conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM workflow_runs
                WHERE workflow_run_id = %s
                LIMIT 1
                """,
                (workflow_run_id,),
            )
            row = cursor.fetchone()

        if not row:
            return None

        return self._row_to_run(row)

    def list_tasks(self, workflow_run_id: str) -> list[WorkflowTask]:
        self._ensure_initialized()

        with self._mysql_conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM workflow_tasks
                WHERE workflow_run_id = %s
                ORDER BY created_at ASC, task_id ASC
                """,
                (workflow_run_id,),
            )
            rows = cursor.fetchall()

        return [self._row_to_task(row) for row in rows]

    def update_run_status(
        self,
        workflow_run_id: str,
        status: WorkflowStatus,
        error_json: Optional[dict[str, Any]] = None,
    ) -> WorkflowRun:
        self._ensure_initialized()

        now = datetime.now()
        finished_at = now if status in TERMINAL_WORKFLOW_STATUSES else None

        with self._mysql_conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE workflow_runs
                SET status = %s,
                    error_json = %s,
                    updated_at = %s,
                    finished_at = %s
                WHERE workflow_run_id = %s
                """,
                (
                    status,
                    self._dump_json(error_json),
                    now,
                    finished_at,
                    workflow_run_id,
                ),
            )

        self._mysql_conn.commit()

        run = self.get_run(workflow_run_id)
        if run is None:
            raise ValueError(f"WorkflowRun not found: {workflow_run_id}")
        return run

    def update_task_status(
        self,
        workflow_run_id: str,
        task_id: str,
        status: WorkflowTaskStatus,
        result_json: Optional[dict[str, Any]] = None,
        error_json: Optional[dict[str, Any]] = None,
    ) -> WorkflowTask:
        self._ensure_initialized()

        now = datetime.now()
        finished_at = now if status in TERMINAL_TASK_STATUSES else None

        with self._mysql_conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE workflow_tasks
                SET status = %s,
                    result_json = %s,
                    error_json = %s,
                    updated_at = %s,
                    finished_at = %s
                WHERE workflow_run_id = %s
                  AND task_id = %s
                """,
                (
                    status,
                    self._dump_json(result_json),
                    self._dump_json(error_json),
                    now,
                    finished_at,
                    workflow_run_id,
                    task_id,
                ),
            )

            cursor.execute(
                """
                SELECT *
                FROM workflow_tasks
                WHERE workflow_run_id = %s
                  AND task_id = %s
                LIMIT 1
                """,
                (workflow_run_id, task_id),
            )
            row = cursor.fetchone()

        self._mysql_conn.commit()

        if not row:
            raise ValueError(f"WorkflowTask not found: {workflow_run_id}/{task_id}")
        return self._row_to_task(row)

    def fail_stale_active_runs(self) -> None:
        self._ensure_initialized()

        now = datetime.now()

        try:
            with self._mysql_conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT workflow_run_id
                    FROM workflow_runs
                    WHERE status IN ('pending', 'running')
                    """
                )
                rows = cursor.fetchall()
                workflow_run_ids = [row["workflow_run_id"] for row in rows]

                for workflow_run_id in workflow_run_ids:
                    cursor.execute(
                        """
                        UPDATE workflow_runs
                        SET status = 'failed',
                            error_json = %s,
                            updated_at = %s,
                            finished_at = %s
                        WHERE workflow_run_id = %s
                        """,
                        (
                            self._dump_json(
                                {
                                    "error_type": "stale_workflow_run",
                                    "error_message": "WorkflowRun was active during service startup.",
                                }
                            ),
                            now,
                            now,
                            workflow_run_id,
                        ),
                    )
                    cursor.execute(
                        """
                        UPDATE workflow_tasks
                        SET status = 'cancelled',
                            updated_at = %s,
                            finished_at = %s
                        WHERE workflow_run_id = %s
                          AND status IN ('pending', 'running')
                        """,
                        (now, now, workflow_run_id),
                    )

            self._mysql_conn.commit()
        except Exception:
            self._mysql_conn.rollback()
            raise

    def _create_tables(self) -> None:
        with self._mysql_conn.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS workflow_runs (
                    workflow_run_id VARCHAR(64) NOT NULL,
                    workflow_name VARCHAR(128) NOT NULL,
                    summary_task_id VARCHAR(128) NOT NULL,
                    user_id VARCHAR(128) NOT NULL,
                    session_id VARCHAR(64) NOT NULL,
                    coordinator_agent_id VARCHAR(128) NOT NULL,
                    status VARCHAR(32) NOT NULL,
                    global_context_json JSON NOT NULL,
                    definition_snapshot_json JSON NOT NULL,
                    error_json JSON NULL,
                    created_at DATETIME(6) NOT NULL,
                    updated_at DATETIME(6) NOT NULL,
                    finished_at DATETIME(6) NULL,
                    expires_at DATETIME(6) NOT NULL,
                    PRIMARY KEY (workflow_run_id),
                    KEY idx_workflow_runs_session_user_status (session_id, user_id, status),
                    KEY idx_workflow_runs_expires_at (expires_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS workflow_tasks (
                    workflow_run_id VARCHAR(64) NOT NULL,
                    task_id VARCHAR(128) NOT NULL,
                    status VARCHAR(32) NOT NULL,
                    result_json JSON NULL,
                    error_json JSON NULL,
                    created_at DATETIME(6) NOT NULL,
                    updated_at DATETIME(6) NOT NULL,
                    finished_at DATETIME(6) NULL,
                    PRIMARY KEY (workflow_run_id, task_id),
                    KEY idx_workflow_tasks_run_status (workflow_run_id, status),
                    CONSTRAINT fk_workflow_tasks_run
                        FOREIGN KEY (workflow_run_id)
                        REFERENCES workflow_runs (workflow_run_id)
                        ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
        self._mysql_conn.commit()

    def _ensure_initialized(self) -> None:
        if self._initialized:
            self._mysql_conn.ping(reconnect=True)
            return

        self._mysql_conn = self._build_mysql_connection()
        self._initialized = True

    def _build_mysql_connection(self) -> Any:
        import pymysql

        parsed = urlparse(settings.mysql_url.removeprefix("jdbc:"))
        query_params = dict(parse_qsl(parsed.query, keep_blank_values=True))
        charset = self._normalize_mysql_charset(
            query_params.get("characterEncoding", "utf8mb4")
        )
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

    @staticmethod
    def _dump_json(value: Any) -> Optional[str]:
        if value is None:
            return None
        return json.dumps(value, ensure_ascii=False)

    @staticmethod
    def _load_json(value: Any, default: Any) -> Any:
        if value in (None, ""):
            return default
        if isinstance(value, (dict, list)):
            return value
        return json.loads(value)

    @classmethod
    def _row_to_run(cls, row: dict[str, Any]) -> WorkflowRun:
        payload = dict(row)
        payload["global_context_json"] = cls._load_json(
            payload.get("global_context_json"),
            {},
        )
        payload["definition_snapshot_json"] = WorkflowDefinition.model_validate(
            cls._load_json(payload.get("definition_snapshot_json"), {})
        )
        payload["error_json"] = cls._load_json(payload.get("error_json"), None)
        return WorkflowRun.model_validate(payload)

    @classmethod
    def _row_to_task(cls, row: dict[str, Any]) -> WorkflowTask:
        payload = dict(row)
        payload["result_json"] = cls._load_json(payload.get("result_json"), None)
        payload["error_json"] = cls._load_json(payload.get("error_json"), None)
        return WorkflowTask.model_validate(payload)

    @staticmethod
    def _normalize_mysql_charset(charset: str) -> str:
        normalized = (charset or "utf8mb4").strip().lower().replace("-", "")
        if normalized in {"utf8", "utf8mb3"}:
            return "utf8mb4"
        return normalized

    def get_active_run_for_owner(
            self,
            *,
            session_id: str,
            user_id: str,
            coordinator_agent_id: str,
    ) -> Optional[WorkflowRun]:
        self._ensure_initialized()

        with self._mysql_conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM workflow_runs
                WHERE session_id = %s
                  AND user_id = %s
                  AND coordinator_agent_id = %s
                  AND status IN ('pending', 'running')
                ORDER BY created_at DESC LIMIT 1
                """,
                (session_id, user_id, coordinator_agent_id),
            )
            row = cursor.fetchone()

        if not row:
            return None

        return self._row_to_run(row)


def create_workflow_storage() -> WorkflowStorage:
    return MySQLWorkflowStorage()
