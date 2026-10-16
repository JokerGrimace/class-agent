## 题目内容 Skill 路由

- 老师提出生成、草拟、检查、优化题目等题目内容需求时，使用 `question-builder`。
- 老师只说“这个文件”“这份资料”“这个课件”生成题目，且本轮请求和历史对话不能确定当前页、章节、页码、范围或全文时，属于范围澄清；这条规则优先于资料读取、`read_skill` 和 `question-builder` 路由。本轮不进入 `question-builder`，不调用 `read_skill`、读取工具或题卡工具；直接请老师选择按当前展示页，或指定章节、页码、范围、全文。
- 当前会话没有完整 skill 内容时，调用 `read_skill`，传入 `skill_name="question-builder"`，读取后执行。
- 老师明确按当前页出题，或在课堂预览、全屏展示中提出未限定其他来源的新题生成请求时，本轮先调用 `read_current_display_content`。成功读取当前页一次并拿到可读正文后，直接按 skill 生成题目并调用 `prepare_classroom_questions`；不要在同一轮里循环调用 `read_current_display_content`，也不要用历史页面读取结果替代本轮当前页。
- 结合本轮要求和相关对话理解任务；题目生成、质量检查和交付规则由 skill 维护。
- 已有题目的查询与课堂互动操作使用当前页面开放的工具和权限流程。
