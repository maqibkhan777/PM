"""Concrete event types for normalized and domain events."""

from typing import Any, Dict, List, Optional
from pydantic import Field
from app.core.events.base import BaseEvent


# ------------------------------------------------------------------------------
# Normalized Task Events (from external systems such as Jira)
# ------------------------------------------------------------------------------

class TaskCreated(BaseEvent):
    event_type: str = "TaskCreated"
    title: str
    description: Optional[str] = None
    status: str = "To Do"
    priority: str = "Medium"
    assignee_id: Optional[str] = None
    assignee_name: Optional[str] = None
    reporter_id: Optional[str] = None
    reporter_name: Optional[str] = None
    due_date: Optional[str] = None


class TaskUpdated(BaseEvent):
    event_type: str = "TaskUpdated"
    changed_fields: List[str] = Field(default_factory=list)
    changes: Dict[str, Any] = Field(default_factory=dict)


class TaskStatusChanged(BaseEvent):
    event_type: str = "TaskStatusChanged"
    old_status: str
    new_status: str
    transition_id: Optional[str] = None


class TaskAssigned(BaseEvent):
    event_type: str = "TaskAssigned"
    old_assignee_id: Optional[str] = None
    old_assignee_name: Optional[str] = None
    new_assignee_id: Optional[str] = None
    new_assignee_name: Optional[str] = None


class TaskCommentAdded(BaseEvent):
    event_type: str = "TaskCommentAdded"
    comment_id: Optional[str] = None
    comment_body: str
    author_id: Optional[str] = None
    author_name: Optional[str] = None


class TaskPriorityChanged(BaseEvent):
    event_type: str = "TaskPriorityChanged"
    old_priority: str
    new_priority: str


class TaskCompleted(BaseEvent):
    event_type: str = "TaskCompleted"
    completion_time: str
    resolved_by: Optional[str] = None


class TaskReopened(BaseEvent):
    event_type: str = "TaskReopened"
    previous_status: str
    new_status: str
    reopened_by: Optional[str] = None


class TaskWorklogged(BaseEvent):
    event_type: str = "TaskWorklogged"
    worklog_id: Optional[str] = None
    time_spent_seconds: int = 0
    time_spent_human: Optional[str] = None
    comment: Optional[str] = None


# ------------------------------------------------------------------------------
# Internal / Rule-Generated Domain Events
# ------------------------------------------------------------------------------

class WorkflowViolation(BaseEvent):
    event_type: str = "WorkflowViolation"
    source: str = "rules_engine"
    violation_type: str  # e.g., "ActiveWorkOnToDo"
    task_key: str
    task_title: Optional[str] = None
    current_status: str
    resource_name: Optional[str] = None
    details: str


class StaleTask(BaseEvent):
    event_type: str = "StaleTask"
    source: str = "scheduler"
    task_key: str
    task_title: Optional[str] = None
    assignee_name: Optional[str] = None
    assignee_id: Optional[str] = None
    hours_inactive: float
    threshold_hours: int = 24


class OverdueTask(BaseEvent):
    event_type: str = "OverdueTask"
    source: str = "scheduler"
    task_key: str
    task_title: Optional[str] = None
    assignee_name: Optional[str] = None
    due_date: str
    current_status: str


class TaskBlocked(BaseEvent):
    event_type: str = "TaskBlocked"
    source: str = "rules_engine"
    task_key: str
    task_title: Optional[str] = None
    reason: Optional[str] = None
    blocked_by: Optional[str] = None


class UserMappingRequired(BaseEvent):
    event_type: str = "UserMappingRequired"
    source: str = "user_mapping_service"
    jira_user_id: str
    jira_display_name: str
    jira_email: Optional[str] = None
    intended_action: str
