from __future__ import annotations

import re


FOLLOWUP_MARKERS = (
    "那",
    "那么",
    "这个",
    "这个呢",
    "那这个",
    "它",
    "它呢",
    "继续",
    "然后",
    "再说",
    "具体",
)


def _tokenize(text: str) -> set[str]:
    if not isinstance(text, str):
        return set()
    return {
        token.lower()
        for token in re.findall(r"[\u4e00-\u9fffA-Za-z0-9]+", text)
        if len(token.strip()) >= 2
    }


def _looks_like_followup(query_text: str) -> bool:
    text = (query_text or "").strip()
    if not text:
        return False
    return any(marker in text for marker in FOLLOWUP_MARKERS)


def select_history_for_query(query_text: str, history_messages):
    history_messages = list(history_messages or [])
    if len(history_messages) < 2:
        return history_messages

    if _looks_like_followup(query_text):
        return history_messages

    recent_messages = history_messages[-2:]
    recent_text = " ".join(getattr(message, "content", "") for message in recent_messages)

    query_tokens = _tokenize(query_text)
    recent_tokens = _tokenize(recent_text)

    if query_tokens and recent_tokens and query_tokens.intersection(recent_tokens):
        return recent_messages

    return []
