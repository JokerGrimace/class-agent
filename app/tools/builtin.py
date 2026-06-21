import asyncio
import locale
import os
import subprocess
from pathlib import Path

from app.core.tool.registry import tool
from app.core.tool.types import ToolResult


def _candidate_console_encodings() -> list[str]:
    candidates = ["utf-8", "utf-8-sig"]
    preferred = locale.getpreferredencoding(False)
    if preferred:
        candidates.append(preferred)
    if os.name == "nt":
        candidates.extend(["mbcs", "cp936", "gb18030"])
    seen: set[str] = set()
    normalized: list[str] = []
    for encoding in candidates:
        key = (encoding or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        normalized.append(encoding)
    return normalized


def _decode_console_output(data: bytes) -> str:
    if not data:
        return ""
    for encoding in _candidate_console_encodings():
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


@tool(
    name="exec",
    description="Execute a shell command and return the output",
    parameters={
        "type": "object",
        "properties": {
            "cmd": {"type": "string", "description": "The shell command to execute"},
            "timeout": {"type": "integer", "description": "Timeout in seconds", "default": 30},
        },
        "required": ["cmd"],
    },
)
async def exec_command(cmd: str, timeout: int = 30) -> ToolResult:
    try:
        result = await asyncio.wait_for(
            asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            ),
            timeout=timeout,
        )
        stdout, stderr = await result.communicate()
        output = _decode_console_output(stdout)
        err = _decode_console_output(stderr)
        if result.returncode != 0:
            return ToolResult(success=False, content=output, error=err)
        return ToolResult(success=True, content=output)
    except asyncio.TimeoutError:
        return ToolResult(success=False, error=f"Command timed out after {timeout}s", timed_out=True)
    except Exception as e:
        return ToolResult(success=False, error=str(e))


@tool(
    name="read_file",
    description="Read the contents of a file. Use offset and limit to read specific sections when content is truncated.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file to read"},
            "offset": {"type": "integer", "description": "Line number to start reading from (0-indexed)", "default": 0},
            "limit": {"type": "integer", "description": "Maximum number of lines to read", "default": 100},
        },
        "required": ["path"],
    },
)
async def read_file(path: str, offset: int = 0, limit: int = 100) -> ToolResult:
    try:
        file_path = Path(path)
        if not file_path.exists():
            return ToolResult(success=False, error=f"File not found: {path}")
        if not file_path.is_file():
            return ToolResult(success=False, error=f"Not a file: {path}")

        def _read():
            content = file_path.read_text("utf-8", errors="replace")
            lines = content.split("\n")
            total_lines = len(lines)

            if offset > 0:
                lines = lines[offset:]

            if len(lines) > limit:
                shown = lines[:limit]
                remaining = len(lines) - limit
                suffix = f"\n... ({remaining} more lines after offset={offset}) [total={total_lines} lines]"
                content = "\n".join(shown) + suffix
            else:
                content = "\n".join(lines)
            return content

        content = await asyncio.to_thread(_read)
        return ToolResult(success=True, content=content)
    except Exception as e:
        return ToolResult(success=False, error=str(e))


@tool(
    name="write_file",
    description="Write content to a file",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file to write"},
            "content": {"type": "string", "description": "Content to write"},
        },
        "required": ["path", "content"],
    },
)
async def write_file(path: str, content: str) -> ToolResult:
    try:
        file_path = Path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(file_path.write_text, content, "utf-8")
        return ToolResult(success=True, content=f"Written to {path}")
    except Exception as e:
        return ToolResult(success=False, error=str(e))


def register_builtin_tools() -> None:
    import app.tools.builtin as _
