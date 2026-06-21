from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator

GovernanceDecision = Literal["allow", "ask", "deny"]
GovernanceVisibility = Literal["hidden", "visible"]
GovernanceOnDeny = Literal["retry_reasoning", "return_error"]


class GovernanceDefaults(BaseModel):
    decision: GovernanceDecision = "allow"
    visibility: GovernanceVisibility = "hidden"
    on_deny: GovernanceOnDeny = "retry_reasoning"


class FieldPreset(BaseModel):
    visible_fields: list[str] = Field(default_factory=list)
    editable_fields: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_editable_fields_are_visible(self) -> "FieldPreset":
        invalid_fields = sorted(set(self.editable_fields) - set(self.visible_fields))
        if invalid_fields:
            raise ValueError(
                f"Field preset has non-visible editable fields: {invalid_fields}"
            )
        return self


class RuleMatch(BaseModel):
    tool_names_any: list[str] = Field(default_factory=list)
    tool_tags_all: list[str] = Field(default_factory=list)
    tool_tags_any: list[str] = Field(default_factory=list)
    action_equals_any: list[str] = Field(default_factory=list)
    action_tags_any: list[str] = Field(default_factory=list)
    action_tags_all: list[str] = Field(default_factory=list)


class RuleEffect(BaseModel):
    decision: Optional[GovernanceDecision] = None
    summary: Optional[str] = None
    field_preset: Optional[str] = None
    visible_fields: list[str] = Field(default_factory=list)
    editable_fields: list[str] = Field(default_factory=list)
    deny_message: Optional[str] = None
    support_user_revision: Optional[bool] = None


class GovernanceRule(BaseModel):
    id: str
    priority: int = 0
    match: RuleMatch = Field(default_factory=RuleMatch)
    effect: RuleEffect = Field(default_factory=RuleEffect)

    @model_validator(mode="after")
    def validate_effect_field_visibility(self) -> "GovernanceRule":
        if self.effect.visible_fields:
            invalid_fields = sorted(
                set(self.effect.editable_fields) - set(self.effect.visible_fields)
            )
            if invalid_fields:
                raise ValueError(
                    f"Rule {self.id} has non-visible editable fields: {invalid_fields}"
                )
        return self


class GovernanceConfig(BaseModel):
    version: int
    defaults: GovernanceDefaults = Field(default_factory=GovernanceDefaults)
    tool_groups: dict[str, dict[str, Any]] = Field(default_factory=dict)
    field_presets: dict[str, FieldPreset] = Field(default_factory=dict)
    rules: list[GovernanceRule] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_rule_field_presets(self) -> "GovernanceConfig":
        known_presets = set(self.field_presets)
        for rule in self.rules:
            preset_name = rule.effect.field_preset
            if preset_name and preset_name not in known_presets:
                raise ValueError(f"Unknown field preset: {preset_name}")
        return self
