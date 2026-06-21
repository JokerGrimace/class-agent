import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.auth import require_websocket_user
from app.core.agent.engine import AgentEngine
from app.core.llm.factory import create_llm_adapter
from app.core.session.manager import session_manager
from app.core.streaming.handler import StreamHandler

router = APIRouter()


@router.websocket("/{session_id}")
async def agent_websocket(websocket: WebSocket, session_id: str):
    await websocket.accept()
    current_user = await require_websocket_user(websocket)

    session = session_manager.get_session(session_id, user_id=current_user.user_id)
    if not session:
        session = session_manager.create_session(user_id=current_user.user_id)

    llm = create_llm_adapter()
    engine = AgentEngine(llm)

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
