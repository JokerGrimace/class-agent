import json
from typing import Any, Optional
from urllib.parse import parse_qsl, urlparse

from app.config import settings


_VALID_PERMISSION_DECISIONS = {"allow", "ask", "deny"}


class PythonToolCatalogStorage:
    def initialize(self) -> None:
        return None

    def upsert_tool(self, row: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def list_active_tool_rows_by_page_name(self, page_name: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    def list_active_tool_rows_by_tool_name_and_page_name(
        self,
        tool_name: str,
        page_name: str,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError


class MySQLPythonToolCatalogStorage(PythonToolCatalogStorage):
    def __init__(self):
        self._mysql_conn: Optional[Any] = None
        self._initialized = False

    def initialize(self) -> None:
        self._ensure_initialized()

    def upsert_tool(self, row: dict[str, Any]) -> dict[str, Any]:
        self._ensure_initialized()
        with self._mysql_conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO `agent_python_tool_catalog` (
                    `tool_name`,
                    `tool_display_name`,
                    `module_path`,
                    `page_name`,
                    `requires_page_match`,
                    `permission_decision`,
                    `permission_summary`,
                    `permission_deny_message`,
                    `visible_fields_json`,
                    `editable_fields_json`,
                    `is_active`,
                    `timeout_ms`,
                    `max_rows`
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    `tool_display_name` = VALUES(`tool_display_name`),
                    `module_path` = VALUES(`module_path`),
                    `requires_page_match` = VALUES(`requires_page_match`),
                    `permission_decision` = VALUES(`permission_decision`),
                    `permission_summary` = VALUES(`permission_summary`),
                    `permission_deny_message` = VALUES(`permission_deny_message`),
                    `visible_fields_json` = VALUES(`visible_fields_json`),
                    `editable_fields_json` = VALUES(`editable_fields_json`),
                    `is_active` = VALUES(`is_active`),
                    `timeout_ms` = VALUES(`timeout_ms`),
                    `max_rows` = VALUES(`max_rows`)
                """,
                (
                    row["tool_name"],
                    row.get("tool_display_name") or "",
                    row.get("module_path") or "",
                    row["page_name"],
                    1 if row["requires_page_match"] else 0,
                    row["permission_decision"],
                    row["permission_summary"],
                    row["permission_deny_message"],
                    json.dumps(row["visible_fields_json"], ensure_ascii=False),
                    json.dumps(row["editable_fields_json"], ensure_ascii=False),
                    1 if row["is_active"] else 0,
                    row["timeout_ms"],
                    row["max_rows"],
                ),
            )
        self._mysql_conn.commit()
        return dict(row)

    def list_active_tool_rows_by_page_name(self, page_name: str) -> list[dict[str, Any]]:
        self._ensure_initialized()
        with self._mysql_conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM `agent_python_tool_catalog`
                WHERE `is_active` = 1
                  AND (
                    `page_name` = ''
                    OR `page_name` = %s
                  )
                ORDER BY `tool_name` ASC, `page_name` ASC
                """,
                (page_name,),
            )
            rows = cursor.fetchall()
        return [normalized for row in rows if (normalized := self._normalize_row(row))]

    def list_active_tool_rows_by_tool_name_and_page_name(
        self,
        tool_name: str,
        page_name: str,
    ) -> list[dict[str, Any]]:
        self._ensure_initialized()
        with self._mysql_conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM `agent_python_tool_catalog`
                WHERE `tool_name` = %s
                  AND `is_active` = 1
                  AND (
                    `page_name` = ''
                    OR `page_name` = %s
                  )
                ORDER BY `page_name` DESC
                """,
                (tool_name, page_name),
            )
            rows = cursor.fetchall()
        return [normalized for row in rows if (normalized := self._normalize_row(row))]

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

    def _create_tables(self) -> None:
        with self._mysql_conn.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS `agent_python_tool_catalog` (
                    `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键 ID',
                    `tool_name` VARCHAR(255) NOT NULL COMMENT 'Python 端 Agent 工具名',
                    `tool_display_name` VARCHAR(255) NOT NULL DEFAULT '' COMMENT 'Agent 工具中文展示名',
                    `module_path` VARCHAR(255) NOT NULL DEFAULT '' COMMENT 'Python 工具模块路径',
                    `page_name` VARCHAR(255) NOT NULL DEFAULT '' COMMENT '页面标识，空字符串表示全局工具',
                    `requires_page_match` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否要求当前页面精确命中',
                    `permission_decision` VARCHAR(16) NOT NULL DEFAULT 'allow' COMMENT '页面级权限决策',
                    `permission_summary` VARCHAR(255) NOT NULL DEFAULT '' COMMENT '权限提示文案',
                    `permission_deny_message` VARCHAR(255) NOT NULL DEFAULT '' COMMENT '拒绝提示文案',
                    `visible_fields_json` JSON NOT NULL COMMENT '权限确认时可见字段',
                    `editable_fields_json` JSON NOT NULL COMMENT '权限确认时可编辑字段',
                    `is_active` TINYINT(1) NOT NULL DEFAULT 1 COMMENT '是否启用',
                    `timeout_ms` INT NOT NULL DEFAULT 5000 COMMENT '单次工具超时时间',
                    `max_rows` INT NOT NULL DEFAULT 500 COMMENT 'SQL 查询结果最大返回行数',
                    `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间',
                    `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
                        ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '更新时间',
                    PRIMARY KEY (`id`),
                    UNIQUE KEY `uniq_agent_python_tool_catalog_tool_page` (`tool_name`, `page_name`),
                    KEY `idx_agent_python_tool_catalog_page_active` (`page_name`, `is_active`),
                    KEY `idx_agent_python_tool_catalog_tool_active` (`tool_name`, `is_active`)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Agent Python 端工具管理表'
                """
            )
            cursor.execute(
                """
                SELECT COUNT(*) AS column_count
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'agent_python_tool_catalog'
                  AND COLUMN_NAME = 'module_path'
                """
            )
            row = cursor.fetchone() or {}
            if int(row.get("column_count") or 0) == 0:
                cursor.execute(
                    """
                    ALTER TABLE `agent_python_tool_catalog`
                    ADD COLUMN `module_path` VARCHAR(255) NOT NULL DEFAULT ''
                    COMMENT 'Python 工具模块路径'
                    AFTER `tool_display_name`
                    """
                )
        self._mysql_conn.commit()

    @staticmethod
    def _normalize_row(row: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
        if not row:
            return None
        normalized = dict(row)
        normalized["module_path"] = str(normalized.get("module_path") or "").strip()
        normalized["requires_page_match"] = bool(normalized.get("requires_page_match"))
        normalized["is_active"] = bool(normalized.get("is_active"))
        normalized["visible_fields_json"] = MySQLPythonToolCatalogStorage._normalize_string_list(
            normalized.get("visible_fields_json")
        )
        normalized["editable_fields_json"] = MySQLPythonToolCatalogStorage._normalize_string_list(
            normalized.get("editable_fields_json")
        )
        normalized["timeout_ms"] = int(normalized.get("timeout_ms") or 5000)
        normalized["max_rows"] = int(normalized.get("max_rows") or 500)
        return normalized

    @staticmethod
    def _normalize_string_list(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return []
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    @staticmethod
    def _normalize_mysql_charset(charset: str) -> str:
        normalized = (charset or "utf8mb4").strip().lower().replace("-", "")
        if normalized in {"utf8", "utf8mb3"}:
            return "utf8mb4"
        return normalized


class PythonToolCatalogService:
    def __init__(self, storage: Optional[PythonToolCatalogStorage] = None):
        self.storage = storage or MySQLPythonToolCatalogStorage()

    def initialize(self) -> None:
        self.storage.initialize()

    def save_tool(
        self,
        *,
        tool_name: str,
        tool_display_name: str = "",
        module_path: str = "",
        page_name: str = "",
        requires_page_match: bool = False,
        permission_decision: str = "allow",
        permission_summary: str = "",
        permission_deny_message: str = "",
        visible_fields_json: Optional[list[str]] = None,
        editable_fields_json: Optional[list[str]] = None,
        is_active: bool = True,
        timeout_ms: int = 5000,
        max_rows: int = 500,
    ) -> dict[str, Any]:
        normalized_tool_name = (tool_name or "").strip()
        normalized_page_name = (page_name or "").strip()
        normalized_permission_decision = (permission_decision or "").strip().lower()
        if not normalized_tool_name:
            raise ValueError("tool_name must not be blank")
        if normalized_permission_decision not in _VALID_PERMISSION_DECISIONS:
            raise ValueError("permission_decision must be one of allow, ask, deny")
        visible_fields = self._normalize_string_list(visible_fields_json)
        editable_fields = self._normalize_string_list(editable_fields_json)
        invalid_fields = sorted(set(editable_fields) - set(visible_fields))
        if invalid_fields:
            raise ValueError(
                f"editable_fields_json must be a subset of visible_fields_json: {invalid_fields}"
            )
        row = {
            "tool_name": normalized_tool_name,
            "tool_display_name": (tool_display_name or "").strip(),
            "module_path": (module_path or "").strip(),
            "page_name": normalized_page_name,
            "requires_page_match": bool(requires_page_match),
            "permission_decision": normalized_permission_decision,
            "permission_summary": (permission_summary or "").strip(),
            "permission_deny_message": (permission_deny_message or "").strip(),
            "visible_fields_json": visible_fields,
            "editable_fields_json": editable_fields,
            "is_active": bool(is_active),
            "timeout_ms": int(timeout_ms or 5000),
            "max_rows": int(max_rows or 500),
        }
        return self.storage.upsert_tool(row)

    def list_tool_rows_for_page(self, page_name: str) -> list[dict[str, Any]]:
        normalized_page_name = (page_name or "").strip()
        rows = self.storage.list_active_tool_rows_by_page_name(normalized_page_name)
        return [
            row
            for row in rows
            if self._row_visible_on_page(row, normalized_page_name)
            and str(row.get("tool_name") or "").strip()
        ]

    def list_tool_names_for_page(self, page_name: str) -> list[str]:
        return sorted(
            {
                str(row.get("tool_name") or "").strip()
                for row in self.list_tool_rows_for_page(page_name)
            }
        )

    def get_page_permission(self, tool_name: str, page_name: str) -> Optional[dict[str, Any]]:
        normalized_tool_name = (tool_name or "").strip()
        normalized_page_name = (page_name or "").strip()
        if not normalized_tool_name:
            return None
        rows = self.storage.list_active_tool_rows_by_tool_name_and_page_name(
            normalized_tool_name,
            normalized_page_name,
        )
        visible_rows = [
            row
            for row in rows
            if self._row_visible_on_page(row, normalized_page_name)
        ]
        visible_rows = self._select_page_visible_rows(
            visible_rows,
            normalized_page_name,
        )
        if not visible_rows:
            return None
        row = visible_rows[0]
        return {
            "permission_decision": str(row.get("permission_decision") or "allow").strip(),
            "permission_summary": str(row.get("permission_summary") or "").strip(),
            "permission_deny_message": str(row.get("permission_deny_message") or "").strip(),
            "visible_fields_json": list(row.get("visible_fields_json") or []),
            "editable_fields_json": list(row.get("editable_fields_json") or []),
            "tool_display_name": str(row.get("tool_display_name") or "").strip(),
            "timeout_ms": int(row.get("timeout_ms") or 5000),
            "max_rows": int(row.get("max_rows") or 500),
        }

    @staticmethod
    def _row_visible_on_page(row: dict[str, Any], page_name: str) -> bool:
        row_page_name = str(row.get("page_name") or "").strip()
        if bool(row.get("requires_page_match")):
            return bool(page_name) and row_page_name == page_name
        return row_page_name in {"", page_name}

    @staticmethod
    def _select_page_visible_rows(
        rows: list[dict[str, Any]],
        page_name: str,
    ) -> list[dict[str, Any]]:
        exact_page_rows = [
            row
            for row in rows
            if str(row.get("page_name") or "").strip() == page_name and page_name
        ]
        if exact_page_rows:
            return exact_page_rows
        return [
            row
            for row in rows
            if str(row.get("page_name") or "").strip() == ""
        ]

    @staticmethod
    def _normalize_string_list(value: Optional[list[str]]) -> list[str]:
        if not value:
            return []
        return [str(item).strip() for item in value if str(item).strip()]
