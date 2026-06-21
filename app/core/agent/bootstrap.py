"""
Bootstrap prompt builder - aligned with OpenClaw's bootstrap-user-prompt-prefix approach.

Key difference from passive context:
- Bootstrap is NOT injected as system prompt context
- It IS injected as a user-level instruction that the agent MUST follow
- Full mode: agent is expected to complete the bootstrap workflow
- Limited mode: agent acknowledges it can't complete bootstrap
"""
from pathlib import Path
from typing import Optional, List, Dict


BOOTSTRAP_FILES_ORDER = [
    ("AGENTS.md", 10),
    ("SOUL.md", 20),
    ("IDENTITY.md", 30),
    ("USER.md", 40),
    ("TOOLS.md", 50),
    ("MEMORY.md", 60),
    ("HEARTBEAT.md", 70),
]

TEMPLATES_DIR = Path(__file__).parent.parent.parent.parent / "templates"


def escape_xml(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def build_bootstrap_user_prefix(bootstrap_mode: str = "full") -> str:
    """Build the bootstrap instruction as a USER-level prefix.
    This is NOT system prompt - it's the first user message the agent sees."""

    if bootstrap_mode == "full":
        return (
            "\n\n[Bootstrap pending]\n"
            "Please read BOOTSTRAP.md from the workspace and follow it before replying normally.\n"
            "If this run can complete the BOOTSTRAP.md workflow, do so.\n"
            "If it cannot, explain the blocker briefly, continue with any bootstrap steps "
            "that are still possible here, and offer the simplest next step.\n"
            "Do not pretend bootstrap is complete when it is not.\n"
            "Do not use a generic first greeting or reply normally until after you have "
            "handled BOOTSTRAP.md.\n"
            "Your first user-visible reply for a bootstrap-pending workspace must follow "
            "BOOTSTRAP.md: introduce yourself, figure out who you are together with the user, "
            "then update IDENTITY.md, USER.md, and SOUL.md. When done, delete BOOTSTRAP.md."
        )
    else:
        return (
            "\n\n[Bootstrap pending]\n"
            "Bootstrap is still pending for this workspace, but this run cannot safely "
            "complete the full bootstrap workflow.\n"
            "Do not claim bootstrap is complete, and do not use a generic first greeting.\n"
            "Briefly explain the limitation, continue only with any bootstrap steps that "
            "are still safely possible here, and offer the simplest next step.\n"
            "Typical next steps include switching to a primary interactive run where you "
            "can complete the bootstrap conversation."
        )


def format_skills_for_prompt(skills: List[Dict[str, str]]) -> str:
    if not skills:
        return ""

    lines = ["<available_skills>"]
    for skill in skills:
        lines.append("  <skill>")
        lines.append(f"    <name>{escape_xml(skill['name'])}</name>")
        lines.append(f"    <description>{escape_xml(skill['description'])}</description>")
        lines.append(f"    <location>{escape_xml(skill['location'])}</location>")
        lines.append("  </skill>")
    lines.append("</available_skills>")

    return "\n".join(lines)
