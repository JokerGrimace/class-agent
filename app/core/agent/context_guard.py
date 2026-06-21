"""Preemptive context guard — runs before every LLM call to detect and
route context overflow proactively, not reactively.

Follows OpenClaw's preemptive-compaction pattern: estimate prompt tokens,
compare against budget, and determine the best recovery route.
"""

from dataclasses import dataclass
from typing import Literal, Optional

from app.core.llm.adapter import Message
from app.core.llm.tokens import (
    estimate_messages_tokens,
    estimate_tool_schema_tokens,
    CHARS_PER_TOKEN,
    SAFETY_MARGIN,
)

# Minimum prompt budget: ensure at least 15% of context or 2K tokens.
MIN_PROMPT_BUDGET_RATIO = 0.15
MIN_PROMPT_BUDGET_TOKENS = 2000

# Tool result chars-per-token estimate (more conservative than general text
# because tool output often contains dense code/structure).
TOOL_RESULT_CHARS_PER_TOKEN = 3.0

# Buffer tokens added to overflow threshold for truncation-only routing
# to avoid false positives where a marginal overflow triggers compaction
# when truncation alone would suffice.
TRUNCATION_ROUTE_BUFFER_TOKENS = 512


PrecheckRoute = Literal["fits", "truncate_only", "compact_only", "compact_then_truncate"]


@dataclass
class PrecheckResult:
    route: PrecheckRoute
    estimated_tokens: int
    prompt_budget: int  # budget before reserve
    overflow_tokens: int
    tool_result_total_chars: int
    tool_result_truncation_chars: int
    should_precheck_block: bool  # True if route != "fits"


def _compute_prompt_budget(
    context_token_budget: int,
    reserve_tokens: int = 0,
) -> int:
    """Compute the prompt budget after subtracting reserve."""
    min_budget = min(
        MIN_PROMPT_BUDGET_TOKENS,
        max(1, int(context_token_budget * MIN_PROMPT_BUDGET_RATIO)),
    )
    effective_reserve = min(
        reserve_tokens,
        max(0, context_token_budget - min_budget),
    )
    return max(1, context_token_budget - effective_reserve)


def _count_tool_result_chars(messages: list[Message]) -> int:
    """Count total characters in tool role messages."""
    total = 0
    for msg in messages:
        if msg.role == "tool" and msg.content:
            total += len(msg.content)
    return total


def _estimate_reducible_chars(
    tool_result_total: int,
    context_token_budget: int,
    tool_result_max_chars: Optional[int] = None,
) -> int:
    """Estimate how many tool result chars could be saved by truncation.

    Each individual tool result is capped at 30% of context window (or
    tool_result_max_chars if specified). Aggregate across all tool results.
    """
    if tool_result_total <= 0:
        return 0
    max_single = tool_result_max_chars or max(
        1, int(context_token_budget * TOOL_RESULT_CHARS_PER_TOKEN * 0.3)
    )
    # Rough estimate: if total tool result chars exceed
    # max_single * tool_count_guess, the excess is potentially reducible.
    # We use a conservative heuristic.
    if tool_result_total <= max_single:
        return 0
    return max(0, tool_result_total - max_single)


def precheck_context(
    messages: list[Message],
    system_prompt: str,
    tools: Optional[list[dict]] = None,
    context_token_budget: int = 128_000,
    reserve_tokens: int = 0,
    tool_result_max_chars: Optional[int] = None,
) -> PrecheckResult:
    """Check whether the current context fits within the model's token budget.

    Args:
        messages: Conversation messages to send.
        system_prompt: System prompt text.
        tools: Tool definitions (optional).
        context_token_budget: Model's max context tokens.
        reserve_tokens: Tokens reserved for model response.
        tool_result_max_chars: Max chars per tool result (for truncation estimation).

    Returns:
        PrecheckResult with the chosen route and diagnostics.
    """
    # Estimate total prompt tokens
    raw = estimate_messages_tokens(messages)
    raw += int(len(system_prompt) / CHARS_PER_TOKEN)  # system prompt
    raw += 64  # formatting overhead
    if tools:
        raw += estimate_tool_schema_tokens(tools)
    estimated_tokens = max(1, int(raw * SAFETY_MARGIN))

    prompt_budget = _compute_prompt_budget(context_token_budget, reserve_tokens)
    overflow_tokens = max(0, estimated_tokens - prompt_budget)

    if overflow_tokens <= 0:
        return PrecheckResult(
            route="fits",
            estimated_tokens=estimated_tokens,
            prompt_budget=prompt_budget,
            overflow_tokens=0,
            tool_result_total_chars=0,
            tool_result_truncation_chars=0,
            should_precheck_block=False,
        )

    # Check tool result truncation potential
    tool_result_total_chars = _count_tool_result_chars(messages)
    tool_result_truncation_chars = _estimate_reducible_chars(
        tool_result_total_chars,
        context_token_budget,
        tool_result_max_chars,
    )
    tool_result_truncation_tokens = max(
        0, int(tool_result_truncation_chars / TOOL_RESULT_CHARS_PER_TOKEN)
    )

    # Determine route
    overflow_chars = overflow_tokens * int(CHARS_PER_TOKEN)
    truncation_buffer_chars = TRUNCATION_ROUTE_BUFFER_TOKENS * int(CHARS_PER_TOKEN)
    truncate_only_threshold_chars = overflow_chars + truncation_buffer_chars

    if tool_result_truncation_chars <= 0:
        route: PrecheckRoute = "compact_only"
    elif tool_result_truncation_chars >= truncate_only_threshold_chars:
        route = "truncate_only"
    else:
        route = "compact_then_truncate"

    return PrecheckResult(
        route=route,
        estimated_tokens=estimated_tokens,
        prompt_budget=prompt_budget,
        overflow_tokens=overflow_tokens,
        tool_result_total_chars=tool_result_total_chars,
        tool_result_truncation_chars=tool_result_truncation_chars,
        should_precheck_block=True,
    )
