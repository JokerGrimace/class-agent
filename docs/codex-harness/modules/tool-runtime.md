# Module Contract: Tool Runtime

## Purpose

Define how tools are registered, exposed to the LLM, governed before execution, executed, and returned to the agent runtime.

## Responsibility

The Tool Runtime module owns:

- registering callable tools with exact names and JSON schema parameters
- filtering available tools by `tool_type`
- exposing tool schemas to the LLM
- executing a tool call by exact tool name
- applying governance decisions before execution
- requesting human permission when a rule returns `ask`
- applying edited arguments for `once_with_changes`
- enforcing per-tool timeout through `ToolExecutor`
- returning normalized `ToolResult` and `ToolError`

The Tool Runtime module does not own:

- selecting which tool the LLM should call
- iClass API endpoint implementation details
- workflow step orchestration
- frontend rendering of permission requests
- session persistence schema

## Inputs

- tool name
- tool call id
- tool call arguments
- `session_id`
- `user_id`
- `tool_type`
- registered tool metadata
- governance metadata
- governance rules from `app/governance/rules/default.yaml`

## Outputs

- `ToolResult`
- optional `ToolError`
- pending permission request through `PermissionService`
- tool schemas returned by `ToolRegistry.get_tools_schema`

## Tool Registration

Tools are registered with the `@tool` decorator from `app.core.tool.registry`.

Each registered tool provides:

- `name`
- `description`
- `parameters`
- async function implementation
- optional `governance`

The registry stores each tool as `ToolDefinition`.

## Tool Exposure Policy

`ToolDefinition.visibility.tool_types` controls type matching when present. Otherwise, `TOOL_TYPE_TO_NAMES` in `app/core/tool/registry.py` is the fallback. Page availability is also constrained by the catalog-derived `available_tool_names`; registering a callable does not make it available on every page.

Known tool types in the current source:

- `mcp`
- `native`
- `level1`

Page-scoped iClass API tools such as `create_folder` are not maintained in this static allowlist. Their registration and `level1` visibility come from SpringDoc operations plus `openapi_tool_mapping_catalog`.

If `tool_type` is missing, `ToolRegistry.get_tools` skips type filtering but still applies its page visibility predicate. This registry method is not the page catalog lookup.

Without explicit `visibility.tool_types`, an unknown `tool_type` matches no fallback entries.

## Python 工具复用与页面注入边界

本节以 `app/main.py`、`app/core/agent/engine.py`、`app/tools/python_loader.py`、`app/core/tool/registry.py`、`app/core/tool/python_tool_catalog_db.py` 为源码证据。

### 四个职责必须分开

| 职责 | 实现入口 | 作用 |
| --- | --- | --- |
| 配置写入 | `PythonToolCatalogService.save_tool` | 写入 `agent_python_tool_catalog`，维护工具与页面的关系、模块路径、启用状态和权限。 |
| 按需加载 | `AgentEngine.run` → `list_tool_rows_for_page` → `load_python_tools` | 每次 Agent 运行时按 `current_workflow_page` 读取配置并导入模块。 |
| 内存注册 | 模块导入时执行 `@tool` → `registry.register` | 保存函数、参数 schema、说明和元数据；不写数据库，不扫描所有工具文件。 |
| 页面可见与执行 | `_initialize_run_state` → `available_tool_names` → runner / executor | 限制当前页面提供给模型及允许执行的工具；内存中存在不代表当前页面可用。 |

```text
agent_python_tool_catalog（已经保存的配置）
  → AgentEngine.run(current_workflow_page)
  → PythonToolCatalogService.list_tool_rows_for_page
  → load_python_tools(module_by_tool_name)
  → importlib.import_module → @tool → registry
  → 当前页面 available_tool_names → 模型工具列表与执行校验
```

这条业务工具链路不是在服务启动时扫描全部装饰器。模块已导入时会复用内存定义；修改工具代码需要按当前服务部署方式重载进程。单纯增加页面目录记录由下一次运行读取，不需要为此编写启动注入代码或专用登记脚本。

页面 prompt 使用独立链路：`AgentPagePromptCatalogService.save_prompt` 写入 `agent_page_prompt_catalog`，`AgentEngine._load_active_page_agents_md` 读取启用版本。页面提示词文件与登记脚本不是该运行链路的必需输入；没有明确同步机制时，不再为一次目录配置额外维护同内容文件。

### 实施前的最小变更判断

