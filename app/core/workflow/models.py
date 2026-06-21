import uuid
from datetime import datetime, timedelta
from typing import Any, Literal, Optional

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


WorkflowStatus = Literal["pending", "running", "completed", "failed", "cancelled"]
WorkflowTaskStatus = Literal["pending", "running", "completed", "failed", "cancelled"]


def _validate_object_schema(
    schema: dict[str, Any],
    field_name: str,
) -> dict[str, Any]:
    Draft202012Validator.check_schema(schema)
    if schema.get("type") != "object":
        raise ValueError(f"{field_name} root type must be object")
    if "required" not in schema:
        raise ValueError(f"{field_name} must declare required")
    if schema.get("additionalProperties") is not False:
        raise ValueError(f"{field_name} must set additionalProperties to false")
    return schema


def _validate_input_schema(schema: dict[str, Any]) -> dict[str, Any]:
    schema = _validate_object_schema(schema, "input_schema")
    expected_keys = {"global_context", "task_results", "constants"}
    properties = schema.get("properties")
    if not isinstance(properties, dict) or set(properties) != expected_keys:
        raise ValueError(
            "input_schema properties must be exactly "
            "global_context, task_results, and constants"
        )
    if set(schema["required"]) != expected_keys:
        raise ValueError(
            "input_schema required must contain exactly "
            "global_context, task_results, and constants"
        )
    return schema


class WorkflowTaskDefinition(BaseModel):
    """
    声明workflow task definition
    """
    model_config = ConfigDict(extra="forbid")

    id: str
    show_name: str
    instruction: str
    depends_on: list[str] = Field(default_factory=list)
    constants: dict[str, Any] = Field(default_factory=dict)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    allowed_tools: list[str] = Field(default_factory=list)

    @field_validator("id", "show_name", "instruction")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("input_schema")
    @classmethod
    def validate_input_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_input_schema(value)

    @field_validator("output_schema")
    @classmethod
    def validate_output_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_object_schema(value, "output_schema")


class WorkflowDefinition(BaseModel):
    """
    声明一个workflowDAG
    """
    model_config = ConfigDict(extra="forbid")

    max_parallel_tasks: Optional[int] = Field(default=None, ge=1)
    summary_task_id: str
    tasks: list[WorkflowTaskDefinition] = Field(min_length=1)

    @field_validator("summary_task_id")
    @classmethod
    def reject_blank_summary_task_id(cls, value: str) -> str:
        """
        对summary_task_id进行检测
        :param value:
        :return:
        """
        normalized = value.strip()
        if not normalized:
            raise ValueError("summary_task_id must not be blank")
        return normalized

    @model_validator(mode="after")
    def validate_dag(self) -> "WorkflowDefinition":
        """
        校验dag图
        所有任务 id 全局唯一，不允许重复；
        配置的汇总任务 ID，必须是真实存在的任务；
        每个任务的依赖 depends_on 里，不能出现不存在的任务 ID；
        任务禁止自依赖；
        整个任务依赖链路不能存在循环（A 依赖 B、B 依赖 A 这种死循环）；
        汇总任务必须有上游依赖，不能孤立无前置；
        汇总任务不能绑定任何执行工具（只做汇总逻辑，不调用工具）；
        汇总任务必须是流程最后一步，任何其他任务都不能依赖它。
        :return:
        """
        # 1. 构建字典：key=任务id，value=任务对象，方便快速查询任务
        tasks_by_id = {task.id: task for task in self.tasks}

        # 校验1：所有task的id必须唯一
        # 如果去重后的字典长度 < 原任务列表长度，说明存在重复task.id
        if len(tasks_by_id) != len(self.tasks):
            raise ValueError("task ids must be unique")

        # 校验2：汇总任务ID必须真实存在于任务列表中
        if self.summary_task_id not in tasks_by_id:
            raise ValueError("summary_task_id must reference an existing task")

        # 遍历每一个任务，校验依赖关系
        for task in self.tasks:
            missing = [
                dependency
                for dependency in task.depends_on
                if dependency not in tasks_by_id
            ]
            # 校验3：任务不能依赖不存在的任务ID
            if missing:
                raise ValueError(f"task {task.id} has missing dependencies: {missing}")

            # 校验4：任务不能自依赖（自己依赖自己）
            if task.id in task.depends_on:
                raise ValueError(f"task {task.id} cannot depend on itself")

        # 校验5：调用内部方法，校验整个任务依赖图无循环依赖（DAG无环）
        self._assert_acyclic(tasks_by_id)

        # 根据汇总任务ID取出汇总任务对象
        summary = tasks_by_id[self.summary_task_id]

        # 校验6：汇总任务必须有前置依赖，不能没有上游任务
        if not summary.depends_on:
            raise ValueError("summary task depends_on must not be empty")

        # 校验7：汇总任务不允许配置工具（allowed_tools必须为空）
        if summary.allowed_tools:
            raise ValueError("summary task allowed_tools must be empty")
        # 校验8：汇总任务必须是终端节点，不能被其他任何任务依赖
        # 遍历所有非汇总任务，只要任意任务依赖summary_task_id则报错
        if any(
            self.summary_task_id in task.depends_on
            for task in self.tasks
            if task.id != self.summary_task_id
        ):
            raise ValueError("summary task must be terminal")
        return self

    @staticmethod
    def _assert_acyclic(
        tasks_by_id: dict[str, WorkflowTaskDefinition],
    ) -> None:
        """
        做DAG的环路检测
        :param tasks_by_id:
        :return:
        """
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(task_id: str) -> None:
            if task_id in visiting:
                raise ValueError("workflow task graph must be acyclic")
            if task_id in visited:
                return
            visiting.add(task_id)
            for dependency in tasks_by_id[task_id].depends_on:
                visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in tasks_by_id:
            visit(task_id)


class WorkflowRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_run_id: str = Field(
        default_factory=lambda: f"wfr_{uuid.uuid4().hex[:16]}"
    )
    workflow_name: str
    summary_task_id: str
    user_id: str
    session_id: str
    coordinator_agent_id: str
    status: WorkflowStatus = "pending"
    global_context_json: dict[str, Any]
    definition_snapshot_json: WorkflowDefinition
    error_json: Optional[dict[str, Any]] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None
    expires_at: datetime = Field(
        default_factory=lambda: datetime.utcnow() + timedelta(days=30)
    )


class WorkflowTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_run_id: str
    task_id: str
    status: WorkflowTaskStatus = "pending"
    result_json: Optional[dict[str, Any]] = None
    error_json: Optional[dict[str, Any]] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None
