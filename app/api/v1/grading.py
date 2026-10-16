import json
import math
from typing import Any, Optional

from anyio import to_thread
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

from app.core.auth import AuthenticatedUser, require_authenticated_user
from app.core.iclass.auth_context import (
    reset_current_iclass_api_token,
    set_current_iclass_api_token,
)
from app.core.llm.adapter import Message
from app.core.llm.factory import create_llm_adapter
from app.core.llm.user_context import reset_current_model_user, set_current_model_user
from app.tools.iclass_d_resource.prase_file import (
    SUPPORTED_HTML_SUFFIXES,
    _MAX_CONTENT_CHUNK_LIMIT,
    download_minio_file_bytes,
    parse_bytes_to_html,
    resolve_suffix,
)

router = APIRouter(prefix="/agent", tags=["agent"])


class GradeAnswerFile(BaseModel):
    file_name: str = Field(min_length=1)
    file_path: str = Field(min_length=1)


class GradeAnswerRequest(BaseModel):
    question: str = Field(min_length=1)
    answer: Optional[str] = None
    max_score: float = Field(gt=0)
    reference_answer: Optional[str] = None
    rubric: Optional[str] = None
    answer_files: list[GradeAnswerFile] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_answer_or_files(self) -> "GradeAnswerRequest":
        if not (self.answer and self.answer.strip()) and not self.answer_files:
            raise ValueError("answer or answer_files is required")
        return self


class GradeAnswerResponse(BaseModel):
    score: float
    answer_result: int
    feedback: str = Field(min_length=1, max_length=1000)


def _build_grading_messages(
    request: GradeAnswerRequest,
    parsed_answer_files: list[dict[str, Any]],
) -> list[Message]:
    # 课堂问题和作业都需要复用同一套评分语义，因此把题目、答案、满分和可选评分依据集中传给模型。
    grading_input = {
        "question": request.question,
        "answer": request.answer or "",
        "answer_files": parsed_answer_files,
        "max_score": request.max_score,
        "reference_answer": request.reference_answer,
        "rubric": request.rubric,
    }
    return [
        Message(
            role="system",
            content=(
                "你是教学场景中的简答题批改器。"
                "请根据题目、学生答案、满分以及可选参考答案和评分标准进行评分。"
                "如果 answer_files 中有解析后的文件内容，也必须作为学生答案的一部分一起评分。"
                "answer_result 只能使用 1（正确）、2（错误）、3（部分正确）。"
                "score 必须是 0 到 max_score 之间的数字。"
                "feedback 必须是一段简洁、具体、面向学生的中文评语。"
                "只能返回一个 JSON 对象，不要返回 Markdown、代码块或额外解释。"
                'JSON 格式必须是 {"score": number, "answer_result": 1|2|3, "feedback": "string"}。'
            ),
        ),
        Message(
            role="user",
            content=json.dumps(grading_input, ensure_ascii=False),
        ),
    ]


def _parse_answer_file(answer_file: GradeAnswerFile) -> dict[str, Any]:
    file_name = answer_file.file_name.strip()
    file_path = answer_file.file_path.strip()
    if not file_name or not file_path:
        raise HTTPException(status_code=422, detail="answer file fields must not be blank")
    suffix = resolve_suffix(file_name, file_path)
    if suffix not in SUPPORTED_HTML_SUFFIXES:
        raise HTTPException(status_code=422, detail=f"Unsupported answer file suffix: {suffix}")

    try:
        html_content = parse_bytes_to_html(
            download_minio_file_bytes(file_path),
            suffix,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Agent grading answer file parse failed") from exc

    truncated = len(html_content) > _MAX_CONTENT_CHUNK_LIMIT
    parsed_content = html_content[:_MAX_CONTENT_CHUNK_LIMIT]
    return {
        "file_name": file_name,
        "file_path": file_path,
        "file_type": suffix,
        "parsed_content": parsed_content,
        "truncated": truncated,
        "total_chars": len(html_content),
    }


async def _parse_answer_files(answer_files: list[GradeAnswerFile]) -> list[dict[str, Any]]:
    parsed_answer_files = []
    for answer_file in answer_files:
        parsed_answer_files.append(await to_thread.run_sync(_parse_answer_file, answer_file))
    return parsed_answer_files


def _parse_grading_result(content: str, max_score: float) -> GradeAnswerResponse:
    # 模型输出属于外部数据，先解析并校验结构，避免把错误结果直接写入业务成绩表。
    raw_content = (content or "").strip()
    if raw_content.startswith("```") and raw_content.endswith("```"):
        raw_content = raw_content.split("\n", 1)[-1]
        raw_content = raw_content.rsplit("```", 1)[0].strip()
    try:
        payload = json.loads(raw_content)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="Agent grading result is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="Agent grading result must be a JSON object")

    try:
        score = float(payload["score"])
        answer_result = int(payload["answer_result"])
        feedback = str(payload["feedback"]).strip()
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="Agent grading result fields are invalid") from exc

    if (
        not math.isfinite(score)
        or score < 0
        or score > max_score
        or answer_result not in {1, 2, 3}
        or not feedback
        or len(feedback) > 1000
    ):
        raise HTTPException(status_code=502, detail="Agent grading result is out of range")
    return GradeAnswerResponse(
        score=score,
        answer_result=answer_result,
        feedback=feedback,
    )


@router.post("/grade", response_model=GradeAnswerResponse)
async def grade_answer(
    request: GradeAnswerRequest,
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
) -> GradeAnswerResponse:
    # 该接口只负责一次结构化模型批改，数据库写回和批量并发由 iClass 后端负责。
    parsed_answer_files = await _parse_answer_files(request.answer_files)
    # 独立评分入口同样绑定认证身份，避免绕过主 Agent 的用户级凭证服务。
    token_context = set_current_iclass_api_token(current_user.token)
    model_user_context = set_current_model_user(
        current_user.user_id,
        current_user.user_role,
    )
    try:
        llm_event = await create_llm_adapter().chat_complete(
            _build_grading_messages(request, parsed_answer_files),
            tools=None,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Agent grading request failed") from exc
    finally:
        reset_current_model_user(model_user_context)
        reset_current_iclass_api_token(token_context)
    return _parse_grading_result(llm_event.content, request.max_score)
