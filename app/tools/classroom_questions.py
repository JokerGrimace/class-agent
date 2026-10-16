"""把模型生成的题目交给当前页面题卡表单；本模块不调用 Java 写接口。"""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from app.core.tool.registry import tool
from app.core.tool.types import ToolResult


class ClassroomQuestionDraft(BaseModel):
    # 草稿使用纯文本；课程、目录和课堂身份由前端真实上下文决定，不允许模型填写。
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)
    type: Literal["选择题", "判断题", "简答题"]
    title: str = Field(min_length=1, max_length=128)
    prompt: str = Field(min_length=1)
    options: list[str]
    correctAnswer: str = Field(min_length=1)
    analysis: str

    @model_validator(mode="after")
    def validate_answer(self):
        # 先校验四选一、判断和简答的答案，再触发前端填表，避免丢失答案或选项。
        if self.type == "选择题":
            if len(self.options) != 4 or any(not option for option in self.options):
                raise ValueError("选择题必须提供四个非空选项")
            if self.correctAnswer not in ("A", "B", "C", "D"):
                raise ValueError("选择题答案必须为 A、B、C、D 之一")
        elif self.options:
            raise ValueError("判断题和简答题的 options 必须为空数组")
        if self.type == "判断题" and self.correctAnswer not in ("正确", "错误"):
            raise ValueError("判断题答案必须为正确或错误")
        return self


# 工具接收已完成的题目草稿，生题依据和读取顺序由任务意图及 skill 决定。
@tool(
    name="prepare_classroom_questions",
    description=("生成题目后调用本工具，把完整草稿交给当前页面的题卡表单；备课页编辑保存，课堂页现场发布。"
                 "用户要求生成题目或创建新问题时使用；题目内容由 question-builder 结合用户意图和相关上下文生成。"
                 "支持四选一选择题、判断题、简答题。参数全部为纯文本，options 只放选项正文。"
                 "本工具不创建、不发布题目；由老师在对应页面检查并点击保存或发布问题。"),
    parameters={"type": "object", "required": ["questions"], "additionalProperties": False,
                "properties": {"questions": {"type": "array", "minItems": 1, "maxItems": 10,
                                             "items": ClassroomQuestionDraft.model_json_schema()}}},
    visibility={"tool_types": ["level1"]},
)
async def prepare_classroom_questions(questions: list[dict]) -> ToolResult:
    # 页面可见性由 agent_python_tool_catalog 过滤；本函数只校验草稿并传给前端，不写数据库。
    if not isinstance(questions, list) or not 1 <= len(questions) <= 10:
        return ToolResult(success=False, error="每次请生成 1 至 10 道问题。")
    try:
        drafts = [ClassroomQuestionDraft.model_validate(item).model_dump() for item in questions]
    except ValidationError as exc:
        return ToolResult(success=False, error=f"问题草稿不完整，请修正后重新提交：{exc}")
    return ToolResult(success=True,
                      content=f"已将 {len(drafts)} 道问题交给当前页面的题卡表单，尚未创建或发布。备课页请老师检查后保存，课堂页请老师检查后发布；不要再请求确认或调用创建、发布工具。",
                      meta={"assistant_payload": {"type": "classroom_question_drafts", "questions": drafts}})
