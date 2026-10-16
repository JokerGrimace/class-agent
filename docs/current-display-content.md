# 当前展示页读取工具

`read_current_display_content` 是备课页面 `prep-workspace` 和课堂页面 `classroom-session` 共用的只读工具，参数对象为 `{}`。“当前页”固定为用户发送本条消息时预览或全屏展示的页面，后续翻页不会改变本轮数据。

## 数据流

1. `AssistantShell` 在点击发送后、第一次异步等待之前，立即调用活动预览组件的采集回调。OnlyOffice 使用浏览器内部 `postMessage`，PDF 使用画面对应的 PDF.js 实例；采集开始早于创建 Agent 会话。
2. 前端通过 `POST /v1/agent/send` 的顶层 `display_snapshot` 携带单页内容。采集失败时仅附带 `display_snapshot_error`；没有文件时二者均省略。普通聊天不因采集失败被禁止，取消发送则不上传正文。
3. 服务端正常鉴权并校验会话后，将本轮内容复制到 `MessageDisplaySnapshot`，绑定真实 `user_id`、`session_id` 和服务端生成的 `message_id`。现有 `Message` 模型没有消息 ID，此标识只用于独立快照，不修改会话消息结构。
4. 副本保存在本次请求的独立内存对象中，通过 Python `ContextVar` 隔离执行作用域；不写入 `runtime_context`、会话历史、Redis 或提示词。上下文中的旧快照引用会被丢弃。
5. 工具从本轮对象中读取，并与引擎 `RunState` 的真实用户和会话核对；只有工具结果将正文提供给模型。本轮没有快照时明确失败，不回退到其他消息的内容。
6. 回答结束、异常或取消后关闭对象并清除正文。并发消息分别持有副本，互不覆盖；已继承作用域的子任务也不能在关闭后读取。

`display_snapshot` 结构：

```json
{
  "file_path": "course/deck.pptx",
  "file_name": "课件.pptx",
  "page_number": 3,
  "page_count": 24,
  "text": "当前页正文",
  "source": "onlyoffice_slide",
  "captured_at": "2026-09-02T04:00:00Z",
  "has_unread_visuals": false
}
```

页码从 1 开始，必须为整数且不超过总页数；正文最多 100000 字符。`source` 允许 `onlyoffice_slide`、`onlyoffice_word`、`pdf_page`。正文是用户文件数据，不能视作指令。

采集仍验证预览 iframe 的 `origin`、`contentWindow` 和文件路径；浏览器采集超时为 10 秒。服务端不再等待前端返回、轮询或重新鉴权；工具读取是本地内存操作，目录权限仍走现有通用治理流程。

已移除反向读取事件 `frontend_request`、`frontend_capabilities`、回传接口 `/v1/agent/frontend-requests/{request_id}/reply` 和 `app/core/frontend_requests.py`。这条读取链路不再要求两个 HTTP 请求落在同一 worker；不代表其他模块的多实例部署约束已消除。

## 启用

部署新代码后，使用项目指定的 Python 环境执行：

```powershell
& '/path/to/python.exe' -B scripts/register_current_display_tool.py
```

脚本通过 `PythonToolCatalogService` 将同一个工具模块 `app.tools.current_display` 注册到 `agent_python_tool_catalog` 的两个页面，并保留现有治理决定；通过 `AgentPagePromptCatalogService` 向 `agent_page_prompt_catalog` 增加当前页规则，保留原页面的生成、确认和发布规则。不会向 executor 或 system_prompt 加入业务分支。

脚本会回读核对启用记录；替换旧版同名首章，保留后续页面业务章节；相同 prompt 重复部署不重复追加。重启 Agent 后，`/openapi.json` 应包含 `SendMessageRequest.display_snapshot` 和 `display_snapshot_error`，不含反向回传接口或能力声明。HTTP 流式和非流式发送均支持；WebSocket 未增加快照字段，工具明确返回无快照。

## 边界和验证

- PPT 采集当前幻灯片文字与表格，包含整页内容，不区分动画逐次出现；不读取演讲备注。
- Word 读取 `GetCurrentVisiblePage()` 对应的实际排版页，不用光标页，也不将整篇 docx 解析结果冒充当前页。通过 SDK 的 `asc_nativePrint` 单页排版行边界提取文本，跨页段落仅返回本页部分；读取不改变选区和原始文档。
- Word 适配固定在仓库当前 SDK 版本。`wordPageContent.ts` 引用的 `AscCommon.tbc`、`AscFormat.o$e/xZa`、`AscCommonWord.WM` 和 run 范围来自该 SDK 实际源码；升级 SDK 必须重跑 Word 浏览器回归并核对接口。
- PDF 采集与画面相同的 PDF.js 文档实例中的第 N 页；只有渲染和提取都结束才允许快照。
- 图片、图表等未识别视觉内容通过 `has_unread_visuals` 标记；不执行 OCR。
- Excel 当前页未接入；Word 的浮动图片、复杂图形和公式视觉语义不在正文完整性保证范围内。
- 客户端原文只能证明用户提交了这些页内容；本接口不把它当作服务器文件完整性校验。
