from copy import deepcopy
from typing import Optional

from app.core.hook.types import HookResult, ToolHookContext


def inject_workflow_tool_context(context: ToolHookContext) -> Optional[HookResult]:
    # start_workflow_tool 需要会话值，大语言模型不应虚构这些值。
    # 将此作为内置钩子，可从智能体循环中去除该特殊情况，同时保留工具使用的确切参数名称。
    if context.tool_name != "start_workflow_tool":
        return None

    arguments = deepcopy(context.arguments)
    session = context.meta.get("session")
    # 执行阶段在钩子元数据中没有会话对象。返回输入内容
    # 保持不变可确保在治理评估之前，当 BeforeToolUse 在 ToolExecutor 内部再次运行时，此钩子具有幂等性。
    if session is None:
        return HookResult(updated_arguments=arguments)

    arguments["session_id"] = session.session_id
    arguments["user_id"] = session.user_id
    arguments["agent_id"] = session.agent_id

    runtime_context = dict(session.context.session_context or {})
    explicit_runtime_context = arguments.get("runtime_context")
    if isinstance(explicit_runtime_context, dict):
        runtime_context.update(explicit_runtime_context)
    if runtime_context:
        arguments["context_parameters"] = runtime_context

    return HookResult(updated_arguments=arguments)
