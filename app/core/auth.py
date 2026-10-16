from dataclasses import dataclass
from typing import Any, Optional

import httpx

from fastapi import Header, HTTPException, Request, WebSocket, status
from starlette.requests import HTTPConnection

from app.config import settings
from app.core.iclass.auth_context import ICLASS_API_TOKEN_HEADER

@dataclass
class AuthenticatedUser:
    user_id: str
    token: str
    user_role: str = ""


def _normalize_user_id(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _extract_current_user_identity_key(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    user_code = _normalize_user_id(payload.get("userCode"))
    return user_code


def _extract_current_user_role(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    return _normalize_user_id(payload.get("userType"))


class SessionTokenValidator:
    async def validate_token(self, token: str) -> Optional[AuthenticatedUser]:
        # 正式凭证只能由 Java 后端确认，认证失败不能降级成测试用户。
        return await iclass_user_resolver.resolve_user(token)


token_validator = SessionTokenValidator()


class IClassCurrentUserClient:
    # iClass 后端当前登录用户接口是 `GET /user/current`。
    # 这里单独封装轻量客户端，避免再依赖已经不存在的旧 `IClassApiClient`。
    def __init__(self, *, token: str) -> None:
        self.token = (token or "").strip()

    async def get_current_user(self) -> Optional[dict[str, Any]]:
        if not self.token:
            return None

        api_url = f"{settings.iclass_api_base_url.rstrip('/')}/user/current"
        headers = {"Authorization": f"Bearer {self.token}"}
        async with httpx.AsyncClient(timeout=settings.iclass_api_timeout_seconds) as client:
            response = await client.get(api_url, headers=headers)
        response.raise_for_status()

        payload = response.json()
        if not isinstance(payload, dict):
            return None
        if str(payload.get("code")) != "200":
            return None
        body = payload.get("body")
        if not isinstance(body, dict):
            return None
        return body


class IClassUserResolver:
    async def resolve_user(self, token: str) -> Optional[AuthenticatedUser]:
        if not token:
            return None
        client = IClassCurrentUserClient(token=token)
        try:
            payload = await client.get_current_user()
        except (httpx.HTTPError, ValueError):
            return None
        user_id = _extract_current_user_identity_key(payload)
        user_role = _extract_current_user_role(payload)
        if not user_id or not user_role:
            return None
        return AuthenticatedUser(user_id=user_id, token=token, user_role=user_role)

    async def resolve_user_id(self, token: str) -> Optional[str]:
        user = await self.resolve_user(token)
        return user.user_id if user else None

    async def resolve_user_role(self, token: str) -> str:
        user = await self.resolve_user(token)
        return user.user_role if user else ""


iclass_user_resolver = IClassUserResolver()


def _parse_bearer_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


async def _authenticate_connection(
    connection: HTTPConnection,
    token: Optional[str],
    iclass_api_token: Optional[str],
) -> AuthenticatedUser:
    # 两种传输共用认证决策；显式提供的 Java 凭证失败后不得切换到另一个身份。
    if iclass_api_token is not None:
        token = iclass_api_token.strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid bearer token",
        )

    user = await token_validator.validate_token(token)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bearer token",
        )
    return user


async def require_authenticated_user(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    iclass_api_token: Optional[str] = Header(default=None, alias=ICLASS_API_TOKEN_HEADER),
) -> AuthenticatedUser:
    return await _authenticate_connection(request, _parse_bearer_token(authorization), iclass_api_token)


async def require_websocket_user(websocket: WebSocket) -> AuthenticatedUser:
    authorization = websocket.headers.get("authorization")
    iclass_api_token = websocket.headers.get(ICLASS_API_TOKEN_HEADER)
    token = _parse_bearer_token(authorization)
    if not token:
        token = websocket.query_params.get("token")

    # 保留 WebSocket 的 4401 协议，同时复用 HTTP 的失败即拒绝逻辑。
    try:
        return await _authenticate_connection(websocket, token, iclass_api_token)
    except HTTPException:
        await websocket.close(code=4401, reason="Missing or invalid bearer token")
        raise
