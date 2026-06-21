from __future__ import annotations

import json
from dataclasses import dataclass
from json import JSONDecodeError
from pathlib import Path

from pydantic import ValidationError

from app.core.workflow.models import WorkflowDefinition


@dataclass
class WorkflowSpec:
    name: str
    description: str
    when_to_use: str
    definition: WorkflowDefinition
    source_path: str


class WorkflowCatalog:
    def __init__(self, specs: dict[str, WorkflowSpec]):
        self._specs = specs

    @classmethod
    def from_directory(cls, directory: str, ignore_errors: bool = False) -> "WorkflowCatalog":
        specs: dict[str, WorkflowSpec] = {}
        for path in sorted(Path(directory).glob("*.md")):
            try:
                spec = parse_workflow_markdown(path)
            except (ValueError, ValidationError):
                if ignore_errors:
                    continue
                raise
            if spec.name in specs:
                existing_path = specs[spec.name].source_path
                raise ValueError(
                    f"Duplicate workflow name '{spec.name}' in {existing_path} and {path}"
                )
            specs[spec.name] = spec
        return cls(specs)

    def get(self, name: str) -> WorkflowSpec | None:
        return self._specs.get(name)

    def summaries(self, limit: int | None = None) -> list[dict[str, str]]:
        summaries = [
            {
                "name": spec.name,
                "file_name": Path(spec.source_path).name,
                "description": spec.description,
                "when_to_use": spec.when_to_use,
            }
            for spec in self._specs.values()
        ]
        if limit is not None:
            return summaries[:limit]
        return summaries


def parse_workflow_markdown(path: str | Path) -> WorkflowSpec:
    workflow_path = Path(path)
    content = workflow_path.read_text(encoding="utf-8")
    sections = _split_sections(content)
    _require_sections(
        sections,
        required=("Workflow", "Description", "When To Use", "Definition"),
    )

    try:
        definition_payload = json.loads(_extract_json_block(sections["Definition"]))
    except JSONDecodeError as exc:
        raise ValueError(f"Definition JSON is invalid in {workflow_path}: {exc.msg}") from exc

    return WorkflowSpec(
        name=_parse_name(sections["Workflow"]),
        description=sections["Description"].strip(),
        when_to_use=sections["When To Use"].strip(),
        definition=WorkflowDefinition.model_validate(definition_payload),
        source_path=str(workflow_path),
    )


def _split_sections(content: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current_section: str | None = None
    in_fenced_block = False

    for raw_line in content.splitlines():
        if raw_line.strip().startswith("```"):
            if current_section is not None:
                sections[current_section].append(raw_line)
            in_fenced_block = not in_fenced_block
            continue
        if raw_line.startswith("# Workflow: "):
            current_section = "Workflow"
            sections[current_section] = [raw_line]
            continue
        if not in_fenced_block and raw_line.startswith("## "):
            current_section = raw_line[3:].strip()
            sections[current_section] = []
            continue
        if current_section is not None:
            sections[current_section].append(raw_line)

    return {name: "\n".join(lines).strip() for name, lines in sections.items()}


def _parse_name(workflow_heading: str) -> str:
    prefix = "# Workflow: "
    if not workflow_heading.startswith(prefix):
        raise ValueError("Workflow heading must start with '# Workflow: '")
    return workflow_heading[len(prefix):].strip()


def _require_sections(sections: dict[str, str], required: tuple[str, ...]) -> None:
    missing = [section for section in required if not sections.get(section)]
    if missing:
        raise ValueError(f"Missing required sections: {', '.join(missing)}")


def _extract_json_block(section_content: str) -> str:
    lines = section_content.splitlines()
    json_lines: list[str] = []
    in_json_block = False

    for line in lines:
        stripped = line.strip()
        if not in_json_block and stripped == "```json":
            in_json_block = True
            continue
        if in_json_block and stripped == "```":
            return "\n".join(json_lines)
        if in_json_block:
            json_lines.append(line)

    raise ValueError("Definition section must contain a fenced json block")
