"""Tool result truncation — caps oversized tool output to prevent
a single tool_result from dominating the context window.

Follows OpenClaw's tool-result-truncation.ts + tool-result-context-guard.ts.
"""

import re

# Max share of context window a single tool result may occupy
MAX_TOOL_RESULT_CONTEXT_SHARE = 0.3

# Default hard cap for a single live tool result text (chars)
DEFAULT_MAX_LIVE_TOOL_RESULT_CHARS = 16_000

# Minimum characters to keep when truncating
MIN_KEEP_CHARS = 2_000

# Truncation notice suffix
CONTEXT_LIMIT_TRUNCATION_NOTICE = "more characters truncated"

# Middle omission marker for head+tail truncation
MIDDLE_OMISSION_MARKER = "\n\n⚠️ [... middle content omitted — showing head and tail ...]\n\n"


def format_truncation_notice(truncated_chars: int) -> str:
    return f"[... {max(1, int(truncated_chars))} {CONTEXT_LIMIT_TRUNCATION_NOTICE}]"


def calculate_max_tool_result_chars(context_window_tokens: int) -> int:
    """Calculate max chars allowed for a single tool result.

    Capped at 30% of context window (chars) or hard fallback.
    """
    max_tokens = max(1, int(context_window_tokens * MAX_TOOL_RESULT_CONTEXT_SHARE))
    # Rough: ~4 chars per token
    max_chars = max_tokens * 4
    return min(max_chars, DEFAULT_MAX_LIVE_TOOL_RESULT_CHARS)


def _has_important_tail(text: str) -> bool:
    """Detect if text tail (~last 2000 chars) contains error/diagnostic content
    that should be preserved during truncation.

    Matches OpenClaw's `hasImportantTail()` logic.
    """
    tail = text[-2000:].lower()
    return bool(
        re.search(
            r"\b(error|exception|failed|fatal|traceback|panic|stack trace|errno|exit code)\b",
            tail,
        )
        or text.rstrip().endswith("}")
        or re.search(r"\b(total|summary|result|complete|finished|done)\b", tail)
    )


def truncate_tool_result_text(
    text: str,
    max_chars: int,
    *,
    min_keep_chars: int = MIN_KEEP_CHARS,
) -> str:
    """Truncate tool result text to fit within max_chars.

    Strategy:
    - If tail has important content (errors, summaries, JSON): preserve head+tail
    - Otherwise: preserve beginning only
    - Appends a truncation notice.

    Args:
        text: The tool result text content.
        max_chars: Maximum allowed characters.
        min_keep_chars: Minimum characters to retain (before suffix).

    Returns:
        Truncated text with notice.
    """
    if len(text) <= max_chars:
        return text

    suffix = format_truncation_notice(len(text) - max_chars)
    if len(suffix) >= max_chars:
        # suffix alone exceeds the max — use a minimal placeholder
        return "[truncated]"[:max_chars]
    budget = max(1, max_chars - len(suffix))

    # Head+tail strategy for text with important content at the end
    if _has_important_tail(text) and budget > 100:
        tail_budget = min(int(budget * 0.3), 4000)
        head_budget = budget - tail_budget - len(MIDDLE_OMISSION_MARKER)

        if head_budget > 0:
            head_cut = head_budget
            head_nl = text.rfind("\n", 0, head_budget)
            if head_nl > head_budget * 0.8:
                head_cut = head_nl

            tail_start = max(0, len(text) - tail_budget)
            tail_nl = text.find("\n", tail_start)
            if tail_nl != -1 and tail_nl < tail_start + tail_budget * 0.2:
                tail_start = tail_nl + 1

            kept = text[:head_cut] + MIDDLE_OMISSION_MARKER + text[tail_start:]
            combined = kept + suffix
            if len(combined) <= max_chars:
                return combined

    # Default: keep beginning only (try to cut at newline)
    cut = budget
    last_nl = text.rfind("\n", 0, budget)
    if last_nl > budget * 0.8:
        cut = last_nl
    if cut < 1:
        cut = 1

    return text[:cut] + suffix


def should_truncate(content: str, max_chars: int) -> bool:
    """Check if content exceeds the limit and needs truncation."""
    return len(content) > max_chars
