"""Upload persistence helpers for the RAG tool.

The web layer may pass a FastAPI ``UploadFile`` here, but this module only
relies on the small ``filename``/``file`` protocol so it can also be used from
scripts, workers, and tests.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import BinaryIO, Optional, Protocol


# 上传文件属于 RAG 模块自身数据，不能混入项目现有的 data/ 或 app/data/。
RAG_ROOT = Path(__file__).resolve().parent
DEFAULT_UPLOAD_DIR = RAG_ROOT / "data" / "uploaded_files"

# 白名单与 SmartDocumentProcessor 当前实际分支保持一致，避免上传成功后才因解析器不支持而失败。
SUPPORTED_RAG_UPLOAD_SUFFIXES = frozenset({
    ".csv",
    ".docx",
    ".md",
    ".pdf",
    ".pptx",
    ".txt",
})


class UploadLike(Protocol):
    filename: Optional[str]
    file: BinaryIO


# RAG 解析器按文件后缀选择处理流程，因此这里统一做大小写无关的扩展名校验并返回标准后缀。
def validate_rag_upload_file_type(file_name: str) -> str:
    suffix = Path(str(file_name or "")).suffix.lower()
    if suffix not in SUPPORTED_RAG_UPLOAD_SUFFIXES:
        supported = ", ".join(sorted(SUPPORTED_RAG_UPLOAD_SUFFIXES))
        display_suffix = suffix or "<missing>"
        raise ValueError(
            f"Unsupported RAG file type: {display_suffix}. Supported types: {supported}"
        )
    return suffix


def persist_uploaded_file(
    upload: UploadLike,
    file_id: str,
    upload_dir: Path | str = DEFAULT_UPLOAD_DIR,
) -> Path:
    """Persist one uploaded file under ``<upload_dir>/<file_id>/``."""
    safe_file_id = Path(str(file_id or "")).name
    if not safe_file_id or safe_file_id in {".", ".."} or safe_file_id != str(file_id):
        raise ValueError("file_id must be a single safe path segment")

    original_name = Path(upload.filename or "upload.bin").name
    # 在创建目录和写入磁盘前拒绝不支持的类型，避免产生永远无法入库的孤立文件。
    validate_rag_upload_file_type(original_name)
    target_dir = Path(upload_dir) / safe_file_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / original_name

    with target_path.open("wb") as buffer:
        shutil.copyfileobj(upload.file, buffer)

    return target_path
