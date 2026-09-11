"""Domain models and schemas for Phase A Performance Data Foundation."""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ConfidenceLevel(str, Enum):
    """Statistical confidence level based on sample size and history span."""
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INSUFFICIENT = "INSUFFICIENT"


class RiskLevel(str, Enum):
    """Schedule / delivery risk classifications."""
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    ORANGE = "ORANGE"
    RED = "RED"


class SignalType(str, Enum):
    """Deterministic performance signal types."""
    DELIVERY_ON_TRACK = "DELIVERY_ON_TRACK"
    DELIVERY_RISK = "DELIVERY_RISK"
    CAPACITY_OVERLOADED = "CAPACITY_OVERLOADED"
    LOW_WORKLOAD = "LOW_WORKLOAD"
    HIGH_ESTIMATION_VARIANCE = "HIGH_ESTIMATION_VARIANCE"
    HIGH_REOPEN_RATE = "HIGH_REOPEN_RATE"
    FREQUENT_BLOCKERS = "FREQUENT_BLOCKERS"
    LOW_UPDATE_ACTIVITY = "LOW_UPDATE_ACTIVITY"
    NORMAL_UPDATE_ACTIVITY = "NORMAL_UPDATE_ACTIVITY"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"


class ResourceRole(str, Enum):
    """Supported team roles."""
    DEVELOPER = "Developer"
    QA = "QA"
    DESIGNER = "Designer"
    SUPPORT = "Support"
    PROJECT_MANAGER = "Project Manager"
    UNKNOWN = "Unknown"


class RoleCategory(str, Enum):
    """Authoritative normalized role categories."""
    WORDPRESS_DEVELOPMENT = "WordPress Development"
    FRONTEND_DEVELOPMENT = "Frontend Development"
    BUSINESS_ANALYSIS = "Business Analysis"
    QA = "QA"
    CONTENT = "Content"
    CONTENT_MARKETING = "Content / Marketing"
    SEO = "SEO"
    CUSTOMER_SUPPORT = "Customer Support"
    DESIGN = "Design"
    UNKNOWN = "Unknown"


class EmployeeRoleAssignment(BaseModel):
    """Authoritative SQLite employee designation and role category assignment."""
    id: str
    account_id: str
    display_name: str
    designation: str
    role_category: str
    effective_from: Optional[str] = None
    effective_to: Optional[str] = None
    source: str = "authoritative_seed"
    created_at: str
    updated_at: str


class TaskComplexity(BaseModel):
    """Task complexity assessment based primarily on intrinsic task characteristics."""
    complexity_score: int = Field(..., ge=1, le=5, description="1=Trivial, 2=Small, 3=Medium, 4=Large, 5=Very Large")
    factors: List[str] = Field(default_factory=list, description="Explicit factors explaining complexity")
    confidence: ConfidenceLevel = Field(default=ConfidenceLevel.MEDIUM, description="Confidence in complexity rating")


class EffortStatistics(BaseModel):
    """Segmented statistical effort breakdown (P25, median, mean, P75)."""
    segment_type: str = Field(..., description="overall, issue_type, complexity, priority, project, role_category")
    segment_key: str = Field(..., description="e.g. Bug, 3, High, WSSS, WordPress Development")
    sample_count: int = Field(default=0)
    mean_hours: float = Field(default=0.0)
    median_hours: float = Field(default=0.0)
    p25_hours: float = Field(default=0.0)
    p75_hours: float = Field(default=0.0)
    min_hours: float = Field(default=0.0)
    max_hours: float = Field(default=0.0)
    confidence: ConfidenceLevel = Field(default=ConfidenceLevel.LOW)
    is_fallback: bool = Field(default=False, description="True if broader benchmark fallback used due to small sample")


class TaskDeliveryForecast(BaseModel):
    """Due date and effort forecast for an active queue task."""
    issue_key: str
    account_id: Optional[str] = None
    summary: Optional[str] = None
    due_date: Optional[str] = None
    jira_remaining_hours: Optional[float] = None
    inferred_expected_hours: float = 0.0
    inferred_remaining_hours: float = 0.0
    expected_base_hours: float = 0.0
    review_buffer_hours: float = 0.0
    total_expected_hours: float = 0.0
    logged_hours: float = 0.0
    remaining_hours: float = 0.0
    expected_effort_source: str = Field(..., description="Source methodology used for expected effort")
    expected_effort_confidence: str = Field(default="medium", description="high, medium, low, unavailable")
    sample_size: int = Field(default=0, description="Number of comparable historical samples used")
    designation: Optional[str] = None
    role_category: Optional[str] = None
    projected_completion_date: Optional[str] = None
    slack_hours: float = 0.0
    risk_level: RiskLevel = RiskLevel.GREEN
    risk_reason: Optional[str] = None


