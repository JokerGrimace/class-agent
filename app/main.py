from contextlib import asynccontextmanager
from pathlib import Path
import sys

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

PROJECT_ROOT = Path(__file__).resolve().parent.parent
project_root_text = str(PROJECT_ROOT)
if sys.path[0] != project_root_text:
    sys.path = [path for path in sys.path if path != project_root_text]
    sys.path.insert(0, project_root_text)

from app.config import settings
from app.api.v1.router import router as v1_router
from app.api.ws.agent_ws import router as ws_router
from app.core.session.manager import session_manager
from app.core.workspace import Workspace
from app.core.llm.factory import create_llm_adapter
from app.core.permission.service import get_permission_service
from app.core.workflow.runtime import configure_workflow_runtime


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_dirs()
    session_manager.initialize()
    workspace = Workspace(str(settings.workspace_dir))
    workspace.ensure_workspace()
    workflow_runtime = configure_workflow_runtime(
        llm_factory=lambda: create_llm_adapter(settings.llm_provider),
        permission_service=get_permission_service(),
    )
    workflow_runtime.manager.initialize()
    workflow_runtime.manager.fail_incomplete_runs_on_startup()
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

static_dir = PROJECT_ROOT / "static"
app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app", host="0.0.0.0",
        port=8000, reload=False, proxy_headers=False,
        access_log=True,
    )
