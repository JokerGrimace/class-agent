"""保存本轮消息的单页快照，正文与模型运行上下文分离。"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DisplaySnapshot(BaseModel):
    # 页面内容由实际预览器采集，页码统一使用从 1 开始的显示编号。
    model_config = ConfigDict(extra="forbid")
    file_path: str = Field(min_length=1, max_length=2048)
    file_name: str = Field(min_length=1, max_length=512)
    page_number: int = Field(ge=1, strict=True)
    page_count: int = Field(ge=1, strict=True)
    text: str = Field(max_length=100000)
    source: Literal["onlyoffice_slide", "onlyoffice_word", "pdf_page"]
    captured_at: str = Field(min_length=1, max_length=64)
    has_unread_visuals: bool = False

    @model_validator(mode="after")
    def validate_page(self):
        # 拒绝越界页码，不能默默换成第一页或整份文件。
        if self.page_number > self.page_count:
            raise ValueError("page_number exceeds page_count")
        return self


@dataclass
class MessageDisplaySnapshot:
    # 现有消息模型没有消息 ID；此标识由服务端为本次发送生成，不接受客户端指定。
    user_id: str
    session_id: str
    snapshot: DisplaySnapshot | None = None
    error: str | None = None
    message_id: str = field(default_factory=lambda: uuid4().hex, init=False)
    closed: bool = field(default=False, init=False)

    def __post_init__(self):
        # 固定入站内容，后续同一文件的翻页或另一条消息不能覆盖本轮副本。
        if self.snapshot is not None:
            self.snapshot = self.snapshot.model_copy(deep=True)

    def close(self):
        # 回答结束后释放正文；已经继承执行作用域的子任务也不能继续读取。
        self.closed = True
        self.snapshot = None
        self.error = None


current_message_display: ContextVar[MessageDisplaySnapshot | None] = ContextVar("message_display_snapshot", default=None)


@contextmanager
def bind_message_display(binding: MessageDisplaySnapshot | None):
    # ContextVar 只隔离 Python 执行任务，不进入 runtime_context、会话历史或提示词。
    # 空快照也建立独立作用域，避免新消息继承上一轮内容。
    token = current_message_display.set(binding)
    try:
        yield
    finally:
        if binding is not None:
            binding.close()
        current_message_display.reset(token)
