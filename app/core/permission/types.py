from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator


PermissionReplyAction = Literal["once", "always", "reject", "once_with_changes"]


class PermissionRequestCancelledError(Exception):
    """Raised when a pending permission request is explicitly cancelled."""


class PermissionReply(BaseModel):
    action: PermissionReplyAction
    edited_fields: dict[str, Any] = Field(default_factory=dict)
    comment: Optional[str] = None

    @model_validator(mode="after")
    def validate_edited_fields_usage(self) -> "PermissionReply":
        if self.action == "once_with_changes" and not self.edited_fields:
            raise ValueError("once_with_changes requires at least one edited field")
        if self.action != "once_with_changes" and self.edited_fields:
            raise ValueError("edited_fields are only allowed for once_with_changes")
        return self


class PendingPermissionRequest(BaseModel):
    request_id: str
    session_id: str
    user_id: str
    tool_call_id: str
    tool_name: str
    tool_action: Optional[str] = None
    summary: str
    original_arguments: dict[str, Any] = Field(default_factory=dict)
    visible_arguments: dict[str, Any] = Field(default_factory=dict)
    editable_fields: list[str] = Field(default_factory=list)
    allowed_actions: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)


@dataclass
class SessionPermissionGrant:
    session_id: str
    tool_name: str
    tool_action: Optional[str]
    created_at: datetime = field(default_factory=datetime.utcnow)
