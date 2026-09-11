"""Concrete action classes and helper constructors."""

from typing import Any, Dict, Optional
from app.core.actions.base import BaseAction
from app.core.models.enums import ActionType, ActionStatus
from app.core.models.domain import ActionPreview


def create_send_message_action(
    target_system: str,
    target_id: str,
    text: str,
    recipient_name: Optional[str] = None,
    channel: Optional[str] = None,
    requested_by: str = "RulesEngine"
) -> BaseAction:
    """Helper to create a SendMessage action."""
    params = {
        "text": text,
        "message": text,
        "recipient": target_id,
        "recipient_id": target_id,
        "recipient_name": recipient_name,
        "channel": channel
    }
    preview = ActionPreview(
        action_type=ActionType.SEND_MESSAGE.value,
        target_system=target_system,
        target_id=target_id,
        requested_by=requested_by,
        summary=f"Send direct message to {recipient_name or target_id} on {target_system.capitalize()}: '{text[:60]}...'",
        requires_approval=False
    )
    action = BaseAction(
        action_type=ActionType.SEND_MESSAGE,
        target_system=target_system,
        target_id=target_id,
        parameters=params,
        requested_by=requested_by,
        requires_approval=False,
        preview=preview
    )
    action.ensure_idempotency_key()
    return action


def create_send_notification_action(
    target_system: str = "discord",
    channel: str = "pm-alerts",
    title: str = "Notification",
    message: str = "",
    level: str = "INFO",
    fields: Optional[Any] = None,
    link: Optional[str] = None,
    severity: Optional[str] = None,
    requested_by: str = "RulesEngine"
) -> BaseAction:
    """Helper to create a SendNotification action."""
    effective_level = (severity or level).upper()
    params = {
        "channel": channel,
        "title": title,
        "message": message,
        "text": message,
        "level": effective_level,
        "severity": effective_level.lower(),
        "fields": fields,
        "link": link
    }
    preview = ActionPreview(
        action_type=ActionType.SEND_NOTIFICATION.value,
        target_system=target_system,
        target_id=channel,
        requested_by=requested_by,
        summary=f"Broadcast notification to {target_system.capitalize()} channel '{channel}': [{effective_level}] {title}",
        requires_approval=False
    )
    action = BaseAction(
        action_type=ActionType.SEND_NOTIFICATION,
        target_system=target_system,
        target_id=channel,
        parameters=params,
        requested_by=requested_by,
        requires_approval=False,
        preview=preview
    )
    action.ensure_idempotency_key()
    return action


def create_transition_task_action(
    target_system: str,
    task_key: str,
    target_status: str,
    current_status: Optional[str] = None,
    task_title: Optional[str] = None,
    requested_by: str = "RulesEngine"
) -> BaseAction:
    """Helper to create a TransitionTask action (Approval required)."""
    params = {
        "status": target_status,
        "task_key": task_key
    }
    preview = ActionPreview(
        action_type=ActionType.TRANSITION_TASK.value,
        target_system=target_system,
        target_id=task_key,
        task_title=task_title,
        current_state=current_status,
        target_state=target_status,
        requested_by=requested_by,
        summary=f"Transition Jira task {task_key} from '{current_status or 'Unknown'}' to '{target_status}'",
        requires_approval=False
    )
    action = BaseAction(
        action_type=ActionType.TRANSITION_TASK,
        target_system=target_system,
        target_id=task_key,
        parameters=params,
        requested_by=requested_by,
        requires_approval=False,
        preview=preview
    )
    action.ensure_idempotency_key()
    return action


def create_add_comment_action(
    target_system: str,
    task_key: str,
    comment_body: str,
    task_title: Optional[str] = None,
    requested_by: str = "RulesEngine"
) -> BaseAction:
    """Helper to create an AddComment action."""
    params = {
        "comment": comment_body,
        "task_key": task_key
    }
    preview = ActionPreview(
        action_type=ActionType.ADD_COMMENT.value,
        target_system=target_system,
        target_id=task_key,
        task_title=task_title,
        requested_by=requested_by,
        summary=f"Post comment to Jira task {task_key}: '{comment_body[:60]}...'",
        requires_approval=False
    )
    action = BaseAction(
        action_type=ActionType.ADD_COMMENT,
        target_system=target_system,
        target_id=task_key,
        parameters=params,
        requested_by=requested_by,
        requires_approval=False,
        preview=preview
    )
    action.ensure_idempotency_key()
    return action


