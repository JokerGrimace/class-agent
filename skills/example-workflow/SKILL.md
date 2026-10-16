---
name: example-workflow
description: Demo skill showing how to use update_plan for multi-step tasks. Use when demonstrating or testing multi-step workflows.
---

## When to Use
- Testing or demonstrating multi-step task tracking
- Any non-trivial task that benefits from step-by-step progress (read, write, exec sequences)

## When NOT to Use
- Simple one-step tasks (like "what is 2+2" or "read this file")

## Workflow

When this skill is triggered, follow these steps:

1. **Initialize the plan** with update_plan:
   - Set the first step to "in_progress"
   - Set remaining steps to "pending"
   - The plan is automatically visible in your context; no need to re-read it

2. **For each step**:
   - Execute the necessary tool calls
   - Mark the step as "completed" via update_plan
   - Mark the next step as "in_progress" via update_plan

3. **When all steps are complete**:
   - Confirm completion to the user

## Constraints
- Never skip a step without explaining why
- If a step fails, report the blocker and do not advance
- Update the plan after each step, not all at once
- Keep plan steps concise, one line each
- Build platform capabilities as reusable framework mechanisms first, not as one-off logic for a single feature
- Use individual business functions only as validation examples or test fixtures for the framework
- Do not let a single feature dictate the framework shape; generalize the rule or extension point before specializing
- For approval, confirmation, editability, and risk-control features, design a tool-agnostic rule layer first, then connect concrete tools to it
