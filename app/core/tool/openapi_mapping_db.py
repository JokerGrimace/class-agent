import json
from typing import Any, Optional
from urllib.parse import parse_qsl, urlparse

from app.config import settings
from app.core.tool.registry import TOOL_TYPE_TO_NAMES


_VALID_PERMISSION_DECISIONS = {"allow", "ask", "deny"}
DEFAULT_COMMON_TOOL_NAMES = sorted(TOOL_TYPE_TO_NAMES.get("level1", set()))


class OpenApiToolMappingStorage:
    def initialize(self) -> None:
        return None

    def upsert_mapping(self, row: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def list_active_mapping_rows_by_operation(
        self,
        http_method: str,
        api_path: str,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    def list_active_mapping_rows_by_tool_name_and_page_name(
        self,
        tool_name: str,
        page_name: str,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    def list_active_mapping_rows_by_page_name(
        self,
        page_name: str,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    def list_active_tool_names_by_page_name(
        self,
        page_name: str,
    ) -> list[str]:
        raise NotImplementedError


class MySQLOpenApiToolMappingStorage(OpenApiToolMappingStorage):
    def __init__(self):
        self._mysql_conn: Optional[Any] = None
        self._initialized = False

    def initialize(self) -> None:
        self._ensure_initialized()

    def upsert_mapping(self, row: dict[str, Any]) -> dict[str, Any]:
        self._ensure_initialized()
        with self._mysql_conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO `openapi_tool_mapping_catalog` (
                    `http_method`,
                    `api_path`,
                    `tool_name`,
                    `tool_display_name`,
                    `page_name`,
                    `requires_page_match`,
                    `permission_decision`,
                    `permission_summary`,
                    `permission_deny_message`,
                    `support_user_revision`,
                    `visible_fields_json`,
                    `editable_fields_json`,
                    `is_active`
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    `tool_name` = VALUES(`tool_name`),
                    `tool_display_name` = VALUES(`tool_display_name`),
                    `requires_page_match` = VALUES(`requires_page_match`),
                    `permission_decision` = VALUES(`permission_decision`),
                    `permission_summary` = VALUES(`permission_summary`),
                    `permission_deny_message` = VALUES(`permission_deny_message`),
                    `support_user_revision` = VALUES(`support_user_revision`),
                    `visible_fields_json` = VALUES(`visible_fields_json`),
                    `editable_fields_json` = VALUES(`editable_fields_json`),
                    `is_active` = VALUES(`is_active`)
                """,
                (
                    row["http_method"],
                    row["api_path"],
                    row["tool_name"],
                    row.get("tool_display_name") or row["tool_name"],
                    row["page_name"],
                    1 if row["requires_page_match"] else 0,
                    row["permission_decision"],
                    row["permission_summary"],
                    row["permission_deny_message"],
                    1 if row["support_user_revision"] else 0,
                    json.dumps(row["visible_fields_json"], ensure_ascii=False),
                    json.dumps(row["editable_fields_json"], ensure_ascii=False),
                    1 if row["is_active"] else 0,
                ),
            )
        self._mysql_conn.commit()
        return dict(row)

    def list_active_mapping_rows_by_operation(
        self,
        http_method: str,
        api_path: str,
    ) -> list[dict[str, Any]]:
        self._ensure_initialized()
        with self._mysql_conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM `openapi_tool_mapping_catalog`
                WHERE `http_method` = %s
                  AND `api_path` = %s
                  AND `is_active` = 1
                ORDER BY `tool_name` ASC, `page_name` ASC
                """,
                (http_method, api_path),
            )
            rows = cursor.fetchall()
        return [normalized for row in rows if (normalized := self._normalize_row(row))]

    def list_active_mapping_rows_by_tool_name_and_page_name(
        self,
        tool_name: str,
        page_name: str,
    ) -> list[dict[str, Any]]:
        self._ensure_initialized()
        with self._mysql_conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM `openapi_tool_mapping_catalog`
                WHERE `tool_name` = %s
                  AND (
                    `page_name` = ''
                    OR `page_name` = %s
                  )
                  AND `is_active` = 1
                ORDER BY `page_name` DESC, `http_method` ASC, `api_path` ASC
                """,
                (tool_name, page_name),
            )
            rows = cursor.fetchall()
        return [normalized for row in rows if (normalized := self._normalize_row(row))]

    def list_active_mapping_rows_by_page_name(
        self,
        page_name: str,
    ) -> list[dict[str, Any]]:
        self._ensure_initialized()
        with self._mysql_conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM `openapi_tool_mapping_catalog`
                WHERE (
                    `page_name` = ''
                    OR `page_name` = %s
                  )
                  AND `is_active` = 1
                ORDER BY `tool_name` ASC, `page_name` DESC, `http_method` ASC, `api_path` ASC
                """,
                (page_name,),
            )
            rows = cursor.fetchall()
        return [normalized for row in rows if (normalized := self._normalize_row(row))]

    def list_active_tool_names_by_page_name(
        self,
        page_name: str,
    ) -> list[str]:
        self._ensure_initialized()
        with self._mysql_conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT `tool_name`
                FROM `openapi_tool_mapping_catalog`
                WHERE (
                    `page_name` = ''
                    OR `page_name` = %s
                  )
                  AND `is_active` = 1
                ORDER BY `tool_name` ASC
                """,
                (page_name,),
            )
            rows = cursor.fetchall()
        return sorted(
            {
                str((row or {}).get("tool_name") or "").strip()
                for row in rows
                if str((row or {}).get("tool_name") or "").strip()
            }
        )

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
                CREATE TABLE IF NOT EXISTS `openapi_tool_mapping_catalog` (
                    `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '涓婚敭 ID',
                    `http_method` VARCHAR(16) NOT NULL COMMENT 'HTTP 鏂规硶',
                    `api_path` VARCHAR(255) NOT NULL COMMENT 'OpenAPI 鎺ュ彛璺緞',
                    `tool_name` VARCHAR(255) NOT NULL COMMENT 'Agent 宸ュ叿鍚?',
                    `tool_display_name` VARCHAR(255) NOT NULL DEFAULT '' COMMENT 'Agent 工具中文展示名',
                    `page_name` VARCHAR(255) NOT NULL DEFAULT '' COMMENT '椤甸潰鍚嶇О',
                    `requires_page_match` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '鏄惁瑕佹眰褰撳墠椤甸潰绮剧‘鍛戒腑',
                    `permission_decision` VARCHAR(16) NOT NULL DEFAULT 'allow' COMMENT '椤甸潰绾ф潈闄愬喅绛?',
                    `permission_summary` VARCHAR(255) NOT NULL DEFAULT '' COMMENT '鏉冮檺鎻愮ず鏂囨',
                    `permission_deny_message` VARCHAR(255) NOT NULL DEFAULT '' COMMENT '鎷掔粷鎻愮ず鏂囨',
                    `support_user_revision` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '鏄惁鏀寔鐢ㄦ埛淇敼鍙傛暟',
                    `visible_fields_json` JSON NOT NULL COMMENT '瀹℃壒鏃跺彲瑙佸瓧娈靛垪琛?',
                    `editable_fields_json` JSON NOT NULL COMMENT '瀹℃壒鏃跺彲缂栬緫瀛楁鍒楄〃',
                    `is_active` TINYINT(1) NOT NULL DEFAULT 1 COMMENT '鏄惁鍚敤',
                    `current_time` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '鍒涘缓鏃堕棿',
                    `update_time` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
                        COMMENT '鏇存柊鏃堕棿'
                        ON UPDATE CURRENT_TIMESTAMP(6),
                    PRIMARY KEY (`id`),
                    UNIQUE KEY `uniq_openapi_tool_mapping_operation_scope` (
                        `http_method`,
                        `api_path`,
                        `page_name`
                    ),
                    KEY `idx_openapi_tool_mapping_operation_active` (
                        `http_method`,
                        `api_path`,
                        `is_active`
                    ),
                    KEY `idx_openapi_tool_mapping_tool_page_active` (
                        `tool_name`,
                        `page_name`,
                        `is_active`
                    )
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='OpenAPI 宸ュ叿鏄犲皠鐩綍琛?'
                """
            )
        self._mysql_conn.commit()

    @staticmethod
    def _normalize_row(row: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
        if not row:
            return None
        normalized = dict(row)
        normalized["tool_display_name"] = (
            str(normalized.get("tool_display_name") or "").strip()
            or str(normalized.get("tool_name") or "").strip()
        )
        normalized["requires_page_match"] = bool(normalized.get("requires_page_match"))
        normalized["support_user_revision"] = bool(normalized.get("support_user_revision"))
        normalized["is_active"] = bool(normalized.get("is_active"))
        normalized["visible_fields_json"] = MySQLOpenApiToolMappingStorage._normalize_string_list(
            normalized.get("visible_fields_json")
        )
        normalized["editable_fields_json"] = MySQLOpenApiToolMappingStorage._normalize_string_list(
            normalized.get("editable_fields_json")
        )
        return normalized

    @staticmethod
    def _normalize_string_list(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError:
                return []
            value = decoded
        if not isinstance(value, list):
            return []
        return [
            str(item).strip()
            for item in value
            if str(item).strip()
        ]

    @staticmethod
    def _normalize_mysql_charset(charset: str) -> str:
        normalized = (charset or "utf8mb4").strip().lower().replace("-", "")
        if normalized in {"utf8", "utf8mb3"}:
            return "utf8mb4"
        return normalized


class OpenApiToolMappingService:
    def __init__(
        self,
        storage: Optional[OpenApiToolMappingStorage] = None,
    ):
        self.storage = storage or MySQLOpenApiToolMappingStorage()

    def initialize(self) -> None:
        self.storage.initialize()

    def save_mapping(
        self,
        *,
        http_method: str,
        api_path: str,
        tool_name: str,
        tool_display_name: Optional[str] = None,
        page_name: str = "",
        requires_page_match: bool = False,
        permission_decision: str = "allow",
        permission_summary: str = "",
        permission_deny_message: str = "",
        support_user_revision: bool = False,
        visible_fields_json: Optional[list[str]] = None,
        editable_fields_json: Optional[list[str]] = None,
        is_active: bool = True,
    ) -> dict[str, Any]:
        normalized_http_method = (http_method or "").strip().upper()
        normalized_api_path = (api_path or "").strip()
        normalized_tool_name = (tool_name or "").strip()
        normalized_tool_display_name = (tool_display_name or "").strip()
        normalized_page_name = (page_name or "").strip()
        normalized_permission_decision = (permission_decision or "").strip().lower()
        normalized_visible_fields = self._normalize_string_list(visible_fields_json)
        normalized_editable_fields = self._normalize_string_list(editable_fields_json)

        if not normalized_http_method:
            raise ValueError("http_method must not be blank")
        if not normalized_api_path:
            raise ValueError("api_path must not be blank")
        if not normalized_tool_name:
            raise ValueError("tool_name must not be blank")
        if normalized_permission_decision not in _VALID_PERMISSION_DECISIONS:
            raise ValueError("permission_decision must be one of allow, ask, deny")
        invalid_fields = sorted(set(normalized_editable_fields) - set(normalized_visible_fields))
        if invalid_fields:
            raise ValueError(
                f"editable_fields_json must be a subset of visible_fields_json: {invalid_fields}"
            )

        row = {
                "http_method": normalized_http_method,
                "api_path": normalized_api_path,
                "tool_name": normalized_tool_name,
                "page_name": normalized_page_name,
                "requires_page_match": bool(requires_page_match),
                "permission_decision": normalized_permission_decision,
                "permission_summary": (permission_summary or "").strip(),
                "permission_deny_message": (permission_deny_message or "").strip(),
                "support_user_revision": bool(support_user_revision),
                "visible_fields_json": normalized_visible_fields,
                "editable_fields_json": normalized_editable_fields,
                "is_active": bool(is_active),
        }
        if normalized_tool_display_name:
            row["tool_display_name"] = normalized_tool_display_name
        return self.storage.upsert_mapping(row)

    def get_registration(
        self,
        http_method: str,
        api_path: str,
    ) -> Optional[dict[str, Any]]:
        normalized_http_method = (http_method or "").strip().upper()
        normalized_api_path = (api_path or "").strip()
        if not normalized_http_method or not normalized_api_path:
            return None

        rows = self.storage.list_active_mapping_rows_by_operation(
            normalized_http_method,
            normalized_api_path,
        )
        if not rows:
            return None

        tool_names = {
            str(row.get("tool_name") or "").strip()
            for row in rows
            if str(row.get("tool_name") or "").strip()
        }
        if len(tool_names) != 1:
            raise ValueError(
                f"inconsistent tool_name mapping for {normalized_http_method} {normalized_api_path}"
            )

        requires_page_match_values = {bool(row.get("requires_page_match")) for row in rows}
        if len(requires_page_match_values) != 1:
            raise ValueError(
                "inconsistent requires_page_match mapping for "
                f"{normalized_http_method} {normalized_api_path}"
            )

        page_names = sorted(
            {
                str(row.get("page_name") or "").strip()
                for row in rows
                if str(row.get("page_name") or "").strip()
            }
        )

        result = {
            "tool_name": next(iter(tool_names)),
            "page_names": page_names,
            "requires_page_match": next(iter(requires_page_match_values)),
        }
        display_name = self._optional_display_name(rows)
        if display_name:
            result["tool_display_name"] = display_name
        return result

    def get_page_permission(
        self,
        tool_name: str,
        page_name: str,
    ) -> Optional[dict[str, Any]]:
        normalized_tool_name = (tool_name or "").strip()
        normalized_page_name = (page_name or "").strip()
        if not normalized_tool_name:
            return None

        rows = self.storage.list_active_mapping_rows_by_tool_name_and_page_name(
            normalized_tool_name,
            normalized_page_name,
        )
        rows = self._select_page_visible_rows(rows, normalized_page_name)
        if not rows:
            return None

        result = {
            "permission_decision": self._single_value(rows, "permission_decision"),
            "permission_summary": self._single_value(rows, "permission_summary"),
            "permission_deny_message": self._single_value(rows, "permission_deny_message"),
            "support_user_revision": bool(self._single_value(rows, "support_user_revision")),
            "visible_fields_json": self._single_list_value(rows, "visible_fields_json"),
            "editable_fields_json": self._single_list_value(rows, "editable_fields_json"),
        }
        display_name = self._optional_display_name(rows)
        if display_name:
            result["tool_display_name"] = display_name
        return result

    def list_tool_names_for_page(self, page_name: str) -> list[str]:
        normalized_page_name = (page_name or "").strip()
        return self.storage.list_active_tool_names_by_page_name(normalized_page_name)

    def list_mappings_for_page(self, page_name: str) -> list[dict[str, Any]]:
        normalized_page_name = (page_name or "").strip()
        return self.storage.list_active_mapping_rows_by_page_name(normalized_page_name)

    def build_available_tool_names(
        self,
        page_name: Optional[str],
        common_tool_names: Optional[list[str]] = None,
    ) -> list[str]:
        tool_names = set(common_tool_names or DEFAULT_COMMON_TOOL_NAMES)
        tool_names.update(self.list_tool_names_for_page(page_name or ""))
        return sorted(tool_names)

    @staticmethod
    def _normalize_string_list(value: Optional[list[str]]) -> list[str]:
        if not value:
            return []
        return [
            str(item).strip()
            for item in value
            if str(item).strip()
        ]

    @staticmethod
    def _single_value(rows: list[dict[str, Any]], key: str) -> Any:
        values = {OpenApiToolMappingService._normalize_scalar(row.get(key)) for row in rows}
        if len(values) != 1:
            raise ValueError(f"inconsistent {key} mapping")
        return next(iter(values))

    @staticmethod
    def _single_list_value(rows: list[dict[str, Any]], key: str) -> list[str]:
        normalized_values = {
            tuple(OpenApiToolMappingService._normalize_string_list(row.get(key)))
            for row in rows
        }
        if len(normalized_values) != 1:
            raise ValueError(f"inconsistent {key} mapping")
        return list(next(iter(normalized_values)))

    @staticmethod
    def _optional_display_name(rows: list[dict[str, Any]]) -> str:
        tool_names = {
            str(row.get("tool_name") or "").strip()
            for row in rows
            if str(row.get("tool_name") or "").strip()
        }
        display_names = {
            str(row.get("tool_display_name") or "").strip()
            for row in rows
            if str(row.get("tool_display_name") or "").strip()
        }
        if len(tool_names) != 1 or len(display_names) != 1:
            return ""
        display_name = next(iter(display_names))
        return "" if display_name in tool_names else display_name

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
    def _normalize_scalar(value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value
