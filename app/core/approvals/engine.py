"""Approval classification and action preview generator."""

from typing import Optional
from app.core.actions.base import BaseAction
from app.core.models.enums import ActionType, ApprovalClassification
from app.core.models.domain import ActionPreview
from app.utils.logger import logger


class ApprovalEngine:
    """Classifies actions into AUTO, APPROVAL_REQUIRED, or BLOCKED, and generates human-readable previews."""

    @staticmethod
    def classify(action: BaseAction) -> ApprovalClassification:
        """Determine approval classification for an action in V1."""
        action_type = action.action_type

        # Destructive operations - unconditionally blocked in V1
        if action.parameters.get("is_destructive") or "delete" in action_type.value.lower():
            return ApprovalClassification.BLOCKED

        # All standard V1 operations are automatically permitted without manual approval
        if action_type in (
            ActionType.SEND_NOTIFICATION,
            ActionType.SEND_MESSAGE,
            ActionType.TRANSITION_TASK,
            ActionType.ASSIGN_TASK,
            ActionType.ADD_COMMENT,
            ActionType.CHANGE_PRIORITY,
            ActionType.CREATE_TASK,
            ActionType.UPDATE_TASK,
        ):
            return ApprovalClassification.AUTO

        return ApprovalClassification.AUTO

    @staticmethod
    def generate_preview(action: BaseAction) -> ActionPreview:
        """Generate structured preview for an action."""
        action_type = action.action_type
        target_sys = action.target_system
        target_id = action.target_id
        params = action.parameters

        current_state = params.get("current_status")
        target_state = params.get("status") or params.get("target_status")
        task_title = params.get("task_title") or params.get("summary")

        if action_type == ActionType.TRANSITION_TASK:
            from_str = f" from '{current_state}'" if current_state else ""
            summary = f"Transition Jira task {target_id}{from_str} to '{target_state or 'Done'}'"
        elif action_type == ActionType.ASSIGN_TASK:
            assignee = params.get("assignee") or params.get("assignee_name") or params.get("account_id") or target_id
            summary = f"Assign Jira task {target_id} to '{assignee}'"
        elif action_type == ActionType.ADD_COMMENT:
            comment_snippet = str(params.get("comment") or params.get("body", ""))[:100]
            summary = f"Add comment to {target_id}:\n{comment_snippet}"
        elif action_type == ActionType.UPDATE_TASK:
            fields = params.get("fields", {})
            field_lines = [f"{k}: {v}" for k, v in fields.items()]
            fields_str = "\n".join(field_lines) if field_lines else "None"
            summary = f"Update {target_id}:\n{fields_str}"
        elif action_type == ActionType.CREATE_TASK:
            proj = params.get("project_key") or target_id
            summ = params.get("summary") or "Task"
            assignee = params.get("assignee")
            assignee_str = f"\nAssignee: {assignee}" if assignee else ""
            summary = f"Create Jira task:\nProject: {proj}\nSummary: {summ}{assignee_str}"
        elif action_type == ActionType.CHANGE_PRIORITY:
            summary = f"Change priority of Jira task {target_id} to '{params.get('priority')}'"
        elif action_type == ActionType.SEND_MESSAGE:
            recip = params.get("recipient") or params.get("recipient_name") or target_id
            msg_snippet = str(params.get("message") or params.get("text", ""))[:100]
            summary = f"Send {target_sys.capitalize()} message to {recip}: {msg_snippet}"
        elif action_type == ActionType.SEND_NOTIFICATION:
            notif_content = params.get("title") or params.get("message") or ""
            summary = f"Send PM notification:\n{notif_content}"
        else:
            summary = f"Execute {action_type.value} on {target_sys} ({target_id})"

        classification = ApprovalEngine.classify(action)
        requires_approval = (classification == ApprovalClassification.APPROVAL_REQUIRED)

        return ActionPreview(
            action_type=action_type.value if hasattr(action_type, "value") else str(action_type),
            target_system=target_sys,
            target_id=target_id,
            task_title=task_title,
            current_state=current_state,
            target_state=target_state,
            requested_by=action.requested_by,
            summary=summary,
            requires_approval=requires_approval
        )


# Global approval engine instance
approval_engine = ApprovalEngine()
