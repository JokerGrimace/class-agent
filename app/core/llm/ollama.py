import json
from typing import Any, AsyncGenerator, Optional

import httpx

from app.config import settings
from app.core.llm.adapter import LLMAdapter, LLMEvent, Message, ToolCall


class OllamaAdapter(LLMAdapter):
    def __init__(self, base_url: Optional[str] = None, model: Optional[str] = None):
        self.base_url = base_url or settings.ollama_base_url
        self.model = model or settings.ollama_model
        self.client = httpx.AsyncClient(base_url=self.base_url, timeout=120.0)

    async def chat(
        self,
        messages: list[Message],
        tools: Optional[list[dict[str, Any]]] = None,
        stream: bool = True,
    ) -> AsyncGenerator[LLMEvent, None]:
        ollama_messages = []
        for m in messages:
            entry: dict[str, Any] = {"role": m.role, "content": m.content}
            if m.role == "tool":
                entry["tool_call_id"] = m.tool_call_id or ""
            if m.tool_name:
                entry["name"] = m.tool_name
            if m.role == "assistant" and m.tool_calls:
                entry["tool_calls"] = [
                    {
                        "function": {
                            "name": tc.name,
                            "arguments": tc.arguments,
                        },
                    }
                    for tc in m.tool_calls
                ]
            ollama_messages.append(entry)

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": ollama_messages,
            "stream": stream,
        }
        if tools:
            payload["tools"] = [t["function"] for t in tools]

        async with self.client.stream("POST", "/api/chat", json=payload) as response:
            async for line in response.aiter_lines():
                if not line:
                    continue
                data = json.loads(line)
                msg = data.get("message", {})

                tool_calls: Optional[list[ToolCall]] = None
                if msg.get("tool_calls"):
                    tool_calls = [
                        ToolCall(
                            id=tc.get("id", f"call_{i}"),
                            name=tc["function"]["name"],
                            arguments=tc["function"].get("arguments", {}),
                        )
                        for i, tc in enumerate(msg["tool_calls"])
                    ]

                yield LLMEvent(
                    type="content" if msg.get("content") else "tool_call",
                    content=msg.get("content", ""),
                    tool_calls=tool_calls,
                    finish_reason=data.get("done_reason"),
                )

    async def chat_complete(
        self,
        messages: list[Message],
        tools: Optional[list[dict[str, Any]]] = None,
    ) -> LLMEvent:
        result: Optional[LLMEvent] = None
        async for event in self.chat(messages, tools, stream=False):
            result = event
        return result or LLMEvent(type="content", content="", finish_reason="empty")

    async def close(self):
        await self.client.aclose()
