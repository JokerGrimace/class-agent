# iClass-Agent Project Guide

## 1. 文档目的

本文档面向接手 `iclass-agent` 的开发者，目标不是只介绍目录，而是帮助读者回答下面几个问题：

- 这个项目在整个 `iclass` 体系里承担什么职责
- 一个请求进入系统后，会经过哪些组件
- Agent、工具、工作流、审批、会话、前端之间是如何协同的
- 如果要新增工具、接工作流、接审批规则、改接口，应该从哪里下手

读完本文档后，开发者应当能够：

- 理解项目的运行主链路
- 找到每一类能力对应的代码位置
- 判断问题属于哪一层
- 在不破坏现有机制的前提下扩展能力

## 2. 项目定位

`iclass-agent` 是一个面向 iClass 业务场景的 Agent 服务。它不是单纯的大模型对话接口，也不是单纯的资源管理 API，而是把以下能力组合在一起的编排层：

- 会话驱动的 Agent 对话
- LLM 与工具调用协同
- iClass 课堂与备课资源接口接入
- 工作流驱动的复杂任务执行
- 执行前治理与审批控制
- Web 搜索 / Web 抓取 / RAG 等辅助能力
- 面向前端页面的流式与非流式返回协议

从系统角色看，它位于：

- 上游：浏览器页面、业务前端、WebSocket 客户端、HTTP 客户端
- 中间：Agent 编排、工具执行、审批治理、工作流执行
- 下游：LLM 服务、iClass API、MySQL、Redis、MinIO、Web Search Provider

## 3. 项目解决的问题

项目主要解决六类问题：

1. 把用户输入转成可持续的 Agent 会话，而不是一次性问答
2. 让模型能够安全地调用外部工具，包括 iClass 业务工具和通用工具
3. 让高风险操作在真正执行前经过统一治理与审批
4. 让复杂场景通过工作流编排执行，而不是全靠模型自由推理
5. 让前端既可以拿到最终回复，也可以拿到工具结果、审批信息、刷新提示等结构化数据
6. 让项目具备扩展能力：能持续增加工具、工作流、治理规则和外部能力

## 4. 项目能力边界

### 4.1 项目负责的内容

- 提供 Agent HTTP / WebSocket 接口
- 维护会话与消息历史
- 构建系统提示词与工作区上下文
- 执行工具调用并回灌结果
- 管理审批请求和恢复执行
- 触发工作流并管理工作流执行
- 暴露给前端可消费的结构化协议

### 4.2 项目不负责的内容

- 不实现 iClass 后端业务本体，iClass 业务能力来自外部接口
- 不实现模型本体，只做 OpenAI / Ollama 等 LLM 适配
- 不承担前端主业务页面本体，当前仓库只包含静态 Agent 演示页面
- 不替代 MySQL / Redis / MinIO，本项目只负责使用这些基础设施

## 5. 顶层目录与组件地图

项目根目录下的主要组件如下：

| 路径 | 组件角色 |
| --- | --- |
| `app/` | 运行时代码主目录 |
| `app/api/` | HTTP / WebSocket API 层 |
| `app/core/` | 核心运行时能力：agent、tool、session、workflow、governance、llm、iclass、websearch、rag |
| `app/tools/` | 具体工具定义与注册入口 |
| `app/tools/iclass_d_resource/` | 拆分后的备课资源类工具集合 |
| `app/governance/rules/` | YAML 治理规则 |
| `app/workflows/` | Markdown 工作流定义 |
| `static/` | 静态前端页面资源 |
| `templates/` | 工作区模板文件 |
| `skills/` | 本地技能说明文件 |
| `docs/` | 设计文档、计划文档、人格工作流文档 |
| `scripts/` | 辅助脚本 |
| `data/` | 根级运行数据目录 |
| `app/data/` | 应用级运行数据目录，当前包含 approvals / sessions / workspace |
| `*.log` | 运行日志与调试日志 |

需要特别说明两点：

- 当前仓库同时存在 `data/` 与 `app/data/` 两套运行产物目录，历史上都被使用过
- `app/tools/` 顶层没有统一的 `iclass_resource.py`，资源类工具当前是拆分在 `app/tools/iclass_d_resource/` 下的多个单工具文件中

## 6. 启动与运行入口

主入口是 [app/main.py](/app/main.py)。

### 6.1 应用初始化

`app.main` 在启动时完成以下动作：

- 创建 FastAPI 应用
- 注册 CORS 中间件
- 注册 `/v1` HTTP 路由和 `/v1/ws` WebSocket 路由
- 挂载根路径静态页面
- 在 `lifespan` 中执行初始化逻辑

