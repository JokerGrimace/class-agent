# ADR-001: Read-only course SQL tool and chart rendering

## Status

Accepted

## Date

2026-07-27

## Context

`iclass-agent` needs a page-scoped tool that helps teachers and students query student information records for the current course. The tool must execute read-only SQL queries. It must not execute DDL, DML, transaction control, privilege control, stored procedure calls, or multi-statement SQL.

The current `iclass-agent` runtime already has page-scoped Tool visibility through `runtime_context.current_workflow_page`, `ToolVisibilityMetadata`, and `openapi_tool_mapping_catalog`. The runtime also has `assistant_payload` in non-stream responses and `tool_result.meta` in stream events. These existing contracts are retained.

The exact page identifiers, queryable business tables, table fields, and business lineage are not present in the current source tree. They must be provided by committed SQL/config files, database records, interface documents, logs, tests, or packet captures before implementation writes concrete values.

## Decision

Implement one Python-side Agent Tool for controlled read-only SQL query execution inside `iclass-agent`. The tool is not a Java OpenAPI business operation, because its core responsibility is query governance, metadata resolution, SQL validation, execution, and result packaging inside the Agent harness.

Use MySQL as the primary configuration source for:

- page-level access to this tool
- queryable table catalog
- queryable column catalog
- business relationship catalog
- lineage catalog
- chart rendering policy

Use a versioned repository SQL seed file as the reviewable source for initial table creation and baseline metadata. Runtime reads from MySQL.

Render query results in `iclass3demo` with Apache ECharts through a Vue wrapper. The front end receives structured chart payloads from `assistant_payload` or stream `tool_result.meta`, converts them in the front-end API layer, and passes the resulting chart option into the chart component.

## Runtime Flow

```text
Vue page
  -> POST /v1/agent/send
  -> runtime_context.current_workflow_page
  -> AgentEngine
  -> ToolRegistry page filtering
  -> readonly_course_sql_query
  -> page access catalog
  -> table/column/lineage catalog
  -> SQL parser and validator
  -> MySQL read-only connection
  -> ToolResult.meta.assistant_payload
  -> iclass3demo front-end API adapter
  -> Vue ECharts component
```

## Tool Contract

Tool name:

```text
readonly_course_sql_query
```

Input schema:

```json
{
  "type": "object",
  "properties": {
    "question": {
      "type": "string",
      "description": "用户关于当前课程学生信息记录的查询问题。"
    },
    "sql": {
      "type": "string",
      "description": "模型生成的只读 SQL。"
    },
    "chart_intent": {
      "type": "object",
      "description": "图表展示意图，只允许声明图表类型、维度字段、指标字段和标题。"
    }
  },
  "required": ["question", "sql"],
  "additionalProperties": false
}
```

The tool receives `session_id`, `user_id`, and `current_workflow_page` from the existing runtime context. Course identity must come from an exact runtime context field confirmed by front-end code, packet capture, or test fixture before implementation. This ADR does not define that field name.

## Page Access Configuration

Use a dedicated MySQL table:

