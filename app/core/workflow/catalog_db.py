import json
from typing import Any, Optional
from urllib.parse import parse_qsl, urlparse

from app.config import settings
from app.core.workflow.models import WorkflowDefinition


class WorkflowCatalogStorage:
    def initialize(self) -> None:
        return None

    def upsert_workflow(self, row: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def get_workflow_by_name(self, workflow_name: str) -> Optional[dict[str, Any]]:
        raise NotImplementedError

    def get_workflow_by_file_name(self, file_name: str) -> Optional[dict[str, Any]]:
        raise NotImplementedError

    def list_active_workflows(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    def upsert_page_mapping(self, row: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def list_active_workflows_by_page(self, page_code: str, limit: Optional[int] = None) -> list[dict[str, Any]]:
        raise NotImplementedError


class MySQLWorkflowCatalogStorage(WorkflowCatalogStorage):
    def __init__(self):
        self._mysql_conn: Optional[Any] = None
        self._initialized = False

    def initialize(self) -> None:
        self._ensure_initialized()

    def upsert_workflow(self, row: dict[str, Any]) -> dict[str, Any]:
        self._ensure_initialized()
        with self._mysql_conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO workflow_catalog (
                    workflow_name,
                    file_name,
                    title,
                    description,
                    when_to_use,
                    markdown_content,
                    definition_json,
                    definition_version,
                    required_inputs_json,
                    allowed_tools_json,
                    step_count,
                    status,
                    is_active,
                    sort_order,
                    tags_json,
                    notes,
                    created_by,
                    updated_by
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    file_name = VALUES(file_name),
                    title = VALUES(title),
                    description = VALUES(description),
                    when_to_use = VALUES(when_to_use),
                    markdown_content = VALUES(markdown_content),
                    definition_json = VALUES(definition_json),
                    definition_version = VALUES(definition_version),
                    required_inputs_json = VALUES(required_inputs_json),
                    allowed_tools_json = VALUES(allowed_tools_json),
                    step_count = VALUES(step_count),
                    status = VALUES(status),
                    is_active = VALUES(is_active),
                    sort_order = VALUES(sort_order),
                    tags_json = VALUES(tags_json),
                    notes = VALUES(notes),
                    created_by = COALESCE(created_by, VALUES(created_by)),
                    updated_by = VALUES(updated_by)
                """,
                (
                    row["workflow_name"],
                    row["file_name"],
                    row.get("title"),
                    row["description"],
                    row["when_to_use"],
                    row.get("markdown_content", ""),
                    self._dump_json(row["definition_json"]),
                    row["definition_version"],
                    self._dump_json(row.get("required_inputs_json")),
                    self._dump_json(row.get("allowed_tools_json")),
                    row["step_count"],
                    row["status"],
                    1 if row["is_active"] else 0,
                    row["sort_order"],
                    self._dump_json(row.get("tags_json")),
                    row.get("notes"),
                    row.get("created_by"),
                    row.get("updated_by"),
                ),
            )
        self._mysql_conn.commit()
        return self.get_workflow_by_name(row["workflow_name"]) or row

    def get_workflow_by_name(self, workflow_name: str) -> Optional[dict[str, Any]]:
        self._ensure_initialized()
        with self._mysql_conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM workflow_catalog
                WHERE workflow_name = %s
                LIMIT 1
                """,
                (workflow_name,),
            )
            row = cursor.fetchone()
        return self._normalize_row(row)

    def get_workflow_by_file_name(self, file_name: str) -> Optional[dict[str, Any]]:
        self._ensure_initialized()
        with self._mysql_conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM workflow_catalog
                WHERE file_name = %s
                LIMIT 1
                """,
                (file_name,),
            )
            row = cursor.fetchone()
        return self._normalize_row(row)

    def list_active_workflows(self ) -> list[dict[str, Any]]:
        self._ensure_initialized()
        sql = """
            SELECT *
            FROM workflow_catalog
            WHERE is_active = 1 AND status = 'active'
            ORDER BY sort_order ASC, workflow_name ASC
        """
        params: list[Any] = []


        with self._mysql_conn.cursor() as cursor:
            cursor.execute(sql, tuple(params))
            rows = cursor.fetchall()
        return [normalized for row in rows if (normalized := self._normalize_row(row))]

    def upsert_page_mapping(self, row: dict[str, Any]) -> dict[str, Any]:
        self._ensure_initialized()
        with self._mysql_conn.cursor() as cursor:
            try:
                cursor.execute(
                    """
                    INSERT INTO workflow_page_mapping (
                        page_code,
                        workflow_name,
                        is_active,
                        sort_order
                    ) VALUES (%s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        is_active = VALUES(is_active),
                        sort_order = VALUES(sort_order)
                    """,
                    (
                        row["page_code"],
                        row["workflow_name"],
                        1 if row["is_active"] else 0,
                        row["sort_order"],
                    ),
                )
            except Exception as exc:
                if not self._is_missing_sort_order_column(exc):
                    raise
                cursor.execute(
                    """
                    INSERT INTO workflow_page_mapping (
                        page_code,
                        workflow_name,
                        is_active
                    ) VALUES (%s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        is_active = VALUES(is_active)
                    """,
                    (
                        row["page_code"],
                        row["workflow_name"],
                        1 if row["is_active"] else 0,
                    ),
                )
        self._mysql_conn.commit()
        return dict(row)

    def list_active_workflows_by_page(self, page_code: str, limit: Optional[int] = None) -> list[dict[str, Any]]:
        self._ensure_initialized()
        sql = """
            SELECT wc.*
            FROM workflow_page_mapping wpm
            INNER JOIN workflow_catalog wc
                ON wc.workflow_name = wpm.workflow_name
            WHERE wpm.page_code = %s
              AND wpm.is_active = 1
              AND wc.is_active = 1
              AND wc.status = 'active'
            ORDER BY wpm.sort_order ASC, wc.sort_order ASC, wc.workflow_name ASC
        """
        fallback_sql = """
            SELECT wc.*
            FROM workflow_page_mapping wpm
            INNER JOIN workflow_catalog wc
                ON wc.workflow_name = wpm.workflow_name
            WHERE wpm.page_code = %s
              AND wpm.is_active = 1
              AND wc.is_active = 1
              AND wc.status = 'active'
            ORDER BY wc.sort_order ASC, wc.workflow_name ASC
        """
        params: list[Any] = [page_code]
        if limit is not None:
            sql += " LIMIT %s"
            fallback_sql += " LIMIT %s"
            params.append(limit)

        with self._mysql_conn.cursor() as cursor:
            try:
                cursor.execute(sql, tuple(params))
            except Exception as exc:
                if not self._is_missing_sort_order_column(exc):
                    raise
                cursor.execute(fallback_sql, tuple(params))
            rows = cursor.fetchall()
        return [normalized for row in rows if (normalized := self._normalize_row(row))]

    @staticmethod
    def _is_missing_sort_order_column(exc: Exception) -> bool:
        message = str(exc)
        return "Unknown column" in message and "sort_order" in message

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
    def _normalize_row(cls, row: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
        if not row:
            return None

        normalized = dict(row)
        normalized["definition_json"] = cls._load_json(normalized.get("definition_json"), {})
        normalized["required_inputs_json"] = cls._load_json(normalized.get("required_inputs_json"), [])
        normalized["allowed_tools_json"] = cls._load_json(normalized.get("allowed_tools_json"), [])
        normalized["tags_json"] = cls._load_json(normalized.get("tags_json"), [])
        normalized["is_active"] = bool(normalized.get("is_active"))
        return normalized

    @staticmethod
    def _normalize_mysql_charset(charset: str) -> str:
        normalized = (charset or "utf8mb4").strip().lower().replace("-", "")
        if normalized in {"utf8", "utf8mb3"}:
            return "utf8mb4"
        return normalized


class WorkflowCatalogService:
    def __init__(self, storage: Optional[WorkflowCatalogStorage] = None):
        self.storage = storage or MySQLWorkflowCatalogStorage()

    def initialize(self) -> None:
        self.storage.initialize()

    def save_workflow(
        self,
        workflow_name: str,
        file_name: str,
        title: Optional[str],
        description: str,
        when_to_use: str,
        definition_json: dict[str, Any],
        markdown_content: str = "",
        required_inputs_json: Optional[list[str]] = None,
        allowed_tools_json: Optional[list[str]] = None,
        status: str = "active",
        is_active: bool = True,
        sort_order: int = 0,
        tags_json: Optional[list[str]] = None,
        notes: Optional[str] = None,
        created_by: Optional[str] = None,
        updated_by: Optional[str] = None,
    ) -> dict[str, Any]:
        normalized_name = (workflow_name or "").strip()
        normalized_file_name = (file_name or "").strip()
        if not normalized_name:
            raise ValueError("workflow_name must not be blank")
        if not normalized_file_name:
            raise ValueError("file_name must not be blank")
        if not normalized_file_name.endswith(".md"):
            raise ValueError("file_name must end with .md")
        if not description.strip():
            raise ValueError("description must not be blank")
        if not when_to_use.strip():
            raise ValueError("when_to_use must not be blank")

        validated_definition = WorkflowDefinition.model_validate(definition_json)

        derived_required_inputs = []
        seen_inputs: set[str] = set()
        for step in validated_definition.steps:
            for input_key in step.pre_task_output:
                if input_key not in seen_inputs:
                    seen_inputs.add(input_key)
                    derived_required_inputs.append(input_key)

        row = {
            "workflow_name": normalized_name,
            "file_name": normalized_file_name,
            "title": (title or "").strip() or None,
            "description": description.strip(),
            "when_to_use": when_to_use.strip(),
            "markdown_content": markdown_content or "",
            "definition_json": validated_definition.model_dump(mode="json"),
            "definition_version": validated_definition.version,
            "required_inputs_json": required_inputs_json if required_inputs_json is not None else derived_required_inputs,
            "allowed_tools_json": allowed_tools_json if allowed_tools_json is not None else list(validated_definition.allowed_tools),
            "step_count": len(validated_definition.steps),
            "status": (status or "active").strip() or "active",
            "is_active": bool(is_active),
            "sort_order": int(sort_order),
            "tags_json": tags_json or [],
            "notes": notes,
            "created_by": created_by,
            "updated_by": updated_by,
        }
        return self.storage.upsert_workflow(row)

    def save_page_mapping(
        self,
        page_code: str,
        workflow_name: str,
        is_active: bool = True,
        sort_order: int = 0,
    ) -> dict[str, Any]:
        normalized_page_code = (page_code or "").strip()
        normalized_workflow_name = (workflow_name or "").strip()
        if not normalized_page_code:
            raise ValueError("page_code must not be blank")
        if not normalized_workflow_name:
            raise ValueError("workflow_name must not be blank")
        return self.storage.upsert_page_mapping(
            {
                "page_code": normalized_page_code,
                "workflow_name": normalized_workflow_name,
                "is_active": bool(is_active),
                "sort_order": int(sort_order),
            }
        )

    def list_prompt_summaries(
        self,
        limit: Optional[int] = None,
        page_code: Optional[str] = None,
    ) -> list[dict[str, str]]:
        normalized_page_code = (page_code or "").strip()
        if normalized_page_code:
            rows = self.storage.list_active_workflows_by_page(normalized_page_code, limit=limit)
        else:
            rows = self.storage.list_active_workflows()
        return [
            {
                "name": row["workflow_name"],
                "file_name": row["file_name"],
                "description": row["description"],
                "when_to_use": row["when_to_use"],
            }
            for row in rows
        ]

    def get_by_name(self, workflow_name: str) -> Optional[dict[str, Any]]:
        return self.storage.get_workflow_by_name(workflow_name)
