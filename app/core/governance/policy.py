from dataclasses import dataclass, field
from typing import Any, Optional

from app.core.governance.types import GovernanceConfig, GovernanceRule


@dataclass
class EvaluationContext:
    tool_name: str
    tool_tags: list[str] = field(default_factory=list)
    action_name: Optional[str] = None
    action_tags: list[str] = field(default_factory=list)
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class GovernanceDecision:
    decision: str
    summary: str = ""
    visible_fields: list[str] = field(default_factory=list)
    editable_fields: list[str] = field(default_factory=list)
    deny_message: str = ""
    support_user_revision: bool = False
    matched_rule_ids: list[str] = field(default_factory=list)


class GovernancePolicyEngine:
    def __init__(self, config: GovernanceConfig):
        self.config = config

    def evaluate(self, context: EvaluationContext) -> GovernanceDecision:
        matched_rules = [
            rule
            for rule in sorted(
                self.config.rules,
                key=lambda item: item.priority,
                reverse=True,
            )
            if self._matches(rule, context)
        ]
        return self._build_decision(matched_rules)

    def _matches(self, rule: GovernanceRule, context: EvaluationContext) -> bool:
        match = rule.match

        if match.tool_names_any and context.tool_name not in match.tool_names_any:
            return False
        if match.tool_tags_all and not all(
            tag in context.tool_tags for tag in match.tool_tags_all
        ):
            return False
        if match.tool_tags_any and not any(
            tag in context.tool_tags for tag in match.tool_tags_any
        ):
            return False
        if match.action_equals_any and context.action_name not in match.action_equals_any:
            return False
        if match.action_tags_any and not any(
            tag in context.action_tags for tag in match.action_tags_any
        ):
            return False
        if match.action_tags_all and not all(
            tag in context.action_tags for tag in match.action_tags_all
        ):
            return False
        return True

    def _build_decision(self, matched_rules: list[GovernanceRule]) -> GovernanceDecision:
        decision = self.config.defaults.decision
        summary = ""
        deny_message = ""
        support_user_revision = False
        visible_fields: list[str] = []
        editable_fields: list[str] = []

        for rule in matched_rules:
            effect = rule.effect
            if effect.decision and decision == self.config.defaults.decision:
                decision = effect.decision
            if effect.summary and not summary:
                summary = effect.summary
            if effect.deny_message and not deny_message:
                deny_message = effect.deny_message
            if effect.support_user_revision is not None:
                support_user_revision = effect.support_user_revision

            if effect.field_preset:
                preset = self.config.field_presets[effect.field_preset]
                visible_fields.extend(preset.visible_fields)
                editable_fields.extend(preset.editable_fields)

            visible_fields.extend(effect.visible_fields)
            editable_fields.extend(effect.editable_fields)

        visible_fields = sorted(set(visible_fields))
        editable_fields = sorted(set(editable_fields))
        invalid_fields = sorted(set(editable_fields) - set(visible_fields))
        if invalid_fields:
            raise ValueError(
                f"Editable fields must be visible fields: {invalid_fields}"
            )

        return GovernanceDecision(
            decision=decision,
            summary=summary,
            visible_fields=visible_fields,
            editable_fields=editable_fields,
            deny_message=deny_message,
            support_user_revision=support_user_revision,
            matched_rule_ids=[rule.id for rule in matched_rules],
        )
