"""Base event definitions."""

import uuid
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field
from app.utils.time import utc_now_iso


class BaseEvent(BaseModel):
    """Abstract base representation for all system and normalized events."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str
    source: str  # e.g., "jira", "mattermost", "rules_engine", "scheduler"
    external_event_id: Optional[str] = None
    timestamp: str = Field(default_factory=utc_now_iso)
    actor_id: Optional[str] = None
    actor_name: Optional[str] = None
    actor_email: Optional[str] = None
    project_id: Optional[str] = None
    project_key: Optional[str] = None
    task_id: Optional[str] = None
    task_key: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()
