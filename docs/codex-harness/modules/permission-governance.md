# Module Contract: Permission and Governance

## Purpose

Define how tool calls are evaluated against governance rules, how user permission is requested, and how permission replies resume or stop tool execution.

## Responsibility

The Permission and Governance module owns:

- loading governance rules from YAML
- validating governance configuration shape
- matching a tool call against governance rules
- producing `allow`, `ask`, or `deny` decisions
- selecting visible and editable argument fields
- creating pending permission requests
- emitting permission request data for the API stream
- validating permission replies
- resolving the blocked tool execution future
- storing session-scoped permission grants
- cancelling stale permission requests when a new user message or stream close occurs

The Permission and Governance module does not own:

- concrete tool business logic
- choosing which tool the LLM should call
- frontend UI rendering of permission prompts
- long-term persistence of permission grants
- workflow step orchestration

## Inputs

### Governance Evaluation

- `tool_name`
- `tool_tags`
- `action_name`
- `action_tags`
- tool call `arguments`
- governance rules from `app/governance/rules/default.yaml`

### Permission Request

- `session_id`
- `user_id`
- `tool_call_id`
- `tool_name`
- `tool_action`
- `summary`
- `original_arguments`
- `visible_arguments`
- `editable_fields`

### Permission Reply

- `request_id`
- authenticated `user_id`
- reply `action`
- `edited_fields`
- optional `comment`

## Outputs

### Governance

- `GovernanceDecision`
  - `decision`
  - `summary`
  - `visible_fields`
  - `editable_fields`
  - `deny_message`
  - `support_user_revision`
  - `matched_rule_ids`

### Permission

- `PendingPermissionRequest`
- resolved `PermissionReply`
- optional `SessionPermissionGrant`
- `permission_asked` runtime event
- HTTP reply result:
  - `request_id`
  - `accepted`

## Governance Rule Evaluation

Governance configuration is loaded by `GovernanceConfigLoader`.

The loader reads YAML with UTF-8 encoding and validates it as `GovernanceConfig`.

`GovernancePolicyEngine.evaluate`:

1. Sorts rules by `priority` descending.
2. Keeps rules matching the current `EvaluationContext`.
3. Builds one `GovernanceDecision` from all matched rules.

Rule match fields currently supported:

- `tool_names_any`
- `tool_tags_all`
- `tool_tags_any`
- `action_equals_any`
- `action_tags_any`
- `action_tags_all`

Rule effect fields currently supported:

- `decision`
- `summary`
- `field_preset`
- `visible_fields`
- `editable_fields`
- `deny_message`
- `support_user_revision`

Editable fields must also be visible fields.

## Current Default Rules

The default rule file is `app/governance/rules/default.yaml`.

Current defaults:

- `decision: allow`
- `visibility: hidden`
- `on_deny: retry_reasoning`

Current field preset:

- `name_like_fields`
  - visible fields:
    - `name`
    - `title`
    - `folder_name`
    - `file_name`
  - editable fields:
    - `name`
    - `title`
    - `folder_name`
    - `file_name`

Current rules:

- `require-approval-for-mutating-remote-tools`
- `require-approval-for-mutating-remote-actions`
- `expose-name-like-fields-for-rename-or-create-tools`
- `expose-chapter-list-for-build-to-chapter`
- `expose-name-like-fields-for-revision`

## Permission Request Lifecycle

1. `ToolExecutor` evaluates governance for a tool call.
2. If the decision is `ask`, `ToolExecutor` builds `visible_arguments` from `decision.visible_fields`.
3. `ToolExecutor` restricts `editable_fields` to fields present in `visible_arguments`.
4. `ToolExecutor` calls `PermissionService.ask`.
5. `PermissionService.ask` creates:
   - `request_id`
   - `PendingPermissionRequest`
   - `asyncio.Future`
6. `PermissionStore.add_pending` stores the pending handle.
7. `ToolExecutor` waits for the future.
8. While the tool task is waiting, `StandardAgentRunner` polls `PermissionService.find_pending_by_tool_call_id`.
9. When a pending request is found, `StandardAgentRunner` emits `permission_asked`.
10. `_generate_events` maps `permission_asked` into an SSE event.
11. The frontend replies through `POST /v1/agent/permissions/{request_id}/reply`.
12. `PermissionService.reply` validates and resolves the future.
13. `ToolExecutor` resumes execution or returns rejection.
14. `PermissionService.ask` removes the pending request in `finally`.

## Reply Actions