### 6.2 lifespan 初始化逻辑

`lifespan()` 中会执行：

- `settings.ensure_dirs()`：确保目录存在
- `session_manager.initialize()`：初始化会话存储
- `register_builtin_tools()`：注册内置工具
- `Workspace.ensure_workspace()`：准备工作区模板文件

### 6.3 路由入口

- [app/api/v1/router.py](/app/api/v1/router.py)
  - 挂载 `agent`、`grading`、`rag`、`session`、`resource`
- [app/api/ws/agent_ws.py](/app/api/ws/agent_ws.py)
  - 提供 WebSocket Agent 通道

## 7. 一次请求进入系统后的主链路

### 7.1 HTTP `/v1/agent/send`

主入口在 [app/api/v1/agent.py](/app/api/v1/agent.py)。

主流程如下：

1. 根据 `session_id` 查找或创建会话
2. 把用户消息写入 session
3. 构建 `AgentEngine`
4. 调用 `engine.run(...)`
5. `AgentEngine` 驱动 LLM 生成文本或工具调用
6. 如有工具调用，交给 `ToolExecutor`
7. 工具结果回到 `AgentEngine`
8. `AgentEngine` 继续生成下一步回复，或触发审批暂停
9. API 层把结果转成：
   - 流式 SSE 事件
   - 非流式 JSON 聚合响应

### 7.2 HTTP `/v1/agent/approvals/{id}/resolve`

审批恢复入口同样在 [app/api/v1/agent.py](/app/api/v1/agent.py)。

主流程如下：

1. 加载审批请求
2. 校验当前用户是否拥有该审批
3. 读取审批所属 session
4. 校验该审批是否仍然是当前 session 的活动审批
5. 把审批请求状态切换为 `approved / denied / approved_with_changes`
6. 调用 `engine.resume_approval(...)`
7. 恢复原工具调用或把拒绝结果回灌给模型
8. 返回：
   - 流式事件
   - 非流式聚合 JSON

### 7.3 流式与非流式

当前协议已经统一语义：

- 流式：逐条发送 `reasoning / text / tool_call / tool_result / approval_required / warning / done`
- 非流式：把同一轮事件聚合成一个 JSON，包含：
  - `response`
  - `approval_required`
  - `tool_results`
  - `warnings`
  - `assistant_payload`

这意味着前端即使使用非流式，也可以消费工具结果和审批信息，而不是只依赖一段文本。

## 8. Agent 编排核心

核心在 [app/core/agent/engine.py](/app/core/agent/engine.py)。

### 8.1 AgentEngine 的职责

`AgentEngine` 负责：

- 构建会话消息与系统提示词
- 驱动 LLM 对话
- 监听 LLM 输出的工具调用
- 调用工具执行器
- 把工具结果写回 session
- 管理审批暂停与恢复
- 处理上下文压缩、告警、未完成轮次提示

### 8.2 核心内部结构

相关组件包括：

- [app/core/agent/context.py](/app/core/agent/context.py)
  - Agent 运行态上下文
- [app/core/agent/context_guard.py](/app/core/agent/context_guard.py)
  - 上下文预算检查
- [app/core/agent/compaction.py](/app/core/agent/compaction.py)
  - 上下文压缩
- [app/core/agent/compaction_marker.py](/app/core/agent/compaction_marker.py)
  - 压缩标记
- [app/core/agent/incomplete.py](/app/core/agent/incomplete.py)
  - 未完成轮次警告
- [app/core/agent/system_prompt.py](/app/core/agent/system_prompt.py)
  - 系统提示词构建
- [app/core/agent/bootstrap.py](/app/core/agent/bootstrap.py)
  - BOOTSTRAP 文件支持
- [app/core/agent/skills.py](/app/core/agent/skills.py)
  - 技能相关支持
- [app/core/agent/plan.py](/app/core/agent/plan.py)
  - 计划状态相关逻辑
- [app/core/agent/state.py](/app/core/agent/state.py)
  - Agent 状态工具
- [app/core/agent/tool_result_truncation.py](/app/core/agent/tool_result_truncation.py)
  - 工具结果裁剪

### 8.3 Agent 轮次逻辑

每一轮大致分为：

1. 构建消息
2. 做 context precheck
3. 如超预算，尝试 shrink / compact
4. 调用 LLM
5. 收集 reasoning / content / tool_calls
6. 执行工具
7. 如命中审批，发出 `approval_required` 并立即中止本轮
8. 如有 tool result，写回 session
9. 如有 assistant 文本，写回 session
10. 发出 `done`

### 8.4 审批恢复逻辑

`resume_approval()` 会：

