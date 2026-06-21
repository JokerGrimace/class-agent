from pathlib import Path
from typing import List, Dict


def discover_local_skills(skills_dir: Path) -> List[Dict[str, str]]:
    if not skills_dir.exists() or not skills_dir.is_dir():
        return []

    skills: list[dict[str, str]] = []
    for skill_file in sorted(skills_dir.glob("*/SKILL.md")):
        metadata = _parse_skill_frontmatter(skill_file)
        if not metadata.get("name"):
            continue
        skills.append(
            {
                "name": metadata["name"],
                "description": metadata.get("description", ""),
                "location": str(skill_file),
            }
        )
    return skills


def _parse_skill_frontmatter(skill_file: Path) -> Dict[str, str]:
    try:
        lines = skill_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    if not lines or lines[0].strip() != "---":
        return {}

    metadata: dict[str, str] = {}
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            break
        key, separator, value = stripped.partition(":")
        if not separator:
            continue
        normalized_key = key.strip()
        if normalized_key in {"name", "description"}:
            metadata[normalized_key] = value.strip().strip("'\"")
    return metadata

def format_skills_for_prompt(skills: List[Dict[str, str]]) -> str:
    if not skills:
        return ""

    lines = ["<available_skills>"]
    for skill in skills:
        lines.append("  <skill>")
        lines.append(f"    <name>{skill['name']}</name>")
        lines.append(f"    <description>{skill['description']}</description>")
        lines.append(f"    <location>{skill['location']}</location>")
        lines.append("  </skill>")
    lines.append("</available_skills>")

    return "\n".join(lines)
