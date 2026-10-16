from __future__ import annotations

from contextvars import ContextVar, Token


ICLASS_API_TOKEN_HEADER = "x-iclass-api-token"

_current_iclass_api_token: ContextVar[str] = ContextVar(
    "current_iclass_api_token",
    default="",
)


def get_current_iclass_api_token() -> str:
    return _current_iclass_api_token.get().strip()


def set_current_iclass_api_token(token: str | None) -> Token[str]:
    return _current_iclass_api_token.set((token or "").strip())


def reset_current_iclass_api_token(token: Token[str]) -> None:
    _current_iclass_api_token.reset(token)
