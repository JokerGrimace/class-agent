import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.auth import require_websocket_user
from app.core.iclass.auth_context import set_current_iclass_api_token, reset_current_iclass_api_token
from app.core.agent.engine import AgentEngine
from app.core.llm.factory import create_llm_adapter
from app.core.llm.user_context import reset_current_model_user, set_current_model_user
from app.core.session.manager import session_manager
from app.core.streaming.handler import StreamHandler

router = APIRouter()


@router.websocket("/{session_id}")
async def agent_websocket(websocket: WebSocket, session_id: str):
    await websocket.accept()
    current_user = await require_websocket_user(websocket)

    session = session_manager.get_session(
        session_id,
        user_id=current_user.user_id,
        user_role=current_user.user_role,
    )
    if not session:
        session = session_manager.create_session(
            user_id=current_user.user_id,
            user_role=current_user.user_role,
        )

    llm = create_llm_adapter()
    engine = AgentEngine(llm)

    # WebSocket 与 HTTP 使用同一已认证凭证上下文；连接退出时恢复，避免跨连接串用身份。
    token_context = set_current_iclass_api_token(current_user.token)
    # 模型身份与连接共存，连接内启动的 workflow 子任务会继承该上下文。
    model_user_context = set_current_model_user(
        current_user.user_id,
        current_user.user_role,
    )
    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)

            if msg.get("type") == "message":
                user_content = msg.get("content", "")
                session.add_user_message(user_content)
                session_manager.update_session(session)

                async for event in engine.run(session, stream=True):
                    ws_msg = StreamHandler.format_ws_message(event)
                    await websocket.send_json(ws_msg)

                    if event.type == "done":
                        break

    except WebSocketDisconnect:
        pass
    finally:
        reset_current_model_user(model_user_context)
        reset_current_iclass_api_token(token_context)
