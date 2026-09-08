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
    requested_by: str = "RulesEngine"
) -> BaseAction:
    """Helper to create a SendNotification action."""
    params = {
        "channel": channel,
        "title": title,
        "message": message,
        "level": level,
        "fields": fields
    }
    preview = ActionPreview(
        action_type=ActionType.SEND_NOTIFICATION.value,
        target_system=target_system,
        target_id=channel,
        requested_by=requested_by,
        summary=f"Broadcast notification to {target_system.capitalize()} channel '{channel}': [{level}] {title}",
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
        requires_approval=True
    )
    action = BaseAction(
        action_type=ActionType.TRANSITION_TASK,
        target_system=target_system,
        target_id=task_key,
        parameters=params,
        requested_by=requested_by,
        requires_approval=True,
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
    """Helper to create an AddComment action (Approval required)."""
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
        requires_approval=True
    )
    action = BaseAction(
        action_type=ActionType.ADD_COMMENT,
        target_system=target_system,
        target_id=task_key,
        parameters=params,
        requested_by=requested_by,
        requires_approval=True,
        preview=preview
    )
    action.ensure_idempotency_key()
    return action
