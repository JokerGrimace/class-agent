import uuid
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


WorkflowStatus = Literal[
    "pending",
    "running",
    "waiting_user_input",
    "completed",
    "failed",
    "cancelled",
]


class WorkflowStep(BaseModel):
    id: str
    instruction: str
    output_key: str
    pre_task_output: list[str] = Field(default_factory=list)
    allowed_tools: Optional[list[str]] = None
    max_retries: int = 1


class WorkflowFinalAnswer(BaseModel):
    instruction: str
    input_from: list[str] = Field(default_factory=list)


class WorkflowDefinition(BaseModel):
    version: int
    system_prompt: str
    allowed_tools: list[str] = Field(default_factory=list)
    steps: list[WorkflowStep]
    final_answer: WorkflowFinalAnswer


class WorkflowRuntimeContext(BaseModel):
    current_step_index: int = 0
    current_step_id: Optional[str] = None
    status: WorkflowStatus = "pending"
    step_status: dict[str, str] = Field(default_factory=dict)
    step_results: dict[str, Any] = Field(default_factory=dict)
    global_context: dict[str, Any] = Field(default_factory=dict)
    waiting_for: Optional[dict[str, Any]] = None
    retry_counts: dict[str, int] = Field(default_factory=dict)
    last_error: Optional[dict[str, Any]] = None
    final_result: Optional[str] = None


class Workflow(BaseModel):
    workflow_id: str = Field(default_factory=lambda: f"wf_{uuid.uuid4().hex[:16]}")
    session_id: str
    user_id: str
    agent_id: str = "default"
    workflow_name: str
    version: int
    status: WorkflowStatus = "pending"
    definition: WorkflowDefinition
    context: WorkflowRuntimeContext = Field(default_factory=WorkflowRuntimeContext)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    def touch(self) -> None:
        self.updated_at = datetime.utcnow()
