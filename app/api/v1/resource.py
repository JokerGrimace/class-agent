from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.auth import require_authenticated_user
from app.tools.iclass_c_file.minio_service import query_exist_file
from app.tools.iclass_d_resource.prase_file import (
    ConvertUploadRequest,
    convert_upload_to_html_file,
)

# 文件转换会读取和写入对象存储，所有资源接口必须先完成身份认证。
router = APIRouter(prefix="/resource", tags=["resource"], dependencies=[Depends(require_authenticated_user)])


class ConvertUploadResponse(BaseModel):
    success: bool
    source_file_path: str
    html_file_path: str


@router.post("/convert-upload", response_model=ConvertUploadResponse)
async def convert_upload(request: ConvertUploadRequest):
    try:
        result = await convert_upload_to_html_file(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return ConvertUploadResponse(**result)