1. 读取现有工具函数、schema、`ToolResult.meta` 协议及前端消费位置，确认能否复用。
2. 读取目标页面当前目录记录，区分“工具不存在”和“页面尚未配置可见性”。同一工具跨页复用保持相同 `tool_name` 与 `module_path`，目录唯一键为 `(tool_name, page_name)`。
3. 仅展示不同，调整前端。仅页面缺配置，通过现有目录服务维护对应记录并回读，不复制工具、扩展 executor 或为每页新增登记脚本。
4. 更新已有配置前保留其权限、可编辑字段和超时设置；页面 prompt 读取当前启用正文后按版本机制更新，保留其他有效规则。
5. 只有现有输入输出无法满足实际需求时才设计工具能力变化。多环境迁移、批量初始化、回滚等需求必须有事实依据，不能为了推测中的未来部署新增产物。
6. 验证目标页动态加载、无关页过滤、原业务协议与调用行为；测试针对这些契约，不为只包装目录服务的页面脚本构造额外测试链。

### 2026-09-03 复盘：备课题卡登记脚本冗余

- **原需求**：备课页将生成的问题按现有题卡格式填入。`prepare_classroom_questions` 的参数及 `classroom_question_drafts` 返回协议已经满足三种题型；实际缺口是前端备课接收逻辑与该工具的备课目录记录。
- **误判**：实现已经读到目录服务，却沿用相邻登记脚本的形式，以可重复部署为由新增 `scripts/register_prep_question_tool.py`，并连带新增仅为该脚本服务的提示词副本及测试。没有证据表明本次需求需要独立迁移入口。
- **用户纠正**：用户指出原工具可以复用，并追问装饰器自动注入为何还需要新增登记脚本，要求删除冗余并固化架构边界。
- **原因**：没有在开发前完整确认“配置写入、模块加载、装饰器注册、页面注入”的职责，也没有先限定最小交付范围；把“必须补配置”错误地等同于“必须新增长期维护的脚本”。复盘时还发现旧文档缺少 Python 目录链路、扩展手册把完整交付普遍化的问题，本次一并纠正；本次冗余的直接责任仍是实现者没有先以源码核对边界并收敛范围。
- **修正**：移除专用登记脚本、无运行时消费者的备课 prompt 副本和仅验证脚本的测试；目录记录及启用 prompt 原样保留。原草稿工具的动态加载和执行限制测试同时覆盖备课页与课堂页。
- **防复发**：遵守上方最小变更判断。功能测试通过不证明新增产物必要；提交前还需说明每个新文件为什么无法由现有模块或配置完成。

## Execution Flow

1. `StandardAgentRunner` receives tool calls from the LLM.
2. `AgentEngine._prepare_tool_call` injects session values for `start_workflow_tool`.
3. `StandardAgentRunner._execute_pending_tool_calls` emits a `tool_call` event.
4. `ToolExecutor.execute_tool_call` builds a `ToolCall`.
5. `ToolExecutor.execute` resolves the registered `ToolDefinition`.
6. `ToolExecutor` resolves the tool action name.
7. `ToolExecutor` checks session permission grants.
8. `ToolExecutor` evaluates governance rules.
9. If decision is `deny`, execution stops with a failed `ToolResult`.
10. If decision is `ask`, `PermissionService.ask` waits for user reply.
11. If the reply is `reject`, execution stops with a failed `ToolResult`.
12. If the reply is `always`, a session grant is stored before execution.
13. If the reply is `once_with_changes`, allowed edited fields are merged into effective arguments.
14. `_execute_tool_definition` calls the registered async tool function.
15. The result is normalized into `ToolResult`.
16. `execute_tool_call` returns `(ToolResult, Optional[ToolError])`.
17. `AgentEngine._persist_tool_call_interaction` stores the assistant tool call and tool result messages.

## Governance Boundary

Governance metadata is provided by each tool registration.

Current governance fields:

- `tool_tags`
- `action_source`
- `action_tags`

`ToolExecutor` builds an `EvaluationContext` containing:

- `tool_name`
- `tool_tags`
- `action_name`
- `action_tags`
- `arguments`

`app/governance/rules/default.yaml` currently defines:

- default decision `allow`
- approval for remote mutating tools
- approval for remote mutating actions
- visible and editable fields for name-like rename/create operations
- visible and editable `chapter_list` for `build_to_chapter`

## Dependencies

- `app.core.tool.registry`
- `app.core.tool.executor`
- `app.core.tool.types`
- `app.core.governance.loader`
- `app.core.governance.policy`
- `app.core.permission.service`
- `app.tools.*`

## Public Interfaces

### Decorator

- `tool(name, description, parameters, governance=None, visibility=None)`

### Registry

- `ToolRegistry.register`
- `ToolRegistry.get`
- `ToolRegistry.get_tools`
- `ToolRegistry.get_tools_schema`
- `ToolRegistry.get_tools_metadata`
- `ToolRegistry.filter_tools_by_list`
- `ToolRegistry.clear`

### Executor

