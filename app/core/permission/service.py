import asyncio
import logging
from copy import deepcopy
from typing import Any, Optional
from uuid import uuid4

from app.core.permission.store import PendingPermissionHandle, PermissionStore
from app.core.permission.types import (
    PendingPermissionRequest,
    PermissionReply,
    PermissionRequestCancelledError,
    SessionPermissionGrant,
)

_default_permission_service: Optional["PermissionService"] = None
logger = logging.getLogger(__name__)


class PermissionService:
    def __init__(
        self,
        store: Optional[PermissionStore] = None,
        timeout_seconds: int = 300,
    ) -> None:
        self.store = store or PermissionStore()
        self.timeout_seconds = timeout_seconds

    def _cancel_handle(self, handle: PendingPermissionHandle, reason: str) -> None:
        if handle.future.done():
            return
        handle.future.set_exception(PermissionRequestCancelledError(reason))

    async def ask(
        self,
        *,
        session_id: str,
        user_id: str,
        tool_call_id: str,
        tool_name: str,
        tool_action: Optional[str],
        summary: str,
        original_arguments: dict[str, Any],
        visible_arguments: dict[str, Any],
        editable_fields: list[str],
    ) -> PermissionReply:
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        request = PendingPermissionRequest(
            request_id=f"perm_{uuid4().hex[:16]}",
            session_id=session_id,
            user_id=user_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            tool_action=tool_action,
            summary=summary,
            original_arguments=deepcopy(original_arguments),
            visible_arguments=deepcopy(visible_arguments),
            editable_fields=list(editable_fields),
            allowed_actions=["once", "reject", "once_with_changes"]
            if editable_fields
            else ["once", "always", "reject"],
        )
        self.store.add_pending(PendingPermissionHandle(request=request, future=future))
        logger.info(
            "Registered permission request session_id=%s tool_call_id=%s request_id=%s tool_name=%s",
            session_id,
            tool_call_id,
            request.request_id,
            tool_name,
        )
        try:
            return await asyncio.wait_for(future, timeout=self.timeout_seconds)
        finally:
            try:
                self.store.pop_pending(request.request_id)
            except KeyError:
                pass

    def reply(
        self,
        request_id: str,
        *,
        user_id: str,
        reply: PermissionReply,
    ) -> None:
        try:
            handle = self.store.get_pending(request_id)
        except KeyError as exc:
            raise KeyError("Permission request not found") from exc
        request = handle.request
        if request.user_id != user_id:
            raise PermissionError("Permission request not found")
        if reply.action not in request.allowed_actions:
            raise ValueError("Unsupported permission reply action")
        invalid_fields = sorted(set(reply.edited_fields) - set(request.editable_fields))
        if invalid_fields:
            raise ValueError(f"Reply edited disallowed fields: {invalid_fields}")
        if handle.future.done():
            raise ValueError("Permission request already resolved")
        logger.info(
            "Resolving permission request session_id=%s tool_call_id=%s request_id=%s action=%s",
            request.session_id,
            request.tool_call_id,
            request.request_id,
            reply.action,
        )
        # 唤醒等待中的 ask()
        handle.future.set_result(reply)

    def list_pending(
        self,
        *,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> list[PendingPermissionRequest]:
        return self.store.list_pending(session_id=session_id, user_id=user_id)

    def find_pending_by_tool_call_id(
        self,
        *,
        session_id: str,
        user_id: str,
        tool_call_id: str,
    ) -> Optional[PendingPermissionRequest]:
        return self.store.find_pending_by_tool_call_id(
            session_id=session_id,
            user_id=user_id,
            tool_call_id=tool_call_id,
        )

    def cancel_session_requests(
        self,
        *,
        session_id: str,
        user_id: Optional[str] = None,
        reason: str,
    ) -> int:
        handles = self.store.list_pending_handles(session_id=session_id, user_id=user_id)
        cancelled = 0
        for handle in handles:
            try:
                popped = self.store.pop_pending(handle.request.request_id)
            except KeyError:
                continue
            logger.warning(
                "Cancelling permission request session_id=%s tool_call_id=%s request_id=%s reason=%s",
                popped.request.session_id,
                popped.request.tool_call_id,
                popped.request.request_id,
                reason,
            )
            self._cancel_handle(popped, reason)
            cancelled += 1
        return cancelled

    def add_session_grant(
        self,
        *,
        session_id: str,
        tool_name: str,
        tool_action: Optional[str],
    ) -> None:
        self.store.add_grant(
            SessionPermissionGrant(
                session_id=session_id,
                tool_name=tool_name,
                tool_action=tool_action,
            )
        )

    def is_session_granted(
        self,
        session_id: str,
        tool_name: str,
        tool_action: Optional[str],
    ) -> bool:
        return self.store.is_granted(
            session_id=session_id,
            tool_name=tool_name,
            tool_action=tool_action,
        )


def get_permission_service() -> PermissionService:
    global _default_permission_service
    if _default_permission_service is None:
        _default_permission_service = PermissionService()
    return _default_permission_service
