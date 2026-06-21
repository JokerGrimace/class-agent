"""LLM-driven conversation compaction — summarizes older history to free
context tokens when the prompt exceeds the model's budget.

Follows OpenClaw's compaction.ts pattern:
  1. splitMessagesByTokenShare — multi-part splitting by token share
  2. chunkMessagesByMaxTokens — chunk per max token limit for summary LLM
  3. summarizeWithFallback — progressive 3-level fallback
  4. summarizeInStages — multi-part independent summarize + merge
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

from app.core.llm.adapter import LLMAdapter, Message
from app.core.llm.tokens import estimate_messages_tokens, SAFETY_MARGIN, CHARS_PER_TOKEN

log = logging.getLogger(__name__)

# ── Constants ───────────────────────────────────────────────────────────────

BASE_CHUNK_RATIO = 0.4
MIN_CHUNK_RATIO = 0.15
DEFAULT_PARTS = 2

SUMMARIZATION_OVERHEAD_TOKENS = 4096  # prompt template + formatting overhead
DEFAULT_SUMMARY_FALLBACK = "No prior history."
MAX_COMPACTION_ATTEMPTS = 3

SUMMARY_SYSTEM_PROMPT = (
    "You are a conversation summarizer. Summarize the conversation history "
    "concisely. Output ONLY the summary — no preamble, no explanation."
)

SUMMARY_INSTRUCTIONS = (
    "Summarize this conversation history. Keep it concise but complete enough "
    "for an agent to resume work.\n\n"
    "MUST PRESERVE:\n"
    "- Active tasks and current status (in-progress, blocked, pending)\n"
    "- Batch operation progress (e.g., '5/17 items completed')\n"
    "- The last thing the user requested and what was being done about it\n"
    "- Decisions made and rationale\n"
    "- TODOs, open questions, and constraints\n"
    "- Any commitments or follow-ups promised\n\n"
    "PRIORITIZE recent context over older history. The agent needs to know "
    "what it was doing, not just what was discussed.\n\n"
    "Omit trivial greetings, redundant errors, and repeated content."
)

MERGE_SUMMARIES_INSTRUCTIONS = (
    "Merge these partial summaries into a single cohesive summary.\n\n"
    "MUST PRESERVE:\n"
    "- Active tasks and their current status (in-progress, blocked, pending)\n"
    "- Batch operation progress (e.g., '5/17 items completed')\n"
    "- The last thing the user requested and what was being done about it\n"
    "- Decisions made and their rationale\n"
    "- TODOs, open questions, and constraints\n"
    "- Any commitments or follow-ups promised\n\n"
    "PRIORITIZE recent context over older history. The agent needs to know "
    "what it was doing, not just what was discussed."
)


@dataclass
class CompactResult:
    ok: bool
    compacted: bool
    summary: str = ""
    reason: str = ""
    tokens_saved: int = 0


# ── Split Helpers ───────────────────────────────────────────────────────────

def _normalize_parts(parts: int, message_count: int) -> int:
    if not isinstance(parts, int) or parts <= 1:
        return 1
    return min(max(2, parts), max(1, message_count))


def _estimate_one(msg: Message) -> int:
    return estimate_messages_tokens([msg])


def split_messages_by_token_share(
    messages: list[Message],
    parts: int = DEFAULT_PARTS,
) -> list[list[Message]]:
    """Split messages into chunks by token share, preserving tool call pairs.

    Follows OpenClaw's splitMessagesByTokenShare exactly:
    - Splits by target token count per chunk
    - Keeps assistant(text)↔tool pairs together
    - Does not split inside a pair
    """
    if not messages:
        return []

    normalized_parts = _normalize_parts(parts, len(messages))
    if normalized_parts <= 1:
        return [messages]

    total_tokens = estimate_messages_tokens(messages)
    target_tokens = total_tokens / normalized_parts
    chunks: list[list[Message]] = []
    current: list[Message] = []
    current_tokens = 0

    pending_tool_call_ids: set[str] = set()
    pending_chunk_start_index: Optional[int] = None

    def _split_at_pending_boundary() -> bool:
        nonlocal current, current_tokens, pending_chunk_start_index
        if (
            pending_chunk_start_index is None
            or pending_chunk_start_index <= 0
            or len(chunks) >= normalized_parts - 1
        ):
            return False
        chunks.append(current[:pending_chunk_start_index])
        current = current[pending_chunk_start_index:]
        current_tokens = sum(_estimate_one(m) for m in current)
        pending_chunk_start_index = 0
        return True

    for msg in messages:
        msg_tokens = _estimate_one(msg)

        if (
            not pending_tool_call_ids
            and len(chunks) < normalized_parts - 1
            and current
            and current_tokens + msg_tokens > target_tokens
        ):
            chunks.append(current)
            current = []
            current_tokens = 0
            pending_chunk_start_index = None

        current.append(msg)
        current_tokens += msg_tokens

        if msg.role == "assistant":
            tool_ids = {tc.id for tc in (msg.tool_calls or [])}
            keeps_pending = bool(tool_ids)
            pending_tool_call_ids = tool_ids if keeps_pending else set()
            pending_chunk_start_index = (len(current) - 1) if keeps_pending else None
        elif msg.role == "tool" and pending_tool_call_ids:
            if msg.tool_call_id:
                pending_tool_call_ids.discard(msg.tool_call_id)
            if (
                not pending_tool_call_ids
                and len(chunks) < normalized_parts - 1
                and current_tokens > target_tokens
            ):
                _split_at_pending_boundary()
                pending_chunk_start_index = None

    if pending_tool_call_ids and current_tokens > target_tokens:
        _split_at_pending_boundary()

    if current:
        chunks.append(current)

    return chunks


def chunk_messages_by_max_tokens(
    messages: list[Message],
    max_tokens: int,
) -> list[list[Message]]:
    """Chunk messages so each chunk fits within max_tokens.

    Applies SAFETY_MARGIN to compensate for estimate inaccuracy.
    Follows OpenClaw's chunkMessagesByMaxTokens.
    """
    if not messages:
        return []

    effective_max = max(1, int(max_tokens / SAFETY_MARGIN))
    chunks: list[list[Message]] = []
    current_chunk: list[Message] = []
    current_tokens = 0

    for msg in messages:
        msg_tokens = _estimate_one(msg)

        if current_chunk and current_tokens + msg_tokens > effective_max:
            chunks.append(current_chunk)
            current_chunk = []
            current_tokens = 0

        current_chunk.append(msg)
        current_tokens += msg_tokens

        if msg_tokens > effective_max:
            chunks.append(current_chunk)
            current_chunk = []
            current_tokens = 0

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


# ── Detection Helpers ───────────────────────────────────────────────────────

def is_oversized_for_summary(msg: Message, context_window: int) -> bool:
    """Check if a single message is too large to summarize (> 50% of context)."""
    tokens = _estimate_one(msg) * SAFETY_MARGIN
    return tokens > context_window * 0.5


def compute_adaptive_chunk_ratio(messages: list[Message], context_window: int) -> float:
    """Compute adaptive chunk ratio based on average message size.

    When average message > 10% of context, reduce chunk ratio dynamically
    to avoid summary chunks exceeding model limits.
    """
    if not messages:
        return BASE_CHUNK_RATIO

    total = estimate_messages_tokens(messages)
    avg_tokens = total / len(messages)
    safe_avg = avg_tokens * SAFETY_MARGIN
    avg_ratio = safe_avg / context_window

    if avg_ratio > 0.1:
        reduction = min(avg_ratio * 2, BASE_CHUNK_RATIO - MIN_CHUNK_RATIO)
        return max(MIN_CHUNK_RATIO, BASE_CHUNK_RATIO - reduction)

    return BASE_CHUNK_RATIO


# ── Summary Helpers ─────────────────────────────────────────────────────────

def _build_summary_text(messages: list[Message]) -> str:
    """Build conversation text from messages for summarization."""
    lines: list[str] = []
    for msg in messages:
        role = msg.role
        content = msg.content or ""

        if role == "system":
            continue
        if role == "tool" and not content:
            continue

        if msg.role == "assistant" and msg.tool_calls:
            tool_names = [tc.name for tc in msg.tool_calls]
            content = f"[called tools: {', '.join(tool_names)}]"

        if content:
            prefix = role.upper() if role not in ("user", "assistant") else role.title()
            lines.append(f"{prefix}: {content}")

    return "\n".join(lines)


async def _generate_summary(
    llm: LLMAdapter,
    conversation_text: str,
    *,
    previous_summary: str = "",
    custom_instructions: Optional[str] = None,
) -> str:
    """Generate a summary for the given conversation chunk via LLM."""
    instructions = custom_instructions or SUMMARY_INSTRUCTIONS
    prompt = instructions
    if previous_summary:
        prompt += f"\n\nPrevious summary (merge into the new summary):\n{previous_summary}"
    prompt += f"\n\nConversation to summarize:\n{conversation_text}"

    msgs: list[Message] = [
        Message(role="system", content=SUMMARY_SYSTEM_PROMPT),
        Message(role="user", content=prompt),
    ]

    result = await llm.chat_complete(msgs, tools=None)
    summary = (result.content or "").strip()
    if not summary:
        raise ValueError("Empty summary from LLM")
    return summary


async def _retry_summary(
    llm: LLMAdapter,
    conversation_text: str,
    *,
    previous_summary: str = "",
    custom_instructions: Optional[str] = None,
) -> str:
    """Generate summary with exponential backoff retry."""
    last_error: Exception | None = None
    for attempt in range(1, MAX_COMPACTION_ATTEMPTS + 1):
        try:
            return await _generate_summary(
                llm,
                conversation_text,
                previous_summary=previous_summary,
                custom_instructions=custom_instructions,
            )
        except Exception as e:
            last_error = e
            log.warning("Compaction summary attempt %d/%d failed: %s", attempt, MAX_COMPACTION_ATTEMPTS, e)
            if attempt >= MAX_COMPACTION_ATTEMPTS:
                raise
            # Exponential backoff: 0.5s, 2s, 5s
            delay = min(0.5 * (4 ** (attempt - 1)), 5.0)
            await asyncio.sleep(delay)
            # Shorten prompt on retry
            max_chars = 8000 * (1 + attempt)
            if len(conversation_text) > max_chars:
                conversation_text = conversation_text[:max_chars] + "\n[... truncated for retry]"
    raise last_error  # type: ignore[misc]


# ── Summarization Pipeline ──────────────────────────────────────────────────

async def summarize_chunks(
    llm: LLMAdapter,
    messages: list[Message],
    max_chunk_tokens: int,
    *,
    custom_instructions: Optional[str] = None,
    previous_summary: str = "",
) -> str:
    """Summarize messages by chunking and generating per-chunk summaries.

    Each chunk is summarized separately; summaries are merged sequentially
    (each chunk summary is folded into the previous summary).
    """
    if not messages:
        return previous_summary or DEFAULT_SUMMARY_FALLBACK

    chunks = chunk_messages_by_max_tokens(messages, max_chunk_tokens)
    summary = previous_summary

    for chunk in chunks:
        text = _build_summary_text(chunk)
        if not text.strip():
            continue
        summary = await _retry_summary(
            llm,
            text,
            previous_summary=summary,
            custom_instructions=custom_instructions,
        )

    return summary or DEFAULT_SUMMARY_FALLBACK


async def summarize_with_fallback(
    llm: LLMAdapter,
    messages: list[Message],
    token_budget: int,
    max_chunk_tokens: int,
    *,
    custom_instructions: Optional[str] = None,
    previous_summary: str = "",
) -> str:
    """Summarize with progressive 3-level fallback.

    Level 1: Full summarization of all messages.
    Level 2: Exclude oversized messages, summarize rest.
    Level 3: Placeholder message (summary unavailable).
    """
    if not messages:
        return previous_summary or DEFAULT_SUMMARY_FALLBACK

    # Level 1: Full summarization
    try:
        return await summarize_chunks(
            llm,
            messages,
            max_chunk_tokens,
            custom_instructions=custom_instructions,
            previous_summary=previous_summary,
        )
    except Exception as e:
        log.warning("Full summarization failed: %s", e)

    # Level 2: Exclude oversized messages
    small_messages: list[Message] = []
    oversized_notes: list[str] = []

    for msg in messages:
        if is_oversized_for_summary(msg, token_budget):
            role = msg.role
            tokens = _estimate_one(msg)
            oversized_notes.append(
                f"[Large {role} (~{round(tokens / 1000)}K tokens) omitted from summary]"
            )
        else:
            small_messages.append(msg)

    if small_messages and len(small_messages) != len(messages):
        try:
            partial = await summarize_chunks(
                llm,
                small_messages,
                max_chunk_tokens,
                custom_instructions=custom_instructions,
                previous_summary=previous_summary,
            )
            notes = "\n\n".join(oversized_notes) if oversized_notes else ""
            return partial + ("\n\n" + notes if notes else "")
        except Exception as e:
            log.warning("Partial summarization also failed: %s", e)

    # Level 3: Placeholder
    return (
        f"Context contained {len(messages)} messages "
        f"({len(oversized_notes)} oversized). Summary unavailable due to size limits."
    )


async def summarize_in_stages(
    llm: LLMAdapter,
    messages: list[Message],
    token_budget: int,
    *,
    custom_instructions: Optional[str] = None,
    previous_summary: str = "",
    parts: int = DEFAULT_PARTS,
    min_messages_for_split: int = 4,
) -> str:
    """Multi-stage summarization: split → independent summarize → merge.

    1. Split messages into N parts by token share.
    2. Summarize each part independently with fallback.
    3. Merge all partial summaries into one cohesive summary.
    """
    if not messages:
        return previous_summary or DEFAULT_SUMMARY_FALLBACK

    normalized_parts = _normalize_parts(parts, len(messages))
    total_tokens = estimate_messages_tokens(messages)

    # Use adaptive chunk ratio to compute max_chunk_tokens
    adaptive_ratio = compute_adaptive_chunk_ratio(messages, token_budget)
    max_chunk_tokens = max(1, int(token_budget * adaptive_ratio * SAFETY_MARGIN))

    if normalized_parts <= 1 or len(messages) < min_messages_for_split or total_tokens <= max_chunk_tokens:
        return await summarize_with_fallback(
            llm, messages, token_budget, max_chunk_tokens,
            custom_instructions=custom_instructions,
            previous_summary=previous_summary,
        )

    splits = split_messages_by_token_share(messages, normalized_parts)
    splits = [s for s in splits if s]
    if len(splits) <= 1:
        return await summarize_with_fallback(
            llm, messages, token_budget, max_chunk_tokens,
            custom_instructions=custom_instructions,
            previous_summary=previous_summary,
        )

    # Summarize each part independently
    partial_summaries: list[str] = []
    for chunk in splits:
        partial = await summarize_with_fallback(
            llm, chunk, token_budget, max_chunk_tokens,
            custom_instructions=custom_instructions,
        )
        partial_summaries.append(partial)

    if len(partial_summaries) == 1:
        return partial_summaries[0]

    # Merge partial summaries
    merge_text = "\n\n---\n\n".join(
        f"Part {i + 1}:\n{s}" for i, s in enumerate(partial_summaries)
    )
    merge_custom = custom_instructions
    merge_instructions = MERGE_SUMMARIES_INSTRUCTIONS
    if merge_custom:
        merge_instructions = f"{merge_instructions}\n\n{merge_custom}"

    merge_result = await summarize_with_fallback(
        llm,
        [Message(role="user", content=merge_text)],
        token_budget,
        max_chunk_tokens,
        custom_instructions=merge_instructions,
    )
    return merge_result


# ── High-Level Entry Point ──────────────────────────────────────────────────

async def compact_history(
    messages: list[Message],
    llm: LLMAdapter,
    token_budget: int,
    *,
    previous_summary: str = "",
    keep_ratio: float = BASE_CHUNK_RATIO,
) -> CompactResult:
    """Compact conversation history by summarizing older messages.

    Uses multi-stage summarization with progressive fallback.

    Args:
        messages: Full conversation messages.
        llm: LLM adapter for generating the summary.
        token_budget: Model's context token limit.
        previous_summary: Existing summary to merge into (if any).
        keep_ratio: Fraction of budget for recent messages to keep as-is.

    Returns:
        CompactResult with summary text if compaction succeeded.
    """
    if len(messages) <= 3:
        return CompactResult(
            ok=True, compacted=False,
            reason="Too few messages to compact; context fits comfortably",
        )

    estimated = estimate_messages_tokens(messages)
    if estimated <= token_budget * 0.6:
        return CompactResult(
            ok=True, compacted=False,
            reason=f"Context fits comfortably ({estimated}/{token_budget} tokens)",
        )

    old_tokens_before = estimate_messages_tokens(messages)

    try:
        summary = await summarize_in_stages(
            llm, messages, token_budget, previous_summary=previous_summary,
        )

        if not summary or summary == DEFAULT_SUMMARY_FALLBACK:
            return CompactResult(
                ok=True, compacted=False,
                reason="Summarization produced no meaningful result",
            )

        # Estimate saved tokens
        summary_tokens = estimate_messages_tokens([Message(role="user", content=summary)])
        saved = max(0, old_tokens_before - summary_tokens)

        log.info("Compaction succeeded: %d messages → summary (%d tokens saved)", len(messages), saved)
        return CompactResult(
            ok=True, compacted=True, summary=summary, tokens_saved=saved,
        )
    except Exception as e:
        log.warning("Compaction failed: %s", e)
        return CompactResult(
            ok=False, compacted=False,
            reason=f"Compaction failed: {e}",
        )
