"""Domain models and metrics schema for Read-Only Historical Jira Data Quality Probe.

Designed for Phase 1 Milestone 1 of the PM AI Historical Work Intelligence initiative.
Strictly read-only; separates elapsed lifecycle time from logged effort.
Separates metrics on a strict per-project basis.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class FeasibilityRecommendation(str, Enum):
    """Recommendation on readiness for an estimation prototype."""
    READY = "READY"
    READY_WITH_RESERVATIONS = "READY_WITH_RESERVATIONS"
    NOT_RECOMMENDED = "NOT_RECOMMENDED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class MetricCounter(BaseModel):
    """Statistical count with percentage representation."""
    count: int = 0
    total: int = 0
    percentage: float = 0.0
    notes: Optional[str] = None


class LifecycleVsEffortSummary(BaseModel):
    """Clear distinction between elapsed lifecycle time and actual logged effort."""
    issues_with_lifecycle_time: int = 0
    total_elapsed_lifecycle_seconds: float = 0.0
    avg_elapsed_lifecycle_hours: float = 0.0
    p50_elapsed_lifecycle_hours: float = 0.0

    issues_with_logged_effort: int = 0
    total_logged_effort_seconds: float = 0.0
    avg_logged_effort_hours: float = 0.0
    p50_logged_effort_hours: float = 0.0

    lifecycle_effort_discrepancy_note: str = (
        "Elapsed lifecycle time (created-to-resolved duration) measures wall-clock calendar lead time "
        "and includes queue wait/idle time. It must NEVER be conflated with logged developer effort."
    )


class SingleIssueQualityRecord(BaseModel):
    """Per-issue normalized diagnostic quality record."""
    issue_key: str
    project_key: str
    has_created: bool = False
    has_resolved: bool = False
    has_updated: bool = False
    created_at: Optional[str] = None
    resolved_at: Optional[str] = None
    updated_at: Optional[str] = None
    elapsed_lifecycle_seconds: Optional[float] = None

    has_issue_type: bool = False
    issue_type_name: Optional[str] = None
    has_priority: bool = False
    priority_name: Optional[str] = None
    has_assignee: bool = False
    assignee_account_id: Optional[str] = None
    has_components: bool = False
    components_count: int = 0
    has_labels: bool = False
    labels_count: int = 0
    has_parent_or_epic: bool = False
    parent_key: Optional[str] = None

    has_original_estimate: bool = False
    original_estimate_seconds: Optional[int] = None
    is_original_estimate_zero: bool = False

    has_time_spent: bool = False
    time_spent_seconds: Optional[int] = None
    is_time_spent_zero: bool = False

    has_worklogs: bool = False
    worklog_count: int = 0
    total_worklog_logged_seconds: int = 0

    has_changelog: bool = False
    status_transition_count: int = 0
    has_estimate_changes: bool = False
    has_reopen_transitions: bool = False


class ProjectQualityReport(BaseModel):
    """Historical data quality analysis metrics for a single Jira project."""
    project_key: str
    date_range_start: str
    date_range_end: str
    days_evaluated: int
    cap_limit: int
    total_issues_retrieved: int = 0
    cap_reached: bool = False

    # Timestamp Coverage
    valid_created_timestamp: MetricCounter = Field(default_factory=MetricCounter)
    valid_resolved_timestamp: MetricCounter = Field(default_factory=MetricCounter)
    valid_updated_timestamp: MetricCounter = Field(default_factory=MetricCounter)
    valid_lifecycle_pair: MetricCounter = Field(default_factory=MetricCounter)

    # Core Attributes Coverage
    has_issue_type: MetricCounter = Field(default_factory=MetricCounter)
    has_priority: MetricCounter = Field(default_factory=MetricCounter)
    has_assignee: MetricCounter = Field(default_factory=MetricCounter)
    has_components: MetricCounter = Field(default_factory=MetricCounter)
    has_labels: MetricCounter = Field(default_factory=MetricCounter)
    has_parent_or_epic: MetricCounter = Field(default_factory=MetricCounter)

    # Estimates & Effort Coverage
    has_original_estimate: MetricCounter = Field(default_factory=MetricCounter)
    original_estimate_zero_count: int = 0
    original_estimate_missing_count: int = 0


    has_time_spent: MetricCounter = Field(default_factory=MetricCounter)
    time_spent_zero_count: int = 0
    time_spent_missing_count: int = 0

    has_at_least_one_worklog: MetricCounter = Field(default_factory=MetricCounter)
    total_worklog_logged_seconds: int = 0
    total_worklog_logged_hours: float = 0.0

    # Changelog / Transitions
    changelog_available: MetricCounter = Field(default_factory=MetricCounter)
    has_estimate_changes: MetricCounter = Field(default_factory=MetricCounter)
    has_reopen_transitions: MetricCounter = Field(default_factory=MetricCounter)

    # Separation of Lifecycle Duration vs Logged Effort
    lifecycle_vs_effort: LifecycleVsEffortSummary = Field(default_factory=LifecycleVsEffortSummary)

    # Limitations, Errors & Categorical distinctions
    data_quality_limitations: List[str] = Field(default_factory=list)
    api_errors: List[str] = Field(default_factory=list)
    partial_failure: bool = False
    error_message: Optional[str] = None

    # Recommendation
    recommendation: FeasibilityRecommendation = FeasibilityRecommendation.INSUFFICIENT_DATA
    recommendation_rationale: str = ""


class MultiProjectProbeSummary(BaseModel):
    """Aggregated probe container for multiple projects (strictly separating per-project metrics)."""
    probe_id: str
    timestamp: str
    project_keys: List[str]
    lookback_days: int
    cap_per_project: int
    project_reports: Dict[str, ProjectQualityReport] = Field(default_factory=dict)
    summary_notes: List[str] = Field(default_factory=list)
