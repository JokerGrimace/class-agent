from typing import Any, Optional


PLAN_STATUSES = {"pending", "in_progress", "completed"}
STRICT_COMPLETION_MODES = {"explicit"}


def is_strict_plan(plan: Any) -> bool:
    return isinstance(plan, dict) and plan.get("strict") is True and isinstance(plan.get("steps"), list)


def get_plan_steps(plan: Any) -> list[dict[str, Any]]:
    if isinstance(plan, list):
        return [item for item in plan if isinstance(item, dict)]
    if is_strict_plan(plan):
        return [item for item in plan["steps"] if isinstance(item, dict)]
    return []


def is_plan_complete(plan: Any) -> bool:
    steps = get_plan_steps(plan)
    return bool(steps) and all(step.get("status") == "completed" for step in steps)


def get_in_progress_step_index(plan: Any) -> Optional[int]:
    matches = [idx for idx, step in enumerate(get_plan_steps(plan)) if step.get("status") == "in_progress"]
    if len(matches) != 1:
        return None
    return matches[0]


def validate_plan_payload(plan: Any) -> Optional[str]:
    steps = get_plan_steps(plan)
    if not steps:
        return "plan must be a non-empty array or strict plan object"

    in_progress = [s for s in steps if s.get("status") == "in_progress"]
    completed = [s for s in steps if s.get("status") == "completed"]

    if len(in_progress) > 1:
        return "At most one step may be in_progress"

    if is_strict_plan(plan):
        if len(in_progress) == 0 and len(completed) != len(steps):
            return "Strict plans must have exactly one in_progress step until all steps are completed"

        seen_ids: set[str] = set()
        for step in steps:
            step_id = step.get("id")
            title = step.get("title")
            status = step.get("status")
            allowed_tools = step.get("allowed_tools")
            completion_mode = step.get("completion_mode")

            if not isinstance(step_id, str) or not step_id.strip():
                return "Every strict plan step must have a non-empty 'id' string"
            if step_id in seen_ids:
                return f"Duplicate strict plan step id: {step_id}"
            seen_ids.add(step_id)

            if not isinstance(title, str) or not title.strip():
                return "Every strict plan step must have a non-empty 'title' string"
            if status not in PLAN_STATUSES:
                return f"Invalid status: {status}. Must be one of {', '.join(sorted(PLAN_STATUSES))}"
            if not isinstance(allowed_tools, list) or not allowed_tools or not all(
                isinstance(tool_name, str) and tool_name.strip() for tool_name in allowed_tools
            ):
                return "Every strict plan step must declare a non-empty 'allowed_tools' list"
            if completion_mode not in STRICT_COMPLETION_MODES:
                return "Strict plan completion_mode must be 'explicit'"
        return None

    for step in steps:
        if step.get("status") not in PLAN_STATUSES:
            return f"Invalid status: {step.get('status')}. Must be one of {', '.join(sorted(PLAN_STATUSES))}"
        if not isinstance(step.get("step"), str) or not step["step"].strip():
            return "Every step must have a non-empty 'step' string"

    return None

def validate_strict_plan_transition(current_plan: Any, proposed_plan: Any) -> Optional[str]:
    if not is_strict_plan(current_plan):
        return None
    if not is_strict_plan(proposed_plan):
        return "Strict plan updates must keep the strict plan object structure"

    current_steps = get_plan_steps(current_plan)
    proposed_steps = get_plan_steps(proposed_plan)

    if len(current_steps) != len(proposed_steps):
        return "Strict plan updates cannot add or remove steps"

    base_error = validate_plan_payload(proposed_plan)
    if base_error:
        return base_error

    for current_step, proposed_step in zip(current_steps, proposed_steps):
        for field in ("id", "title", "allowed_tools", "completion_mode"):
            if current_step.get(field) != proposed_step.get(field):
                return f"Strict plan updates cannot change step {field}"

    current_idx = get_in_progress_step_index(current_plan)
    if current_idx is None:
        return "Strict plan has no active step to advance"

    is_last_step = current_idx == len(current_steps) - 1

    for idx, (current_step, proposed_step) in enumerate(zip(current_steps, proposed_steps)):
        current_status = current_step.get("status")
        proposed_status = proposed_step.get("status")

        if idx < current_idx and proposed_status != current_status:
            return "Strict plan updates cannot modify earlier completed steps"
        if idx == current_idx and proposed_status != "completed":
            return "Strict plan updates must mark the current in_progress step as completed"
        if idx == current_idx + 1 and not is_last_step and proposed_status != "in_progress":
            return "Strict plan updates must move the next pending step to in_progress"
        if idx > current_idx + 1 and proposed_status != current_status:
            return "Strict plan updates cannot skip ahead to later steps"
        if is_last_step and idx > current_idx and proposed_status != current_status:
            return "Strict final step update cannot modify later steps"

    if is_last_step and not is_plan_complete(proposed_plan):
        return "Strict final step update must complete the plan"

    return None
