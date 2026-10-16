# ADR-002：只读课程 SQL 工具与 pc-web 图表展示方案

## 状态

Accepted

## 日期

2026-07-27

## 取代关系

本文档取代 `ADR-001-readonly-course-sql-tool-and-chart-rendering.md`。

取代原因：

- 已重新读取本地 `/path/to/iclass3demo` 前端仓库。
- `@iclass/pc-web` 已安装 `echarts`，未安装 `vue-echarts`。
- 当前前端 Agent 接入链路、页面标识、运行上下文字段、SSE 事件解析均已在本地源码中确认。

## 结论

在 `iclass-agent` 中新增两个独立工具：

```text
readonly_course_sql_query
render_query_result_chart
```

`readonly_course_sql_query` 只服务“当前课程相关学生信息记录查询”，只执行单条 `SELECT`，DDL、DML、事务控制、权限控制、存储过程调用、多语句 SQL 全部在执行前拦截。

`render_query_result_chart` 负责校验并包装 Agent 自行组织的图表展示 JSON，生成前端展示载荷。SQL 工具不生成前端图表载荷，制图工具不访问数据库，也不直接接收 SQL 工具的原始返回对象。

Python 端工具页面准入、可查询表、可查询字段、业务关系、血缘关系、图表策略全部使用 MySQL catalog 显式维护。仓库内提供 SQL 建表与初始化脚本，运行时读取 MySQL。

前端展示落在 `/path/to/pc-web`。该包已依赖 `echarts`，因此图表展示直接基于 `echarts` 封装 Vue 组件，不引入 `vue-echarts`。

## 已读取证据路径

`iclass-agent`：

- `AGENTS.md`
- `docs/iclass-agent-project-guide.md`
- `docs/codex-harness/modules/tool-runtime.md`
- `docs/codex-harness/modules/permission-governance.md`
- `app/core/tool/registry.py`
- `app/core/tool/executor.py`
- `app/api/v1/agent.py`

`iclass3demo`：

- `/path/to/AGENTS.md`
- `/path/to/package.json`
- `/path/to/pnpm-workspace.yaml`
- `/path/to/package.json`
- `/path/to/agentApi.ts`
- `/path/to/agentRuntimeContext.ts`
- `/path/to/AssistantShell.vue`
- `/path/to/assistantUploadAction.ts`
- `/path/to/DistributionChart.vue`
- `/path/to/useCourseRouteContext.ts`
- `/path/to/assistantPageConfigs.ts`

## 前端事实

`/path/to/package.json` 定义 monorepo：

```text
packages/*
mock
```

`/path/to/package.json` 中已存在：

```text
echarts: ^6.1.0
vue: ^3.5.13
pinia: ^2.3.0
vue-router: ^4.5.0
element-plus: ^2.9.0
```

`/path/to/DistributionChart.vue` 已使用：

```text
echarts/core
echarts/charts
echarts/components
echarts/renderers
```

因此图表方案使用当前已接入的 `echarts` 模块化写法。

## 页面标识事实

前端通过 `createAgentRuntimeContext(pageType, context, files)` 生成：

```text
runtime_context.current_workflow_page
```

生成逻辑位于：

```text
/path/to/agentRuntimeContext.ts
```

页面标识来自：

```text
/path/to/assistantPageConfigs.ts
```

当前已确认的 PC 页面映射：

| pageType | current_workflow_page |
| --- | --- |
| `classCover` | `class-cover` |
| `studentDetail` | `student-detail` |
| `homework` | `homework` |
| `lessonDetail` | `lesson-detail` |
| `review` | `review` |
| `notice` | `notice` |
| `resource` | `resource` |
| `prepWorkspace` | `lesson-prep-space-2` |
| `preClassroom` | `classroom-session` |

本工具启用页面由 MySQL 表控制，不在代码中写死。

首批启用页面由产品或业务负责人向 MySQL 表写入精确 `page_name`。在写入前，代码只提供准入机制，不启用任何页面。

## 当前课程上下文字段

`/path/to/agentApi.ts` 中 `AgentRuntimeContext` 已定义课程相关字段：

```text
courseId
course_code
subject_code
session_id
teacher_code
user_code
user_type
student_code
student_name
homeworkId
homeworkTitle
```

`/path/to/agentRuntimeContext.ts` 已提供：

