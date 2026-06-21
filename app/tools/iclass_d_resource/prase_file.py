import base64
import re
from io import BytesIO
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from app.core.tool.registry import tool
from app.core.tool.types import ToolResult
from app.core.file_cache import get_file_content_cache
from app.tools.iclass_c_file.minio_service import (
    download_minio_file_bytes,
    upload_html_for_source_file,
)

SUPPORTED_HTML_SUFFIXES = {
    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx",
    "txt", "png", "jpg", "jpeg", "md", "csv",
}

class ConvertUploadRequest(BaseModel):
    course_code: str
    teacher_code: str
    file_path: str
    file_name: str
    file_extra: Optional[str] = None
    source: Optional[str] = None
    trigger_type: Optional[str] = None


def parse_bytes_to_html(file_bytes: bytes, suffix: str) -> str:
    from docling.document_converter import DocumentConverter
    from docling_core.types.io import DocumentStream
    bio = BytesIO(file_bytes)
    bio.name = f"tmp.{suffix}"
    source = DocumentStream(name=bio.name, stream=bio)
    converter = DocumentConverter()

    result = converter.convert(source)
    return result.document.export_to_html()




def parse_temp_file_bytes(file_name: str, file_bytes: bytes) -> dict[str, object]:
    suffix = resolve_suffix(file_name, file_name)
    if suffix not in SUPPORTED_HTML_SUFFIXES:
        raise ValueError(f"Unsupported file suffix: {suffix}")
    html_text = parse_bytes_to_html(file_bytes, suffix)
    return {
        "success": True,
        "file_name": file_name,
        "file_type": suffix,
        "html_content": html_text,
     }


async def convert_upload_to_html_file(request: ConvertUploadRequest) -> dict[str, object]:
    suffix = resolve_suffix(request.file_name, request.file_path)
    if suffix not in SUPPORTED_HTML_SUFFIXES:
        raise ValueError(f"Unsupported file suffix: {suffix}")

    file_bytes = download_minio_file_bytes(request.file_path)
    html_text = parse_bytes_to_html(file_bytes, suffix)
    html_file_path = upload_html_for_source_file(request.file_path, html_text)

    return {
        "success": True,
        "source_file_path": request.file_path,
        "html_file_path": str(html_file_path),
    }


@tool(
    name="parse_bytes_to_html",
    description=(
        "Parse raw file bytes into HTML using docling. "
        "Pass file_bytes as base64 text and suffix as the file extension without the dot."
    ),
    parameters={
        "type": "object",
        "properties": {
            "file_bytes": {
                "type": "string",
                "description": "Raw file bytes encoded as base64 text.",
            },
            "suffix": {
                "type": "string",
                "description": "File suffix only, which is the file extension without the dot, for example pdf or docx.",
            },
        },
        "required": ["file_bytes", "suffix"],
        "additionalProperties": False,
    },
)
async def parse_bytes_to_html_tool(file_bytes: str, suffix: str) -> ToolResult:
    try:
        raw_file_bytes = base64.b64decode(file_bytes)
        html_text = parse_bytes_to_html(raw_file_bytes, suffix)
        return ToolResult(
            success=True,
            content=html_text,
            meta={
                "file_type": suffix,
                "html_content": html_text,
             },
        )
    except Exception as exc:
        return ToolResult(success=False, error=str(exc))


