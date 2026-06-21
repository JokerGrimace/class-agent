import inspect
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Optional, Union

from app.core.hook.types import HookContext, HookEventName, HookResult

HookHandler = Callable[
    [HookContext],
    Union[Optional[HookResult], Awaitable[Optional[HookResult]]],
]


class HookManager:
    def __init__(self):
        self._handlers: dict[HookEventName, list[HookHandler]] = defaultdict(list)

    def register(
        self,
        event_name: HookEventName,
        handler: HookHandler,
        *,
        prepend: bool = False,
    ) -> None:
        # 前置操作用于那些必须在项目特定钩子检查上下文之前对其进行规范化的平台钩子。例如，工作流参数注入必须在审计钩子读取最终参数之前运行。
        if prepend:
            self._handlers[event_name].insert(0, handler)
            return
        self._handlers[event_name].append(handler)

    async def trigger(self, context: HookContext) -> HookResult:
        merged = HookResult()
        for handler in self._handlers.get(context.event_name, []):
            result = handler(context)
            if inspect.isawaitable(result):
                result = await result
            if result is None:
                continue
            if result.updated_arguments is not None:
                merged.updated_arguments = dict(result.updated_arguments)
                # 后续的钩子函数应该留意早期钩子函数产生的参数，这样归一化钩子函数和审计钩子函数就能协同工作。
                if hasattr(context, "arguments"):
                    context.arguments = dict(result.updated_arguments)
            if result.additional_context:
                merged.additional_context = (
                    f"{merged.additional_context}\n{result.additional_context}"
                    if merged.additional_context
                    else result.additional_context
                )
            if result.meta:
                merged.meta.update(result.meta)
            if result.continue_execution is False or result.blocking_error:
                # 在第一个阻塞挂钩处停止。在阻塞之前收集的非阻塞更新将保留用于诊断。
                return HookResult(
                    continue_execution=False,
                    blocking_error=result.blocking_error,
                    updated_arguments=merged.updated_arguments,
                    additional_context=merged.additional_context,
                    meta=merged.meta,
                )
        return merged