```text
createCourseRuntimeContext
createTeacherRuntimeContext
createStudentRuntimeContext
createHomeworkRuntimeContext
```

课程范围过滤优先使用：

```text
courseId
```

当页面未提供 `courseId` 时，工具拒绝执行 SQL，并返回缺少 `courseId` 的失败结果。本文档不把 `course_code` 替代为课程主过滤字段。

## 总体链路

```text
pc-web 页面
  -> AssistantShell
  -> createAgentRuntimeContext
  -> POST /v1/agent/send
  -> iclass-agent AgentEngine
  -> Agent 判断用户问题需要课程数据分析统计
  -> Agent 读取 skills/databaseopearation/SKILL.md
  -> Agent 从 skill 获取 schema、业务关系、血缘关系和 SQL 生成约束
  -> Agent 编写只读 SELECT
  -> readonly_course_sql_query
  -> MySQL catalog 校验页面、表、字段、关系、血缘
  -> SQL AST 校验
  -> MySQL 只读账号执行 SELECT
  -> ToolResult.content 返回结构化 SQL 查询结果给 Agent
  -> Agent 判断直接输出文本，或组织图表展示 JSON
  -> 需要建图时 Agent 调用 render_query_result_chart
  -> 制图工具生成 assistant_payload
  -> agentApi.parseAgentToolResultEvent
  -> AssistantShell.applyAssistantToolResult
  -> AssistantSqlChartMessage.vue
  -> echarts
```

## 后端工具设计

### 工具一：只读 SQL 查询工具

```text
readonly_course_sql_query
```

该工具的调用者是 Agent。Agent 根据用户问题生成 SQL，并将 SQL 作为工具参数传入。工具执行完成后，查询结果通过 `ToolResult.content` 以 JSON 文本返回给 Agent，供下一轮工具调用使用。

工具返回：

```json
{
  "type": "sql_query_result",
  "columns": [
    {
      "name": "student_name",
      "business_name": "学生姓名",
      "data_type": "string"
    }
  ],
  "rows": [
    {
      "student_name": "张三"
    }
  ],
  "row_count": 1,
  "limited": false
}
```

该返回值不是前端图表展示载荷。`ToolResult.meta` 可保留 `query_audit`，用于工具事件和日志记录，不放置 `assistant_payload`。

