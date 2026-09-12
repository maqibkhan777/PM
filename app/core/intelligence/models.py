"""Domain models and schemas for Phase B v1.1 Historical Intelligence & Evidence Layer.

Strictly deterministic, explainable, and factual.
Contains ZERO employee ranking, leaderboards, or productivity scores.
Designed as the structured evidence data contract for a future AI analyst layer.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.core.models.performance import ConfidenceLevel, RiskLevel, RoleCategory


class TaskNature(str, Enum):
    """Deterministic task nature categories based on Jira attributes."""
    DEVELOPMENT = "DEVELOPMENT"
    BUG_FIX = "BUG_FIX"
    ENHANCEMENT = "ENHANCEMENT"
    MAINTENANCE = "MAINTENANCE"
    RESEARCH = "RESEARCH"
    BUSINESS_ANALYSIS = "BUSINESS_ANALYSIS"
    QA_TESTING = "QA_TESTING"
    CONTENT = "CONTENT"
    SEO = "SEO"
    CUSTOMER_SUPPORT = "CUSTOMER_SUPPORT"
    DESIGN = "DESIGN"
    DOCUMENTATION = "DOCUMENTATION"
    REVIEW = "REVIEW"
    DEPLOYMENT = "DEPLOYMENT"
    COMMUNICATION = "COMMUNICATION"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class BaselineComparisonState(str, Enum):
    """Statistical comparison of current metric against personal historical baseline."""
    ABOVE_PERSONAL_BASELINE = "ABOVE_PERSONAL_BASELINE"
    NEAR_PERSONAL_BASELINE = "NEAR_PERSONAL_BASELINE"
    BELOW_PERSONAL_BASELINE = "BELOW_PERSONAL_BASELINE"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"


class TrendDirection(str, Enum):
    """Deterministic rolling trend direction."""
    INCREASING = "INCREASING"
    DECREASING = "DECREASING"
    STABLE = "STABLE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class WorkloadPressureLevel(str, Enum):
    """Contextual workload pressure state (not a performance rating)."""
    LOW = "LOW"
    NORMAL = "NORMAL"
    ELEVATED = "ELEVATED"
    HIGH = "HIGH"
    UNKNOWN = "UNKNOWN"


class ReviewReworkReason(str, Enum):
    """Explicitly evidenced reason for reopen or rework."""
    QA_REWORK = "QA_REWORK"
    REQUIREMENT_CHANGE = "REQUIREMENT_CHANGE"
    CUSTOMER_CHANGE = "CUSTOMER_CHANGE"
    TECHNICAL_ISSUE = "TECHNICAL_ISSUE"
    UNKNOWN = "UNKNOWN"


class DataCompletenessRating(str, Enum):
    """Data completeness state for profile dimensions."""
    GOOD = "GOOD"
    PARTIAL = "PARTIAL"
    INSUFFICIENT = "INSUFFICIENT"
    UNAVAILABLE = "UNAVAILABLE"


class AIReadinessStatus(str, Enum):
    """Readiness of historical intelligence evidence for future AI layer consumption."""
    READY_FOR_AI_LAYER = "READY_FOR_AI_LAYER"
    READY_WITH_LIMITATIONS = "READY_WITH_LIMITATIONS"
    NOT_READY_FOR_AI_LAYER = "NOT_READY_FOR_AI_LAYER"


# -------------------------------------------------------------------------
# Sub-Models & Component Profiles
# -------------------------------------------------------------------------

class TaskNatureClassification(BaseModel):
    """Task nature classification result for a specific Jira issue."""
    issue_key: str
    task_nature: TaskNature
    classification_source: str = Field(..., description="issue_type, component, label, summary_keyword, fallback")
    classification_confidence: ConfidenceLevel = Field(default=ConfidenceLevel.MEDIUM)
    matched_rule: str
    components: List[str] = Field(default_factory=list)
    labels: List[str] = Field(default_factory=list)


class TaskMixDistributionItem(BaseModel):
    """Individual item distribution in task mix."""
    key: str
    count: int = 0
    percentage: float = 0.0
    logged_hours: float = 0.0


class TaskMixProfile(BaseModel):
    """Comprehensive historical task mix profile across multiple dimensions."""
    total_tasks: int = 0
    issue_type_distribution: List[TaskMixDistributionItem] = Field(default_factory=list)
    task_nature_distribution: List[TaskMixDistributionItem] = Field(default_factory=list)
    complexity_distribution: List[TaskMixDistributionItem] = Field(default_factory=list)
    priority_distribution: List[TaskMixDistributionItem] = Field(default_factory=list)
    project_distribution: List[TaskMixDistributionItem] = Field(default_factory=list)
    subtask_count: int = 0
    primary_task_nature: TaskNature = TaskNature.UNKNOWN
    primary_issue_type: str = "Unknown"


class HistoricalEffortBenchmark(BaseModel):
    """Statistical effort benchmark across 11 segmentation tiers."""
    segmentation_tier: str = Field(..., description="1-11 segmentation level")
    segment_type: str = Field(..., description="employee, role, team, etc.")
    segment_key: str = Field(..., description="Identifier or composite key")
    sample_count: int = 0
    mean_hours: float = 0.0
    median_hours: float = 0.0
    p25_hours: float = 0.0
    p75_hours: float = 0.0
    min_hours: float = 0.0
    max_hours: float = 0.0
    stddev_hours: float = 0.0
    confidence: ConfidenceLevel = ConfidenceLevel.LOW
    is_fallback: bool = False


class MetricBaselineItem(BaseModel):
    """Individual personal baseline comparison for a specific operational metric."""
    metric_name: str
    typical_historical_value: float = 0.0
    current_value: float = 0.0
    difference: float = 0.0
    percent_difference: float = 0.0
    comparison_state: BaselineComparisonState = BaselineComparisonState.INSUFFICIENT_HISTORY
    explanation: str = ""


class PersonalBaselineProfile(BaseModel):
    """Employee personal historical baselines vs current workload state."""
    account_id: str
    has_sufficient_history: bool = False
    active_queue_baseline: MetricBaselineItem
    logged_hours_baseline: MetricBaselineItem
    complexity_baseline: MetricBaselineItem
    expected_effort_baseline: MetricBaselineItem
    reopen_rate_baseline: MetricBaselineItem
    overdue_rate_baseline: MetricBaselineItem


class RollingTrendMetric(BaseModel):
    """Metric values across rolling windows and derived trend direction."""
    metric_name: str
    value_30d: float = 0.0
    value_90d: float = 0.0
    value_180d: float = 0.0
    value_365d: float = 0.0
    direction: TrendDirection = TrendDirection.INSUFFICIENT_DATA
    explanation: str = ""


class HistoricalTrendsProfile(BaseModel):
    """Rolling trend analysis across 30d, 90d, 180d, and 365d windows."""
    account_id: str
    logged_hours_trend: RollingTrendMetric
    completed_tasks_trend: RollingTrendMetric
    active_queue_trend: RollingTrendMetric
    expected_workload_trend: RollingTrendMetric
    complexity_trend: RollingTrendMetric
    reopen_rate_trend: RollingTrendMetric
    overdue_rate_trend: RollingTrendMetric
    blocker_hours_trend: RollingTrendMetric
    capacity_pressure_trend: RollingTrendMetric


class WorkloadPressureAssessment(BaseModel):
    """Contextual workload pressure evaluation."""
    account_id: str
    pressure_level: WorkloadPressureLevel = WorkloadPressureLevel.UNKNOWN
    active_tasks_count: int = 0
    inferred_remaining_workload_hours: float = 0.0
    forecast_capacity_hours: float = 0.0
    capacity_difference_hours: float = 0.0
    tasks_due_within_7_days: int = 0
    high_complexity_tasks_count: int = 0
    effort_confidence: str = "medium"
    active_blockers_count: int = 0
    explanation: str = ""


class ReviewReworkEvent(BaseModel):
    """Explicitly evidenced review or rework occurrence."""
    issue_key: str
    reason: ReviewReworkReason
    evidence_text: str
    timestamp: str
    author_name: Optional[str] = None


class ReviewReworkProfile(BaseModel):
    """Review and rework analysis with explicit evidence classification."""
    account_id: str
    reopened_tasks_count: int = 0
    total_reopen_events: int = 0
    rework_reasons_breakdown: Dict[str, int] = Field(default_factory=dict)
    rework_events: List[ReviewReworkEvent] = Field(default_factory=list)


class BlockerHistoryProfile(BaseModel):
    """Historical blocker frequency, duration, and task impact."""
    account_id: str
    total_blocker_events: int = 0
    total_blocked_seconds: int = 0
    total_blocked_hours: float = 0.0
    average_blocker_hours: float = 0.0
    affected_tasks_count: int = 0
    affected_tasks_keys: List[str] = Field(default_factory=list)
    active_period_blocker_percentage: float = 0.0


class DeliveryContextProfile(BaseModel):
    """Historical due-date delivery context (not an employee penalty)."""
    account_id: str
    total_completed_tasks: int = 0
    completed_before_due_date: int = 0
    completed_on_due_date: int = 0
    completed_after_due_date: int = 0
    currently_overdue: int = 0
    tasks_without_due_date: int = 0
    due_date_coverage_percent: float = 0.0
    average_days_late: float = 0.0
    correlated_blocker_count: int = 0
    correlated_missing_estimates_count: int = 0


class DataQualityProfile(BaseModel):
    """Completeness and confidence breakdown for an employee's evidence profile."""
    identity_completeness: DataCompletenessRating = DataCompletenessRating.GOOD
    historical_completeness: DataCompletenessRating = DataCompletenessRating.GOOD
    worklog_completeness: DataCompletenessRating = DataCompletenessRating.GOOD
    task_classification_completeness: DataCompletenessRating = DataCompletenessRating.GOOD
    expected_effort_completeness: DataCompletenessRating = DataCompletenessRating.GOOD
    role_completeness: DataCompletenessRating = DataCompletenessRating.GOOD
    confidence_limitations: List[str] = Field(default_factory=list)


