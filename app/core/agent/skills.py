from typing import List, Dict

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
