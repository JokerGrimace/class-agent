from fastapi import APIRouter

from app.api.v1 import agent, grading, rag, resource, session

router = APIRouter()

router.include_router(agent.router)
router.include_router(grading.router)
# RAG 二进制上传独立于 Java 资源接口，前端可并行调用并在文件落盘后立即获得 202。
router.include_router(rag.router)
router.include_router(session.router)
router.include_router(resource.router)