1. 校验 `session.context.pending_approval_id`
2. 根据审批状态决定：
   - 已批准：执行原工具
   - 修改后批准：执行合并参数后的工具
   - 已拒绝：构造一个失败型 `ToolResult`
3. 先发出一条 `tool_result`
4. 再重新进入 `run()`，继续后续推理

## 9. API 层组件

### 9.1 HTTP API

- [app/api/v1/agent.py](/app/api/v1/agent.py)
  - Agent 对话、审批恢复、临时文件解析
- [app/api/v1/session.py](/app/api/v1/session.py)
  - Session 创建、查询、删除、列举
- [app/api/v1/resource.py](/app/api/v1/resource.py)
  - 资源转换相关接口

### 9.2 WebSocket API

- [app/api/ws/agent_ws.py](/app/api/ws/agent_ws.py)
  - 提供 WebSocket Agent 交互入口

## 10. 会话、消息与持久化

相关代码：

- [app/core/session/models.py](/app/core/session/models.py)
- [app/core/session/manager.py](/app/core/session/manager.py)
- [app/core/session/storage.py](/app/core/session/storage.py)

### 10.1 Session 模型

Session 中主要包含：

- `session_id`
- `user_id`
- `agent_id`
- `messages`
- `plan`
- `context`
- `created_at`
- `updated_at`

`SessionContext` 中当前与审批最相关的字段是：

- `pending_approval_id`
- `last_tool_error`
- `session_context`

### 10.2 存储实现

当前 `create_session_storage()` 返回的是 `MySQLSessionStorage`，因此生产运行时 session 读写默认走 MySQL，而不是本地 JSON 文件。

MySQL session storage 同时维护：

- `sessions`
- `session_messages`
- `session_plan`

### 10.3 本地数据目录的角色

虽然 session 默认走 MySQL，但仓库中仍然存在：

- `app/data/sessions/`
- `data/sessions/`

这些目录更多用于：

- 调试
- 兼容旧的文件型存储流程

## 11. 工具体系

核心代码：

- [app/core/tool/types.py](/app/core/tool/types.py)
- [app/core/tool/registry.py](/app/core/tool/registry.py)
- [app/core/tool/executor.py](/app/core/tool/executor.py)

### 11.1 工具注册机制

工具模块被导入时，`@tool(...)` 装饰器把定义注册到内存 `registry`，不会写入目录表。

Python 工具的页面加载发生在每次 `AgentEngine.run(...)`：先由 `PythonToolCatalogService.list_tool_rows_for_page(...)` 读取 `agent_python_tool_catalog`，再由 `load_python_tools(...)` 按记录的 `module_path` 导入模块。引擎随后构造 `available_tool_names`，限制当前页面向模型暴露和执行的工具。

