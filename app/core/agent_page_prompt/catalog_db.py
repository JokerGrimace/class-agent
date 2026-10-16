from datetime import datetime
from typing import Any, Optional
from urllib.parse import parse_qsl, urlparse

from app.config import settings


class AgentPagePromptCatalogStorage:
    def initialize(self) -> None:
        return None

    def upsert_prompt(self, row: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def get_active_prompt_by_page(
        self,
        web_page_name: str,
    ) -> Optional[dict[str, Any]]:
        raise NotImplementedError


class MySQLAgentPagePromptCatalogStorage(AgentPagePromptCatalogStorage):
    def __init__(self):
        self._mysql_conn: Optional[Any] = None
        self._initialized = False

    def initialize(self) -> None:
        self._ensure_initialized()

    def upsert_prompt(self, row: dict[str, Any]) -> dict[str, Any]:
        self._ensure_initialized()
        now = datetime.utcnow()
        current_time = row.get("current_time") or now
        update_time = row.get("update_time") or now

        try:
            with self._mysql_conn.cursor() as cursor:
                if row["active"]:
                    cursor.execute(
                        """
                        UPDATE `agent_page_prompt_catalog`
                        SET `active` = 0, `update_time` = %s
                        WHERE `web_page_name` = %s
                          AND `active` = 1
                          AND `version` <> %s
                        """,
                        (
                            update_time,
                            row["web_page_name"],
                            row["version"],
                        ),
                    )

                    cursor.execute(
                        """
                    INSERT INTO `agent_page_prompt_catalog` (
                        `web_page_name`,
                        `content`,
                        `version`,
                        `active`,
                        `uploader`,
                        `current_time`,
                        `update_time`
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        `content` = VALUES(`content`),
                        `active` = VALUES(`active`),
                        `uploader` = VALUES(`uploader`),
                        `update_time` = VALUES(`update_time`)
                    """,
                    (
                        row["web_page_name"],
                        row["content"],
                        row["version"],
                        1 if row["active"] else 0,
                        row["uploader"],
                        current_time,
                        update_time,
                    ),
                )
            self._mysql_conn.commit()
        except Exception:
            self._mysql_conn.rollback()
            raise

        return self.get_prompt_by_page_and_version(
            web_page_name=row["web_page_name"],
            version=row["version"],
        ) or dict(row)

    def get_active_prompt_by_page(
        self,
        web_page_name: str,
    ) -> Optional[dict[str, Any]]:
        self._ensure_initialized()
        with self._mysql_conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM `agent_page_prompt_catalog`
                WHERE `web_page_name` = %s AND `active` = 1
                LIMIT 1
                """,
                (web_page_name,),
            )
            row = cursor.fetchone()
        return self._normalize_row(row)

    def get_prompt_by_page_and_version(
        self,
        *,
        web_page_name: str,
        version: str,
    ) -> Optional[dict[str, Any]]:
        self._ensure_initialized()
        with self._mysql_conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM `agent_page_prompt_catalog`
                WHERE `web_page_name` = %s AND `version` = %s
                LIMIT 1
                """,
                (web_page_name, version),
            )
            row = cursor.fetchone()
        return self._normalize_row(row)

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
                CREATE TABLE IF NOT EXISTS `agent_page_prompt_catalog` (
                    `id` BIGINT NOT NULL AUTO_INCREMENT,
                    `web_page_name` VARCHAR(255) NOT NULL,
                    `content` LONGTEXT NOT NULL,
                    `version` VARCHAR(64) NOT NULL,
                    `active` TINYINT(1) NOT NULL DEFAULT 0,
                    `uploader` VARCHAR(128) NOT NULL,
                    `current_time` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                    `update_time` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
                        ON UPDATE CURRENT_TIMESTAMP(6),
                    `active_web_page_name` VARCHAR(255)
                        GENERATED ALWAYS AS (
                            CASE
                                WHEN `active` = 1 THEN `web_page_name`
                                ELSE NULL
                            END
                        ) STORED,
                    PRIMARY KEY (`id`),
                    UNIQUE KEY `uniq_agent_page_prompt_page_version` (`web_page_name`, `version`),
                    UNIQUE KEY `uniq_agent_page_prompt_active_page` (`active_web_page_name`),
                    KEY `idx_agent_page_prompt_page_active` (`web_page_name`, `active`)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
        self._mysql_conn.commit()

    @staticmethod
    def _normalize_row(row: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
        if not row:
            return None
        normalized = dict(row)
        normalized["active"] = bool(normalized.get("active"))
        return normalized

    @staticmethod
    def _normalize_mysql_charset(charset: str) -> str:
        normalized = (charset or "utf8mb4").strip().lower().replace("-", "")
        if normalized in {"utf8", "utf8mb3"}:
            return "utf8mb4"
        return normalized


class AgentPagePromptCatalogService:
    def __init__(
        self,
        storage: Optional[AgentPagePromptCatalogStorage] = None,
    ):
        self.storage = storage or MySQLAgentPagePromptCatalogStorage()

    def initialize(self) -> None:
        self.storage.initialize()

    def save_prompt(
        self,
        *,
        web_page_name: str,
        content: str,
        version: str,
        uploader: str,
        active: bool,
    ) -> dict[str, Any]:
        normalized_web_page_name = (web_page_name or "").strip()
        normalized_content = content or ""
        normalized_version = (version or "").strip()
        normalized_uploader = (uploader or "").strip()

        if not normalized_web_page_name:
            raise ValueError("web_page_name must not be blank")
        if not normalized_content.strip():
            raise ValueError("content must not be blank")
        if not normalized_version:
            raise ValueError("version must not be blank")
        if not normalized_uploader:
            raise ValueError("uploader must not be blank")

        row = {
            "web_page_name": normalized_web_page_name,
            "content": normalized_content,
            "version": normalized_version,
            "active": bool(active),
            "uploader": normalized_uploader,
        }
        saved = self.storage.upsert_prompt(row)
        return {
            "web_page_name": saved["web_page_name"],
            "content": saved["content"],
            "version": saved["version"],
            "uploader": saved["uploader"],
            "active": bool(saved["active"]),
        }

    def get_active_prompt(
        self,
        web_page_name: str,
    ) -> Optional[dict[str, Any]]:
        normalized_web_page_name = (web_page_name or "").strip()
        if not normalized_web_page_name:
            return None
        row = self.storage.get_active_prompt_by_page(normalized_web_page_name)
        if not row:
            return None
        return {
            "web_page_name": row["web_page_name"],
            "content": row["content"],
            "version": row["version"],
            "uploader": row["uploader"],
            "active": bool(row["active"]),
        }
