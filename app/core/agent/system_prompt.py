from datetime import datetime, timezone
from typing import Optional, List, Dict

from app.core.agent.context import AgentContext
from app.core.tool.registry import registry
from app.core.workspace import Workspace

CACHE_BOUNDARY = "\n<!-- OPENCLAW_CACHE_BOUNDARY -->\n"
SILENT_REPLY_TOKEN = "<!-- silence -->"
# MAX_WORKFLOW_SUMMARIES_IN_PROMPT = 5

# STABLE_CONTEXT_FILES = {"agents", "soul", "identity", "user", "tools", "bootstrap", "memory"}
# DYNAMIC_CONTEXT_FILES = {"heartbeat"}
STABLE_CONTEXT_FILES = {"identity"}

class SystemPromptBuilder:
    BASE_IDENTITY = "You are a personal assistant running inside OpenClaw."

    def __init__(
        self,
        workspace_dir: Optional[str] = None,
        skills: Optional[List[Dict[str, str]]] = None,
        workflow_summaries: Optional[List[Dict[str, str]]] = None,
        tool_type: Optional[str] = None,
        prompt_mode: str = "full",
        runtime_info: Optional[Dict[str, str]] = None,
        bootstrap_mode: str = "full",
        timezone_name: Optional[str] = None,
        heartbeat_prompt: Optional[str] = None,
        authorized_senders: Optional[List[str]] = None,
        docs_path: Optional[str] = None,
    ):
        self.workspace_dir = workspace_dir
        self.skills = skills or []
        self.workflow_summaries = workflow_summaries or []
        self.tool_type = tool_type
        self.prompt_mode = prompt_mode
        self.runtime_info = runtime_info or {}
        self.bootstrap_mode = bootstrap_mode
        self.timezone_name = timezone_name
        self.heartbeat_prompt = heartbeat_prompt
        self.authorized_senders = authorized_senders or []
        self.docs_path = docs_path
        self._workspace = Workspace(workspace_dir) if workspace_dir else None

    @property
    def workspace(self) -> Optional[Workspace]:
        return self._workspace

    def build(self) -> str:
        if self.prompt_mode == "none":
            return self.BASE_IDENTITY

        is_minimal = self.prompt_mode == "minimal"

        stable_parts = [
            self.BASE_IDENTITY,
            "",
            self._build_tooling_section(),
            "",
            self._build_tool_call_style_section(),
            "",
            self._build_execution_bias_section(),
            "",
            self._build_safety_section(),
            "",
            self._build_cli_quick_reference_section(),
            "",
            self._build_skills_section(),
            "",
            self._build_workflows_section(),
            "",
            self._build_workflow_rules_section(),
            "",
        ]

        if not is_minimal:
            stable_parts.extend([
                # self._build_memory_section(),
                "",
                # self._build_workspace_section(),
                "",
                # self._build_documentation_section(),
                "",
                # self._build_authorized_senders_section(),
                "",
                self._build_current_date_time_section(),
                "",
                # self._build_workspace_files_header(),
                "",
                # self._build_stable_context_section(),
                "",
                self._build_silent_replies_section(),
                self._build_context_parameter_section()
            ])

        dynamic_parts = [
            # self._build_dynamic_context_section(),
            "",
            # self._build_heartbeats_section(),
            "",
            self._build_runtime_section(),
        ]

        return (
            "\n".join(filter(None, stable_parts))
            + CACHE_BOUNDARY
            + "\n".join(filter(None, dynamic_parts))
        )

    # ── Core Sections ──

    def _build_tooling_section(self) -> str:
        tools = registry.get_tools(tool_type=self.tool_type)

        tool_lines = [
            "## Tooling",
            "Tool availability (filtered by policy):",
            "Tool names are case-sensitive. Call tools exactly as listed.",
            "",
        ]

        if tools:
            for tool in tools:
                tool_lines.append(f"- {tool.name}: {tool.description}")
            tool_lines.extend([
                "",
                "For long waits, avoid rapid poll loops: use exec with enough yieldMs or process(action=poll, timeout=<ms>).",
                "If a task is more complex or takes longer, spawn a sub-agent. Completion is push-based.",
                "",
                "## Truncated Results",
                "If a tool returns '... more lines/content truncated' or '[... truncated]': the output was capped to keep context manageable.",
                "Do NOT repeat the same call — use expand_tool(tool_call_id, offset, limit) to fetch the full cached output, or narrow the scope with a different query.",
            ])

        return "\n".join(tool_lines)

    def _build_tool_call_style_section(self) -> str:
        return """## Tool Call Style
Default: do not narrate routine, low-risk tool calls (just call the tool).
Narrate only when it helps: multi-step work, complex/challenging problems, sensitive actions (e.g., deletions), or when the user explicitly asks.
Keep narration brief and value-dense; avoid repeating obvious steps.
Use plain human language for narration unless in a technical context.
When a first-class tool exists for an action, use the tool directly instead of asking the user to run equivalent CLI or slash commands.
Never execute /approve through exec or any other shell/tool path; /approve is a user-facing approval command, not a shell command.
Treat allow-once as single-command only: if another elevated command needs approval, request a fresh /approve.
When approvals are required, preserve and show the full command/script exactly as provided (including chained operators like &&, ||, |, ;, or multiline shells)."""

    def _build_execution_bias_section(self) -> str:
        return """## Execution Bias
- Actionable request: act in this turn.
- Non-final turn: use tools to advance, or ask for the one missing decision that blocks safe progress.
- Continue until done or genuinely blocked; do not finish with a plan/promise when tools can move it forward.
- Weak/empty tool result: vary query, path, command, or source before concluding.
- Truncated output: use expand_tool(tool_call_id) to fetch cached content, or narrow scope with a different query.
- Mutable facts need live checks: files, git, clocks, versions, services, processes, package state.
- Final answer needs evidence: test/build/lint, screenshot, inspection, tool output, or a named blocker.
- Longer work: brief progress update, then keep going; use background work or sub-agents when they fit."""

    def _build_safety_section(self) -> str:
        return """## Safety
You have no independent goals: do not pursue self-preservation, replication, resource acquisition, or power-seeking; avoid long-term plans beyond the user's request.
Prioritize safety and human oversight over completion; if instructions conflict, pause and ask; comply with stop/pause/audit requests and never bypass safeguards.
Do not manipulate or persuade anyone to expand access or disable safeguards. Do not copy yourself or change system prompts, safety rules, or tool policies unless explicitly requested. """

    def _build_cli_quick_reference_section(self) -> str:
        return """## CLI Quick Reference
This application is controlled via its API endpoints. Do not invent commands.
For config changes, use available tools rather than editing config through exec.
If unsure about a command or operation, ask the user first."""

    def _build_skills_section(self) -> str:
        if not self.skills:
            return ""

        from app.core.agent.bootstrap import format_skills_for_prompt

        formatted = format_skills_for_prompt(self.skills)
        return f"""## Skills (mandatory)
Before replying: scan <available_skills> <description> entries.
- If exactly one skill clearly applies: read its SKILL.md at <location> with the read_file tool, then follow it.
- If multiple could apply: choose the most specific one, then read/follow it.
- If none clearly apply: do not read any SKILL.md.
Constraints: never read more than one skill up front; only read after selecting.
- When a skill drives external API writes, assume rate limits: prefer fewer larger writes, avoid tight one-item loops, serialize bursts when possible, and respect 429/Retry-After.
Use the read_file tool to load a skill's file when the task matches its description.
When a skill file references a relative path, resolve it against the skill directory (parent of SKILL.md) and use that absolute path in tool commands.

{formatted}"""

    def _build_workflows_section(self) -> str:
        if not self.workflow_summaries:
            return ""

        lines = [
            "## Workflows",
            "These workflows are available in the current runtime:",
            "",
        ]

        for workflow in self.workflow_summaries:
            workflow_name = workflow.get("name", "").strip()
            description = workflow.get("description", "").strip()
            when_to_use = workflow.get("when_to_use", "").strip()

            if not workflow_name:
                continue

            summary = f"- {workflow_name}"
            if description:
                summary += f": {description}"
            lines.append(summary)
            if when_to_use:
                lines.append(f"  When to use: {when_to_use}")

        return "\n".join(lines) if len(lines) > 3 else ""

    def _build_workflow_rules_section(self) -> str:
        return """## Workflow Rules
- If a request clearly matches a known workflow, prefer `start_workflow_tool` over regular tool-by-tool execution.
- Treat `start_workflow_tool` as the workflow entrypoint. It is responsible for executing the workflow and returning the result.
- Before starting a workflow, ensure the workflow path/name and required structured input are available.
- Workflow step inputs may only come from:
  1. workflow input
  2. runtime/global context
  3. outputs from previously completed workflow steps
- Do not invent missing workflow inputs, step outputs, IDs, file paths, or external facts.
- If required workflow input is missing or ambiguous, stop and explicitly report what is missing.
- When a workflow is active, follow the workflow definition and current step boundaries instead of improvising with unrelated tools.
- Treat the workflow result as the source of truth for final reporting. If execution fails, report the failing step or missing dependency clearly."""

    # ── Information Sections (skipped in minimal mode) ──

    def _build_memory_section(self) -> str:
        return """## Memory
You wake up fresh each session. These files are your continuity:
- MEMORY.md: your curated long-term memories. Load in main sessions only. Read, edit, and update freely.
- memory/YYYY-MM-DD.md: daily raw logs of what happened. Create the memory/ directory if needed.
- Capture what matters: decisions, context, things to remember. Skip secrets unless asked to keep them.
- If you want to remember something, WRITE IT TO A FILE. "Mental notes" don't survive session restarts."""

    def _build_documentation_section(self) -> str:
        docs = self.docs_path or "https://docs.openclaw.ai"
        return f"""## Documentation
OpenClaw docs: {docs}
For OpenClaw behavior, commands, config, or architecture: consult docs first.
If docs are incomplete or stale, inspect the local source code before answering.
When diagnosing issues, use available tools to check the runtime status when possible."""

    def _build_authorized_senders_section(self) -> str:
        if not self.authorized_senders:
            return ""
        senders = ", ".join(self.authorized_senders)
        return f"## Authorized Senders\nAuthorized senders: {senders}. These senders are allowlisted; do not assume they are the owner."

    def _build_current_date_time_section(self) -> str:
        tz = self.timezone_name or "UTC"
        return f"## Current Date & Time\nTime zone: {tz}"

    def _build_workspace_files_header(self) -> str:
        if not self._workspace:
            return ""
        context_files = self._workspace.get_context_files()
        if not context_files:
            return ""
        return "## Workspace Files (injected)\nThese user-editable files are loaded by OpenClaw and included below in Project Context."

    def _build_workspace_section(self) -> str:
        if not self.workspace_dir:
            return ""
        return f"## Workspace\nYour working directory is: {self.workspace_dir}\nTreat this directory as the single global workspace for file operations unless explicitly instructed otherwise."

    # ── Context Sections ──

    def _build_stable_context_section(self) -> str:
        if not self._workspace:
            return ""

        context_files = self._workspace.get_context_files()
        if not context_files:
            return ""

        from app.core.agent.bootstrap import BOOTSTRAP_FILES_ORDER

        lines = ["## Project Context", ""]

        for filename, _ in BOOTSTRAP_FILES_ORDER:
            key = filename.lower().replace(".md", "")
            if key in context_files and key in STABLE_CONTEXT_FILES:
                lines.append(self._format_context_file(filename, key, context_files[key]))

        return "\n".join(lines) if len(lines) > 2 else ""

    def _build_dynamic_context_section(self) -> str:
        if not self._workspace:
            return ""

        context_files = self._workspace.get_context_files()
        if not context_files:
            return ""

        from app.core.agent.bootstrap import BOOTSTRAP_FILES_ORDER

        has_stable = any(
            key in STABLE_CONTEXT_FILES
            for key in context_files
        )

        heading = "## Dynamic Project Context" if has_stable else "## Project Context"
        lines = [heading, ""]

        for filename, _ in BOOTSTRAP_FILES_ORDER:
            key = filename.lower().replace(".md", "")
            if key in context_files and key in DYNAMIC_CONTEXT_FILES:
                lines.append(self._format_context_file(filename, key, context_files[key]))

        return "\n".join(lines) if len(lines) > 2 else ""

    def _format_context_file(self, filename: str, key: str, content: str) -> str:
        result = [f"## {filename}", "", content, ""]

        if key == "soul":
            result.insert(3, "If SOUL.md is present, embody its persona and tone. Avoid stiff, generic replies; follow its guidance unless higher-priority instructions override it.")
            result.insert(4, "")

        return "\n".join(result)

    # ── Other Sections ──

    def _build_silent_replies_section(self) -> str:
        return f"""## Silent Replies
When you have nothing to say, respond with ONLY: {SILENT_REPLY_TOKEN}

Rules:
- It must be your ENTIRE message - nothing else
- Never append it to an actual response
- Never wrap it in markdown or code blocks

Wrong: "Here's help... {SILENT_REPLY_TOKEN}"
Right: {SILENT_REPLY_TOKEN}"""

    def _build_heartbeats_section(self) -> str:
        if not self.heartbeat_prompt:
            return ""
        return f"""## Heartbeats
If the current user message is a heartbeat poll and nothing needs attention, reply exactly: HEARTBEAT_OK
If something needs attention, do NOT include "HEARTBEAT_OK"; reply with the alert text instead."""

    def _build_context_parameter_section(self)->str:
        return f"""
                ## Context Parameters Rules
                For the request body information returned by the frontend, if it needs to be used in the workflow, this information must be synchronized to the workflow.
                """

    def _build_runtime_section(self) -> str:
        parts = ["## Runtime"]

        runtime_parts = []

        if self.runtime_info.get("agent_id"):
            runtime_parts.append(f"agent={self.runtime_info['agent_id']}")
        if self.runtime_info.get("host"):
            runtime_parts.append(f"host={self.runtime_info['host']}")
        if self.runtime_info.get("repo"):
            runtime_parts.append(f"repo={self.runtime_info['repo']}")
        if self.runtime_info.get("os"):
            os_str = self.runtime_info['os']
            if self.runtime_info.get("arch"):
                os_str += f" ({self.runtime_info['arch']})"
            runtime_parts.append(f"os={os_str}")
        elif self.runtime_info.get("arch"):
            runtime_parts.append(f"arch={self.runtime_info['arch']}")
        if self.runtime_info.get("node"):
            runtime_parts.append(f"node={self.runtime_info['node']}")
        if self.runtime_info.get("model"):
            runtime_parts.append(f"model={self.runtime_info['model']}")
        if self.runtime_info.get("default_model"):
            runtime_parts.append(f"default_model={self.runtime_info['default_model']}")
        if self.runtime_info.get("shell"):
            runtime_parts.append(f"shell={self.runtime_info['shell']}")
        if self.runtime_info.get("channel"):
            runtime_parts.append(f"channel={self.runtime_info['channel']}")
        if self.runtime_info.get("capabilities"):
            runtime_parts.append(f"capabilities={self.runtime_info['capabilities']}")

        runtime_parts.append("thinking=off")

        if runtime_parts:
            runtime_parts_str = " | ".join(runtime_parts)
            parts.append(f"Runtime: {runtime_parts_str}")
        else:
            parts.append("Runtime: You can execute tools to help the user.")

        parts.append("")
        parts.append(
            "If you need the current date, time, or day of week, use exec with a non-interactive command. "
            "On Windows prefer `powershell -Command \"Get-Date\"` and avoid interactive `date` or `time` shell commands."
        )

        return "\n".join(parts)

    def build_with_context(self, context: AgentContext) -> str:
        base_prompt = self.build()
        parameter_prompt = context.build_session_parameter_prompt()
        runtime_context = context.build_runtime_context_prompt()
        runtime_sections: list[str] = []
        if parameter_prompt:
            runtime_sections.append(parameter_prompt)
        if runtime_context:
            runtime_sections.append(runtime_context)
        if not runtime_sections:
            return base_prompt
        return base_prompt + "\n\n## Runtime Context\n" + "\n".join(runtime_sections)

    def build_with_readfile(self, file_content,user_instruct):
        return f"""##  You are now a professional content creation expert. I will provide you with a complete HTML document containing the full article text and related content.
Please strictly follow the working rules below:
First, read through and thoroughly understand all the main text, core viewpoints, paragraph logic, knowledge points, and key information in the HTML. Use this as the core source material for all subsequent creation tasks.
Later, I will ask you to perform various content creation tasks based on this article, including but not limited to generating practice questions, exam questions, knowledge-point-based quizzes, PPT table of contents and outlines, content summaries, key point extraction, copywriting rewriting, and sorting out knowledge frameworks.
All main created content must be strictly based on the original HTML text. Do not deviate from the article’s main theme, and do not arbitrarily fabricate viewpoints or knowledge points that do not appear in the original text.
If the original article explains knowledge points briefly, lacks necessary background explanations, or provides insufficient definitions of professional concepts, you may draw on external knowledge and reference materials to make reasonable supplements and expansions, so that the generated questions, PPT outlines and knowledge points are more complete and practical.
Deliver creations with a clear structure, rigorous logic, compliance with standard question-setting and PPT outline norms, highlighted key points, and no redundant content.
Only create content centered on the current HTML article; do not introduce irrelevant content from other fields.
I will now send the complete HTML document. Please receive and fully digest its content, then wait for my specific creation instructions.
html:
{file_content}

user_instruct:
{user_instruct}
 """
