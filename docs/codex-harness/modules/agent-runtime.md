# Module Contract: Agent Runtime

## Purpose

Define how an authenticated user request enters the agent runtime, becomes LLM messages, executes tool calls, relays permissions and workflow events, and streams results back to the API layer.

## Responsibility

The Agent Runtime module owns:

- receiving chat requests from `POST /v1/agent/send`
- creating or loading the current session
- appending user messages
- building runtime context for the agent
- building the system prompt and tool schema list
- running the standard LLM/tool loop
- executing tool calls through `ToolExecutor`
- injecting hidden workflow ownership context
- relaying workflow runtime events while dispatch tools run
- streaming runtime events back to the API layer
- cancelling pending permission requests and active workflow runs on SSE cleanup

The Agent Runtime module does not own:

- concrete iClass API endpoint implementations
- resource tool business logic
- workflow catalog persistence schema
- workflow task execution internals
- permission rule definitions
- frontend rendering

## Inputs

- `SendMessageRequest.message`
- `SendMessageRequest.session_id`
- `SendMessageRequest.stream`
- `SendMessageRequest.tool_type`
- `SendMessageRequest.runtime_context`
- authenticated `current_user.user_id`
- existing `Session` messages and context

`SendMessageRequest` no longer accepts legacy workflow resume fields.

## Outputs

The runtime emits `AgentEvent` values that the API layer maps to SSE events:

- `reasoning`
- `reasoning_end`
- `content`
- `tool_call`
- `tool_result`
- `workflow_status`
- `permission_asked`
- `warning`
- `done`

For non-stream calls, the API layer aggregates:

- response text
- tool results
- warnings
- assistant payload

## Runtime Flow

1. `send_message` loads an existing session or creates a new session.
2. Pending permission requests for the session are cancelled.
3. The user message is appended to the session.
4. `_build_agent_engine` creates the LLM adapter, `ToolExecutor`, and `AgentEngine`.
5. `AgentEngine.run` initializes `RunState`.
6. `StandardAgentRunner.run` executes the only active branch.
7. `StandardAgentRunner` builds messages through `AgentEngine._build_messages`.
8. `_build_messages` uses `SystemPromptBuilder.build_with_context`.
9. `StandardAgentRunner` sends messages and tool schemas to the LLM.
10. If the LLM returns tool calls, each tool call is prepared and executed through `ToolExecutor`.
11. Tool calls and tool results are persisted as assistant/tool message pairs.
12. If a dispatch tool is running, workflow runtime events are relayed before the dispatch tool result.
13. When the LLM returns assistant text, the assistant message is persisted and the stream ends with `done`.

There is no `_run_workflow_branch` and no workflow transition state.

## Tool Preparation

Workflow coordinator tools receive hidden runtime fields before execution:

- `session_id`
- `user_id`
- `agent_id`

`create_workflow_run` also receives:

- `global_context`

This injection is performed by the hook path in `app/core/hook/builtins.py` and by the fallback path in `AgentEngine._prepare_tool_call`.

## Workflow Event Relay

The workflow event bridge works during dispatch tool calls:

1. The prepare-phase hook subscribes to the `workflow_run_id` event queue.
2. `StandardAgentRunner._execute_pending_tool_calls` polls the queue while the dispatch tool is running.
3. `WorkflowRuntimeEvent` values become `AgentEvent(type="workflow_status")`.
4. Subagent permission requests pass through as `permission_asked`.
5. The observe-phase hook drains backlog and unsubscribes.

`workflow_status` event metadata contains:

- `workflow_run_id`
- `task_id`
- `show_name`
- `status`
- `attempt`
- `max_attempts`

## Permission Flow

The current permission flow is OpenCode-style inline permission gating:

1. `ToolExecutor` evaluates governance.
2. `allow` executes immediately.
3. `deny` returns a failed `ToolResult`.
4. `ask` waits inside `PermissionService.ask(...)`.
5. The stream emits `permission_asked`.
6. The frontend replies through `POST /v1/agent/permissions/{request_id}/reply`.
7. The waiting tool call continues or returns a rejection result.

The legacy approval ticket and resume path is not part of the current runtime.

## SSE Cleanup

When the SSE generator closes, `_generate_events`:

- cancels pending permission requests for the current session
- calls `get_workflow_runtime().cancel_active_run(...)`

This cancels the active workflow run for the current `(session_id, user_id, agent_id)` and sets cancellation events for active Subagent attempts.

## Dependencies

- `app.api.v1.agent`
- `app.core.agent.engine`
- `app.core.agent.standard_runner`
- `app.core.agent.system_prompt`
- `app.core.hook.builtins`
- `app.core.llm.factory`
- `app.core.session.manager`
- `app.core.tool.executor`
- `app.core.tool.registry`
- `app.core.workflow.runtime`
- `app.core.workflow.runtime_events`

## Public Interfaces

### HTTP

- `POST /v1/agent/send`
- `POST /v1/agent/permissions/{request_id}/reply`
- `POST /v1/agent/workflow-runs/{workflow_run_id}/cancel`

### Runtime Classes

- `AgentEngine`
- `StandardAgentRunner`
- `SystemPromptBuilder`

### Key Methods

- `AgentEngine.run`
- `AgentEngine._initialize_run_state`
- `AgentEngine._run_standard_branch`
- `AgentEngine._prepare_tool_call`
- `StandardAgentRunner.run`
- `StandardAgentRunner._prepare_tool_calls`
- `StandardAgentRunner._execute_pending_tool_calls`
- `SystemPromptBuilder.build`
- `SystemPromptBuilder.build_with_context`

## Error and Stop Conditions

- Missing session returns HTTP 404 in `send_message`.
- LLM call failure emits a `warning` event and stops the standard branch.
- Context overflow emits a `warning` event and stops the branch if compaction cannot fit the context.
- Max turn limit marks the task ended with reason `max_turns`.
- Repeated identical tool failure can trigger the tool call limit path.
- Permission rejection returns a failed tool result and does not create a resume ticket.
- SSE cleanup cancels pending permission requests and the active workflow run.

## Acceptance

This module contract is satisfied when:

- a user request can be traced from `POST /v1/agent/send` to `AgentEngine.run`
- the standard branch can expose filtered tool schemas to the LLM
- tool calls are persisted as assistant/tool message pairs
- workflow coordinator tools stay in the standard loop
- workflow runtime events are relayed before dispatch tool results
- `permission_asked` events unblock through the permission reply API
- SSE close cancels active workflow runs
