from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional, TypeVar

import httpx

from app.config import settings
from app.core.iclass.auth_context import get_current_iclass_api_token
from app.core.llm.user_context import get_current_model_user


T = TypeVar("T")


class UserModelCredentialError(RuntimeError):
    pass


class UserModelCredentialForbidden(UserModelCredentialError):
    pass


class UserModelCredentialUpstreamError(UserModelCredentialError):
    pass


@dataclass(frozen=True)
class UserModelCredential:
    api_key: str
    model: str


@dataclass(frozen=True)
class UserModelCredentialCacheEntry:
    teacher_code: str
    credential: UserModelCredential
    expires_at_monotonic: float


class UserScopedLlmService:
    # 服务集中处理教师身份、凭证缓存和 relay 刷新，所有 LLM 入口共享这一实例。
    def __init__(
        self,
        *,
        cache_ttl_seconds: Optional[int] = None,
        cache_max_entries: Optional[int] = None,
    ) -> None:
        self.cache_ttl_seconds = (
            settings.new_api_api_key_cache_ttl_seconds
            if cache_ttl_seconds is None
            else cache_ttl_seconds
        )
        self.cache_max_entries = (
            settings.new_api_api_key_cache_max_entries
            if cache_max_entries is None
            else cache_max_entries
        )
        self._cache: dict[str, UserModelCredentialCacheEntry] = {}
        self._inflight: dict[str, asyncio.Task[UserModelCredentialCacheEntry]] = {}
        self._state_lock = asyncio.Lock()

    # Token 只生成不可逆指纹作为缓存键，原始值不进入字典键和日志。
    @staticmethod
    def _cache_key(auth_token: str) -> str:
        return hashlib.sha256(auth_token.encode("utf-8")).hexdigest()

    # 缓存清理使用单调时钟，系统时间调整不会延长凭证有效期。
    def _purge_expired_locked(self, now: float) -> None:
        expired = [
            key
            for key, entry in self._cache.items()
            if entry.expires_at_monotonic <= now
        ]
        for key in expired:
            self._cache.pop(key, None)

    # 容量达到上限时优先移除最早过期的条目。
    def _enforce_capacity_locked(self) -> None:
        while len(self._cache) > max(1, self.cache_max_entries):
            oldest_key = min(
                self._cache,
                key=lambda key: self._cache[key].expires_at_monotonic,
            )
            self._cache.pop(oldest_key, None)

    # 内部接口只接收认证后的教师编码，不接收 iClass 登录 Token。
    async def _fetch_credential(self, teacher_code: str) -> UserModelCredential:
        internal_token = settings.new_api_internal_token.strip()
        if not internal_token:
            raise UserModelCredentialUpstreamError("new-api internal token is not configured")

        url = (
            f"{settings.new_api_internal_base_url.rstrip('/')}"
            "/api/iclass/agent/api-key/resolve"
        )
        headers = {"X-IClass-Agent-Internal-Token": internal_token}
        try:
            async with httpx.AsyncClient(
                timeout=settings.new_api_api_key_resolve_timeout_seconds
            ) as client:
                response = await client.post(
                    url,
                    headers=headers,
                    json={"teacher_code": teacher_code},
                )
        except httpx.HTTPError as exc:
            raise UserModelCredentialUpstreamError(
                "new-api credential request failed"
            ) from exc

        if response.status_code == 403:
            raise UserModelCredentialForbidden("teacher API key is unavailable")
        if response.status_code != 200:
            raise UserModelCredentialUpstreamError(
                f"new-api credential request returned {response.status_code}"
            )

        try:
            payload = response.json()
            data = payload["data"]
            api_key = str(data["api_key"]).strip()
            model = str(data["model"]).strip()
        except (KeyError, TypeError, ValueError) as exc:
            raise UserModelCredentialUpstreamError(
                "new-api credential response is invalid"
            ) from exc
        if payload.get("success") is not True or not api_key or not model:
            raise UserModelCredentialUpstreamError(
                "new-api credential response is invalid"
            )
        return UserModelCredential(api_key=api_key, model=model)

    # 首个请求负责加载并写缓存；Task 自身清理占位，等待方取消也不会遗留 inflight 项。
    async def _fetch_and_cache(
        self,
        cache_key: str,
        teacher_code: str,
    ) -> UserModelCredentialCacheEntry:
        current_task = asyncio.current_task()
        try:
            credential = await self._fetch_credential(teacher_code)
            entry = UserModelCredentialCacheEntry(
                teacher_code=teacher_code,
                credential=credential,
                expires_at_monotonic=time.monotonic() + self.cache_ttl_seconds,
            )
            async with self._state_lock:
                self._cache[cache_key] = entry
                self._purge_expired_locked(time.monotonic())
                self._enforce_capacity_locked()
            return entry
        finally:
            async with self._state_lock:
                if self._inflight.get(cache_key) is current_task:
                    self._inflight.pop(cache_key, None)

    # 解析前同时校验 Token、教师编码和角色，禁止非教师进入教师凭证链路。
    async def resolve_credential(
        self,
        auth_token: str,
        teacher_code: str,
        user_role: str,
        *,
        force_refresh: bool = False,
    ) -> UserModelCredential:
        auth_token = (auth_token or "").strip()
        teacher_code = (teacher_code or "").strip()
        user_role = (user_role or "").strip()
        if not auth_token or not teacher_code:
            raise UserModelCredentialForbidden("authenticated teacher context is required")
        if user_role != "2":
            raise UserModelCredentialForbidden("teacher model access is required")

        cache_key = self._cache_key(auth_token)
        async with self._state_lock:
            now = time.monotonic()
            self._purge_expired_locked(now)
            if force_refresh:
                self._cache.pop(cache_key, None)
            else:
                cached = self._cache.get(cache_key)
                if cached is not None:
                    if cached.teacher_code != teacher_code:
                        self._cache.pop(cache_key, None)
                        raise UserModelCredentialForbidden(
                            "authenticated teacher does not match cached credential"
                        )
                    return cached.credential

            task = self._inflight.get(cache_key)
            if task is None:
                task = asyncio.create_task(
                    self._fetch_and_cache(cache_key, teacher_code)
                )
                self._inflight[cache_key] = task

        entry = await asyncio.shield(task)

        if entry.teacher_code != teacher_code:
            await self.invalidate(auth_token)
            raise UserModelCredentialForbidden(
                "authenticated teacher does not match resolved credential"
            )
        return entry.credential

    # relay 明确拒绝凭证时只删除当前 Token 的缓存项。
    async def invalidate(self, auth_token: str) -> None:
        auth_token = (auth_token or "").strip()
        if not auth_token:
            return
        async with self._state_lock:
            self._cache.pop(self._cache_key(auth_token), None)

    # 统一模型调用在当前认证上下文中解析凭证，并对 401/403 仅刷新重试一次。
    async def call_llm(
        self,
        invoke: Callable[[UserModelCredential], Awaitable[T]],
        *,
        auth_token: Optional[str] = None,
        teacher_code: Optional[str] = None,
        user_role: Optional[str] = None,
    ) -> T:
        model_user = get_current_model_user()
        resolved_token = (
            get_current_iclass_api_token()
            if auth_token is None
            else auth_token
        )
        resolved_teacher_code = (
            model_user.teacher_code
            if teacher_code is None and model_user is not None
            else teacher_code or ""
        )
        resolved_user_role = (
            model_user.user_role
            if user_role is None and model_user is not None
            else user_role or ""
        )
        credential = await self.resolve_credential(
            resolved_token,
            resolved_teacher_code,
            resolved_user_role,
        )
        try:
            return await invoke(credential)
        except Exception as exc:
            if getattr(exc, "status_code", None) not in {401, 403}:
                raise

        credential = await self.resolve_credential(
            resolved_token,
            resolved_teacher_code,
            resolved_user_role,
            force_refresh=True,
        )
        return await invoke(credential)


# 进程级单例让主 Agent、Workflow 子 Agent 和 RAG 共用 TTL 缓存与 single-flight。
_user_scoped_llm_service = UserScopedLlmService()


# 工厂函数便于测试替换，同时避免各调用方自行创建独立缓存。
def get_user_scoped_llm_service() -> UserScopedLlmService:
    return _user_scoped_llm_service
