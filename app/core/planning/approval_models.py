"""Domain models and schemas for Milestone 4C: Human Approval and Pre-Execution Conflict Validation.

Enforces:
1. Explicit human approval gating (never implicit).
2. Exact proposal ID and version binding.
3. Strict field-level authorization scope (UPDATE_DUE_DATE only).
4. Pre-execution live-state conflict detection before invoking the Action Engine.
5. Fail-closed safety invariants.
"""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator


class ApprovalStatus(str, Enum):
    """Deterministic lifecycle state for schedule human approvals."""
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"
    EXECUTED = "EXECUTED"


class AllowedScheduleActionType(str, Enum):
    """Strictly allowed execution action types for schedule approvals in Milestone 4C."""
    UPDATE_DUE_DATE = "UPDATE_DUE_DATE"


class ConflictCategory(str, Enum):
    """Deterministic conflict classification for live Jira pre-execution checks."""
    PROPOSAL_MISMATCH = "PROPOSAL_MISMATCH"
    VERSION_MISMATCH = "VERSION_MISMATCH"
    APPROVAL_INVALID = "APPROVAL_INVALID"
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
    APPROVAL_INVALIDATED = "APPROVAL_INVALIDATED"
    UNAUTHORIZED_ACTION = "UNAUTHORIZED_ACTION"
    ACTION_VALUE_CHANGED = "ACTION_VALUE_CHANGED"
    ISSUE_UNAVAILABLE = "ISSUE_UNAVAILABLE"
    PROJECT_MISMATCH = "PROJECT_MISMATCH"
    ASSIGNEE_CHANGED = "ASSIGNEE_CHANGED"
    STATUS_CHANGED = "STATUS_CHANGED"
    PRIORITY_CHANGED = "PRIORITY_CHANGED"
    ESTIMATE_CHANGED = "ESTIMATE_CHANGED"
    DUE_DATE_CHANGED = "DUE_DATE_CHANGED"
    DEPENDENCY_CHANGED = "DEPENDENCY_CHANGED"
    ISSUE_CHANGED = "ISSUE_CHANGED"


class ApprovedScheduleAction(BaseModel):
    """Field-level action specification bound to an exact proposal and version."""
    action_id: str = Field(..., description="Deterministic unique action identifier")
    issue_key: str = Field(..., description="Jira issue key")
    action_type: AllowedScheduleActionType = Field(
        default=AllowedScheduleActionType.UPDATE_DUE_DATE,
        description="Strictly allowed action type",
    )
    proposed_due_date: str = Field(..., description="Proposed due date in YYYY-MM-DD format")
    expected_pre_execution_due_date: Optional[str] = Field(
        default=None,
        description="Expected baseline due date before execution",
    )
    baseline_assignee: Optional[str] = None
    baseline_status: Optional[str] = None
    baseline_priority: Optional[str] = None
    baseline_remaining_hours: Optional[float] = None
    baseline_blocker_keys: List[str] = Field(default_factory=list)

    @field_validator("proposed_due_date")
    @classmethod
    def check_due_date(cls, v: str) -> str:
        if not v or not isinstance(v, str) or len(v.strip()) < 10:
            raise ValueError("proposed_due_date must be a valid date string YYYY-MM-DD")
        return v.strip()[:10]

    class Config:
        populate_by_name = True
        extra = "forbid"


class ScheduleReviewerIdentity(BaseModel):
    """Authenticated human reviewer identity."""
    user_id: str = Field(..., description="System-authenticated unique user identifier")
    display_name: str = Field(..., description="Human reviewer display name")
    roles: List[str] = Field(default_factory=list, description="Authenticated RBAC roles")

    @field_validator("user_id")
    @classmethod
    def check_user_id(cls, v: str) -> str:
        if not v or not isinstance(v, str) or not v.strip():
            raise ValueError("user_id must be a non-empty string")
        return v.strip()

    class Config:
        populate_by_name = True
        extra = "forbid"


class ScheduleApprovalDecision(BaseModel):
    """Immutable record of a human approval or rejection decision."""
    decision_id: str = Field(..., description="Unique decision ID")
    approval_id: str = Field(..., description="Target ScheduleApprovalRequest ID")
    proposal_id: str = Field(..., description="Target proposal ID")
    proposal_version: int = Field(default=1, description="Target proposal version")
    decision: ApprovalStatus = Field(..., description="APPROVED or REJECTED")
    reviewer: ScheduleReviewerIdentity = Field(..., description="Authenticated reviewer identity")
    decided_at: str = Field(..., description="ISO-8601 UTC timestamp of decision")
    approved_action_ids: List[str] = Field(
        default_factory=list,
        description="Explicit subset of approved action IDs (empty = approve all actions in request)",
    )
    comments: Optional[str] = None

    @field_validator("decision")
    @classmethod
    def check_decision(cls, v: ApprovalStatus) -> ApprovalStatus:
        if v not in (ApprovalStatus.APPROVED, ApprovalStatus.REJECTED):
            raise ValueError("Decision must be either APPROVED or REJECTED")
        return v

    class Config:
        populate_by_name = True
        extra = "forbid"


class ScheduleApprovalRequest(BaseModel):
    """Deterministic, immutable container for human schedule approval."""
    approval_id: str = Field(..., description="Unique approval request ID (e.g. app-uuid)")
    proposal_id: str = Field(..., description="Unique proposal identifier")
    proposal_version: int = Field(default=1, description="Proposal version number")
    validation_report_id: str = Field(..., description="Bound planning validation report ID")
    anchor_date: str = Field(..., description="Anchor date YYYY-MM-DD")
    
    # State & Lifecycle
    status: ApprovalStatus = Field(default=ApprovalStatus.PENDING)
    created_at: str = Field(..., description="ISO-8601 UTC timestamp of creation")
    expires_at: str = Field(..., description="ISO-8601 UTC timestamp of expiration")
    
    # Scope & Baseline Context
    configured_projects: List[str] = Field(default_factory=list)
    resolved_projects: List[str] = Field(default_factory=list)
    authorization_scope: List[AllowedScheduleActionType] = Field(
        default_factory=lambda: [AllowedScheduleActionType.UPDATE_DUE_DATE],
        description="Authorized action types permitted for execution",
    )
    
    # Explicit Actions Requested
    actions: List[ApprovedScheduleAction] = Field(default_factory=list)
    
    # Terminal Decision (if decided)
    decision: Optional[ScheduleApprovalDecision] = None

    class Config:
        populate_by_name = True
        extra = "forbid"


class ExecutionConflict(BaseModel):
    """Detailed description of a single pre-execution conflict."""
    conflict_category: ConflictCategory
    issue_key: str
    action_type: str = "UPDATE_DUE_DATE"
    field_name: str = "duedate"
    approved_value: Any = None
    live_value: Any = None
    baseline_value: Any = None
    message: str = Field(..., description="Human-readable explanation of the conflict")

    class Config:
        populate_by_name = True
        extra = "forbid"


class PreExecutionValidationResult(BaseModel):
    """Outcome of pre-execution live-state conflict validation."""
    is_valid: bool = False
    approval_id: str
    proposal_id: str
    proposal_version: int
    validated_at: str
    conflicts: List[ExecutionConflict] = Field(default_factory=list)
    executable_actions: List[ApprovedScheduleAction] = Field(default_factory=list)
    summary: str = ""

    class Config:
        populate_by_name = True
        extra = "forbid"
