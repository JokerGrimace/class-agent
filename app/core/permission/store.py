import asyncio
from dataclasses import dataclass
from typing import Optional

from app.core.permission.types import PendingPermissionRequest, SessionPermissionGrant


@dataclass
class PendingPermissionHandle:
    request: PendingPermissionRequest
    future: asyncio.Future


class PermissionStore:
    def __init__(self) -> None:
        self._pending: dict[str, PendingPermissionHandle] = {}
        self._session_grants: dict[str, list[SessionPermissionGrant]] = {}

    def add_pending(self, handle: PendingPermissionHandle) -> None:
        self._pending[handle.request.request_id] = handle

    def get_pending(self, request_id: str) -> PendingPermissionHandle:
        return self._pending[request_id]

    def pop_pending(self, request_id: str) -> PendingPermissionHandle:
        return self._pending.pop(request_id)

    def list_pending(
        self,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> list[PendingPermissionRequest]:
        items = [handle.request for handle in self.list_pending_handles(session_id=session_id, user_id=user_id)]
        return items

    def list_pending_handles(
        self,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> list[PendingPermissionHandle]:
        items = list(self._pending.values())
        if session_id is not None:
            items = [item for item in items if item.request.session_id == session_id]
        if user_id is not None:
            items = [item for item in items if item.request.user_id == user_id]
        return items

    def find_pending_by_tool_call_id(
        self,
        *,
        session_id: str,
        user_id: str,
        tool_call_id: str,
    ) -> Optional[PendingPermissionRequest]:
        for handle in self._pending.values():
            request = handle.request
            if request.session_id != session_id:
                continue
            if request.user_id != user_id:
                continue
            if request.tool_call_id != tool_call_id:
                continue
            return request
        return None

    def add_grant(self, grant: SessionPermissionGrant) -> None:
        self._session_grants.setdefault(grant.session_id, []).append(grant)

    def is_granted(
        self,
        *,
        session_id: str,
        tool_name: str,
        tool_action: Optional[str],
    ) -> bool:
        grants = self._session_grants.get(session_id, [])
        for grant in grants:
            if grant.tool_name != tool_name:
                continue
            if grant.tool_action != tool_action:
                continue
            return True
        return False
