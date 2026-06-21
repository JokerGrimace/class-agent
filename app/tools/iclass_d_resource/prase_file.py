import base64
import html
import importlib.util
import os
import re
import shutil
import tempfile
from io import BytesIO
from pathlib import Path
import sys
from typing import Optional

from pydantic import BaseModel

from app.config import settings
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

TEXT_HTML_SUFFIXES = {"txt", "md", "csv"}
_DOCLING_PARSE_RUNTIME_READY = False
_DEFAULT_CONTENT_CHUNK_LIMIT = 6000
_MAX_CONTENT_CHUNK_LIMIT = 20000

# os.environ["DOCLING_ARTIFACTS_PATH"] = r"/path/to/docling-models"
class ConvertUploadRequest(BaseModel):
    course_code: str
    teacher_code: str
    file_path: str
    file_name: str
    file_extra: Optional[str] = None
    source: Optional[str] = None
    trigger_type: Optional[str] = None


def parse_bytes_to_html(file_bytes: bytes, suffix: str) -> str:
    normalized_suffix = suffix.lower().lstrip(".")
    if normalized_suffix in TEXT_HTML_SUFFIXES:
        return parse_text_bytes_to_html(file_bytes)

    ensure_docling_parse_ascii_runtime_path()
    from docling.document_converter import DocumentConverter
    from docling_core.types.io import DocumentStream
    bio = BytesIO(file_bytes)
    bio.name = f"tmp.{normalized_suffix}"
    source = DocumentStream(name=bio.name, stream=bio)
    converter = build_document_converter(normalized_suffix)

    result = converter.convert(source)
    return result.document.export_to_html()


def parse_text_bytes_to_html(file_bytes: bytes) -> str:
    text = decode_text_bytes(file_bytes)
    return f"<pre>{html.escape(text)}</pre>"


def decode_text_bytes(file_bytes: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return file_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    return file_bytes.decode("utf-8", errors="replace")


def ensure_docling_parse_ascii_runtime_path() -> None:
    """Make docling_parse native PDF resources readable on Windows paths with non-ASCII text."""
    global _DOCLING_PARSE_RUNTIME_READY
    if _DOCLING_PARSE_RUNTIME_READY:
        return
    if os.name != "nt" or "docling_parse" in sys.modules:
        _DOCLING_PARSE_RUNTIME_READY = True
        return

    spec = importlib.util.find_spec("docling_parse")
    if spec is None or spec.origin is None:
        _DOCLING_PARSE_RUNTIME_READY = True
        return

    package_dir = Path(spec.origin).parent
    try:
        str(package_dir).encode("ascii")
        _DOCLING_PARSE_RUNTIME_READY = True
        return
    except UnicodeEncodeError:
        pass

    runtime_site = Path(tempfile.gettempdir()) / "openclaw_docling_runtime_site"
    runtime_package_dir = runtime_site / "docling_parse"
    shutil.copytree(package_dir, runtime_package_dir, dirs_exist_ok=True)
    runtime_site_text = str(runtime_site)
    if runtime_site_text not in sys.path:
        sys.path.insert(0, runtime_site_text)
    _DOCLING_PARSE_RUNTIME_READY = True


def resolve_docling_artifacts_path() -> Optional[Path]:
    configured_path = settings.docling_artifacts_path
    if configured_path is None:
        raw_path = os.environ.get("DOCLING_ARTIFACTS_PATH", "").strip()
        configured_path = Path(raw_path) if raw_path else None
    if configured_path is None:
        return None
    return configured_path if configured_path.is_dir() else None


def build_rapidocr_options(docling_artifacts_path: Optional[Path]):
    from docling.datamodel.pipeline_options import RapidOcrOptions

    if docling_artifacts_path is None:
        return RapidOcrOptions(backend="onnxruntime")

    rapidocr_path = docling_artifacts_path / "RapidOcr"
    model_paths = {
        "det_model_path": rapidocr_path / "onnx" / "PP-OCRv4" / "det" / "ch_PP-OCRv4_det_mobile.onnx",
        "cls_model_path": rapidocr_path / "onnx" / "PP-OCRv4" / "cls" / "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
        "rec_model_path": rapidocr_path / "onnx" / "PP-OCRv4" / "rec" / "ch_PP-OCRv4_rec_mobile.onnx",
        "rec_keys_path": rapidocr_path / "paddle" / "PP-OCRv4" / "rec" / "ch_PP-OCRv4_rec_mobile" / "ppocr_keys_v1.txt",
        "font_path": rapidocr_path / "resources" / "fonts" / "FZYTK.TTF",
    }
    if not all(model_path.is_file() for model_path in model_paths.values()):
        return RapidOcrOptions(backend="onnxruntime")

    return RapidOcrOptions(
        backend="onnxruntime",
        **{key: str(value) for key, value in model_paths.items()},
    )


def build_document_converter(suffix: str):
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.settings import settings as docling_settings
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    docling_artifacts_path = resolve_docling_artifacts_path()
    if docling_artifacts_path is not None:
        docling_settings.artifacts_path = docling_artifacts_path

    if suffix != "pdf":
        return DocumentConverter()

    pdf_pipeline_options = {
        "do_table_structure": True,
        "ocr_options": build_rapidocr_options(docling_artifacts_path),
    }
    if docling_artifacts_path is not None:
        pdf_pipeline_options["artifacts_path"] = str(docling_artifacts_path)

    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(
                pipeline_options=PdfPipelineOptions(**pdf_pipeline_options)
            )
        }
    )




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