def create_create_task_action(
    project_key: str,
    summary: str,
    description: Optional[str] = None,
    issue_type: str = "Task",
    assignee: Optional[str] = None,
    priority: Optional[str] = None,
    labels: Optional[list] = None,
    target_system: str = "jira",
    requested_by: str = "RulesEngine"
) -> BaseAction:
    """Helper to create a CreateTask action."""
    params: Dict[str, Any] = {
        "project_key": project_key,
        "summary": summary,
        "issue_type": issue_type,
    }
    if description is not None:
        params["description"] = description
    if assignee is not None:
        params["assignee"] = assignee
    if priority is not None:
        params["priority"] = priority
    if labels is not None:
        params["labels"] = labels

    preview = ActionPreview(
        action_type=ActionType.CREATE_TASK.value,
        target_system=target_system,
        target_id=project_key,
        requested_by=requested_by,
        summary=f"Create Jira task:\nProject: {project_key}\nSummary: {summary}\nAssignee: {assignee or 'Unassigned'}",
        requires_approval=False
    )
    action = BaseAction(
        action_type=ActionType.CREATE_TASK,
        target_system=target_system,
        target_id=project_key,
        parameters=params,
        requested_by=requested_by,
        requires_approval=False,
        preview=preview
    )
    action.ensure_idempotency_key()
    return action


def create_update_task_action(
    task_key: str,
    fields: Dict[str, Any],
    target_system: str = "jira",
    task_title: Optional[str] = None,
    requested_by: str = "RulesEngine"
) -> BaseAction:
    """Helper to create an UpdateTask action."""
    params = {
        "task_key": task_key,
        "fields": fields
    }
    field_lines = [f"{k}: {v}" for k, v in fields.items()]
    preview_summary = f"Update {task_key}:\n" + "\n".join(field_lines) if field_lines else f"Update {task_key}"
    preview = ActionPreview(
        action_type=ActionType.UPDATE_TASK.value,
        target_system=target_system,
        target_id=task_key,
        task_title=task_title,
        requested_by=requested_by,
        summary=preview_summary,
        requires_approval=False
    )
    action = BaseAction(
        action_type=ActionType.UPDATE_TASK,
        target_system=target_system,
        target_id=task_key,
        parameters=params,
        requested_by=requested_by,
        requires_approval=False,
        preview=preview
    )
    action.ensure_idempotency_key()
    return action


def create_assign_task_action(
    task_key: str,
    assignee: str,
    assignee_name: Optional[str] = None,
    target_system: str = "jira",
    task_title: Optional[str] = None,
    requested_by: str = "RulesEngine"
) -> BaseAction:
    """Helper to create an AssignTask action."""
    params = {
        "task_key": task_key,
        "assignee": assignee,
        "account_id": assignee,
        "assignee_name": assignee_name
    }
    preview = ActionPreview(
        action_type=ActionType.ASSIGN_TASK.value,
        target_system=target_system,
        target_id=task_key,
        task_title=task_title,
        requested_by=requested_by,
        summary=f"Assign {task_key} to {assignee_name or assignee}",
        requires_approval=False
    )
    action = BaseAction(
        action_type=ActionType.ASSIGN_TASK,
        target_system=target_system,
        target_id=task_key,
        parameters=params,
        requested_by=requested_by,
        requires_approval=False,
        preview=preview
    )
    action.ensure_idempotency_key()
    return action


def create_change_priority_action(
    task_key: str,
    priority: str,
    current_priority: Optional[str] = None,
    target_system: str = "jira",
    task_title: Optional[str] = None,
    requested_by: str = "RulesEngine"
) -> BaseAction:
    """Helper to create a ChangePriority action."""
    params = {
        "task_key": task_key,
        "priority": priority
    }
    preview = ActionPreview(
        action_type=ActionType.CHANGE_PRIORITY.value,
        target_system=target_system,
        target_id=task_key,
        task_title=task_title,
        requested_by=requested_by,
        summary=f"Change priority of {task_key} from '{current_priority or 'Unknown'}' to '{priority}'",
        requires_approval=False
    )
    action = BaseAction(
        action_type=ActionType.CHANGE_PRIORITY,
        target_system=target_system,
        target_id=task_key,
        parameters=params,
        requested_by=requested_by,
        requires_approval=False,
        preview=preview
    )
    action.ensure_idempotency_key()
    return action

