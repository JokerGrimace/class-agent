from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Callable, Awaitable, AsyncGenerator, Literal

from app.core.llm.adapter import Message
from app.core.llm.stream_parser import LLMToolCallExecutor, AgentEvent, LLMRunStreamEvent, LLMRunResult
from app.core.session.models import Session
from app.core.tool.executor import ToolExecutor
from app.core.tool.registry import registry


@dataclass
class StepConfig:
    id: str
    instruction: str
    output_key: str
    pre_task_output: List[str]
    allowed_tools: List[str]
    max_retries: int


@dataclass
class FinalAnswerConfig:
    instruction: str
    input_from: List[str]


@dataclass
class WorkflowDefinition:
    version: int
    system_prompt: str
    allowed_tools: List[str]
    steps: List[StepConfig]
    final_answer: FinalAnswerConfig

    @classmethod
    def _parse_data(cls, data: Dict[str, Any]) -> "WorkflowDefinition":
        steps = [
            StepConfig(
                id=step["id"],
                instruction=step["instruction"],
                output_key=step["output_key"],
                pre_task_output=step.get("pre_task_output", step.get("input_from", [])),
                allowed_tools=step.get("allowed_tools", []),
                max_retries=step.get("max_retries", 1),
            )
            for step in data["steps"]
        ]

        final_answer = FinalAnswerConfig(
            instruction=data["final_answer"]["instruction"],
            input_from=data["final_answer"].get("input_from", []),
        )

        return cls(
            version=data["version"],
            system_prompt=data["system_prompt"],
            allowed_tools=data.get("allowed_tools", []),
            steps=steps,
            final_answer=final_answer,
        )


@dataclass
class WorkflowStepEvent:
    type: Literal["runtime", "completed"]
    runtime_event: Optional[AgentEvent] = None
    step_success: Optional[bool] = None
    error_message: str = ""