def _read_content_chunk(
    html_content: str,
    *,
    offset: int,
    limit: int,
) -> tuple[str, bool]:
    if offset < 0:
        raise ValueError("offset must be greater than or equal to 0")
    if limit <= 0 or limit > _MAX_CONTENT_CHUNK_LIMIT:
        raise ValueError(f"limit must be between 1 and {_MAX_CONTENT_CHUNK_LIMIT}")

    total_chars = len(html_content)
    chunk = html_content[offset:offset + limit]
    return chunk, offset + limit < total_chars


@tool(
    name="read_course_material_content",
    description=(
        "Read and parse the currently selected preparation-workspace course material. "
        "Use the selected file_path and file_name from runtime context. "
        "The result contains parsed HTML content and a file_cache_key for later chunks."
    ),
    parameters={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "MinIO object key of the selected course material.",
            },
            "file_name": {
                "type": "string",
                "description": "Original name of the selected course material.",
            },
            "offset": {
                "type": "integer",
                "description": "Character offset in the parsed HTML content.",
                "default": 0,
                "minimum": 0,
            },
            "limit": {
                "type": "integer",
                "description": "Maximum parsed HTML characters to return in this call.",
                "default": _DEFAULT_CONTENT_CHUNK_LIMIT,
                "minimum": 1,
                "maximum": _MAX_CONTENT_CHUNK_LIMIT,
            },
        },
        "required": ["file_path", "file_name"],
        "additionalProperties": False,
    },
)
async def read_course_material_content(
    file_path: str,
    file_name: str,
    offset: int = 0,
    limit: int = _DEFAULT_CONTENT_CHUNK_LIMIT,
) -> ToolResult:
    try:
        normalized_file_path = file_path.strip() if isinstance(file_path, str) else ""
        normalized_file_name = file_name.strip() if isinstance(file_name, str) else ""
        if not normalized_file_path:
            return ToolResult(success=False, error="file_path must not be blank")
        if not normalized_file_name:
            return ToolResult(success=False, error="file_name must not be blank")

        suffix = resolve_suffix(normalized_file_name, normalized_file_path)
        if suffix not in SUPPORTED_HTML_SUFFIXES:
            return ToolResult(success=False, error=f"Unsupported file suffix: {suffix}")

        html_content = parse_bytes_to_html(
            download_minio_file_bytes(normalized_file_path),
            suffix,
        )
        cached_file = get_file_content_cache().put(
            file_name=normalized_file_name,
            file_type=suffix,
            html_content=html_content,
        )
        chunk, truncated = _read_content_chunk(
            html_content,
            offset=offset,
            limit=limit,
        )
        if truncated:
            remaining_chars = len(html_content) - offset - limit
            chunk = (
                f"{chunk}\n"
                f"... ({remaining_chars} more chars after offset={offset}) "
                f"[use read_cached_file_content with file_cache_key={cached_file.file_cache_key}, "
                f"offset={offset + limit}; total={len(html_content)} chars]"
            )

        return ToolResult(
            success=True,
            content=chunk,
            meta={
                "file_cache_key": cached_file.file_cache_key,
                "file_id": cached_file.file_id,
                "file_name": normalized_file_name,
                "file_type": suffix,
                "source_file_path": normalized_file_path,
                "offset": offset,
                "limit": limit,
                "total_chars": len(html_content),
                "truncated": truncated,
            },
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
            "offset": {
                "type": "integer",
                "description": "Character offset to start reading from.",
                "default": 0,
                "minimum": 0,
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of characters to return.",
                "default": _DEFAULT_CONTENT_CHUNK_LIMIT,
                "minimum": 1,
                "maximum": _MAX_CONTENT_CHUNK_LIMIT,
            },
        },
        "required": ["file_cache_key"],
        "additionalProperties": False,
    },
)
async def read_cached_file_content(
    file_cache_key: str,
    offset: int = 0,
    limit: int = _DEFAULT_CONTENT_CHUNK_LIMIT,
) -> ToolResult:
    try:
        if not file_cache_key or not file_cache_key.strip():
            return ToolResult(success=False, error="file_cache_key must not be blank")
        cached_file = get_file_content_cache().get(file_cache_key)
        if cached_file is None:
            return ToolResult(success=False, error=f"Cached file not found: {file_cache_key}")

        content = cached_file.html_content
        total_chars = len(content)
        chunk, truncated = _read_content_chunk(content, offset=offset, limit=limit)
        if truncated:
            remaining_chars = total_chars - offset - limit
            chunk = (
                f"{chunk}\n"
                f"... ({remaining_chars} more chars after offset={offset}) "
                f"[total={total_chars} chars]"
            )

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
                "truncated": truncated,
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
