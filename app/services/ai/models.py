"""AI domain models, decision contracts, and contexts for PM Operations Agent."""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator


class AIDecisionType(str, Enum):
    """Categorical type of AI evaluation/decision."""
    PM_ATTENTION = "PM_ATTENTION"
    TASK_ASSESSMENT = "TASK_ASSESSMENT"
    WORKLOG_ANOMALY = "WORKLOG_ANOMALY"
    SCHEDULE_FORECAST = "SCHEDULE_FORECAST"
    PERFORMANCE_INSIGHT = "PERFORMANCE_INSIGHT"
    GENERAL_ANALYSIS = "GENERAL_ANALYSIS"


class AIRecommendationType(str, Enum):
    """Type of recommendation proposed by AI."""
    NO_ACTION = "NO_ACTION"
    REVIEW_TASK = "REVIEW_TASK"
    NOTIFY_PM = "NOTIFY_PM"
    FOLLOW_UP = "FOLLOW_UP"
    PROPOSE_COMMENT = "PROPOSE_COMMENT"
    PROPOSE_TRANSITION = "PROPOSE_TRANSITION"


class TaskSummaryContext(BaseModel):
    """Sanitized, provider-neutral task context for AI."""
    task_id: str
    key: str
    title: str
    status: str
    priority: str
    assignee_name: Optional[str] = None
    reporter_name: Optional[str] = None
    due_date: Optional[str] = None
    updated_at: Optional[str] = None
    labels: List[str] = Field(default_factory=list)


class ResourceSummaryContext(BaseModel):
    """Sanitized, provider-neutral resource/assignee context."""
    account_id: Optional[str] = None
    display_name: str
    role: Optional[str] = None
    is_team_member: bool = True


class MetricSummaryContext(BaseModel):
    """Contextual metrics relevant to the evaluation."""
    metric_name: str
    value: Any
    description: Optional[str] = None


class AIContext(BaseModel):
    """Provider-neutral context payload supplied to AI for decision support.
    
    Contains strictly bounded domain information without credentials, tokens, or raw DB dumps.
    """
    context_id: str
    timestamp: str
    objective: str
    task: Optional[TaskSummaryContext] = None
    resource: Optional[ResourceSummaryContext] = None
    team_name: Optional[str] = None
    recent_activity_summary: List[str] = Field(default_factory=list)
    metrics: List[MetricSummaryContext] = Field(default_factory=list)
    applicable_policies: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ProposedAction(BaseModel):
    """Data-only representation of a potential action proposed by AI.
    
    Never directly executable by AI; must pass safety validation and human/system approval.
    """
    action_type: str
    target_system: str
    target_id: str
    parameters: Dict[str, Any] = Field(default_factory=dict)
    rationale: Optional[str] = None


class AIDecision(BaseModel):
    """Structured decision output from an AI provider.
    
    This is pure data, not executable behavior.
    """
    decision_type: AIDecisionType
    recommendation: AIRecommendationType
    confidence: float = Field(..., ge=0.0, le=1.0)
    evidence: List[str] = Field(default_factory=list)
    explanation: str
    proposed_action: Optional[ProposedAction] = None
    requires_approval: bool = True

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError("confidence must be between 0.0 and 1.0 inclusive")
        return round(float(v), 4)


class AttentionItemAnalysis(BaseModel):
    """Structured analysis for an individual Jira issue or task requiring attention."""
    issue_key: str
    title: str
    current_status: str
    assignee: Optional[str] = None
    priority: Optional[str] = None
    due_date: Optional[str] = None
    updated_at: Optional[str] = None
    inactivity_duration: Optional[str] = None
    attention_reason: str
    supporting_evidence: List[str] = Field(default_factory=list)
    recommendation: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    uncertainty_or_missing_info: Optional[str] = None
    proposed_action: Optional[ProposedAction] = None

    @field_validator("confidence")
    @classmethod
    def validate_item_confidence(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError("confidence must be between 0.0 and 1.0 inclusive")
        return round(float(v), 4)


class PMAttentionAnalysis(BaseModel):
    """Typed domain model for a comprehensive PM Attention Analysis.
    
    Provides structured recommendations for human review across flagged items.
    Purely advisory; never executes external mutations.
    """
    analysis_id: str
    generated_at: str
    scope_team: str
    summary: str
    attention_items: List[AttentionItemAnalysis] = Field(default_factory=list)
    evidence: List[str] = Field(default_factory=list)
    recommendation: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    uncertainty_or_missing_info: Optional[str] = None
    proposed_action: Optional[ProposedAction] = None
    requires_human_review: bool = True

    @field_validator("confidence")
    @classmethod
    def validate_analysis_confidence(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError("confidence must be between 0.0 and 1.0 inclusive")
        return round(float(v), 4)