class WorkflowExecutor:
    def __init__(
        self,
        definition: Optional[WorkflowDefinition] = None,
        session: Optional[Session] = None,
        llm=None,
        context_parameters: Optional[dict[str, Any]] = None,
        tool_executor: Optional[ToolExecutor] = None,
    ):
        if definition is None or session is None or llm is None:
            raise ValueError("definition, session, and llm are required")
        if isinstance(definition, dict):
            definition = WorkflowDefinition._parse_data(definition)

        self.definition = definition
        self.session = session
        self.llm_tool_executor = LLMToolCallExecutor(
            llm,
            session,
            tool_executor=tool_executor,
            persist_messages=False,
        )
        self.step_outputs: Dict[str, Any] = {}
        self.context_parameters: Dict[str, Any] = context_parameters or {}

    def load_runtime_state(
        self,
        *,
        step_outputs: Optional[dict[str, Any]] = None,
        context_parameters: Optional[dict[str, Any]] = None,
    ) -> None:
        self.step_outputs = dict(step_outputs or {})
        if context_parameters is not None:
            self.context_parameters = dict(context_parameters)

    def tool_registry(self, tool_list: list) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in tool_list
        ]

    def build_message(self, role: str, prompt: str) -> Message:
        return Message(role=role, content=prompt)

    def build_workflow_messages(self) -> list[Message]:
        system_prompt = self.definition.system_prompt
        system_prompt += "Context Parameters:\n"
        for key, value in self.context_parameters.items():
            system_prompt += f"  - {key}: {json.dumps(value, ensure_ascii=False)}\n"
        system_prompt += (
            "Workflow Step Usage Rules:\n"
            "- Use only the current step instruction.\n"
            "- Use only the provided pre_task_output and current global context.\n"
            "- Do not assume outputs from future steps.\n"
            "- If a file_cache_key is provided and full file content is needed, call read_cached_file_content.\n"
            "- Return valid JSON only, with step_success and output.\n"
        )
        return [self.build_message("system", system_prompt)]

    def _resolve_input_value(self, input_key: str) -> Any:
        if input_key in self.step_outputs:
            return self.step_outputs[input_key]
        return self.context_parameters.get(input_key)

    @staticmethod
    def _extract_json(text: str) -> str:
        match = re.search(r"```(?:json)?\s*\n(.*?)\n\s*```", text, re.DOTALL)
        if match:
            return match.group(1).strip()

        start = text.find("{")
        if start >= 0:
            depth = 0
            for index in range(start, len(text)):
                if text[index] == "{":
                    depth += 1
                elif text[index] == "}":
                    depth -= 1
                    if depth == 0:
                        return text[start:index + 1].strip()

        return text.strip()

    # 向前端生成对应的事件
    async def execute_step_events(
        self,
        step: StepConfig,
        list_messages: list[Message],
        session: Session,
    ) -> AsyncGenerator[WorkflowStepEvent, None]:
        retry_count = 0
        while retry_count <= step.max_retries:
            try:
                step_messages = list(list_messages)

                prompt = f"Step ID: {step.id}\n"
                prompt += f"Instruction: {step.instruction}\n"
                prompt += "PreTask Result:\n"
                for input_key in step.pre_task_output:
                    input_value = self._resolve_input_value(input_key)
                    prompt += f"  - {input_key}: {json.dumps(input_value, ensure_ascii=False)}\n"
                prompt += (
                    f"Output Requirement: Return structured JSON with 'step_success' (bool) "
                    f"and 'output' (value for {step.output_key}).\n"
                )

                step_messages.append(self.build_message("user", prompt))

                register_tool = []
                allowed_tools = list(step.allowed_tools or [])
                if "read_cached_file_content" not in allowed_tools:
                    allowed_tools.append("read_cached_file_content")
                if allowed_tools:
                    register_tool = self.tool_registry(registry.filter_tools_by_list(allowed_tools))

                async for event in self.llm_tool_executor.run_until_done(
                        session=session,
                        messages=step_messages,
                        tools=register_tool,
                ):
                    if event.type == "runtime":
                        yield WorkflowStepEvent(type="runtime", runtime_event=event.runtime_event)
                        continue

                    if event.type == "completed":
                        run_result = event.result
                        tool_error_message = self._extract_tool_error_message(run_result)
                        if tool_error_message:
                            raise RuntimeError(tool_error_message)

                        raw_content = (run_result.content if run_result else "").strip()
                        if not raw_content:
                            raise RuntimeError("LLM returned no step result")

                        json_str = self._extract_json(raw_content)
                        try:
                            llm_result = json.loads(json_str)
                        except json.JSONDecodeError as exc:
                            raise RuntimeError(f"LLM returned invalid step JSON: {raw_content}") from exc

                        if llm_result.get("step_success"):
                            self.step_outputs[step.output_key] = llm_result["output"]
                            list_messages[:] = step_messages
                            yield WorkflowStepEvent(type="completed", step_success=True)
                            return

                        raise RuntimeError(f"LLM returned step failure: {llm_result.get('thought')}")

            except Exception as exc:
                retry_count += 1
                if retry_count > step.max_retries:
                    yield WorkflowStepEvent(
                        type="completed",
                        step_success=False,
                        error_message=str(exc),
                    )
                    return

    @staticmethod
    def _extract_tool_error_message(run_result: Optional[LLMRunResult]) -> str:
        if run_result is None or not run_result.tool_call_results:
            return ""
        for tool_call_result in reversed(run_result.tool_call_results):
            error = getattr(tool_call_result, "error", None)
            if error is None:
                continue
            error_message = str(getattr(error, "error", "") or "")
            if error_message:
                return error_message
        return ""

    async def generate_final_answer(self) -> tuple[Any, str]:
        final_prompt = f"{self.definition.system_prompt}\n"
        final_prompt += f"Final Instruction: {self.definition.final_answer.instruction}\n"
        final_prompt += "Input:\n"
        for input_key in self.definition.final_answer.input_from:
            input_value = self._resolve_input_value(input_key)
            final_prompt += f"  - {input_key}: {json.dumps(input_value, ensure_ascii=False)}\n"

        final_messages = [self.build_message("system", self.definition.system_prompt)]
        final_messages.append(self.build_message("user", final_prompt))
        final_run_result = await self._collect_llm_run_result(
                self.llm_tool_executor.run_until_done(
                session=self.session,
                 messages=final_messages,
                tools=[],
                stream=True,
         )
            )

        raw_final_content = final_run_result.content.strip()
        if not raw_final_content:
            raise RuntimeError("LLM returned no final workflow result")
        json_str = self._extract_json(raw_final_content)
        try:
            final_result = json.loads(json_str)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"LLM returned invalid final JSON: {raw_final_content}") from exc

        final_answer = final_result.get("output", final_result)
        summary = final_result.get("thought", "")
        return final_answer, summary

    async def _collect_llm_run_result(self, events) -> LLMRunResult:
        async for event in events:
            if event.type == "completed" and event.result is not None:
                return event.result
        return LLMRunResult(content="")