```sql
CREATE TABLE IF NOT EXISTS `agent_readonly_sql_tool_page_access` (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键 ID',
  `page_name` VARCHAR(255) NOT NULL COMMENT '页面标识，必须与 runtime_context.current_workflow_page 完全一致',
  `tool_name` VARCHAR(255) NOT NULL DEFAULT 'readonly_course_sql_query' COMMENT 'Agent 工具名',
  `is_active` TINYINT(1) NOT NULL DEFAULT 1 COMMENT '是否启用',
  `max_rows` INT NOT NULL DEFAULT 200 COMMENT '单次查询最大返回行数',
  `timeout_ms` INT NOT NULL DEFAULT 5000 COMMENT '单次查询超时时间',
  `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间',
  `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_agent_readonly_sql_tool_page_access` (`page_name`, `tool_name`),
  KEY `idx_agent_readonly_sql_tool_page_access_active` (`page_name`, `is_active`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Agent 只读 SQL 工具页面准入表';
```

The tool is visible only when all conditions hold:

1. `runtime_context.current_workflow_page` is non-empty.
2. `agent_readonly_sql_tool_page_access` contains an active row for that exact page.
3. The registered Tool visibility contains the same page name with `requires_page_match = true`.

## Metadata Catalog

Queryable tables:

```sql
CREATE TABLE IF NOT EXISTS `agent_sql_query_table_catalog` (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键 ID',
  `table_name` VARCHAR(255) NOT NULL COMMENT '物理表名',
  `business_name` VARCHAR(255) NOT NULL COMMENT '业务名称',
  `business_description` TEXT NOT NULL COMMENT '业务说明',
  `course_scope_expression` VARCHAR(255) NOT NULL COMMENT '课程范围过滤表达式模板',
  `is_active` TINYINT(1) NOT NULL DEFAULT 1 COMMENT '是否启用',
  `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间',
  `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_agent_sql_query_table_catalog_table_name` (`table_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Agent SQL 可查询表目录';
```

Queryable columns:

```sql
CREATE TABLE IF NOT EXISTS `agent_sql_query_column_catalog` (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键 ID',
  `table_name` VARCHAR(255) NOT NULL COMMENT '物理表名',
  `column_name` VARCHAR(255) NOT NULL COMMENT '物理字段名',
  `business_name` VARCHAR(255) NOT NULL COMMENT '字段业务名称',
  `business_description` TEXT NOT NULL COMMENT '字段业务说明',
  `data_category` VARCHAR(64) NOT NULL COMMENT '数据分类',
  `is_queryable` TINYINT(1) NOT NULL DEFAULT 1 COMMENT '是否允许查询',
  `is_dimension` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否允许作为维度',
  `is_metric` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否允许作为指标',
  `is_sensitive` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否敏感字段',
  `masking_rule` VARCHAR(255) NOT NULL DEFAULT '' COMMENT '脱敏规则',
  `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间',
  `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_agent_sql_query_column_catalog_column` (`table_name`, `column_name`),
  KEY `idx_agent_sql_query_column_catalog_queryable` (`table_name`, `is_queryable`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Agent SQL 可查询字段目录';
```

Business relationships:

```sql
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Agent SQL 表业务关系目录';
```

Lineage:

```sql
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Agent SQL 表字段血缘目录';
```

## SQL Safety Rules

The tool must validate SQL before execution with a real SQL parser. String prefix checks are not a security boundary.

Required enforcement:

1. Exactly one SQL statement.
2. Root statement type is `SELECT`.
3. No `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `REPLACE`, `CREATE`, `ALTER`, `DROP`, `TRUNCATE`, `RENAME`, `GRANT`, `REVOKE`, `CALL`, `EXPLAIN`, `ANALYZE`, `LOCK`, `UNLOCK`, `SET`, `USE`, `SHOW`, `DESCRIBE`, or transaction control.
4. All referenced tables exist in `agent_sql_query_table_catalog` with `is_active = 1`.
5. All referenced columns exist in `agent_sql_query_column_catalog` with `is_queryable = 1`.
6. Query contains the confirmed current-course filter, injected by code from runtime context.
7. Query contains a deterministic `LIMIT` no larger than page access `max_rows`; code injects or lowers the limit.
8. Query runs through a MySQL account with database-level read-only privileges and no DDL/DML privileges.
9. Query execution uses timeout and row-count caps.
10. Returned sensitive columns use the configured masking rule.

Use `sqlglot` for AST parsing and table/column extraction in Python. The implementation must pin the package version in `pyproject.toml` or the project dependency lockfile before use.

## Prompt Context

The Agent must not infer table names or field names from user text. Before exposing this tool, `SystemPromptBuilder` or a dedicated tool context provider must inject a compact metadata summary generated from the catalog:

- allowed table names
- allowed column names
- business descriptions
- relationship descriptions
- lineage descriptions
- chartable dimensions
- chartable metrics

The prompt summary is derived from the catalog; it is not maintained separately in page AGENTS.md.

## Chart Rendering Contract

Tool result `meta.assistant_payload` uses this shape:

```json
{
  "reply_text": "查询完成。",
  "action_type": "readonly_course_sql_query",
  "needs_confirmation": false,
  "pending_action": null,
  "ui_refresh_hint": null,
  "result_payload": {
    "type": "chart",
    "chart": {
      "library": "echarts",
      "chart_type": "bar",
      "title": "图表标题",
      "dimensions": [],
      "measures": [],
      "dataset": {
        "columns": [],
        "rows": []
      }
    },
    "table": {
      "columns": [],
      "rows": []
    },
    "sql_audit": {
      "tables": [],
      "columns": [],
      "row_count": 0
    }
  }
}
```

The backend returns data and chart intent. The frontend API layer builds the concrete ECharts option. Page components and display components do not call the Agent HTTP endpoint directly.

## Frontend Rendering Library

Use Apache ECharts as the chart engine and a Vue ECharts wrapper in `iclass3demo`.

Reasons:

- Apache ECharts is open source under the Apache License 2.0.
- It supports bar, line, pie, scatter, heatmap, dataset transforms, tooltips, legends, and responsive resizing.
- Vue wrappers allow `option` to remain declarative in Vue components.

The exact `iclass3demo` package manager, dependency file, and Vue wrapper import path must be confirmed from the real `iclass3demo` source before implementation. This repository snapshot does not contain `iclass3demo/package.json`.

## Frontend Integration Boundary

`iclass3demo` must use this direction:

```text
Vue page
  -> front-end business API
  -> unified request client
  -> POST /v1/agent/send
  -> assistant_payload mapper
  -> chart display component
```

Rules:

1. Pages pass `runtime_context.current_workflow_page`.
2. Pages do not build ECharts options directly.
3. Display components receive props only.
4. The front-end API mapper validates `assistant_payload.result_payload.type = "chart"`.
5. The chart component receives a completed ECharts option.
6. Raw SQL is not displayed unless a future product requirement explicitly adds an audit view.

## Implementation Tasks

1. Confirm exact page identifiers from `iclass3demo` runtime context code or packet capture.
2. Confirm exact current-course context field from `iclass3demo` runtime context code or packet capture.
3. Create SQL migration/seed file for the catalog tables in `iclass-agent/scripts/`.
4. Add catalog storage and service modules under `app/core/tool/`.
5. Add `readonly_course_sql_query` under `app/tools/`.
6. Register the tool with page visibility from `agent_readonly_sql_tool_page_access`.
7. Add SQL parser validation with `sqlglot`.
8. Add MySQL read-only execution using a dedicated read-only database account.
9. Add `assistant_payload` chart result packaging.
10. Add unit tests for page gating, SQL validation, catalog loading, masking, and payload shape.
11. Add front-end API mapper and ECharts component in `iclass3demo` after the real frontend path is provided.
12. Add browser verification for chart rendering after frontend implementation.

## Acceptance Criteria

1. On pages without an active `agent_readonly_sql_tool_page_access` row, the model cannot see or call `readonly_course_sql_query`.
2. On active pages, the model sees `readonly_course_sql_query` only when `runtime_context.current_workflow_page` exactly matches an active row.
3. Non-`SELECT` SQL returns a failed `ToolResult` before database execution.
4. Multi-statement SQL returns a failed `ToolResult` before database execution.
5. SQL referencing an unapproved table or field returns a failed `ToolResult` before database execution.
6. Every successful query is constrained to the current course.
7. Every successful query obeys the configured row limit and timeout.
8. Sensitive fields are masked according to catalog configuration.
9. Successful chartable results include `assistant_payload.result_payload.type = "chart"`.
10. `iclass3demo` renders charts through Apache ECharts/Vue ECharts rather than handwritten chart code.

## References

- `iclass-agent/docs/iclass-agent-project-guide.md`
- `iclass-agent/docs/codex-harness/modules/tool-runtime.md`
- `iclass-agent/docs/codex-harness/modules/permission-governance.md`
- Apache ECharts official site: https://echarts.apache.org/
- Apache ECharts GitHub repository: https://github.com/apache/echarts
- Vue ECharts GitHub repository: https://github.com/ecomfe/vue-echarts
- sqlglot GitHub repository: https://github.com/tobymao/sqlglot
