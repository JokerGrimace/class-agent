import re
from pathlib import Path

from app.core.tool.registry import tool
from app.core.tool.types import ToolResult


_SKILL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SKILLS_ROOT = _PROJECT_ROOT / "skills"


@tool(
    name="read_skill",
    description="Read a project skill by skill_name or skill_path from skills/{skill_name}/SKILL.md.",
    parameters={
        "type": "object",
        "properties": {
            "skill_name": {
                "type": "string",
                "description": "Skill directory name, for example studentdetaildashboard. A relative skills/{skill_name}/SKILL.md path is also accepted for compatibility.",
            },
            "skill_path": {
                "type": "string",
                "description": "Optional skill file path, for example skills/studentdetaildashboard/SKILL.md.",
            },
        },
    },
    visibility={"tool_types": ["level1"]},
)
async def read_skill(skill_name: str = "", skill_path: str = "") -> ToolResult:
    resolved = _resolve_skill_reference(skill_name=skill_name, skill_path=skill_path)
    if not resolved.success:
        return resolved
    normalized_skill_name = resolved.meta["skill_name"]
    skill_path_obj = Path(resolved.meta["skill_path"])

    if not skill_path_obj.exists():
        return ToolResult(
            success=False,
            error=f"Skill not found: {normalized_skill_name}",
        )
    if not skill_path_obj.is_file():
        return ToolResult(
            success=False,
            error=f"Skill path is not a file: {normalized_skill_name}",
        )

    try:
        content = await _read_text(skill_path_obj)
    except OSError as exc:
        return ToolResult(success=False, error=str(exc))

    return ToolResult(
        success=True,
        content=content,
        meta={
            "skill_name": normalized_skill_name,
            "skill_path": str(skill_path_obj),
            "tool_display_name": "读取技能说明",
        },
    )


def _resolve_skill_reference(skill_name: str = "", skill_path: str = "") -> ToolResult:
    raw_skill_name = (skill_name or "").strip()
    raw_skill_path = (skill_path or "").strip()
    if raw_skill_name and raw_skill_path:
        return ToolResult(success=False, error="Provide either skill_name or skill_path, not both")

    reference = raw_skill_path or raw_skill_name
    if not reference:
        return ToolResult(success=False, error="skill_name or skill_path must be provided")

    if _SKILL_NAME_PATTERN.fullmatch(reference):
        resolved_path = (_SKILLS_ROOT / reference / "SKILL.md").resolve()
        return _validate_resolved_skill_path(reference, resolved_path)

    normalized_reference = reference.replace("\\", "/")
    if "://" in normalized_reference:
        return ToolResult(success=False, error="skill path must be a local project path")

    input_path = Path(reference)
    if input_path.is_absolute():
        resolved_path = input_path.resolve()
    else:
        if not normalized_reference.startswith("skills/"):
            return ToolResult(
                success=False,
                error="skill path must start with skills/ or use a bare skill_name",
            )
        resolved_path = (_PROJECT_ROOT / input_path).resolve()
    return _validate_resolved_skill_path(reference, resolved_path)


def _validate_resolved_skill_path(reference: str, skill_path: Path) -> ToolResult:
    skills_root = _SKILLS_ROOT.resolve()
    if skills_root not in skill_path.parents:
        return ToolResult(success=False, error="skill path is outside the skills directory")
    if skill_path.name != "SKILL.md" or skill_path.parent.parent != skills_root:
        return ToolResult(
            success=False,
            error="skill path must match skills/{skill_name}/SKILL.md",
        )
    skill_name = skill_path.parent.name
    if not _SKILL_NAME_PATTERN.fullmatch(skill_name):
        return ToolResult(
            success=False,
            error="skill_name may only contain letters, numbers, underscores, and hyphens",
        )
    return ToolResult(
        success=True,
        meta={
            "skill_name": skill_name,
            "skill_path": str(skill_path),
            "reference": reference,
        },
    )


async def _read_text(path: Path) -> str:
    import asyncio

    return await asyncio.to_thread(path.read_text, "utf-8", errors="replace")
