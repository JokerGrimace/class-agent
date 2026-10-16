from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ModelUserContext:
    teacher_code: str
    user_role: str


# 模型身份与业务工具 Token 分开保存，避免把 API Key 放进请求上下文。
_current_model_user: ContextVar[Optional[ModelUserContext]] = ContextVar(
    "current_model_user",
    default=None,
)


# 入口只写入已经由 Java `/user/current` 验证过的教师编码和角色。
def set_current_model_user(teacher_code: str, user_role: str) -> Token[Optional[ModelUserContext]]:
    context = ModelUserContext(
        teacher_code=(teacher_code or "").strip(),
        user_role=(user_role or "").strip(),
    )
    return _current_model_user.set(context)


# LLM 服务只读当前异步任务所属的模型身份。
def get_current_model_user() -> Optional[ModelUserContext]:
    return _current_model_user.get()


# 每个 HTTP、SSE、WebSocket 或评分作用域结束时恢复进入前的上下文。
def reset_current_model_user(token: Token[Optional[ModelUserContext]]) -> None:
    _current_model_user.reset(token)
