from typing import Any, Mapping, Optional, Union

from app.core.workflow.models import Workflow, WorkflowDefinition, WorkflowRuntimeContext
from app.core.workflow.storage import WorkflowStorage, create_workflow_storage


class WorkflowManager:
    def __init__(self, storage: Optional[WorkflowStorage] = None):
        self.storage = storage or create_workflow_storage()

    def initialize(self) -> None:
        self.storage.initialize()

    def start_workflow(
        self,
        session_id: str,
        user_id: str,
        agent_id: str,
        workflow_name: str,
        definition: Union[WorkflowDefinition, Mapping[str, Any]],
        user_input: str,
        global_context: Optional[dict[str, Any]] = None,
    ) -> Workflow:
        workflow_definition = WorkflowDefinition.model_validate(definition)
        first_step_id = workflow_definition.steps[0].id if workflow_definition.steps else None
        context = WorkflowRuntimeContext(
            current_step_index=0,
            current_step_id=first_step_id,
            status="pending",
            step_status={step.id: "pending" for step in workflow_definition.steps},
            step_results={"user_input": user_input},
            global_context=global_context or {},
            retry_counts={step.id: 0 for step in workflow_definition.steps},
        )
        workflow = Workflow(
            session_id=session_id,
            user_id=user_id,
            agent_id=agent_id,
            workflow_name=workflow_name,
            version=workflow_definition.version,
            status="pending",
            definition=workflow_definition,
            context=context,
        )
        self.storage.save_workflow(workflow)
        return workflow

    def get_workflow(self, workflow_id: str, user_id: str) -> Optional[Workflow]:
        return self.storage.get_workflow(workflow_id, user_id)

    def get_active_workflow(self, session_id: str, user_id: str) -> Optional[Workflow]:
        return self.storage.get_active_workflow(session_id, user_id)

    def mark_step_completed(
        self,
        workflow_id: str,
        user_id: str,
        step_id: str,
        output_key: str,
        output_value: Any,
    ) -> Workflow:
        workflow = self._require_workflow(workflow_id, user_id)
        workflow.context.step_status[step_id] = "completed"
        workflow.context.step_results[output_key] = output_value

        next_index = workflow.context.current_step_index + 1
        if next_index < len(workflow.definition.steps):
            workflow.context.current_step_index = next_index
            workflow.context.current_step_id = workflow.definition.steps[next_index].id
        else:
            workflow.context.current_step_index = next_index
            workflow.context.current_step_id = None

        workflow.context.status = "running"
        workflow.status = "running"
        workflow.touch()
        self.storage.save_workflow(workflow)
        return workflow

    def pause_for_user_input(
        self,
        workflow_id: str,
        user_id: str,
        question: str,
        response_key: str = "user_input",
    ) -> Workflow:
        workflow = self._require_workflow(workflow_id, user_id)
        workflow.context.status = "waiting_user_input"
        workflow.context.waiting_for = {
            "type": "user_input",
            "question": question,
            "response_key": response_key,
            "step_id": workflow.context.current_step_id,
        }
        workflow.status = "waiting_user_input"
        workflow.touch()
        self.storage.save_workflow(workflow)
        return workflow



    def mark_failed(self, workflow_id: str, user_id: str, error_message: str) -> Workflow:
        workflow = self._require_workflow(workflow_id, user_id)
        if workflow.context.current_step_id:
            workflow.context.step_status[workflow.context.current_step_id] = "failed"
        workflow.context.last_error = {"message": error_message}
        workflow.context.status = "failed"
        workflow.status = "failed"
        workflow.touch()
        self.storage.save_workflow(workflow)
        return workflow

    def mark_completed(self, workflow_id: str, user_id: str, final_result: str) -> Workflow:
        workflow = self._require_workflow(workflow_id, user_id)
        workflow.context.final_result = final_result
        workflow.context.current_step_id = None
        workflow.context.status = "completed"
        workflow.status = "completed"
        workflow.touch()
        self.storage.save_workflow(workflow)
        return workflow

    def mark_cancelled(self, workflow_id: str, user_id: str) -> Workflow:
        workflow = self._require_workflow(workflow_id, user_id)
        workflow.context.status = "cancelled"
        workflow.status = "cancelled"
        workflow.touch()
        self.storage.save_workflow(workflow)
        return workflow

    def delete_workflow(self, workflow_id: str, user_id: str) -> bool:
        return self.storage.delete_workflow(workflow_id, user_id)

    def _require_workflow(self, workflow_id: str, user_id: str) -> Workflow:
        workflow = self.storage.get_workflow(workflow_id, user_id)
        if not workflow:
            raise ValueError(f"Workflow not found: {workflow_id}")
        return workflow

    def merge_global_context(
            self,
            workflow_id: str,
            user_id: str,
            values: dict[str, Any],
    ) -> Workflow:
        workflow = self._require_workflow(workflow_id, user_id)
        workflow.context.global_context.update(values or {})
        workflow.touch()
        self.storage.save_workflow(workflow)
        return workflow

    def set_global_context_value(
            self,
            workflow_id: str,
            user_id: str,
            key: str,
            value: Any,
    ) -> Workflow:
        workflow = self._require_workflow(workflow_id, user_id)
        workflow.context.global_context[key] = value
        workflow.touch()
        self.storage.save_workflow(workflow)
        return workflow
