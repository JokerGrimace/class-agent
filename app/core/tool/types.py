from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional


@dataclass
class ToolMetadata:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class ToolGovernanceMetadata:
    tool_tags: list[str] = field(default_factory=list)
    action_source: Optional[str] = None
    action_tags: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class ToolResult:
    success: bool
    content: str = ""
    error: Optional[str] = None
    timed_out: bool = False
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]
    func: Callable[..., Awaitable[ToolResult]]
    metadata: Optional[ToolMetadata] = None
    governance: ToolGovernanceMetadata = field(default_factory=ToolGovernanceMetadata)

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = ToolMetadata(
                name=self.name,
                description=self.description,
                parameters=self.parameters,
            )


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolError:
    tool_name: str
    error: str
    timed_out: bool = False
    meta: Optional[str] = None
