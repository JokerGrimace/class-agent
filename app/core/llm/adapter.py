from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Optional


@dataclass
class Message:
    role: str
    content: str
    tool_call_id: Optional[str] = None
    tool_name: Optional[str] = None
    tool_calls: Optional[list['ToolCall']] = None
    reasoning_content: Optional[str] = None


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMEvent:
    type: str
    content: str = ""
    tool_calls: Optional[list[ToolCall]] = None
    finish_reason: Optional[str] = None
    reasoning_content: Optional[str] = None


class LLMAdapter(ABC):
    @abstractmethod
    async def chat(
        self,
        messages: list[Message],
        tools: Optional[list[dict[str, Any]]] = None,
        stream: bool = True,
    ) -> AsyncGenerator[LLMEvent, None]:
        ...

    @abstractmethod
    async def chat_complete(
        self,
        messages: list[Message],
        tools: Optional[list[dict[str, Any]]] = None,
    ) -> LLMEvent:
        ...
