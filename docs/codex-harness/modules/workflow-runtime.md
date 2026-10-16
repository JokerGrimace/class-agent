# Module Contract: Workflow Runtime

## Purpose

Define how catalog-backed task workflows are discovered, created, persisted, dispatched as fixed DAG batches, cancelled, and summarized inside the agent harness.

## Responsibility

The Workflow Runtime module owns:

- storing workflow catalog content and structured `definition_json` in MySQL
- exposing workflow summaries to the system prompt
- creating `WorkflowRun` records and their `WorkflowTask` rows
- enforcing the active-run rule for `(session_id, user_id)`
- selecting ready DAG batches
- building fixed task inputs from `global_context`, dependency `task_results`, and task `constants`
- executing each ready task through an in-process one-shot Subagent runtime
- enforcing task `input_schema` and `output_schema`
- retrying failed task execution up to three fresh Subagent attempts
- failing the run on terminal task failure
- cancelling unfinished sibling tasks when a terminal failure occurs
- cancelling active attempts when the user cancels a run or closes the SSE stream
- emitting workflow status and permission events through the runtime event broker

The Workflow Runtime module does not own:

- deciding whether the user request should use a workflow
- concrete business tool implementation details
- concrete iClass API endpoint implementation details
- frontend rendering of workflow status
- database migration management beyond table creation currently present in storage classes

## Catalog Source Of Truth

Runtime workflow definitions come from `workflow_catalog` through `WorkflowCatalogService`.

The catalog record separates:

- `content`: human-readable workflow instructions loaded through `read_workflow_content`
- `definition_json`: structured fixed DAG definition used to create `WorkflowRun`

Local Markdown workflow files and `app/core/workflow/catalog.py` are no longer runtime sources of truth.

## Runtime Data

Workflow persistence uses:

- `workflow_runs`
- `workflow_tasks`

`WorkflowRun` stores:

- `workflow_run_id`
- `workflow_name`
- `summary_task_id`
- `user_id`
- `session_id`
- `coordinator_agent_id`
- `status`
- `global_context_json`
- `definition_snapshot_json`
- `error_json`
- timestamps

`WorkflowTask` stores:

- `workflow_run_id`
- `task_id`
- `status`
- `result_json`
- `error_json`
- timestamps

Known run statuses are `pending`, `running`, `completed`, `failed`, and `cancelled`.
Known task statuses are `pending`, `running`, `completed`, `failed`, and `cancelled`.

## Coordinator Tools

The main Agent coordinates workflows with these tools:

- `read_workflow_content`
- `create_workflow_run`
- `dispatch_workflow_batch_and_wait`
- `get_workflow_run`
- `cancel_workflow_run`

`start_workflow_tool` is removed.

The platform injects hidden ownership arguments into runtime workflow tools:

- `session_id`
- `user_id`
- `agent_id`

For `create_workflow_run`, the platform also injects:

- `global_context`

The LLM-facing schema for these tools exposes only the public arguments documented in `app/tools/workflow_tools.py`.

## Main Agent Flow

The main Agent stays in the standard LLM/tool loop. There is no workflow branch transition.

Typical flow:

1. The Agent calls `read_workflow_content(workflow_name)`.
2. The Agent calls `create_workflow_run(workflow_name)`.
3. The Agent calls `dispatch_workflow_batch_and_wait(workflow_run_id)`.
4. If the dispatch result has `status: running`, the Agent calls dispatch again.
5. When the dispatch result has `status: completed`, the Agent reports only the summary task result.

Intermediate task `result_json` values are stored in `workflow_tasks`, but dispatch tool output does not expose those intermediate results to the main Agent.

## Fixed DAG Scheduling

`WorkflowScheduler.select_ready_batch` selects pending tasks whose dependencies are complete.

The summary task:

- must exist in the definition
- must have dependencies
- must have no `allowed_tools`
- must be terminal
- starts only after all non-summary tasks are completed

`WorkflowScheduler.build_task_input` returns:

```json
{
  "global_context": {},
  "task_results": {},
  "constants": {}
}
```

`task_results` contains only direct dependency results.

## Subagent Runtime

`WorkflowSubagentRuntime` executes each ready task with an isolated one-shot Subagent.

For each task attempt:

- a fresh `subagent_id` is created
- only the task's `allowed_tools` are copied into the isolated registry
- `complete_task` and `fail_task` terminal tools are added
- the Subagent receives the task instruction, fixed input, output schema, current attempt, and previous error

`complete_task` validates `result_json` against the task `output_schema`.
Invalid output does not complete the task and is retried through a fresh Subagent attempt.

`fail_task` accepts only normalized task failure payloads and returns a failed execution result.

## Retry, Failure, And Cancellation

The runtime uses at most three attempts per task.

Input schema validation happens before Subagent start. If input validation fails:

- no Subagent starts
- the task fails
- the run fails
- unfinished tasks are cancelled

Permission rejection is terminal and is not retried.

When a task reaches terminal failure:

- in-flight sibling attempts are cancelled
- unfinished sibling tasks are marked `cancelled`
- the run is marked `failed`

When the SSE stream closes, `app/api/v1/agent.py` calls `WorkflowRuntime.cancel_active_run(...)` for the current `(session_id, user_id, agent_id)`.

Late terminal submissions are rejected by active Subagent identity checks.

## Events

The runtime emits `workflow_status` events with this payload:

- `workflow_run_id`
- `task_id`
- `show_name`
- `status`
- `attempt`
- `max_attempts`

Subagent permission requests are relayed as `permission_asked` events through the same runtime event bridge.

## Public Interfaces

### Runtime

- `configure_workflow_runtime`
- `get_workflow_runtime`
- `WorkflowRuntime.dispatch_owned`
- `WorkflowRuntime.cancel_owned`
- `WorkflowRuntime.cancel_for_user`
- `WorkflowRuntime.cancel_active_run`

### Manager

- `WorkflowManager.create_workflow_run`
- `WorkflowManager.get_workflow_run`
- `WorkflowManager.get_workflow_run_for_user`
- `WorkflowManager.get_workflow_tasks`
- `WorkflowManager.complete_task`
- `WorkflowManager.fail_task`
- `WorkflowManager.cancel_workflow_run`
- `WorkflowManager.cancel_workflow_run_for_user`
- `WorkflowManager.get_active_workflow_run`
- `WorkflowManager.fail_incomplete_runs_on_startup`

### Storage

- `WorkflowStorage.create_run_with_tasks`
- `WorkflowStorage.get_run`
- `WorkflowStorage.list_tasks`
- `WorkflowStorage.update_run_status`
- `WorkflowStorage.update_task_status`
- `WorkflowStorage.fail_stale_active_runs`
- `WorkflowStorage.get_active_run_for_owner`

## Dependencies

- `app.core.workflow.models`
- `app.core.workflow.manager`
- `app.core.workflow.scheduler`
- `app.core.workflow.storage`
- `app.core.workflow.catalog_db`
- `app.core.workflow.runtime`
- `app.core.workflow.runtime_events`
- `app.core.workflow.subagent_runtime`
- `app.tools.workflow_tools`
- `app.core.agent.standard_runner`
- `app.core.llm.stream_parser`
- `app.core.tool.executor`
- `app.core.tool.registry`

## Acceptance

This module contract is satisfied when:

- workflow catalog summaries can be listed in the system prompt
- `read_workflow_content` loads catalog content
- `create_workflow_run` persists a run and all tasks
- a second pending/running run in the same session and user is rejected
- different sessions for the same user can run concurrently
- ready root tasks start in the same batch
- the summary task starts only after every non-summary task completes
- intermediate task results do not enter main Agent dispatch output
- final dispatch returns only run status, `summary_task_id`, and summary `result_json`
- input validation failure, output retry, permission rejection, timeout, terminal failure, cancellation, and stale terminal submission are tested
