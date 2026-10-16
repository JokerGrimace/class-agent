from __future__ import annotations

import asyncio
import secrets
import uuid
from typing import Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, HTTPException, Query, UploadFile
from pydantic import BaseModel, ValidationError

from app.core.auth import AuthenticatedUser, require_authenticated_user
from app.core.rag.background_ingestion import import_persisted_upload_in_background
from app.core.rag.upload import persist_uploaded_file
from app.core.rag.management import (SERVICE_HEADER, get_knowledge_manager, management_service_token,
                                     rag_management_lifespan)
from app.core.rag.schemas import KnowledgeScope, KnowledgeTask, KnowledgeTaskResult, KnowledgeChunkPage
from app.core.rag.task_store import TaskConflict, TaskNotFound


router = APIRouter(prefix="/rag", tags=["rag"], lifespan=rag_management_lifespan)


class RAGUploadAcceptedResponse(BaseModel):
    success: bool
    status: Literal["accepted"]
    file_id: str
    file_name: str


# 独立上传接口兼容旧 RAG 的 multipart 字段名；只等待安全落盘，耗时摄取在响应后的后台任务中完成。
@router.post(
    "/upload",
    response_model=RAGUploadAcceptedResponse,
    status_code=202,
)
async def upload_rag_file(
    background_tasks: BackgroundTasks,
    upload_file: UploadFile = File(..., alias="uploadFile"),
    file_id: Optional[str] = Form(None, alias="fileId"),
    subject: Optional[str] = Form(None),
    course: Optional[str] = Form(None),
    teacher: Optional[str] = Form(None),
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
):
    del current_user
    final_file_id = str(file_id or "").strip() or uuid.uuid4().hex
    file_name = str(upload_file.filename or "").strip()

    try:
        # 个人/官方管理文件归属由 Java 建立；旧普通上传不能抢占或覆盖其逻辑 ID。
        if final_file_id.startswith(("pk_", "ok_", "managed_")):
            raise ValueError("fileId belongs to the managed RAG namespace")
        # 空文件无法形成有效切片，在写盘和排队之前快速拒绝；读取探针后复位文件游标。
        first_byte = await upload_file.read(1)
        await upload_file.seek(0)
        if not first_byte:
            raise ValueError("Uploaded file is empty")

        # UploadFile 会在请求结束后关闭，必须先把原文件完整落到 RAG 私有目录，再返回 202。
        await asyncio.to_thread(
            persist_uploaded_file,
            upload_file,
            final_file_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to persist RAG upload") from exc
    finally:
        await upload_file.close()

    # 元数据只保留非空检索范围字段；数据库名称、文件路径等系统字段由后台摄取核心生成。
    metadata = {
        key: value
        for key, value in {
            "subject": str(subject or "").strip(),
            "course": str(course or "").strip(),
            "teacher": str(teacher or "").strip(),
        }.items()
        if value
    }
    background_tasks.add_task(
        import_persisted_upload_in_background,
        file_id=final_file_id,
        metadata=metadata,
    )
    return RAGUploadAcceptedResponse(
        success=True,
        status="accepted",
        file_id=final_file_id,
        file_name=file_name,
    )


# 内部服务身份与普通用户身份分离，未配置管理密钥时 fail closed。
def require_rag_service(token: str | None = Header(None, alias=SERVICE_HEADER)):
    configured = management_service_token()
    if len(configured) < 32:
        raise HTTPException(status_code=503, detail="RAG management is not configured")
    if token is None or not secrets.compare_digest(token.encode("utf-8"), configured.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Invalid RAG service identity")


class RAGManagementAcceptedResponse(BaseModel):
    file_id: str
    task_version: int
    task_action: Literal["INGEST", "REMOVE"]
    task_state: str


# 精确归属错误不暴露文件路径和 ES 凭据，业务冲突与不存在保持可判断的 HTTP 状态。
def management_http_error(exc):
    if isinstance(exc, TaskConflict):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, TaskNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, (ValidationError, ValueError)):
        return HTTPException(status_code=400, detail="Invalid RAG management request")
    return HTTPException(status_code=503, detail="RAG management operation is unavailable")


# scope 参数来自可信 Java；路径 ID 不被重写，额外 file_id query 若提供则必须完全一致。
def management_scope(file_id: str, binding_type: Literal["PERSONAL", "OFFICIAL"],
                     binding_id: int = Query(..., gt=0), subject_code: str = Query(..., min_length=1, max_length=64),
                     teacher_code: str | None = Query(None), library_id: int | None = Query(None, gt=0),
                     department_id: int | None = Query(None, gt=0),
                     query_file_id: str | None = Query(None, alias="file_id")):
    try:
        if query_file_id is not None and query_file_id != file_id:
            raise ValueError("path and query file_id must match")
        return KnowledgeScope(binding_type=binding_type, binding_id=binding_id, file_id=file_id,
                              subject_code=subject_code, teacher_code=teacher_code, library_id=library_id,
                              department_id=department_id)
    except (ValidationError, ValueError) as exc:
        raise management_http_error(exc) from exc


@router.post("/manage/ingest", status_code=202, response_model=RAGManagementAcceptedResponse,
             dependencies=[Depends(require_rag_service)])
async def manage_ingest(upload_file: UploadFile = File(..., alias="uploadFile"), context: str = Form(...)):
    try:
        # 大文件与原件落盘在线程中完成，202 只代表私有技术任务已经持久接收。
        task = KnowledgeTask.model_validate_json(context)
        if task.task_action != "INGEST":
            raise ValueError("task_action must be INGEST")
        first_byte = await upload_file.read(1)
        await upload_file.seek(0)
        if not first_byte:
            raise ValueError("Uploaded file is empty")
        return await asyncio.to_thread(get_knowledge_manager().submit, task, upload_file)
    except Exception as exc:
        raise management_http_error(exc) from exc
    finally:
        await upload_file.close()


@router.post("/manage/remove", status_code=202, response_model=RAGManagementAcceptedResponse,
             dependencies=[Depends(require_rag_service)])
async def manage_remove(task: KnowledgeTask):
    try:
        if task.task_action != "REMOVE":
            raise ValueError("task_action must be REMOVE")
        return await asyncio.to_thread(get_knowledge_manager().submit, task)
    except Exception as exc:
        raise management_http_error(exc) from exc


@router.get("/manage/files/{file_id}/chunks", response_model=KnowledgeChunkPage,
            dependencies=[Depends(require_rag_service)])
async def manage_chunks(scope: KnowledgeScope = Depends(management_scope),
                        active_version: int = Query(..., gt=0), page: int = Query(1, gt=0),
                        page_size: int = Query(20, gt=0, le=100)):
    try:
        if page * page_size > 10000:
            raise ValueError("requested page exceeds Elasticsearch result window")
        return await asyncio.to_thread(get_knowledge_manager().chunks, scope, active_version, page, page_size)
    except Exception as exc:
        raise management_http_error(exc) from exc


@router.get("/manage/tasks/{file_id}", response_model=KnowledgeTaskResult,
            dependencies=[Depends(require_rag_service)])
async def manage_task(scope: KnowledgeScope = Depends(management_scope), task_version: int = Query(..., gt=0)):
    try:
        return await asyncio.to_thread(get_knowledge_manager().store.result, scope, task_version)
    except Exception as exc:
        raise management_http_error(exc) from exc
