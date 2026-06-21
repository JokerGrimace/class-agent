from copy import deepcopy
from typing import Optional
from app.core.hook.types import HookResult, ToolHookContext
from app.core.workflow.runtime_events import workflow_runtime_events
import logging
import asyncio
logger = logging.getLogger(__name__)

WORKFLOW_CONTEXT_INJECTED_TOOL = {
    "create_workflow_run",
    "dispatch_workflow_batch_and_wait",
    "get_workflow_run",
    "cancel_workflow_run",
}


def inject_workflow_tool_context(context: ToolHookContext) -> Optional[HookResult]:
    if context.tool_name not in WORKFLOW_CONTEXT_INJECTED_TOOL:
        return None

    arguments = deepcopy(context.arguments)
    session = context.meta.get("session")
    if session is None:
        return HookResult(updated_arguments=arguments)

    arguments["session_id"] = session.session_id
    arguments["user_id"] = session.user_id
    arguments["agent_id"] = session.agent_id


    if context.tool_name == "create_workflow_run":
        arguments["global_context"] = dict(session.context.session_context or {})


    return HookResult(updated_arguments=arguments)
# 在工具真正执行前，通过 hook 把 workflow_run_id 对应的 queue 挂到当前 run_state.runtime_context
def subscribe_workflow_runtime_events(context: ToolHookContext) -> Optional[HookResult]:
    if context.meta.get("phase") != "prepare":
        return None

    run_state = context.meta.get("run_state")
    if run_state is None:
        return None

    workflow_run_id = context.arguments.get("workflow_run_id")
    if not isinstance(workflow_run_id, str) or not workflow_run_id.strip():
        return None

    subscriptions = run_state.runtime_context.setdefault(
        "workflow_runtime_event_subscriptions",
        {}
    )
    if context.tool_call_id in subscriptions:
        return None

    queue = workflow_runtime_events.subscribe(workflow_run_id)
    subscriptions[context.tool_call_id] = {
        "workflow_run_id": workflow_run_id,
        "queue": queue,
    }

    return None

# 工具结束后，通过 hook 自动清理订阅
def unsubscribe_workflow_runtime_events(context: ToolHookContext) -> Optional[HookResult]:
    if context.meta.get("phase") != "observe":
        return None

    run_state = context.meta.get("run_state")
    if run_state is None:
        return None

    subscriptions = run_state.runtime_context.get("workflow_runtime_event_subscriptions")
    if not isinstance(subscriptions, dict):
        return None

    subscription = subscriptions.pop(context.tool_call_id, None)
    if not subscription:
        return None

    workflow_run_id = subscription.get("workflow_run_id")
    queue = subscription.get("queue")

    if queue is not None:
        backlog = run_state.runtime_context.setdefault(
            "workflow_runtime_event_backlog",
            {},
        )
        events = backlog.setdefault(context.tool_call_id, [])
        while True:
            try:
                events.append(queue.get_nowait())
            except asyncio.QueueEmpty:
                break
            except Exception:
                logger.exception(
                    "Failed to drain workflow runtime event queue: workflow_run_id=%s tool_call_id=%s",
                    workflow_run_id,
                    context.tool_call_id,
                )
                break

    if isinstance(workflow_run_id, str) and queue is not None:
        workflow_runtime_events.unsubscribe(workflow_run_id, queue)

    if not subscriptions:
        run_state.runtime_context.pop("workflow_runtime_event_subscriptions", None)

    return None
