# Codex Harness Templates

This folder contains Markdown templates for a Codex development harness.

## Use This Set When

- You want Codex to work from explicit rules instead of ad hoc assumptions
- Multiple agents need to work in parallel
- One agent must hand off to another without rereading the whole codebase
- Module interfaces need to be understood before implementation

## Template Set

1. `01-harness-charter.md`
2. `02-module-contract.md`
3. `03-task-packet.md`
4. `04-handoff-note.md`

## Writing Rules

- Write one file for one responsibility.
- Use exact identifiers only. Do not guess names or shapes.
- Put unknowns in `Open Questions`.
- Keep module boundaries explicit.
- Keep handoff material readable without source code context.
- Treat interface contracts as the source of truth for parallel work.

## Suggested Order

1. Write the harness charter.
2. Write one module contract per module.
3. Write one task packet per assigned task.
4. Write a handoff note before stopping or switching agents.

## Folder Rule

If a project has multiple independent modules, create one `02-module-contract.md` per module and one `03-task-packet.md` per task.
