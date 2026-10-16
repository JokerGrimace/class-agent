import json
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import settings
from app.core.iclass.auth_context import get_current_iclass_api_token
from app.core.tool.registry import tool
from app.core.tool.types import ToolResult
from app.tools.iclass_d_resource import resource_common


_CREATE_FOLDER_PATH = "/resource/course/material/folder/create"


@dataclass
class ChapterList:
    chapter: str
    section: list[str]


@tool(
    name="build_to_chapter",
    description="Create chapter and section folders in course materials based on a parsed chapter outline.",
    parameters={
        "type": "object",
        "properties": {
            "course_code": {
                "type": "string",
                "description": "Course code associated with the course material.",
            },
            "teacher_code": {
                "type": "string",
                "description": "Teacher code associated with the course material.",
            },
            "chapter_list": {
                "type": "array",
                "description": "Ordered chapter outline used to create top-level chapter folders and their child section folders.",
                "items": {
                    "type": "object",
                    "properties": {
                        "chapter": {
                            "type": "string",
                            "description": "Top-level chapter folder name.",
                        },
                        "section": {
                            "type": "array",
                            "description": "Child section folder names under the chapter, created in order.",
                            "items": {
                                "type": "string",
                            },
                        },
                    },
                    "required": ["chapter", "section"],
                },
            },
        },
        "required": ["course_code", "teacher_code", "chapter_list"],
    },
    governance={
        "tool_tags": ["remote_api", "resource_management", "mutating", "create"],
    },
)
async def build_to_chapter(
    course_code: str,
    teacher_code: str,
    chapter_list: list[dict],
) -> ToolResult:
    action = "build_to_chapter"
    try:
        # build_to_chapter 是 Python 组合工具，在工具内直接调用创建目录接口。
        api_url = f"{settings.iclass_api_base_url.rstrip('/')}{_CREATE_FOLDER_PATH}"
        # 组合工具与动态工具使用同一认证边界，无真实用户凭证时不得创建远程目录。
        token = get_current_iclass_api_token().strip()
        if not token:
            raise ValueError("iClass API authentication required")
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}

        created_tree: list[dict[str, Any]] = []
        failed_items: list[dict[str, Any]] = []
        section_count = 0

        async with httpx.AsyncClient(timeout=settings.iclass_api_timeout_seconds) as client:
            # 章和节共用同一个请求契约：外层为 ApiRequest.body，课程编码字段为 subjectCode。
            async def create_folder(folder_name: str, parent_id: int) -> dict[str, Any]:
                response = await client.post(
                    api_url,
                    json={
                        "body": {
                            "teacherCode": teacher_code,
                            "subjectCode": course_code,
                            "parentId": parent_id,
                            "folderName": folder_name,
                        }
                    },
                    headers=headers,
                )
                response.raise_for_status()
                response_payload = response.json()
                if str(response_payload.get("code")) != "200":
                    raise RuntimeError(
                        response_payload.get("message") or "iclass api request failed"
                    )
                return response_payload.get("body")

            for chapter in chapter_list:
                chapter_name = chapter["chapter"]
                chapter_sections = chapter["section"]
                chapter_node = {
                    "chapter": chapter_name,
                    "folder_id": None,
                    "parent_id": -1,
                    "sections": [],
                }

                try:
                    chapter_result = await create_folder(chapter_name, -1)
                    chapter_node["folder_id"] = chapter_result["id"]
                except Exception as exc:
                    failed_items.append(
                        {
                            "type": "chapter",
                            "name": chapter_name,
                            "parent_id": -1,
                            "error": str(exc),
                        }
                    )
                    created_tree.append(chapter_node)
                    continue

                for section in chapter_sections:
                    try:
                        section_result = await create_folder(
                            section,
                            chapter_result["id"],
                        )
                        chapter_node["sections"].append(
                            {
                                "section": section,
                                "folder_id": section_result["id"],
                                "parent_id": chapter_result["id"],
                            }
                        )
                        section_count += 1
                    except Exception as exc:
                        failed_items.append(
                            {
                                "type": "section",
                                "name": section,
                                "parent_id": chapter_result["id"],
                                "chapter": chapter_name,
                                "error": str(exc),
                            }
                        )

                created_tree.append(chapter_node)

        payload = {
            "created_tree": created_tree,
            "failed_items": failed_items,
            "summary": {
                "chapter_count": len(chapter_list),
                "section_count": section_count,
            },
        }
        return ToolResult(
            success=len(failed_items) == 0,
            content=json.dumps(payload, ensure_ascii=False),
            meta={
                "action": action,
                "risk_level": resource_common.RISK_LEVELS[action],
                "body": payload,
                "assistant_payload": resource_common.build_assistant_payload(
                    reply_text="已完成章节目录构建",
                    action_type=action,
                    result_payload=payload,
                    refresh_directory=True,
                ),
            },
        )
    except Exception as exc:
        return ToolResult(
            success=False,
            error=str(exc),
            content=resource_common.json_content(
                {
                    "action": action,
                    "success": False,
                    "error": str(exc),
                }
            ),
            meta={
                "action": action,
                "risk_level": resource_common.RISK_LEVELS[action],
                "body": None,
                "assistant_payload": resource_common.build_assistant_payload(
                    reply_text=str(exc),
                    action_type=action,
                    result_payload=None,
                    refresh_directory=False,
                ),
            },
        )