### 工具参数

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
  },
  "required": ["question", "sql"],
  "additionalProperties": false
}
```

### 工具二：查询结果制图工具

```text
render_query_result_chart
```

该工具由 Agent 在收到 `readonly_course_sql_query` 成功结果后调用。Agent 根据用户问题、SQL 查询结果和可视化语义，自行判断图表类型、标题、维度、指标、表格预览和图表数据，并按工具参数 description 组织完整展示 JSON。

该工具不直接接收 `readonly_course_sql_query` 的原始返回对象，不读取会话历史，不访问 MySQL。它只校验并包装 Agent 传入的展示 JSON。

工具参数：

```json
{
  "type": "object",
  "properties": {
    "chart_payload": {
      "type": "object",
      "description": "Agent 根据 SQL 查询结果自行组织的图表展示 JSON。必须包含 type、chart、table。type 固定为 sql_chart。chart.library 固定为 echarts。chart.chart_type 只允许 bar、line、pie、scatter。chart.title 为中文图表标题。chart.dimension_fields 为维度字段名数组。chart.metric_fields 为指标字段名数组。chart.dataset.columns 为字段名数组。chart.dataset.rows 为对象数组，每个对象的键必须来自 chart.dataset.columns。table.columns 为表格字段名数组。table.rows 为对象数组，每个对象的键必须来自 table.columns。"
    }
  },
  "required": [
    "chart_payload"
  ],
  "additionalProperties": false
}
```

制图工具必须校验：

1. `chart_payload.type = sql_chart`。
2. `chart_payload.chart.library = echarts`。
3. `chart_payload.chart.chart_type` 只允许 `bar`、`line`、`pie`、`scatter`。
4. `chart_payload.chart.dataset.columns` 是字符串数组。
5. `chart_payload.chart.dataset.rows` 是对象数组。
6. `chart_payload.chart.dimension_fields` 中每个字段都存在于 `chart_payload.chart.dataset.columns`。
7. `chart_payload.chart.metric_fields` 中每个字段都存在于 `chart_payload.chart.dataset.columns`。
8. `chart_payload.table.columns` 是字符串数组。
9. `chart_payload.table.rows` 是对象数组。

制图工具返回：

```python
ToolResult(
    success=True,
    content="图表已生成。",
    meta={
        "assistant_payload": {
            "action_type": "render_query_result_chart",
            "result_payload": {
                "type": "sql_chart",
                "chart": {
                    "library": "echarts",
                    "chart_type": "bar",
                    "title": "图表标题",
                    "dimension_fields": [],
                    "metric_fields": [],
                    "dataset": {
                        "columns": [],
                        "rows": []
                    }
                },
                "table": {
                    "columns": [],
                    "rows": []
                }
            }
        }
    }
)
```

### 工具注册位置

新增文件：

```text
app/tools/readonly_course_sql.py
```

工具通过 `@tool(...)` 注册到现有 `ToolRegistry`。注册时 `visibility.requires_page_match = true`，`visibility.page_names` 来自 MySQL Python 工具管理表。

`app/main.py` 的启动导入列表增加该工具模块。

### 工具执行边界

执行前必须完成以下校验：

1. `run_state.current_workflow_page` 非空。
2. `run_state.current_workflow_page` 在 `agent_python_tool_catalog` 中存在当前工具 active 记录。
3. `runtime_context.courseId` 存在，类型为正整数。
4. SQL 解析结果只有一条语句。
5. SQL 根节点为 `SELECT`。
6. SQL 引用的表均在表 catalog 中启用。
7. SQL 引用的字段均在字段 catalog 中启用。
8. SQL 带有课程过滤；最终执行 SQL 由代码注入 `courseId` 过滤。
9. 查询连接使用 MySQL 只读账号。
10. 查询超时使用 Python 工具管理表 `timeout_ms`。
11. 返回结果按字段 catalog 中的脱敏规则处理。

SQL 工具不强制注入 `LIMIT`，不改写 `LIMIT`。返回数量由 Agent 在生成 SQL 时自行判断。

拦截列表：

```text
INSERT
UPDATE
DELETE
MERGE
REPLACE
CREATE
ALTER
DROP
TRUNCATE
RENAME
GRANT
REVOKE
CALL
EXPLAIN
ANALYZE
LOCK
UNLOCK
SET
USE
SHOW
DESCRIBE
START TRANSACTION
COMMIT
ROLLBACK
SAVEPOINT
```

SQL 解析使用 Python 包：

```text
sqlglot
```

依赖写入：

```text
pyproject.toml
```

## MySQL catalog 设计

### Python 工具管理表

```sql
CREATE TABLE IF NOT EXISTS `agent_python_tool_catalog` (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键 ID',
  `tool_name` VARCHAR(255) NOT NULL COMMENT 'Python 端 Agent 工具名',
  `page_name` VARCHAR(255) NOT NULL COMMENT '页面标识，对应 runtime_context.current_workflow_page',
  `requires_page_match` TINYINT(1) NOT NULL DEFAULT 1 COMMENT '是否要求页面精确命中',
  `permission_decision` VARCHAR(16) NOT NULL DEFAULT 'allow' COMMENT '页面级权限决策',
  `permission_summary` VARCHAR(255) NOT NULL DEFAULT '' COMMENT '权限提示文案',
  `permission_deny_message` VARCHAR(255) NOT NULL DEFAULT '' COMMENT '拒绝提示文案',
  `visible_fields_json` JSON NOT NULL COMMENT '权限确认时可见字段',
  `editable_fields_json` JSON NOT NULL COMMENT '权限确认时可编辑字段',
  `is_active` TINYINT(1) NOT NULL DEFAULT 1 COMMENT '是否启用',
  `timeout_ms` INT NOT NULL DEFAULT 5000 COMMENT '单次查询超时时间',
  `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间',
  `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_agent_python_tool_catalog_tool_page` (`tool_name`, `page_name`),
  KEY `idx_agent_python_tool_catalog_page_active` (`page_name`, `is_active`),
  KEY `idx_agent_python_tool_catalog_tool_active` (`tool_name`, `is_active`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Agent Python 端工具管理表';
```

该表管理 Python 端通用工具，不为 `readonly_course_sql_query` 单独建权限控制表。

`readonly_course_sql_query` 与 `render_query_result_chart` 均通过该表控制页面可见性。新增 Python 端工具继续复用该表。

### 可查询表目录

```sql
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Agent SQL 可查询表目录';
```

### 可查询字段目录

```sql
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Agent SQL 可查询字段目录';
```

### 表关系目录

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

### 血缘目录

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

## Skill 渐进式披露

SQL catalog 不进入常驻系统提示词。

`SystemPromptBuilder` 的常驻提示词只暴露 skill 发现和按需读取规则，不暴露任何可查询表、字段、业务关系、血缘关系、SQL 范围清单。

当 Agent 需要使用 SQL 进行数据分析统计时，才加载以下 skill：

```text
skills/databaseopearation/SKILL.md
```

该 skill 当前尚未存在，实施时新增该目录和文件。

`skills/databaseopearation/SKILL.md` 不手写表字段清单。它由程序根据 MySQL catalog 格式化生成。

新增生成脚本：

```text
scripts/generate_databaseopearation_skill.py
```

生成脚本读取：

```text
agent_sql_query_table_catalog
agent_sql_query_column_catalog
agent_sql_query_relationship_catalog
agent_sql_query_lineage_catalog
```

生成内容包含：

```text
技能触发条件
SQL 只读边界
可查询表
可查询字段
字段业务说明
表业务关系
字段血缘关系
SQL 生成约束
readonly_course_sql_query 调用规范
render_query_result_chart 调用规范
```

`skills/databaseopearation/SKILL.md` 是 Agent 进行 SQL 数据分析统计时的渐进式披露上下文。它不是 Python Tool，不使用 `@tool(...)` 注册，不进入 `ToolRegistry`。

该 skill 的 frontmatter 固定承载 skill 名称和触发描述。该 skill 的正文承载 SQL 分析统计的执行规则、可查询 catalog、业务关系、血缘关系、工具调用顺序和图表 JSON 组织规则。

`skills/databaseopearation/SKILL.md` 由 `scripts/generate_databaseopearation_skill.py` 从 MySQL catalog 生成。业务人员或数据负责人通过维护 MySQL catalog 改变 Agent 可见的 SQL 查询范围；Agent 下一次读取该 skill 时使用生成后的文件内容。

## System Prompt Skill 指导规范

`SystemPromptBuilder` 不注入 SQL 表字段范围。

`SystemPromptBuilder` 只注入 skill 使用指导规范。该规范按 Claude Platform Docs 的 Agent Skills 机制编排：启动时只预加载 skill 的名称和描述；当 skill 与当前任务相关时，Agent 再读取 `SKILL.md`；`SKILL.md` 引用的附加文件和脚本只在需要时读取或执行。

新增系统提示词段落：

```text
## Skills
- Skills are task-specific instruction packages stored under skills/.
- At startup, only skill metadata is resident in the system prompt.
- Select a skill by matching the current user request to the skill name and description.
- When a skill applies, read its SKILL.md before using tools governed by that skill.
- Load referenced files or run referenced scripts only when the SKILL.md instructs you to do so.
- Do not preload database table, column, relationship, or lineage catalogs into the system prompt.
- For SQL data analysis and statistics, use skills/databaseopearation.
- After reading skills/databaseopearation/SKILL.md, follow its SQL boundary, catalog, lineage, and tool-calling rules.
```

该段落只告诉 Agent 如何使用 skill，不承载任何数据库 catalog 内容。

本方案使用以下公开文档作为 skill 渐进式披露依据：

- [Claude Platform Docs: Agent Skills overview](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview)
- [Claude Platform Docs: Skill authoring best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)
- [Anthropic Engineering: Equipping agents for the real world with Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)

页面 AGENTS.md 只写页面角色、页面参数来源和页面工具边界，不写物理表名和字段名。

## 两个工具的结果边界

### SQL 工具结果

SQL 工具结果只返回给 Agent：

```json
{
  "type": "sql_query_result",
  "columns": [],
  "rows": [],
  "row_count": 0,
  "limited": true
}
```

当前 Agent Runtime 的 `StandardAgentRunner` 会把 `ToolResult.content` 写入下一轮 Agent 消息，因此 SQL 查询结果必须放在 `content` 中，而不是只放在 `meta` 中。

### 制图工具结果

制图工具结果才使用现有 `assistant_payload`：

```text
ToolResult.meta.assistant_payload
```

`assistant_payload` 只承载制图工具产生的前端展示结果。SQL 工具不直接把查询结果推送给前端。

## pc-web 改造点

### Agent API 类型与解析

修改：

```text
/path/to/agentApi.ts
```

继续使用现有 `AgentToolResultInfo.meta` 接收 `tool_result` 事件。

新增：

```text
/path/to/agentSqlChartPayload.ts
```

该文件只解析：

```text
meta.assistant_payload.action_type = render_query_result_chart
meta.assistant_payload.result_payload.type = sql_chart
```

它不解析 `readonly_course_sql_query` 的 SQL 查询结果，因为该结果属于 Agent 内部的下一轮工具输入。

### 扩展助手消息类型

修改：

```text
/path/to/AssistantShell.vue
```

在 `AssistantConversationMessage` 增加：

```ts
sqlChartPayload?: AgentSqlChartPayload
```

在 `applyAssistantToolResult` 中只对工具名 `render_query_result_chart` 调用图表 payload 解析器。解析成功后写入：

```ts
message.sqlChartPayload = payload
```

### 新增图表展示组件

新增：

```text
/path/to/AssistantSqlChartMessage.vue
```

该组件只接收 `AgentSqlChartPayload`，使用当前已有 `echarts` 渲染，不发起接口请求，不读取 SQL 工具原始结果。

`AssistantShell.vue` 模板中渲染：

```vue
<AssistantSqlChartMessage
  v-if="message.sqlChartPayload"
  :payload="message.sqlChartPayload"
/>
```

## Agent 两阶段调用规则

`skills/databaseopearation/SKILL.md` 必须明确以下固定流程：

```text
1. 用户提出需要课程数据分析统计的问题。
2. Agent 判断该问题需要 SQL 数据分析统计能力。
3. Agent 读取 skills/databaseopearation/SKILL.md。
4. Agent 从 skill 获取 schema、表字段业务信息、业务关系、血缘关系和 SQL 生成约束。
5. Agent 读取当前课程上下文。
6. Agent 根据用户问题、当前课程上下文和 skill catalog 编写只读 SELECT。
7. Agent 调用 readonly_course_sql_query。
8. Agent 读取 SQL 工具返回的 sql_query_result。
9. Agent 根据用户问题和 sql_query_result 判断直接输出文本，或生成图表。
10. 需要建图时，Agent 自行组织 `chart_payload` 并调用 render_query_result_chart。
11. 制图工具成功后向用户说明图表已生成。
12. 只把 render_query_result_chart 的 assistant_payload 推送给前端图表组件。
```

如果 SQL 工具失败，Agent 不得调用制图工具。

如果 SQL 查询结果为空，Agent 可以返回空结果说明；制图工具不得接收无数据结果。

示例链路：

```text
用户：帮我看下哪些同学迟到
  -> Agent 读取 skills/databaseopearation/SKILL.md
  -> Agent 根据 skill 中的 schema 和业务说明编写只读 SELECT
  -> Agent 调用 readonly_course_sql_query
  -> Agent 获得迟到学生查询结果
  -> Agent 判断该问题以名单文本输出即可，或在需要统计分布时调用 render_query_result_chart
```

## 前端事件流

```text
tool_call(readonly_course_sql_query)
  -> tool_result(sql_query_result，仅供 Agent 下一轮上下文使用)
  -> Agent 组织 chart_payload
  -> tool_call(render_query_result_chart)
  -> tool_result(meta.assistant_payload)
  -> AssistantShell.applyAssistantToolResult
  -> AssistantSqlChartMessage.vue
  -> ECharts
```

当前前端 `parseAgentToolResultEvent` 已能读取 `meta`，因此图表 payload 从第二个工具的 `meta.assistant_payload` 进入前端。

`tool_result` 事件的统一序列化入口仍是：

```text
app/api/v1/agent.py
```

该接口不需要为图表增加新的 HTTP endpoint。

### ECharts 使用方式

沿用当前 `DistributionChart.vue` 的模块化写法：

```ts
import * as echarts from 'echarts/core'
import { BarChart, LineChart, PieChart, ScatterChart } from 'echarts/charts'
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  TitleComponent,
  DatasetComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
```

不新增 `vue-echarts`。

## 页面接入方式

当前 `AssistantShell` 已由多个页面传入 `runtimeContext`：

```text
StudentDetailView.vue
HomeworkView.vue
PrepWorkspaceView.vue
PreClassroomView.vue
ResourceView.vue
```

本工具依赖 `courseId`。当前已确认：

- `StudentDetailView.vue` 调用 `createCourseRuntimeContext`，传入 `courseId` 和 `courseCode`。
- `HomeworkView.vue` 调用 `createCourseRuntimeContext`，传入 `courseId` 和 `courseCode`。
- `useCourseRouteContext.ts` 从 `route.query.courseId` 解析 `courseId`。

页面不直接调用 `readonly_course_sql_query`。页面只传入运行上下文。

## 权限与治理

该工具只读，默认不走用户确认弹窗。

必须使用代码治理，而不是提示词治理：

- 页面准入由 `agent_python_tool_catalog` 控制。
- 表字段准入由 catalog 控制。
- SQL AST 校验由 `sqlglot` 控制。
- 数据库权限由只读 MySQL 账号控制。
- 超时由 `agent_python_tool_catalog` 控制。

YAML governance 不承担 SQL 安全主职责。

## 实施顺序

1. `iclass-agent` 增加 MySQL catalog 建表脚本。
2. `iclass-agent` 增加 catalog storage/service。
3. `iclass-agent` 增加 SQL AST 校验模块。
4. `iclass-agent` 增加 `readonly_course_sql_query` 工具。
5. `iclass-agent` 增加 `render_query_result_chart` 工具。
6. `iclass-agent` 增加 `generate_databaseopearation_skill.py`。
7. `iclass-agent` 生成 `skills/databaseopearation/SKILL.md`。
8. `iclass-agent` 在 `SystemPromptBuilder` 增加 skill 指导规范。
10. MySQL 写入目标页面的 `agent_python_tool_catalog` 记录。
11. MySQL 写入可查询表、字段、关系、血缘记录。
12. `iclass3demo` 增加 `agentSqlChartPayload.ts`。
13. `iclass3demo` 增加 `AssistantSqlChartMessage.vue`。
14. `iclass3demo` 修改 `AssistantShell.vue` 承载图表 payload。
15. `iclass3demo` 增加单元测试、类型检查、构建验证。

## 验收标准

1. 未配置 `agent_python_tool_catalog` 页面记录时，`readonly_course_sql_query` 不出现在模型工具列表。
2. `agent_python_tool_catalog` 页面记录精确命中 `runtime_context.current_workflow_page` 后，工具出现在模型工具列表。
3. 缺少 `courseId` 时，工具返回失败结果，不访问数据库。
4. DDL、DML、多语句 SQL 在解析阶段失败，不访问数据库。
5. 未登记表和未登记字段在 catalog 校验阶段失败，不访问数据库。
6. 查询结果只包含当前 `courseId` 范围内的数据。
7. SQL 工具不注入 `LIMIT`，不改写 `LIMIT`。
8. 敏感字段返回前完成脱敏。
9. `readonly_course_sql_query` 成功结果只进入 Agent 下一轮上下文，不直接进入前端图表组件。
10. `render_query_result_chart` 成功结果的 `tool_result.meta.assistant_payload.result_payload.type` 为 `sql_chart`。
11. `AssistantShell.vue` 只把制图工具的 `sql_chart` payload 渲染为 ECharts 图表。
12. `pnpm --filter @iclass/pc-web typecheck` 通过。
13. `pnpm --filter @iclass/pc-web build` 通过。

## 风险

1. 如果 MySQL catalog 中表名或字段名录入错误，工具会拒绝 SQL。
2. 如果页面未传 `courseId`，SQL 工具会拒绝执行。
3. 如果只读 MySQL 账号权限配置错误，安全验收不通过。
4. 如果 SQL 查询结果过大，Agent 上下文和制图工具输入都会变大。

## 禁止项

1. 不在提示词中手写物理表名和字段名。
2. 不在页面组件中直接调用 Agent 接口。
3. 不在展示组件中解析后端原始响应。
4. 不把 `course_code` 当作 `courseId` 的替代字段。
5. 不新增 `vue-echarts` 依赖。
6. 不展示完整 SQL。
7. 不返回未脱敏敏感字段。
8. SQL 工具不得返回 `assistant_payload` 作为前端图表结果。
9. 制图工具不得访问 MySQL。