class PerformanceSignal(BaseModel):
    """Traceable, deterministic signal with observation evidence."""
    signal: SignalType
    value: Optional[float] = None
    threshold: Optional[float] = None
    evidence: str
    confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM


class PerformanceEvidence(BaseModel):
    """Auditable evidence record."""
    evidence_id: str
    analysis_run_id: str
    account_id: str
    issue_key: Optional[str] = None
    evidence_type: str
    observed_value: Optional[float] = None
    expected_value: Optional[float] = None
    difference: Optional[float] = None
    source: str
    confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM
    explanation: str
    timestamp: str
    snapshot_date: str


class ResourcePerformanceProfile(BaseModel):
    """Full deterministic Resource Performance Profile."""
    analysis_run_id: str
    algorithm_version: str = "1.0.0"

    resource: Dict[str, Any] = Field(
        ...,
        description="Resource identity: account_id, display_name, designation, role_category, role, team_group"
    )

    history: Dict[str, Any] = Field(
        ...,
        description="History window: requested_history_days, actual_available_history_days, start_date, end_date, days, completed_tasks, active_working_days, total_logged_seconds, rolling_windows"
    )

    workload: Dict[str, Any] = Field(
        ...,
        description="Workload metrics: average_daily_hours, median_daily_hours"
    )

    pace: Dict[str, Any] = Field(
        ...,
        description="Pace statistics: average_task_hours, median_task_hours, p25_task_hours, p75_task_hours, pace_factor, confidence"
    )

    delivery: Dict[str, Any] = Field(
        ...,
        description="Delivery metrics: tasks_due, tasks_completed, tasks_completed_on_time, tasks_completed_late, on_time_rate, average_days_late, median_days_late"
    )

    estimation: Dict[str, Any] = Field(
        ...,
        description="Estimation metrics: estimated_tasks, average_estimated_hours, average_actual_hours, estimation_variance_percent, median_estimation_variance_percent"
    )

    quality: Dict[str, Any] = Field(
        ...,
        description="Quality / rework indicators: reopened_tasks, reopen_rate"
    )

    blockers: Dict[str, Any] = Field(
        ...,
        description="Blocker metrics: blocker_count, blocked_seconds, average_blocker_hours"
    )

    distributions: Dict[str, Any] = Field(
        default_factory=dict,
        description="Task distributions by issue_type, priority, complexity, project"
    )

    capacity: Dict[str, Any] = Field(
        ...,
        description="Capacity model: nominal_capacity_hours, observed_logged_capacity_hours, forecast_capacity_hours, available_capacity_hours"
    )

    current_queue: Dict[str, Any] = Field(
        ...,
        description="Current queue: task_count, expected_base_hours, review_buffer_hours, total_expected_hours, remaining_hours, capacity_difference_hours"
    )

    forecast: Dict[str, Any] = Field(
        ...,
        description="Forecast overview: status (GREEN/YELLOW/ORANGE/RED), projected_completion, reason"
    )

    signals: List[PerformanceSignal] = Field(default_factory=list)
    evidence: List[PerformanceEvidence] = Field(default_factory=list)
    task_forecasts: List[TaskDeliveryForecast] = Field(default_factory=list)
    effort_statistics: List[EffortStatistics] = Field(default_factory=list)
    confidence_level: ConfidenceLevel = ConfidenceLevel.LOW


class PerformanceAnalysisRun(BaseModel):
    """Metadata for an executed performance analysis run."""
    analysis_run_id: str
    calculated_at: str
    analysis_window_start: str
    analysis_window_end: str
    requested_history_days: int = 365
    actual_available_history_days: int = 0
    algorithm_version: str = "1.0.0"
    team_group: Optional[str] = None
    resources_analyzed: int = 0
    tasks_analyzed: int = 0
    unresolved_employees_count: int = 0
    duration_ms: int = 0
    status: str = "COMPLETED"
    error_message: Optional[str] = None


class TeamPerformanceSummary(BaseModel):
    """Aggregated team-level performance foundation overview."""
    analysis_run_id: str
    calculated_at: str
    team_group: str
    algorithm_version: str = "1.0.0"
    resources_count: int
    active_resources_count: int
    total_completed_tasks_history: int
    total_active_queue_tasks: int
    total_active_queue_remaining_hours: float
    total_available_capacity_hours: float
    team_capacity_difference_hours: float
    resources_at_risk_count: int
    risk_breakdown: Dict[str, int]
    resources: List[Dict[str, Any]]
    unresolved_employees: List[Dict[str, Any]] = Field(default_factory=list)