@tool(
    name="convert_upload_to_html_file",
    description=(
        "Convert a MinIO source file into an HTML derivative and return the generated HTML file path. "
        "Use this when a workflow needs an uploaded teaching file normalized into HTML."
    ),
    parameters={
        "type": "object",
        "properties": {
            "course_code": {
                "type": "string",
                "description": "Course code associated with the uploaded source file.",
            },
            "teacher_code": {
                "type": "string",
                "description": "Teacher code associated with the uploaded source file.",
            },
            "file_path": {
                "type": "string",
                "description": "MinIO object key for the source file.",
            },
            "file_name": {
                "type": "string",
                "description": "Original file name with suffix.",
            },
            "file_extra": {
                "type": "string",
                "description": "Optional extra metadata for the source file.",
            },
            "source": {
                "type": "string",
                "description": "Optional source tag describing where the file came from.",
            },
            "trigger_type": {
                "type": "string",
                "description": "Optional trigger type describing how the conversion was initiated.",
            },
        },
        "required": ["course_code", "teacher_code", "file_path", "file_name"],
        "additionalProperties": False,
    },
)
async def convert_upload_to_html_file_tool(
    course_code: str,
    teacher_code: str,
    file_path: str,
    file_name: str,
    file_extra: Optional[str] = None,
    source: Optional[str] = None,
    trigger_type: Optional[str] = None,
) -> ToolResult:
    try:
        result = await convert_upload_to_html_file(
            ConvertUploadRequest(
                course_code=course_code,
                teacher_code=teacher_code,
                file_path=file_path,
                file_name=file_name,
                file_extra=file_extra,
                source=source,
                trigger_type=trigger_type,
            )
        )
        return ToolResult(
            success=True,
            content=str(result.get("html_file_path", "")),
            meta=result,
        )
    except Exception as exc:
        return ToolResult(success=False, error=str(exc))


@tool(
    name="read_cached_file_content",
    description=(
        "Read a converted file's full HTML content from Redis by file_cache_key. "
        "Use this when the prompt only contains file metadata or a truncated preview."
    ),
    parameters={
        "type": "object",
        "properties": {
            "file_cache_key": {
                "type": "string",
                "description": "Redis cache key returned by /v1/agent/parse-temp-file.",
            },
            # "offset": {
            #     "type": "integer",
            #     "description": "Character offset to start reading from.",
            #     "default": 0,
            # },
            # "limit": {
            #     "type": "integer",
            #     "description": "Maximum number of characters to return.",
            #     "default": 6000,
            # },
        },
        "required": ["file_cache_key"],
        "additionalProperties": False,
    },
)
async def read_cached_file_content(
    file_cache_key: str,
    offset: int = 0,
    limit: int = 6000,
) -> ToolResult:
    try:
        if not file_cache_key or not file_cache_key.strip():
            return ToolResult(success=False, error="file_cache_key must not be blank")
        if offset < 0:
            return ToolResult(success=False, error="offset must be greater than or equal to 0")
        if limit <= 0:
            return ToolResult(success=False, error="limit must be greater than 0")

        cached_file = get_file_content_cache().get(file_cache_key)
        if cached_file is None:
            return ToolResult(success=False, error=f"Cached file not found: {file_cache_key}")

        content = cached_file.html_content
        total_chars = len(content)
        chunk = content[offset:total_chars]
        # truncated = offset + limit < total_chars
        # if truncated:
        #     chunk = (
        #         f"{chunk}\n"
        #         f"... ({total_chars - offset - limit} more chars after offset={offset}) "
        #         f"[total={total_chars} chars]"
        #     )

        return ToolResult(
            success=True,
            content=chunk,
            meta={
                "file_cache_key": file_cache_key,
                "file_id": cached_file.file_id,
                "file_name": cached_file.file_name,
                "file_type": cached_file.file_type,
                "offset": offset,
                "limit": limit,
                "total_chars": total_chars,
                # "truncated": truncated,
            },
        )
    except Exception as exc:
        return ToolResult(success=False, error=str(exc))


def resolve_suffix(file_name: str, file_path: str) -> str:
    suffix = Path(file_name or "").suffix.lower().lstrip(".")
    if suffix:
        return suffix
    return Path(file_path or "").suffix.lower().lstrip(".")


def resolve_html(file_path: str):
    file_bytes = download_minio_file_bytes(file_path)
    html_text = file_bytes.decode("utf-8")
    return html_text


if __name__ == '__main__':
    print("===== 场景1：本地PPT文件解析 =====")
    test_ppt_path = r"答辩PPT.pptx"  # 替换为你的本地PPT路径
    try:
        with open(test_ppt_path, "rb") as f:
            ppt_bytes = f.read()
        # 调用解析函数
        result = parse_temp_file_bytes("demo.pptx", ppt_bytes)
        print(f"解析成功：{result['success']}")
        print(f"文件类型：{result['file_type']}")
        # 保存HTML到本地（可选）
        with open("demo_ppt.html", "w", encoding="utf-8") as f:
            f.write(result["html_content"])
        print("HTML已保存到 demo_ppt.html\n")
    except Exception as e:
        print(f"场景1失败：{str(e)}\n")
