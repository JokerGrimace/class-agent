from __future__ import annotations

from contextvars import ContextVar, Token
import logging
import re
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator, ValidationError

from app.config import settings
from app.core.iclass.auth_context import get_current_iclass_api_token
from app.core.llm.user_context import get_current_model_user
from . import config

logger = logging.getLogger(__name__)


# Java 的检索授权响应使用精确 camelCase 契约，不与管理 binding/library 模型混用；记录不可变。
class RetrievalFile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    binding_type: Literal["PERSONAL", "OFFICIAL"] = Field(alias="bindingType")
    file_id: str = Field(alias="fileId", min_length=4, max_length=128)
    subject_code: str = Field(alias="subjectCode", min_length=1, max_length=64)
    teacher_code: str | None = Field(alias="teacherCode")
    department_id: int | None = Field(alias="departmentId", gt=0)
    active_version: int = Field(alias="activeVersion", gt=0)

    @model_validator(mode="after")
    def validate_file(self):
        # 个人必须具有教师归属，学院必须具有学院归属；不能通过错前缀或混合字段扩大范围。
        prefix = "pk_" if self.binding_type == "PERSONAL" else "ok_"
        if not self.file_id.startswith(prefix) or not re.fullmatch(r"[A-Za-z0-9_-]+", self.file_id):
            raise ValueError("invalid authorized fileId")
        if self.subject_code != self.subject_code.strip() or not self.subject_code.strip():
            raise ValueError("invalid authorized subjectCode")
        if self.binding_type == "PERSONAL":
            if not self.teacher_code or self.teacher_code != self.teacher_code.strip() or self.department_id is not None:
                raise ValueError("invalid personal retrieval scope")
        elif self.teacher_code is not None or self.department_id is None:
            raise ValueError("invalid official retrieval scope")
        return self

    @property
    def index_name(self) -> str:
        return config.teacher_vector_store_name if self.binding_type == "PERSONAL" else config.subject_vector_store_name


# body 只允许 files 字段；未知结构、重复文件或非法版本必须整体拒绝，不能猜测转换后继续。
class RetrievalScopeBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    files: list[RetrievalFile]

    @model_validator(mode="after")
    def unique_files(self):
        if len({item.file_id for item in self.files}) != len(self.files):
            raise ValueError("duplicate authorized fileId")
        return self


async def resolve_managed_retrieval_files(*, course_code: str | None = None) -> tuple[RetrievalFile, ...]:
    # 学生课程只能来自执行器隐藏的会话上下文；Java 再检查真实选课、授课教师与公开资源权限。
    token = get_current_iclass_api_token()
    user = get_current_model_user()
    if not token or (user is not None and user.user_role == "1" and not course_code):
        return ()
    if course_code is not None and (not isinstance(course_code, str) or not course_code.strip() or course_code != course_code.strip()):
        return ()
    try:
        # 每次检索重新查询 Java，取消订阅或移出文件随下次请求生效；禁止共享 token 和重定向。
        async with httpx.AsyncClient(timeout=settings.iclass_api_timeout_seconds, follow_redirects=False) as client:
            response = await client.get(
                f"{settings.iclass_api_base_url.rstrip('/')}/resource/knowledge/retrieval-scope",
                headers={"Authorization": f"Bearer {token}"},
                params={"courseCode": course_code} if course_code is not None else {},
            )
        if response.status_code != 200:
            return ()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("code") != "200":
            return ()
        return tuple(RetrievalScopeBody.model_validate(payload.get("body")).files)
    except (httpx.HTTPError, ValueError, ValidationError) as exc:
        logger.warning("RAG retrieval authorization unavailable: %s", type(exc).__name__)
        return ()


# ContextVar 跟随请求及 asyncio.to_thread，默认空集合使匿名、服务故障和直接旧调用均拒绝管理内容。
_managed_grants: ContextVar[tuple[RetrievalFile, ...]] = ContextVar("rag_managed_retrieval_grants", default=())


def set_managed_retrieval_grants(grants: tuple[RetrievalFile, ...]) -> Token:
    if type(grants) is not tuple or any(type(grant) is not RetrievalFile for grant in grants):
        raise TypeError("managed grants must be immutable internal records")
    return _managed_grants.set(grants)


def reset_managed_retrieval_grants(token: Token) -> None:
    _managed_grants.reset(token)


def get_managed_retrieval_grants() -> tuple[RetrievalFile, ...]:
    return _managed_grants.get()


def managed_access_filter() -> dict:
    # 旧公共文档保留既有检索逻辑；pk_/ok_ 仅接受索引、文件、版本和归属全部匹配的可信分支。
    legacy = {"bool": {"must_not": [
        {"prefix": {"metadata.id.keyword": "pk_"}},
        {"prefix": {"metadata.id.keyword": "ok_"}},
        # 两个业务索引只能使用 Java 发布范围，不能让缺新 metadata 的旧内容通过公共分支。
        {"terms": {"_index": [config.teacher_vector_store_name, config.subject_vector_store_name]}},
    ]}}
    grants = _managed_grants.get()
    if not grants:
        return legacy
    branches = [legacy]
    # 同成功版本、学科及归属的文件以 terms(id) 合并，保留所有授权文件且不会交叉匹配不同版本。
    grouped = {}
    for grant in grants:
        key = (grant.index_name, grant.active_version, grant.subject_code, grant.teacher_code, grant.department_id)
        grouped.setdefault(key, []).append(grant.file_id)
    for (index, version, subject, teacher, department), file_ids in grouped.items():
        filters = [
            {"term": {"_index": index}},
            {"terms": {"metadata.id.keyword": file_ids}},
            {"term": {"metadata.task_version": version}},
            {"term": {"metadata.subject.keyword": subject}},
        ]
        if teacher is not None:
            filters.append({"term": {"metadata.teacher.keyword": teacher}})
        else:
            filters.append({"term": {"metadata.department_id": department}})
        branches.append({"bool": {"filter": filters}})
    return {"bool": {"should": branches, "minimum_should_match": 1}}
