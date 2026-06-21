from fastapi import APIRouter

from app.api.v1 import agent, resource, session, tool

router = APIRouter()

router.include_router(agent.router)
router.include_router(session.router)
router.include_router(tool.router)
router.include_router(resource.router)