class HistoricalEvidenceRecord(BaseModel):
    """Immutable, auditable historical evidence record for future AI reference."""
    evidence_id: str
    analysis_run_id: str
    account_id: str
    issue_key: Optional[str] = None
    evidence_type: str = Field(..., description="TASK_COMPLETION, WORKLOG, QUEUE, CAPACITY, BLOCKER, REOPEN, OVERDUE, TASK_COMPLEXITY, TASK_NATURE, EFFORT_ESTIMATE, ROLE_CONTEXT, TREND, WORKLOAD_PRESSURE")
    metric: str
    value: Optional[float] = None
    comparison_baseline: Optional[str] = None
    source: str
    confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM
    timestamp: str
    explanation: str


# -------------------------------------------------------------------------
# Top-Level AI-Ready Historical Intelligence Profile Contract
# -------------------------------------------------------------------------

class HistoricalIntelligenceProfile(BaseModel):
    """Comprehensive, structured historical intelligence profile for a single employee.
    
    Primary evidence contract for future AI decision-support and analysis layers.
    Contains NO performance score, ranking, or leaderboard metric.
    """
    analysis_run_id: str
    calculated_at: str
    algorithm_version: str = "1.1.0"
    
    # 1. Identity & Role Context
    account_id: str
    display_name: str
    designation: str = "Unknown"
    role_category: str = "Unknown"
    team_group: Optional[str] = None
    identity_resolution_status: str = "CANONICAL_RESOLVED"
    known_aliases: List[str] = Field(default_factory=list)
    
    # 2. Historical Coverage
    requested_history_days: int = 365
    actual_available_history_days: int = 0
    earliest_record_date: Optional[str] = None
    latest_record_date: Optional[str] = None
    
    # 3. Work Profile & Task Mix
    task_mix: TaskMixProfile
    
    # 4. Effort Profile & Benchmarks
    total_logged_hours: float = 0.0
    active_working_days: int = 0
    average_logged_hours_per_active_day: float = 0.0
    median_logged_hours_per_active_day: float = 0.0
    effort_benchmarks: List[HistoricalEffortBenchmark] = Field(default_factory=list)
    
    # 5. Capacity & Queue Profile
    nominal_daily_capacity_hours: float = 6.75
    observed_daily_capacity_hours: float = 6.75
    forecast_daily_capacity_hours: float = 6.75
    current_active_tasks_count: int = 0
    current_queue_inferred_remaining_hours: float = 0.0
    capacity_difference_hours: float = 0.0
    
    # 6. Workload Pressure & Personal Baseline
    workload_pressure: WorkloadPressureAssessment
    personal_baseline: PersonalBaselineProfile
    
    # 7. Rolling Trends (30d / 90d / 180d / 365d)
    trends: HistoricalTrendsProfile
    
    # 8. Delivery & Context
    delivery_context: DeliveryContextProfile
    blocker_history: BlockerHistoryProfile
    review_rework: ReviewReworkProfile
    
    # 9. Data Quality & Investigation Signals
    data_quality: DataQualityProfile
    investigation_signals: List[str] = Field(default_factory=list)
    
    # 10. Evidence References
    evidence_records_count: int = 0
    evidence_sample: List[HistoricalEvidenceRecord] = Field(default_factory=list)
