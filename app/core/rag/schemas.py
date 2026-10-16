from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


# 新管理契约只接受完整、准确的可信归属，未知字段不能绕过服务端固定范围。
class KnowledgeScope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    binding_type: Literal["PERSONAL", "OFFICIAL"]
    binding_id: int = Field(gt=0)
    file_id: str = Field(min_length=4, max_length=128)
    subject_code: str = Field(min_length=1, max_length=64)
    teacher_code: str | None = Field(default=None, min_length=1, max_length=64)
    library_id: int | None = Field(default=None, gt=0)
    # 学院编号供官方检索授权精确过滤，个人库不得携带学院归属。
    department_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_scope(self):
        # 文件标识必须是跨平台安全的单一路径段，个人与官方归属不能混用。
        prefix = "pk_" if self.binding_type == "PERSONAL" else "ok_"
        if not self.file_id.startswith(prefix) or not re.fullmatch(r"[A-Za-z0-9_-]+", self.file_id):
            raise ValueError("invalid managed file_id")
        if self.subject_code != self.subject_code.strip() or not self.subject_code.strip():
            raise ValueError("subject_code must be exact and nonblank")
        if self.binding_type == "PERSONAL":
            if self.teacher_code is None or self.teacher_code != self.teacher_code.strip() or self.library_id is not None or self.department_id is not None:
                raise ValueError("personal scope requires only teacher_code")
        elif self.library_id is None or self.department_id is None or self.teacher_code is not None:
            raise ValueError("official scope requires library_id and department_id")
        return self

    def scope(self) -> KnowledgeScope:
        return KnowledgeScope(**{name: getattr(self, name) for name in KnowledgeScope.model_fields})


# Java 生成版本；客户端不能指定索引、物理路径或回报地址。
class KnowledgeTask(KnowledgeScope):
    task_version: int = Field(gt=0)
    task_action: Literal["INGEST", "REMOVE"]


TaskState = Literal["QUEUED", "PROCESSING", "READY", "FAILED", "REMOVING", "REMOVED", "REMOVE_FAILED"]


# Java 回执包含 apply 后真实绑定状态，业务送达与某个版本已发布是两个独立事实。
class KnowledgeReportReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    applied: bool
    file_id: str = Field(alias="fileId", min_length=4, max_length=128, pattern=r"^(pk_|ok_)[A-Za-z0-9_-]+$")
    task_version: int = Field(alias="taskVersion", gt=0)
    task_action: Literal["INGEST", "REMOVE"] = Field(alias="taskAction")
    task_state: TaskState = Field(alias="taskState")
    active_version: int | None = Field(alias="activeVersion", gt=0)

    @model_validator(mode="after")
    def validate_receipt(self):
        if self.active_version is not None and self.active_version > self.task_version:
            raise ValueError("published version exceeds current Java task")
        if self.task_state == "READY" and (self.task_action != "INGEST" or self.active_version != self.task_version):
            raise ValueError("ready Java receipt requires current published ingest version")
        if self.task_state == "REMOVED" and (self.task_action != "REMOVE" or self.active_version is not None):
            raise ValueError("removed Java receipt cannot contain an active version")
        return self


# 对账返回技术任务当前状态，失败重解析时保留上一成功版本。
class KnowledgeTaskResult(BaseModel):
    file_id: str
    task_version: int
    task_action: Literal["INGEST", "REMOVE"]
    task_state: TaskState
    chunk_count: int = 0
    active_version: int | None = None
    error_message: str | None = None


# 只读片段保持平铺，不假造章节，也不透出向量和本机解析路径。
class KnowledgeChunk(BaseModel):
    chunk_id: str
    chunk_order: int
    text: str
    images: list[dict[str, str]] = Field(default_factory=list)


class KnowledgeChunkPage(BaseModel):
    file_id: str
    active_version: int
    page: int
    page_size: int
    total: int
    items: list[KnowledgeChunk]
