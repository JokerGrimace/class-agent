"""Token estimation utilities for context window management.

Uses tiktoken when available (OpenAI models), falls back to a chars/4
heuristic for non-OpenAI providers and models with unknown tokenizers.
"""

import json
import logging
from typing import Optional

from app.core.llm.adapter import Message

log = logging.getLogger(__name__)

try:
    import tiktoken

    _OPENAI_ENCODER = tiktoken.get_encoding("cl100k_base")
    _TIKTOKEN_AVAILABLE = True
except Exception:
    _OPENAI_ENCODER = None
    _TIKTOKEN_AVAILABLE = False

# Rough heuristic: ~4 chars per token for English text.
# Conservative — actual ratio varies by tokenizer and language.
CHARS_PER_TOKEN = 4.0

# Safety margin to compensate for estimation inaccuracy
# (multi-byte chars, special tokens, tool schemas, etc.).
SAFETY_MARGIN = 1.2

# Reserved tokens for tool definitions, formatting overhead.
TOOL_DEFINITION_OVERHEAD_PER_TOOL = 200

# Reserved tokens for system prompt formatting wrapper.
SYSTEM_PROMPT_FORMATTING_OVERHEAD = 64


def _count_tiktoken_tokens(text: str) -> int:
    """Count tokens using tiktoken cl100k_base encoder."""
    if not text:
        return 0
    if _OPENAI_ENCODER is None:
        raise RuntimeError("tiktoken not available")
    return len(_OPENAI_ENCODER.encode(text))


def _count_char_tokens(text: str) -> int:
    """Fallback: estimate tokens as chars / CHARS_PER_TOKEN."""
    if not text:
        return 0
    return max(1, int(len(text) / CHARS_PER_TOKEN))


def count_tokens(text: str, *, use_tiktoken: bool = True) -> int:
    """Count tokens in a text string.

    Uses tiktoken when available and requested, otherwise falls back
    to the chars/4 heuristic.
    """
    if use_tiktoken and _TIKTOKEN_AVAILABLE:
        try:
            return _count_tiktoken_tokens(text)
        except Exception:
            log.debug("tiktoken token counting failed, falling back to char heuristic")
    return _count_char_tokens(text)


def estimate_message_tokens(message: Message) -> int:
    """Estimate token count for a single adapter Message.

    Accounts for role, content, tool_calls, and tool metadata.
    Uses chars/4 heuristic (fast, no tiktoken dependency for per-message
    estimation in hot paths).
    """
    tokens = 4  # role + overhead

    if message.content:
        tokens += _count_char_tokens(message.content)

    if message.role == "assistant" and message.tool_calls:
        for tc in message.tool_calls:
            try:
                args_text = json.dumps(tc.arguments, ensure_ascii=False)
            except Exception:
                args_text = str(tc.arguments)
            tokens += 8  # id + type overhead
            tokens += _count_char_tokens(tc.name)
            tokens += _count_char_tokens(args_text)

    if message.role == "tool":
        if message.tool_call_id:
            tokens += _count_char_tokens(message.tool_call_id)
        if message.tool_name:
            tokens += _count_char_tokens(message.tool_name)

    return max(1, tokens)


def estimate_messages_tokens(messages: list[Message]) -> int:
    """Estimate total token count for a list of adapter Messages."""
    return sum(estimate_message_tokens(msg) for msg in messages)


def estimate_tool_schema_tokens(tools: list[dict]) -> int:
    """Estimate tokens consumed by tool definitions."""
    if not tools:
        return 0
    return len(tools) * TOOL_DEFINITION_OVERHEAD_PER_TOOL


def estimate_prompt_total(
    messages: list[Message],
    tools: Optional[list[dict]] = None,
    system_prompt_text: str = "",
    *,
    apply_safety_margin: bool = True,
) -> int:
    """Estimate total tokens for a full prompt (messages + tools + system prompt).

    Returns:
        Estimated token count, with safety margin applied if requested.
    """
    raw = estimate_messages_tokens(messages)
    raw += count_tokens(system_prompt_text, use_tiktoken=False)
    raw += SYSTEM_PROMPT_FORMATTING_OVERHEAD
    if tools:
        raw += estimate_tool_schema_tokens(tools)
    if apply_safety_margin:
        raw = int(raw * SAFETY_MARGIN)
    return max(1, raw)
