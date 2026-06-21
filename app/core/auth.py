from dataclasses import dataclass
from typing import Optional

from fastapi import Header, HTTPException, WebSocket, status

from app.config import settings


@dataclass
class AuthenticatedUser:
    user_id: str
    token: str


class SessionTokenValidator:
    async def validate_token(self, token: str) -> Optional[AuthenticatedUser]:
        if token == settings.session_auth_test_token:
            return AuthenticatedUser(
                user_id=settings.session_auth_test_user_id,
                token=token,
            )
        return None


token_validator = SessionTokenValidator()


def _build_test_user(token: str = "") -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=settings.session_auth_test_user_id,
        token=token or settings.session_auth_test_token,
    )


def _parse_bearer_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


async def require_authenticated_user(
    authorization: Optional[str] = Header(default=None),
) -> AuthenticatedUser:
    token = _parse_bearer_token(authorization)
    if not token:
        if settings.session_auth_bypass_for_testing:
            return _build_test_user()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid bearer token",
        )

    user = await token_validator.validate_token(token)
    if not user:
        if settings.session_auth_bypass_for_testing:
            return _build_test_user(token)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bearer token",
        )
    return user


async def require_websocket_user(websocket: WebSocket) -> AuthenticatedUser:
    authorization = websocket.headers.get("authorization")
    token = _parse_bearer_token(authorization)
    if not token:
        token = websocket.query_params.get("token")

    if not token:
        if settings.session_auth_bypass_for_testing:
            return _build_test_user()
        await websocket.close(code=4401, reason="Missing bearer token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
        )

    user = await token_validator.validate_token(token)
    if not user:
        if settings.session_auth_bypass_for_testing:
            return _build_test_user(token)
        await websocket.close(code=4401, reason="Invalid bearer token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bearer token",
        )
    return user
