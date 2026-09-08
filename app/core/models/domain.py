"""Core domain entity models."""

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from app.core.models.enums import TaskStatus, TaskPriority, ActionStatus, ApprovalClassification


class User(BaseModel):
    """Generic domain user representation."""
    id: str
    external_system: str
    external_id: str
    display_name: str
    email: Optional[str] = None
    active: bool = True


class Project(BaseModel):
    """Generic domain project representation."""
    id: str
    key: str
    name: str
    description: Optional[str] = None


class Task(BaseModel):
    """Generic domain task representation."""
    id: str
    key: str
    title: str
    description: Optional[str] = None
    status: TaskStatus = TaskStatus.UNKNOWN
    priority: TaskPriority = TaskPriority.UNKNOWN
    project_id: Optional[str] = None
    project_key: Optional[str] = None
    assignee_id: Optional[str] = None
    assignee_name: Optional[str] = None
    assignee_email: Optional[str] = None
    reporter_id: Optional[str] = None
    reporter_name: Optional[str] = None
    due_date: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    labels: List[str] = Field(default_factory=list)
    raw_data: Dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    """Generic message representation for direct messages or channel broadcasts."""
    recipient_id: Optional[str] = None
    recipient_name: Optional[str] = None
    channel: Optional[str] = None
    text: str
    title: Optional[str] = None
    fields: Optional[Dict[str, str]] = None
    color: Optional[str] = None  # Hex color or name e.g. "red", "green", "amber", "blue"


class Notification(BaseModel):
    """Generic notification payload."""
    title: str
    message: str
    level: str = "INFO"  # INFO, WARNING, ERROR, SUCCESS
    channel: str = "default"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ActionPreview(BaseModel):
    """Human-readable preview of an action before execution or approval."""
    action_type: str
    target_system: str
    target_id: str
    task_title: Optional[str] = None
    current_state: Optional[str] = None
    target_state: Optional[str] = None
    requested_by: str = "RulesEngine"
    summary: str
    requires_approval: bool = False


class HealthStatus(BaseModel):
    """System and connector health summary."""
    name: str
    status: str  # OK, DEGRADED, DOWN, NOT_CONFIGURED
    is_connected: bool
    details: Dict[str, Any] = Field(default_factory=dict)