- `ToolExecutor.execute`
- `ToolExecutor.execute_tool_call`

### Data Types

- `ToolMetadata`
- `ToolGovernanceMetadata`
- `ToolResult`
- `ToolDefinition`
- `ToolCall`
- `ToolError`

## Error Conditions

- Unregistered tool returns `ToolResult(success=False, error="Tool not found: ...")`.
- Governance `deny` returns failed `ToolResult`.
- User `reject` returns failed `ToolResult`.
- Tool timeout returns failed `ToolResult` with `timed_out=True`.
- Exceptions from tool execution are caught and returned as failed `ToolResult` with `meta.exception_type`.
- If a returned value is not `ToolResult`, `_execute_tool_definition` wraps it as `ToolResult(success=True, content=str(result))`.

## Acceptance

This module contract is satisfied when:

- every exposed tool is registered through `ToolRegistry`
- `get_tools_schema(tool_type)` returns JSON schemas for only the allowed tools
- every tool call goes through `ToolExecutor`
- remote mutating tools are checked by governance before execution
- permission-required tool calls produce a pending permission request
- tool results are returned as `ToolResult`

## Open Questions

- New page-specific Python tools use `agent_python_tool_catalog` and decorator visibility; extending `TOOL_TYPE_TO_NAMES` is not the page registration mechanism.
- Should `settings.max_concurrent_tools` be part of the Tool Runtime contract? It exists in settings, but current `StandardAgentRunner` executes prepared tool calls sequentially.
- Should concrete tool modules document a shared `assistant_payload` contract, or should that remain specific to iClass resource tools?

## Current Page-Scoped OpenAPI Injection

The current runtime supports page-scoped OpenAPI tool injection in addition to statically imported tools.

### Injection trigger

Before a standard Agent run proceeds, `AgentEngine.run(...)` reads `current_workflow_page` from runtime context and calls:

- `load_iclass_openapi_tools(page_name=current_workflow_page)`

Then `AgentEngine._initialize_run_state(...)` builds the visible tool set with:

- `OpenApiToolMappingService.list_tool_names_for_page(current_workflow_page)`
- `PythonToolCatalogService.list_tool_names_for_page(current_workflow_page)`

The engine combines these names with its existing runtime file tool set into `available_tool_names`.

If `current_workflow_page` is blank, page-scoped OpenAPI injection does not occur.

### Injection inputs

The current injection path requires both:

1. remote OpenAPI spec from:
   - `/v3/api-docs/agent`
2. page mapping rows from MySQL table:
   - `openapi_tool_mapping_catalog`

The runtime does not register all remote OpenAPI operations. It registers only operations declared for the current page in `openapi_tool_mapping_catalog`.

### Mapping table contract

`openapi_tool_mapping_catalog` currently provides the runtime contract for page-scoped OpenAPI tools.

Fields used by the runtime:

- `http_method`
- `api_path`
- `tool_name`
- `page_name`
- `requires_page_match`
- `permission_decision`
- `permission_summary`
- `permission_deny_message`
- `support_user_revision`
- `visible_fields_json`
- `editable_fields_json`
- `is_active`

Current unique key:

- `http_method`
- `api_path`
- `page_name`

### Registration behavior

`IClassOpenApiToolRegistrar.register_from_spec(...)` currently:

1. lists active mapping rows for the current `page_name`
2. locates the exact operation in the remote OpenAPI spec by `http_method + api_path`
3. builds a tool schema from the OpenAPI operation
4. registers the runtime tool under mapped `tool_name`

Important current behavior:

- runtime tool naming comes from MySQL `tool_name`
- old local `iclass_api__...` naming is not reused in this path

### Parameter schema source

Tool parameters come from the OpenAPI operation, not from the mapping table.

Current behavior:

- query parameters are exposed to the model in snake_case
- outgoing HTTP requests translate them back to original OpenAPI field names
- `requestBody` is resolved recursively through `$ref`
- the resolved request body is exposed under tool argument `body`

### Permission precedence

For page-scoped OpenAPI tools, `ToolExecutor.execute(...)` currently applies permission logic in this order:

1. session grant
2. page-level permission from `openapi_tool_mapping_catalog`
3. fallback YAML governance policy

Current page-level decisions:

- `allow`
- `ask`
- `deny`

When decision is `ask`, only fields in `visible_fields_json` are exposed to the user, and only fields in `editable_fields_json` are editable.

### Practical consequence for question tools

Under the current runtime, a `question` OpenAPI endpoint is available to the Agent only when:

- the frontend sends the exact `current_workflow_page`
- `/v3/api-docs/agent` exposes the endpoint
- `openapi_tool_mapping_catalog` contains an active row for that page and operation
- the page permission row allows the intended execution path