已有工具新增页面可见性，只需通过现有目录服务维护配置并回读验证，不需要新增装饰器、工具函数或页面专用登记脚本。页面规则维护在 `agent_page_prompt_catalog`。详细链路、最小变更判断和 2026-09-03 复盘见 [Tool Runtime 模块契约](codex-harness/modules/tool-runtime.md#python-工具复用与页面注入边界)。

`ToolDefinition` 包含：

- `name`
- `description`
- `parameters`
- `func`
- `metadata`
- `governance`

### 11.2 工具分组

`ToolDefinition.visibility.tool_types` 声明工具类型；没有该声明时，`registry` 的 `TOOL_TYPE_TO_NAMES` 才作为类型筛选回退。已存在的类型包括：

- `mcp`
- `native`
- `level1`

`tool_type` 会影响：

- 系统提示词可见的工具范围
- 实际暴露给 LLM 的 tool schema

### 11.3 ToolExecutor 的职责

`ToolExecutor` 负责：

- 根据工具名查找注册项
- 在执行前进行治理判定
- 命中审批时构造 `approval_required`
- 未命中审批时执行工具
- 对工具异常进行封装

## 12. 执行前治理与审批机制

治理相关代码：

- [app/core/governance/types.py](/app/core/governance/types.py)
- [app/core/governance/loader.py](/app/core/governance/loader.py)
- [app/core/governance/policy.py](/app/core/governance/policy.py)
- [app/core/governance/approval_service.py](/app/core/governance/approval_service.py)
- [app/core/governance/storage.py](/app/core/governance/storage.py)
- [app/governance/rules/default.yaml](/app/governance/rules/default.yaml)

### 12.1 设计目标

治理层的目标是把“是否允许执行、是否需要人工确认、哪些字段可见、哪些字段可编辑”从具体业务工具里抽出来，做成平台级能力。

### 12.2 规则来源

当前规则来源是 `app/governance/rules/default.yaml`。

规则至少描述：

- 匹配条件
- 决策结果
- 可见字段
- 可编辑字段
- 审批摘要
- 拒绝后的提示

### 12.3 审批请求生命周期

审批请求由 `ApprovalService` 管理，状态主要包括：

- `pending`
- `approved`
- `approved_with_changes`
- `denied`

审批数据当前落在：

- `app/data/approvals/`

### 12.4 stale approval

当前实现已经支持 stale approval 检查：

- 如果提交的 `approval_id` 不是当前 session 正在等待的那条
- 接口返回 `409`
- 并附带 `current_pending_approval`

这个机制的核心目的是避免前端拿旧审批 ID 去恢复新的会话状态。

## 13. LLM 适配层

LLM 相关组件：

- [app/core/llm/adapter.py](/app/core/llm/adapter.py)
- [app/core/llm/factory.py](/app/core/llm/factory.py)
- [app/core/llm/openai.py](/app/core/llm/openai.py)
- [app/core/llm/ollama.py](/app/core/llm/ollama.py)
- [app/core/llm/stream_parser.py](/app/core/llm/stream_parser.py)
- [app/core/llm/tokens.py](/app/core/llm/tokens.py)

职责包括：

- 统一模型调用接口
- 适配不同 provider
- 解析流式输出
- 提供 token 相关支持

## 14. iClass 业务能力组件

### 14.1 SpringDoc 动态工具

- [app/tools/iclass_openapi.py](/app/tools/iclass_openapi.py)

该组件从 `/v3/api-docs/agent` 读取 OpenAPI 契约，根据 `openapi_tool_mapping_catalog` 注册当前页面可用的 iClass 工具，并直接发送 HTTP 请求。仓库不再保留 `app/core/iclass/client.py` 和 `app/tools/iclass_api.py` 这两层重复封装。

### 14.2 课堂类工具

课堂类 Java 接口同样通过 SpringDoc 契约和页面映射动态注册，不再维护静态 `iclass_api` action 分发工具。

### 14.3 资源类工具

资源类 Java 接口通过 SpringDoc 动态注册。`app/tools/iclass_d_resource/` 只保留需要 Python 编排或本地文件处理的工具：

- `generate_chapter.py`
- `prase_file.py`
- `resource_common.py`

其中 `build_to_chapter` 在 `generate_chapter.py` 内直接调用目录创建接口，完成章、节目录的顺序编排。

### 14.4 MinIO 文件能力

- [app/tools/iclass_c_file/minio_service.py](/app/tools/iclass_c_file/minio_service.py)

该组件负责对象存储相关能力。

## 15. 工作流体系

工作流相关代码：

- [app/core/workflow/catalog.py](/app/core/workflow/catalog.py)
- [app/core/workflow/catalog_db.py](/app/core/workflow/catalog_db.py)
- [app/core/workflow/models.py](/app/core/workflow/models.py)
- [app/core/workflow/executor.py](/app/core/workflow/executor.py)
- [app/core/workflow/manager.py](/app/core/workflow/manager.py)
- [app/core/workflow/storage.py](/app/core/workflow/storage.py)
- [app/tools/workflow_tools.py](/app/tools/workflow_tools.py)
- [app/workflows/chapter-build.md](/app/workflows/chapter-build.md)

### 15.1 工作流定义来源

当前工作流既支持：

- 目录中的 Markdown 工作流定义
- 数据库中的 workflow catalog 记录

### 15.2 `start_workflow_tool`

`start_workflow_tool` 是工作流执行公共入口，负责：

- 根据工作流名称查找定义
- 加载 session
- 创建子 LLM
- 构建 `WorkflowExecutor`
- 执行全部步骤
- 返回 step outputs 与 final answer

### 15.3 工作流目录与文档

工作流相关设计与说明还包括：

- `docs/superpowers/plans/*workflow*`

## 16. Web Search、Web Fetch 与 RAG

### 16.1 Web Search / Web Fetch

相关组件：

- [app/tools/web_search.py](/app/tools/web_search.py)
- [app/tools/web_fetch.py](/app/tools/web_fetch.py)
- [app/core/websearch/service.py](/app/core/websearch/service.py)
- [app/core/websearch/fetch.py](/app/core/websearch/fetch.py)
- [app/core/websearch/base.py](/app/core/websearch/base.py)
- [app/core/websearch/providers/open_websearch.py](/app/core/websearch/providers/open_websearch.py)

当前搜索配置只保留 `web_search_provider="auto" | "open_websearch" | "disabled"` 和
`open_websearch_engines="duckduckgo,bing,baidu,sogou"`。`auto` 与 `open_websearch` 均使用同一个
HTML 搜索 provider；2026-09-05 已移除 Brave、SearXNG 配置和实现。

引擎按配置顺序执行，每次请求剩余数量，够数即停止。单个引擎异常不会中断后续引擎，已有结果会保留。
DuckDuckGo、Bing 均失败或没有足够结果时，继续请求百度、搜狗公开搜索页，返回真实的标题、链接、摘要与来源，不需要 API key。
百度优先保留结果块 `mu` 中的正文 URL；搜狗保留搜索页提供的链接，不尝试解码不透明跳转 token。搜狗验证页会作为失败记录，不作为搜索结果返回。
请求和解析依据 [open-webSearch 百度实现](https://github.com/Aas-ee/open-webSearch/blob/main/src/engines/baidu/baidu.ts)、
[搜狗实现](https://github.com/Aas-ee/open-webSearch/blob/main/src/engines/sogou/sogou.ts) 与 2026-09-05 的实际 HTML；每个引擎只读取第一页。
部署环境若显式设置 `OPENCLAW_OPEN_WEBSEARCH_ENGINES`，需同步设为 `duckduckgo,bing,baidu,sogou` 才会启用完整链路。
全部引擎异常或无结果时返回失败，并汇总引擎与异常类型；Docker 警告日志记录失败引擎、异常类型、耗时和超时配置。
`web_search` 对异常和零结果均返回 `success=False`，提示 Agent 基于已有课件或知识库继续，并明确说明未完成联网核验。
该提示不自动调用知识库或正文抓取工具，也不保证模型遵守；后续工具仍由现有 Agent 运行流程决定。

### 16.2 RAG

相关组件：

- [app/tools/rag_search.py](/app/tools/rag_search.py)
- [app/tools/rag_answer.py](/app/tools/rag_answer.py)
- [app/core/rag/adapter.py](/app/core/rag/adapter.py)

## 17. 资源转换与临时文件上下文

相关组件：

- [app/core/file_cache.py](/app/core/file_cache.py)
- [app/api/v1/agent.py](/app/api/v1/agent.py) 中的 `/parse-temp-file`
- [app/tools/iclass_d_resource/prase_file.py](/app/tools/iclass_d_resource/prase_file.py)

该链路的作用是：

- 接收前端临时文件
- 解析内容
- 写入缓存
- 在后续对话中通过 `runtime_context.file_content` 注入给 Agent

## 18. 前端组件

当前仓库包含两类前端资源：

### 18.1 静态 Agent 页面

- [static/index.html](/static/index.html)
- [static/app.js](/static/app.js)
- [static/style.css](/static/style.css)

### 18.2 其他页面与资源

- [app/trending.html](/app/trending.html)

这些页面主要用于演示或辅助场景，不等同于主业务前端仓库。

## 19. Workspace、模板与技能文件

### 19.1 工作区模板

模板文件位于：

- `templates/AGENTS.md`
- `templates/BOOTSTRAP.md`
- `templates/HEARTBEAT.md`
- `templates/IDENTITY.md`
- `templates/SOUL.md`
- `templates/TOOLS.md`
- `templates/USER.md`

这些模板会被 `Workspace.ensure_workspace()` 用来初始化工作区文件。

### 19.2 工作区数据目录

当前仓库中存在：

- `app/data/workspace/`
- `data/workspace/`

其中包含：

- `AGENTS.md`
- `BOOTSTRAP.md`
- `HEARTBEAT.md`
- `IDENTITY.md`
- `SOUL.md`
- `TOOLS.md`
- `USER.md`
- `workspace-state.json`

### 19.3 技能文件

技能文件位于：

- `skills/example-workflow/SKILL.md`
- `skills/persona-intake/SKILL.md`
- `skills/persona-scout/SKILL.md`
- `skills/persona-reflection/SKILL.md`
- `skills/persona-governance/SKILL.md`

这些文件主要服务于工作流、人设治理与扩展说明，并不是核心运行时代码。

## 20. 配置体系

配置主入口是 [app/config.py](/app/config.py)。

主要配置类别包括：

- 目录配置
  - `data_dir`
  - `sessions_dir`
  - `workspace_dir`
- LLM 配置
  - `llm_provider`
  - `openai_*`
  - `ollama_*`
- 执行配置
  - `tool_timeout_ms`
  - `max_concurrent_tools`
  - `max_turns`
  - `strict_plan_max_turns`
- Web Search 配置
- Context token 配置
- iClass API 配置
- MinIO 配置
- Redis 配置

业务请求只能使用后端验证的真实登录凭证；开源版本不提供本机测试身份或原始工具调试接口。

## 21. 流式返回与前端协议

项目支持两种协议形态：

### 21.1 流式协议

通过 SSE 返回事件，当前至少包括：

- `reasoning`
- `reasoning_end`
- `text`
- `tool_call`
- `tool_result`
- `approval_required`
- `warning`
- `done`

### 21.2 非流式协议

非流式协议不再只是 `response` 文本，而是聚合同一轮事件，返回：

- `response`
- `approval_required`
- `tool_results`
- `warnings`
- `assistant_payload`

这套协议现在已经支持：

- 让前端根据 `tool_results` 获取工具执行信息
- 根据 `assistant_payload.ui_refresh_hint` 刷新页面
- 根据 `approval_required` 展示审批卡片

## 22. 公开范围

本开源版本仅包含运行时代码与使用文档；测试、评测、调试脚本、历史开发计划和运行产物不纳入发布。

## 25. 典型时序场景

开发者理解项目时，至少要掌握以下场景：

1. 普通问答
2. 工具调用
3. 命中治理规则后进入审批
4. 审批通过后恢复执行
5. 审批拒绝后回模型重生成
6. 工作流启动与执行
7. 资源目录操作
8. 临时文件解析并注入上下文
9. 流式返回
10. 非流式聚合返回

建议后续在本文档基础上补充 Mermaid 时序图。

## 26. 扩展开发指南

### 26.1 复用或新增工具

先核对现有工具的 schema、返回协议和目标页面目录记录：

- 只是展示格式不同：由前端复用已有结构化结果，修改对应页面组件。
- 已有工具满足需求但页面不可见：通过 `PythonToolCatalogService.save_tool(...)` 或 Java 工具目录服务补配置；页面规则通过 `AgentPagePromptCatalogService.save_prompt(...)` 维护。
- 参数与返回结构无法表达所需能力：明确缺口后，才评估扩展现有工具或新增工具。

不要为一次页面配置调整新增专用登记脚本、Workflow 或额外部署框架。确需新增工具时再关注：

- 在对应目录创建工具文件
- 使用 `@tool(...)` 注册
- 定义参数 schema
- 返回 `ToolResult`
- 必要时补充 governance metadata
- Python 工具登记到 `agent_python_tool_catalog`，Java API 工具登记到 `openapi_tool_mapping_catalog`；配置保存后验证动态过滤与加载。

### 26.2 新增一个治理规则

需要关注：

- 修改 `app/governance/rules/default.yaml`
- 校验匹配条件是否已有对应 tool tags / action tags

### 26.3 新增一个工作流

需要关注：

- 在 `app/workflows/` 中新增 Markdown 工作流定义
- 或写入 workflow catalog
- 通过 `start_workflow_tool` 接入运行链

### 26.4 新增一个 API

需要关注：

- 放入 `app/api/v1/` 或 `app/api/ws/`
- 在 router 中注册
- 明确流式 / 非流式协议

## 27. 当前实现中的关键约束

1. Session 默认走 MySQL 存储，不是文件存储
2. 审批是平台级能力，规则来源于 YAML
3. 高风险操作必须通过治理层，而不是分散在个别工具内自行判断
4. 非流式和流式语义已统一，但返回方式仍不同
5. 资源类工具当前是拆分式实现，不是集中式包装器
6. 私有运行产物和测试资料不属于开源发布范围

## 28. 建议的阅读顺序

如果是第一次接手该项目，建议按下面顺序阅读：

1. `app/main.py`
2. `app/api/v1/agent.py`
3. `app/core/agent/engine.py`
4. `app/core/tool/registry.py`
5. `app/core/tool/executor.py`
6. `app/core/governance/*`
7. `app/core/session/*`
8. `app/tools/iclass_api.py`
9. `app/tools/iclass_d_resource/*`
10. `app/tools/workflow_tools.py`
11. `app/core/workflow/*`

这样可以先理解主链路，再理解业务能力及其权限边界。
## 29. iClass D Resource 与 Governance 补充说明

### 29.1 资源类工具的当前组织方式

单个 Java 接口能力由 `/v3/api-docs/agent` 和 `openapi_tool_mapping_catalog` 动态注册，不再保留重复的 Python 具名客户端和资源工具适配文件。

`app/tools/iclass_d_resource/` 只保留需要 Python 编排或本地文件能力的工具：

- `generate_chapter.py` 内的 `build_to_chapter` 直接调用目录创建 API，负责章与节的顺序编排。
- `prase_file.py` 负责本地文件解析与缓存读取。
- `resource_common.py` 保留组合工具需要的返回契约 helper。

### 29.2 返回契约边界

资源类工具的返回契约当前固定为两条通道：

- `ToolResult.content`
  - 只给 LLM 使用
  - 放结构化结果，供模型后续推理继续使用
- `ToolResult.meta.assistant_payload`
  - 只给前端和 API 聚合层使用
  - 放 `reply_text`、`action_type`、`ui_refresh_hint`、`result_payload`

备课空间页面当前依赖的是 `assistant_payload`，而不是 `content`。

### 29.3 与治理规则的关系

当前治理规则在 `app/governance/rules/default.yaml` 中。

需要区分两类工具：

- `app/tools/iclass_api.py`
  - 主要通过 `action_tags` 接入 governance
- `app/tools/iclass_d_resource/*`
  - 主要通过 `tool_tags` 接入 governance

当前拆分后的资源类写操作工具都已经按 `tool_tags` 接入治理层，例如：

- `create_folder`
- `rename_folder`
- `rename_material`
- `move_folder`
- `move_material`
- `copy_folder`
- `delete_folder`
- `delete_material`
- `build_to_chapter`

这些工具命中“远程写操作”规则时会进入审批流；只读工具如 `list_material_directory`、`preview_material`、`download_material` 不进入审批流。

### 29.4 扩展时必须保持的约束

如果后续继续新增资源类工具，至少要保持：

- `content` 只承载 LLM 需要继续消费的数据
- `assistant_payload` 只承载前端刷新和交互需要的数据
- 新工具必须补齐 `tool_tags`
- 如需进入审批流，规则必须能通过现有 `tool_tags` 或 `action_tags` 精确命中
## 30. OpenCode Style Permission Gate

- The main path no longer pauses the agent by creating an approval ticket and then resuming it later.
- `ToolExecutor` evaluates governance with `allow`, `ask`, and `deny`.
- When a tool call hits `ask`, the executor waits inside `PermissionService.ask(...)` during the same streaming run.
- SSE emits `permission_asked`, and the frontend replies through `/v1/agent/permissions/{request_id}/reply`.
- `always` grants are stored only in memory and apply only to the current session.
- If the user edits parameters during confirmation, the reply action must be `once_with_changes`; edited confirmations cannot become `always`.

## 31. Historical Note

- Any earlier section in this guide that still mentions `approval_required`, `pending_approval_id`, `resume_approval()`, or approval ticket persistence is historical and no longer matches the current runtime implementation.
- The current runtime source of truth is the permission gate described in Section 30 and implemented by `app/core/permission/*`, `app/core/tool/executor.py`, `app/core/agent/engine.py`, and `app/api/v1/agent.py`.

## 32. Current Runtime Summary

- Governance decisions are now `allow`, `ask`, and `deny`.
- Risky tool calls no longer create approval tickets in the main runtime path.
- `ToolExecutor` performs the permission check inline before the tool executes.
- When a decision is `ask`, the executor waits inside `PermissionService.ask(...)`.
- The running agent stream emits `permission_asked` and continues after `/v1/agent/permissions/{request_id}/reply`.
- Session state no longer stores `pending_approval_id`.
- Session-scoped `always` approvals are stored only in memory through the permission store.
- The current SSE event set for permission flow is `reasoning`, `reasoning_end`, `text`, `tool_call`, `tool_result`, `permission_asked`, `warning`, `done`.

## 33. Current Workflow Runtime

Earlier workflow sections in this guide describe the historical Markdown and `start_workflow_tool` runtime. The current workflow runtime is the task-scheduler runtime implemented under `app/core/workflow/` and `app/tools/workflow_tools.py`.

Current workflow source of truth:

- Workflow catalog content and structured definitions are stored in MySQL through `WorkflowCatalogService`.
- `content` is loaded by `read_workflow_content`.
- `definition_json` is copied into each `WorkflowRun.definition_snapshot_json`.
- Local Markdown workflow files are not runtime sources.

Current coordinator tools:

- `read_workflow_content`
- `create_workflow_run`
- `dispatch_workflow_batch_and_wait`
- `get_workflow_run`
- `cancel_workflow_run`

The Agent stays in the standard loop. There is no workflow branch transition and no `_run_workflow_branch`.

Workflow persistence uses:

- `workflow_runs`
- `workflow_tasks`

The scheduler executes a fixed DAG:

- ready root tasks can run in the same batch
- the main Agent waits at the batch barrier
- summary starts only after all non-summary tasks complete
- summary tasks have no business tools
- intermediate task results stay in `workflow_tasks`
- final dispatch returns only run status, `summary_task_id`, and summary `result_json`

The in-process Subagent runtime:

- creates one fresh Subagent identity per attempt
- copies only the task `allowed_tools`
- adds terminal `complete_task` and `fail_task` tools
- validates input before Subagent start
- validates `complete_task.result_json` against output Schema
- retries execution failures up to three attempts
- treats permission rejection as terminal without retry
- cancels unfinished sibling tasks on terminal failure

SSE cleanup calls `WorkflowRuntime.cancel_active_run(...)` for the current `session_id`, `user_id`, and `agent_id`. Late terminal submissions are rejected by active Subagent identity checks.

The current workflow HTTP cancellation endpoint is:

- `POST /v1/agent/workflow-runs/{workflow_run_id}/cancel`

## 31. Current OpenAPI Page Injection

This section records the current source-of-truth path for page-scoped iClass OpenAPI tool injection.

### 31.1 Runtime entry

The standard Agent run path reads `current_workflow_page` from runtime context and uses it in two places:

1. `AgentEngine.run(...)` calls:
   - `load_iclass_openapi_tools(page_name=current_workflow_page)`
2. `AgentEngine._initialize_run_state(...)` calls:
   - `OpenApiToolMappingService.build_available_tool_names(current_workflow_page)`

If `current_workflow_page` is blank, page-scoped OpenAPI injection does not happen.

### 31.2 Required data sources

The current injection path depends on two inputs that must both exist:

1. Remote OpenAPI spec from:
   - `/v3/api-docs/agent`
2. MySQL mapping rows from:
   - `openapi_tool_mapping_catalog`

An operation is registered only when:

- the current page has an active mapping row in `openapi_tool_mapping_catalog`
- the same `http_method + api_path` exists in `/v3/api-docs/agent`

### 31.3 Role of `openapi_tool_mapping_catalog`

`openapi_tool_mapping_catalog` is the runtime page-scoped injection catalog, not a passive cache table.

The current implementation uses these fields as the effective contract:

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

The current unique key is:

- `http_method`
- `api_path`
- `page_name`

That means one OpenAPI operation can be mapped into multiple pages, but only one active row is allowed for the same operation in the same page scope.

### 31.4 Dynamic registration flow

`IClassOpenApiToolRegistrar.register_from_spec(...)` currently performs this exact flow:

1. Read all active mapping rows for the current `page_name`
2. For each row, extract:
   - `tool_name`
   - `http_method`
   - `api_path`
3. Find the exact operation under `paths[api_path][http_method.lower()]` in the remote spec
4. Build the tool schema from the OpenAPI operation
5. Register the tool into `registry` using the mapped `tool_name`

Important current behavior:

- the registered runtime tool name comes from MySQL `tool_name`
- it does not reuse old local `iclass_api__...` naming

### 31.5 Parameter schema source

Dynamic OpenAPI tool parameters are generated from the remote OpenAPI operation, not from MySQL.

The current parser behavior is:

- normalize OpenAPI parameter names into snake_case for the model-facing tool schema
- map snake_case back to original OpenAPI field names when the HTTP request is sent
- resolve `requestBody`
- recursively resolve `$ref`
- expose the resolved request body under tool argument `body`

Examples from the current implementation:

- `userCode` becomes `user_code`
- `folderId` becomes `folder_id`
- nested `ApiRequest<T>` request bodies are expanded through `$ref`

### 31.6 Tool visibility in prompt construction

`OpenApiToolMappingService.build_available_tool_names(...)` merges:

1. default common tools
2. tool names mapped for the current `page_name`

The merged result is passed into the prompt builder, so the mapping table controls both:

- whether a dynamic OpenAPI tool can be registered
- whether the tool is visible to the LLM in the current page scope

### 31.7 Page-level permission source

`ToolExecutor.execute(...)` currently checks permissions in this order:

1. session grant
2. page-level permission from `openapi_tool_mapping_catalog` by `tool_name + page_name`
3. fallback governance evaluation from YAML rules

Current page-level permission fields used at runtime:

- `permission_decision`
  - `allow`
  - `ask`
  - `deny`
- `permission_summary`
- `permission_deny_message`
- `support_user_revision`
- `visible_fields_json`
- `editable_fields_json`

Current behavior:

- `allow`: execute directly
- `deny`: reject directly
- `ask`: enter permission flow and only expose fields listed in `visible_fields_json`; only fields listed in `editable_fields_json` can be edited

### 31.8 What is required to inject question tools

Under the current implementation, a `question` operation is injected into the Agent only when all four conditions are true:

1. frontend runtime context includes the exact `current_workflow_page`
2. `/v3/api-docs/agent` contains the target `question` operation
3. `openapi_tool_mapping_catalog` contains an active row for that page and operation
4. the row's page-level permission fields match the intended interaction model

If any one of these conditions is missing, the `question` tool is not injected through the current page-scoped OpenAPI path.
