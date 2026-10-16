from __future__ import annotations

import json
from typing import Optional


RISK_LEVELS = {
    "build_to_chapter": "high",
}


def json_content(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def build_assistant_payload(
    *,
    reply_text: str,
    action_type: str,
    result_payload: Optional[dict[str, object]],
    refresh_directory: bool,
    needs_confirmation: bool = False,
    pending_action: Optional[dict[str, object]] = None,
) -> dict[str, object]:
    return {
        "reply_text": reply_text,
        "action_type": action_type,
        "needs_confirmation": needs_confirmation,
        "pending_action": pending_action,
        "ui_refresh_hint": {
            "refresh_directory": refresh_directory,
        },
        "result_payload": result_payload,
    }
