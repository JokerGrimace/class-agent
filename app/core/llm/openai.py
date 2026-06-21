import json
from typing import Any, AsyncGenerator, Optional

from openai import AsyncOpenAI

from app.config import settings
from app.core.llm.adapter import LLMAdapter, LLMEvent, Message, ToolCall


class OpenAIAdapter(LLMAdapter):
    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.client = AsyncOpenAI(api_key=api_key or settings.openai_api_key,base_url=settings.openai_base_url)
        self.model = model or settings.openai_model

    async def chat(
        self,
        messages: list[Message],
        tools: Optional[list[dict[str, Any]]] = None,
        stream: bool = True,
    ) -> AsyncGenerator[LLMEvent, None]:
        openai_messages = []
        for m in messages:
            entry: dict[str, Any] = {"role": m.role}
            if m.content is not None:
                entry["content"] = m.content
            if m.role == "tool":
                entry["tool_call_id"] = m.tool_call_id or ""
            if m.tool_name:
                entry["name"] = m.tool_name
            if m.reasoning_content:
                entry["reasoning_content"] = m.reasoning_content
            if m.role == "assistant" and m.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                        },
                    }
                    for tc in m.tool_calls
                ]
            openai_messages.append(entry)

        params: dict[str, Any] = {
            "model": self.model,
            "messages": openai_messages,
            "stream": stream,
        }
        if tools:
            params["tools"] = tools
            params["tool_choice"] = "auto"

        response = await self.client.chat.completions.create(**params)

        if stream:
            pending_tool_calls: dict[int, dict[str, Any]] = {}
            accumulated_reasoning = ""

            async for chunk in response:
                if chunk.choices and chunk.choices[0].delta:
                    delta = chunk.choices[0].delta
                    finish_reason = chunk.choices[0].finish_reason

                    if getattr(delta, "reasoning_content", None):
                        accumulated_reasoning += delta.reasoning_content
                        yield LLMEvent(
                            type="reasoning",
                            content=delta.reasoning_content,
                            reasoning_content=delta.reasoning_content,
                            finish_reason=finish_reason,
                        )

                    if delta.content:
                        yield LLMEvent(
                            type="content",
                            content=delta.content,
                            finish_reason=finish_reason,
                        )

                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            if idx not in pending_tool_calls:
                                pending_tool_calls[idx] = {"id": "", "name": "", "arguments": ""}
                            entry = pending_tool_calls[idx]
                            if tc.id:
                                entry["id"] = tc.id
                            if tc.function and tc.function.name:
                                entry["name"] = tc.function.name
                            if tc.function and tc.function.arguments:
                                entry["arguments"] += tc.function.arguments

                    if finish_reason and pending_tool_calls:
                        completed: list[ToolCall] = []
                        for idx in sorted(pending_tool_calls.keys()):
                            entry = pending_tool_calls[idx]
                            try:
                                args = json.loads(entry["arguments"] or "{}")
                            except json.JSONDecodeError:
                                args = {}
                            completed.append(ToolCall(
                                id=entry["id"] or f"call_{idx}",
                                name=entry["name"],
                                arguments=args,
                            ))
                        yield LLMEvent(
                            type="tool_call",
                            content="",
                            tool_calls=completed,
                            finish_reason=finish_reason,
                            reasoning_content=accumulated_reasoning or None,
                        )
                        accumulated_reasoning = ""
                        pending_tool_calls.clear()
                    elif finish_reason == "stop":
                        accumulated_reasoning = ""
        else:
            choice = response.choices[0]
            content = choice.message.content or ""
            reasoning = getattr(choice.message, "reasoning_content", None) or None
            tool_calls: Optional[list[ToolCall]] = None
            if choice.message.tool_calls:
                tool_calls = [
                    ToolCall(
                        id=tc.id or f"call_{i}",
                        name=tc.function.name,
                        arguments=json.loads(tc.function.arguments or "{}"),
                    )
                    for i, tc in enumerate(choice.message.tool_calls)
                ]
            yield LLMEvent(
                type="content",
                content=content,
                tool_calls=tool_calls,
                reasoning_content=reasoning,
                finish_reason=choice.finish_reason,
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
