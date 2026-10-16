from __future__ import annotations

from typing import Any, Optional
from urllib.parse import parse_qsl, urlparse

from app.config import settings
from app.core.tool.readonly_sql import (
    SqlCatalogSnapshot,
    SqlColumnCatalog,
    SqlTableCatalog,
    build_catalog_snapshot,
)


class SqlQueryCatalogStorage:
    def initialize(self) -> None:
        return None

    def load_snapshot(self) -> SqlCatalogSnapshot:
        raise NotImplementedError

    def execute_select(self, sql: str, max_rows: int) -> tuple[list[dict[str, Any]], bool]:
        raise NotImplementedError


class MySQLSqlQueryCatalogStorage(SqlQueryCatalogStorage):
    def __init__(self):
        self._mysql_conn: Optional[Any] = None
        self._initialized = False

    def initialize(self) -> None:
        self._ensure_initialized()

    def load_snapshot(self) -> SqlCatalogSnapshot:
        self._ensure_initialized()
        return build_catalog_snapshot(
            tables=self._fetch_tables(),
            columns=self._fetch_columns(),
            relationships=self._fetch_rows("agent_sql_query_relationship_catalog"),
            lineages=self._fetch_rows("agent_sql_query_lineage_catalog"),
        )

    def execute_select(self, sql: str, max_rows: int) -> tuple[list[dict[str, Any]], bool]:
        self._ensure_initialized()
        with self._mysql_conn.cursor() as cursor:
            cursor.execute(sql)
            rows = cursor.fetchmany(max(1, max_rows) + 1)
        limited = len(rows) > max_rows
        return [dict(row) for row in rows[:max_rows]], limited

    def _fetch_tables(self) -> list[SqlTableCatalog]:
        with self._mysql_conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT `table_name`, `business_name`, `business_description`,
                       `course_scope_column`, `is_active`
                FROM `agent_sql_query_table_catalog`
                WHERE `is_active` = 1
                ORDER BY `table_name` ASC
                """
            )
            rows = cursor.fetchall()
        return [
            SqlTableCatalog(
                table_name=str(row.get("table_name") or "").strip(),
                business_name=str(row.get("business_name") or "").strip(),
                business_description=str(row.get("business_description") or "").strip(),
                course_scope_column=str(row.get("course_scope_column") or "").strip(),
                is_active=bool(row.get("is_active")),
            )
            for row in rows
        ]

    def _fetch_columns(self) -> list[SqlColumnCatalog]:
        with self._mysql_conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT `table_name`, `column_name`, `business_name`, `business_description`,
                       `data_category`, `is_queryable`, `is_dimension`, `is_metric`,
                       `is_sensitive`, `masking_rule`
                FROM `agent_sql_query_column_catalog`
                WHERE `is_queryable` = 1
                ORDER BY `table_name` ASC, `column_name` ASC
                """
            )
            rows = cursor.fetchall()
        return [
            SqlColumnCatalog(
                table_name=str(row.get("table_name") or "").strip(),
                column_name=str(row.get("column_name") or "").strip(),
                business_name=str(row.get("business_name") or "").strip(),
                business_description=str(row.get("business_description") or "").strip(),
                data_category=str(row.get("data_category") or "").strip(),
                is_queryable=bool(row.get("is_queryable")),
                is_dimension=bool(row.get("is_dimension")),
                is_metric=bool(row.get("is_metric")),
                is_sensitive=bool(row.get("is_sensitive")),
                masking_rule=str(row.get("masking_rule") or "").strip(),
            )
            for row in rows
        ]

    def _fetch_rows(self, table_name: str) -> list[dict[str, Any]]:
        with self._mysql_conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT *
                FROM `{table_name}`
                WHERE `is_active` = 1
                ORDER BY `id` ASC
                """
            )
            rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def _ensure_initialized(self) -> None:
        if self._initialized:
            self._mysql_conn.ping(reconnect=True)
            return
        self._mysql_conn = self._build_mysql_connection()
        self._create_tables()
        self._initialized = True

    def _build_mysql_connection(self) -> Any:
        import pymysql

        url = settings.mysql_readonly_url or settings.mysql_url
        username = settings.mysql_readonly_username or settings.mysql_username
        password = settings.mysql_readonly_password or settings.mysql_password
        parsed = urlparse(url.removeprefix("jdbc:"))
        query_params = dict(parse_qsl(parsed.query, keep_blank_values=True))
        charset = self._normalize_mysql_charset(
            query_params.get("characterEncoding", "utf8mb4")
        )
        database = parsed.path.lstrip("/")
        return pymysql.connect(
            host=parsed.hostname or "localhost",
            port=parsed.port or 3306,
            user=username,
            password=password,
            database=database,
            charset=charset,
            autocommit=True,
            cursorclass=pymysql.cursors.DictCursor,
        )

    def _create_tables(self) -> None:
        with self._mysql_conn.cursor() as cursor:
            cursor.execute(_CREATE_TABLE_CATALOG_SQL)
            cursor.execute(_CREATE_COLUMN_CATALOG_SQL)
            cursor.execute(_CREATE_RELATIONSHIP_CATALOG_SQL)
            cursor.execute(_CREATE_LINEAGE_CATALOG_SQL)
        self._mysql_conn.commit()

    @staticmethod
    def _normalize_mysql_charset(charset: str) -> str:
        normalized = (charset or "utf8mb4").strip().lower().replace("-", "")
        if normalized in {"utf8", "utf8mb3"}:
            return "utf8mb4"
        return normalized


class SqlQueryCatalogService:
    def __init__(self, storage: Optional[SqlQueryCatalogStorage] = None):
        self.storage = storage or MySQLSqlQueryCatalogStorage()

    def initialize(self) -> None:
        self.storage.initialize()

    def load_snapshot(self) -> SqlCatalogSnapshot:
        return self.storage.load_snapshot()

    def execute_select(self, sql: str, max_rows: int) -> tuple[list[dict[str, Any]], bool]:
        return self.storage.execute_select(sql, max_rows)


_CREATE_TABLE_CATALOG_SQL = """
CREATE TABLE IF NOT EXISTS `agent_sql_query_table_catalog` (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键 ID',
  `table_name` VARCHAR(255) NOT NULL COMMENT '物理表名',
  `business_name` VARCHAR(255) NOT NULL COMMENT '业务名称',
  `business_description` TEXT NOT NULL COMMENT '业务说明',
  `course_scope_column` VARCHAR(255) NOT NULL COMMENT '课程范围字段名',
  `is_active` TINYINT(1) NOT NULL DEFAULT 1 COMMENT '是否启用',
  `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间',
  `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_agent_sql_query_table_catalog_table_name` (`table_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Agent SQL 可查询表目录'
"""

_CREATE_COLUMN_CATALOG_SQL = """
CREATE TABLE IF NOT EXISTS `agent_sql_query_column_catalog` (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键 ID',
  `table_name` VARCHAR(255) NOT NULL COMMENT '物理表名',
  `column_name` VARCHAR(255) NOT NULL COMMENT '物理字段名',
  `business_name` VARCHAR(255) NOT NULL COMMENT '字段业务名称',
  `business_description` TEXT NOT NULL COMMENT '字段业务说明',
  `data_category` VARCHAR(64) NOT NULL COMMENT '数据分类',
  `is_queryable` TINYINT(1) NOT NULL DEFAULT 1 COMMENT '是否允许查询',
  `is_dimension` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否允许作为图表维度',
  `is_metric` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否允许作为图表指标',
  `is_sensitive` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否敏感字段',
  `masking_rule` VARCHAR(255) NOT NULL DEFAULT '' COMMENT '脱敏规则',
  `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间',
  `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_agent_sql_query_column_catalog_column` (`table_name`, `column_name`),
  KEY `idx_agent_sql_query_column_catalog_queryable` (`table_name`, `is_queryable`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Agent SQL 可查询字段目录'
"""

_CREATE_RELATIONSHIP_CATALOG_SQL = """
CREATE TABLE IF NOT EXISTS `agent_sql_query_relationship_catalog` (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键 ID',
  `source_table_name` VARCHAR(255) NOT NULL COMMENT '来源表名',
  `source_column_name` VARCHAR(255) NOT NULL COMMENT '来源字段名',
  `target_table_name` VARCHAR(255) NOT NULL COMMENT '目标表名',
  `target_column_name` VARCHAR(255) NOT NULL COMMENT '目标字段名',
  `relationship_type` VARCHAR(64) NOT NULL COMMENT '关系类型',
  `business_description` TEXT NOT NULL COMMENT '业务关系说明',
  `is_active` TINYINT(1) NOT NULL DEFAULT 1 COMMENT '是否启用',
  `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间',
  `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '更新时间',
  PRIMARY KEY (`id`),
  KEY `idx_agent_sql_query_relationship_source` (`source_table_name`, `is_active`),
  KEY `idx_agent_sql_query_relationship_target` (`target_table_name`, `is_active`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Agent SQL 表业务关系目录'
"""

_CREATE_LINEAGE_CATALOG_SQL = """
CREATE TABLE IF NOT EXISTS `agent_sql_query_lineage_catalog` (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键 ID',
  `source_table_name` VARCHAR(255) NOT NULL COMMENT '上游表名',
  `source_column_name` VARCHAR(255) NOT NULL DEFAULT '' COMMENT '上游字段名',
  `target_table_name` VARCHAR(255) NOT NULL COMMENT '下游表名',
  `target_column_name` VARCHAR(255) NOT NULL DEFAULT '' COMMENT '下游字段名',
  `lineage_type` VARCHAR(64) NOT NULL COMMENT '血缘类型',
  `transform_description` TEXT NOT NULL COMMENT '转换说明',
  `is_active` TINYINT(1) NOT NULL DEFAULT 1 COMMENT '是否启用',
  `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间',
  `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '更新时间',
  PRIMARY KEY (`id`),
  KEY `idx_agent_sql_query_lineage_source` (`source_table_name`, `is_active`),
  KEY `idx_agent_sql_query_lineage_target` (`target_table_name`, `is_active`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Agent SQL 表字段血缘目录'
"""
