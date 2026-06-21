from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.api.v1.router import router as v1_router
from app.api.ws.agent_ws import router as ws_router
from app.core.session.manager import session_manager
from app.tools.builtin import register_builtin_tools
import app.tools.expand as _expand
import app.tools.web_fetch as _web_fetch
import app.tools.web_search as _web_search
import app.tools.workflow_tools as _workflow_tools
from app.core.workspace import Workspace


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_dirs()
    session_manager.initialize()
    register_builtin_tools()
    workspace = Workspace(str(settings.workspace_dir))
    workspace.ensure_workspace()
    yield


app = FastAPI(
    title="OpenClaw FastAPI",
    description="Agent interaction loop implementation",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(v1_router, prefix="/v1")
app.include_router(ws_router, prefix="/v1/ws")


@app.get("/v1/health")
async def health():
    return {"status": "ok"}

static_dir = Path(__file__).resolve().parent.parent / "static"
app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)