Known reply actions:

- `once`
- `always`
- `reject`
- `once_with_changes`

`PermissionService.ask` sets allowed actions as:

- if editable fields exist:
  - `once`
  - `reject`
  - `once_with_changes`
- if editable fields do not exist:
  - `once`
  - `always`
  - `reject`

Validation rules:

- `once_with_changes` requires at least one edited field.
- `edited_fields` are only allowed for `once_with_changes`.
- edited field names must be included in the request's `editable_fields`.
- reply `user_id` must match the pending request's `user_id`.

## Session Grants

When the reply action is `always`, `ToolExecutor` calls `PermissionService.add_session_grant`.

Session grants are stored in memory by `PermissionStore`.

Grant identity consists of:

- `session_id`
- `tool_name`
- `tool_action`

Before evaluating governance, `ToolExecutor` checks `PermissionService.is_session_granted`.

If a matching session grant exists, the tool executes without creating a new permission request.

## API Surface

### Reply

- `POST /v1/agent/permissions/{request_id}/reply`

Request body:

- `action`
- `edited_fields`
- `comment`

Success response:

- `request_id`
- `accepted: true`

### Pending List

- `GET /v1/agent/permissions/pending`

Query:

- optional `session_id`

Response:

- `items`

## Cancellation

`send_message` cancels pending permission requests for the session before appending a new user message.

`_generate_events` cancels pending permission requests when the stream closes.

Cancelled tool calls are persisted as tool messages with an error reason when the corresponding assistant tool call exists in session history.

## Dependencies

- `app.core.governance.loader`
- `app.core.governance.policy`
- `app.core.governance.types`
- `app.core.permission.service`
- `app.core.permission.store`
- `app.core.permission.types`
- `app.core.tool.executor`
- `app.core.agent.standard_runner`
- `app.api.v1.agent`
- `app/governance/rules/default.yaml`

## Public Interfaces

### Governance

- `GovernanceConfigLoader.load`
- `GovernancePolicyEngine.evaluate`
- `EvaluationContext`
- `GovernanceDecision`
- `GovernanceConfig`
- `GovernanceRule`
- `RuleMatch`
- `RuleEffect`

### Permission

- `PermissionService.ask`
- `PermissionService.reply`
- `PermissionService.list_pending`
- `PermissionService.find_pending_by_tool_call_id`
- `PermissionService.cancel_session_requests`
- `PermissionService.add_session_grant`
- `PermissionService.is_session_granted`
- `PermissionStore.add_pending`
- `PermissionStore.get_pending`
- `PermissionStore.pop_pending`
- `PermissionStore.list_pending`
- `PermissionStore.list_pending_handles`
- `PermissionStore.find_pending_by_tool_call_id`
- `PermissionStore.add_grant`
- `PermissionStore.is_granted`

## Error Conditions

- Missing permission request id raises `KeyError`.
- Reply from a different user raises `PermissionError`.
- Unsupported reply action raises `ValueError`.
- Edited disallowed fields raise `ValueError`.
- Replying to an already resolved request raises `ValueError`.
- `once_with_changes` without edited fields fails validation.
- Edited fields on actions other than `once_with_changes` fail validation.
- Permission wait timeout raises `asyncio.TimeoutError` to the caller waiting in `ToolExecutor`.
- Cancelled pending request sets `PermissionRequestCancelledError` on the pending future.
- Governance configuration fails validation if editable fields are not visible fields.
- Governance configuration fails validation if a rule references an unknown field preset.

## Acceptance

This module contract is satisfied when:

- governance YAML can be loaded into `GovernanceConfig`
- every tool call can be evaluated into one governance decision
- `ask` decisions create pending permission requests
- pending permission requests can be surfaced as `permission_asked` events
- accepted permission replies resume blocked tool execution
- rejected replies return failed tool results
- `once_with_changes` only modifies editable fields
- `always` grants apply only to matching session/tool/action combinations

## Open Questions

- Should permission grants remain in memory, or should they be persisted for long-running sessions?
- Should `support_user_revision` influence allowed reply actions? It is set by governance rules, but `PermissionService.ask` currently uses the existence of editable fields to decide whether `once_with_changes` is allowed.
- Should `GovernanceDefaults.visibility` and `GovernanceDefaults.on_deny` be enforced by runtime code? They are defined in the model and YAML, but the current executor path does not branch on them directly.
- Should cancelled permission requests be exposed to the frontend as explicit cancellation events instead of only persisted tool error messages?
