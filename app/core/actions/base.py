"""Base Action models and result containers."""

import hashlib
import json
import uuid
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field
from app.core.models.enums import ActionType, ActionStatus, ApprovalClassification
from app.core.models.domain import ActionPreview
from app.utils.time import utc_now_iso


class BaseAction(BaseModel):
    """Generic domain action to be processed and executed by the Action Engine."""
    action_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    idempotency_key: Optional[str] = None
    action_type: ActionType
    target_system: str  # "jira", "mattermost", "discord", etc.
    target_id: str      # Task key, Channel ID, or User ID
    parameters: Dict[str, Any] = Field(default_factory=dict)
    requested_by: str = "RulesEngine"
    requires_approval: bool = False
    dry_run: bool = False
    status: ActionStatus = ActionStatus.PENDING_APPROVAL
    preview: Optional[ActionPreview] = None
    attempt_count: int = 0
    created_at: str = Field(default_factory=utc_now_iso)

    def generate_idempotency_key(self) -> str:
        """Generate a deterministic idempotency key based on action contents."""
        params_serialized = json.dumps(self.parameters, sort_keys=True)
        raw_str = f"{self.action_type.value}:{self.target_system}:{self.target_id}:{params_serialized}"
        return hashlib.sha256(raw_str.encode("utf-8")).hexdigest()

    def ensure_idempotency_key(self) -> str:
        if not self.idempotency_key:
            self.idempotency_key = self.generate_idempotency_key()
        return self.idempotency_key


class ActionResult(BaseModel):
    """Result of an action execution attempt."""
    success: bool
    action_id: str
    status: ActionStatus
    target_system: str
    target_id: str
    result_data: Dict[str, Any] = Field(default_factory=dict)
    error_message: Optional[str] = None
    dry_run: bool = False
    executed_at: str = Field(default_factory=utc_now_iso)
