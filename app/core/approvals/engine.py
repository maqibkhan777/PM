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
        """Determine approval classification for an action."""
        action_type = action.action_type

        # Automatically allowed in V0.1
        if action_type in (ActionType.SEND_NOTIFICATION, ActionType.SEND_MESSAGE):
            return ApprovalClassification.AUTO

        # Destructive operations - unconditionally blocked in V0.1
        if action.parameters.get("is_destructive") or "delete" in action_type.value.lower():
            return ApprovalClassification.BLOCKED

        # Mutating operations on Jira - require approval
        if action_type in (
            ActionType.TRANSITION_TASK,
            ActionType.ASSIGN_TASK,
            ActionType.ADD_COMMENT,
            ActionType.CHANGE_PRIORITY,
            ActionType.CREATE_TASK,
            ActionType.UPDATE_TASK,
        ):
            return ApprovalClassification.APPROVAL_REQUIRED

        return ApprovalClassification.APPROVAL_REQUIRED

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
            summary = f"Transition Jira Task {target_id} from '{current_state or 'To Do'}' to '{target_state or 'In Progress'}'"
        elif action_type == ActionType.ASSIGN_TASK:
            summary = f"Assign Jira Task {target_id} to '{params.get('assignee_name') or params.get('account_id')}'"
        elif action_type == ActionType.ADD_COMMENT:
            comment_snippet = str(params.get('comment') or params.get('body', ''))[:60]
            summary = f"Add comment to Jira Task {target_id}: '{comment_snippet}...'"
        elif action_type == ActionType.CHANGE_PRIORITY:
            summary = f"Change priority of Jira Task {target_id} to '{params.get('priority')}'"
        elif action_type == ActionType.CREATE_TASK:
            summary = f"Create new Jira Task in project '{target_id}': '{params.get('summary')}'"
        elif action_type == ActionType.SEND_MESSAGE:
            summary = f"Send Mattermost DM to '{params.get('recipient_name') or target_id}': '{str(params.get('text', ''))[:60]}...'"
        elif action_type == ActionType.SEND_NOTIFICATION:
            summary = f"Send Discord alert to #{target_id}: [{params.get('level', 'INFO')}] {params.get('title', '')}"
        else:
            summary = f"Execute {action_type.value} on {target_sys} ({target_id})"

        classification = ApprovalEngine.classify(action)
        requires_approval = (classification == ApprovalClassification.APPROVAL_REQUIRED)

        return ActionPreview(
            action_type=action_type.value,
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
