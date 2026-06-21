from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.tools.iclass_c_file.minio_service import query_exist_file
from app.tools.iclass_d_resource.prase_file import (
    ConvertUploadRequest,
    convert_upload_to_html_file,
)

router = APIRouter(prefix="/resource", tags=["resource"])


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
