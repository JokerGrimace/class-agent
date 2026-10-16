from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Literal


WorkflowRuntimeStatus = Literal[
    "running",
    "retrying",
    "completed",
    "failed",
    "cancelled",
]


@dataclass
class WorkflowRuntimeEvent:
    workflow_run_id: str
    task_id: str
    show_name: str
    status: WorkflowRuntimeStatus
    attempt: int
    max_attempts: int
    workflow_name: str = ""


class WorkflowRuntimeEventBroker:
    def __init__(self):
        self._queues: dict[str, list[asyncio.Queue[Any]]] = {}

    def subscribe(self, workflow_run_id: str) -> asyncio.Queue[Any]:
        queue: asyncio.Queue[Any] = asyncio.Queue()
        self._queues.setdefault(workflow_run_id, []).append(queue)
        return queue

    def unsubscribe(
        self,
        workflow_run_id: str,
        queue: asyncio.Queue[Any],
    ) -> None:
        queues = self._queues.get(workflow_run_id)
        if not queues:
            return

        self._queues[workflow_run_id] = [item for item in queues if item is not queue]
        if not self._queues[workflow_run_id]:
            self._queues.pop(workflow_run_id, None)

    async def publish(self, workflow_run_id: str, event: Any) -> None:
        queues = self._queues.get(workflow_run_id, [])
        for queue in queues:
            await queue.put(event)


workflow_runtime_events = WorkflowRuntimeEventBroker()
