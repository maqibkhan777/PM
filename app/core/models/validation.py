"""Domain models and schemas for Phase A Data Quality & Analytics Validation.

Validates data integrity, historical coverage, worklog consistency, task mix,
expected-effort inference, role-aware categorization, capacity boundaries,
and blocker evidence without ranking, scoring, or evaluating human performance.
"""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from app.core.models.performance import ConfidenceLevel, RiskLevel


class DataCompletenessState(str, Enum):
    """Discrete data completeness classification for a specific dimension."""
    GOOD = "GOOD"
    PARTIAL = "PARTIAL"
    INSUFFICIENT = "INSUFFICIENT"
    UNAVAILABLE = "UNAVAILABLE"


class ValidationRecommendation(str, Enum):
    """Overall assessment of the performance foundation's data and calculation trustworthiness."""
    READY_FOR_AI_FOUNDATION = "READY_FOR_AI_FOUNDATION"
    READY_WITH_DATA_QUALITY_LIMITATIONS = "READY_WITH_DATA_QUALITY_LIMITATIONS"
    NOT_READY_FOR_AI_FOUNDATION = "NOT_READY_FOR_AI_FOUNDATION"


class AnomalyFlagType(str, Enum):
    """Deterministic, auditable anomaly and investigation flags."""
    DATA_GAP = "DATA_GAP"
    IDENTITY_SPLIT = "IDENTITY_SPLIT"
    LOW_SAMPLE_SIZE = "LOW_SAMPLE_SIZE"
    LOW_EFFORT_CONFIDENCE = "LOW_EFFORT_CONFIDENCE"
    HIGH_QUEUE_PRESSURE = "HIGH_QUEUE_PRESSURE"
    CAPACITY_OVERLOAD = "CAPACITY_OVERLOAD"
    HIGH_REOPEN_RATE = "HIGH_REOPEN_RATE"
    HIGH_OVERDUE_COUNT = "HIGH_OVERDUE_COUNT"
    MISSING_DESIGNATION = "MISSING_DESIGNATION"
    UNRESOLVED_IDENTITY = "UNRESOLVED_IDENTITY"
    UNUSUAL_WORKLOG_PATTERN = "UNUSUAL_WORKLOG_PATTERN"


class ValidationAnomaly(BaseModel):
    """Discrete investigation flag with supporting metrics and explanation."""
    flag: AnomalyFlagType
    account_id: Optional[str] = None
    display_name: Optional[str] = None
    issue_key: Optional[str] = None
    reason: str
    supporting_metric: Dict[str, Any] = Field(default_factory=dict)
    evidence: str
    confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM


class IdentityAuditRecord(BaseModel):
    """Audit item for a historical Jira user identity or alias."""
    raw_identifier: str
    display_name: str
    resolved_canonical_account_id: Optional[str] = None
    resolution_method: str = Field(..., description="AUTHORITATIVE_MAP, AUTHORITATIVE_SEED, EXACT_NAME, UNRESOLVED")
    affected_worklogs_count: int = 0
    affected_issues_count: int = 0
    inclusion_status: str = Field(..., description="INCLUDED_AUTHORITATIVE, INCLUDED_ACTIVE, EXCLUDED_GLOBAL, UNRESOLVED, DEACTIVATED")


class RoleCategoryValidationSummary(BaseModel):
    """Summary of data volume and metric health for a normalized role category."""
    role_category: str
    employee_count: int = 0
    task_volume: int = 0
    total_logged_hours: float = 0.0
    active_queue_tasks: int = 0
    complexity_distribution: Dict[str, int] = Field(default_factory=dict)
    expected_effort_source_distribution: Dict[str, int] = Field(default_factory=dict)
    confidence_distribution: Dict[str, int] = Field(default_factory=dict)
    historical_coverage: str = "INSUFFICIENT"


class EmployeeDataValidationProfile(BaseModel):
    """Individual data quality and analytical context breakdown for a team member."""
    employee_name: str
    account_id: str
    designation: str
    role_category: str
    identity_resolution_status: str
    historical_data_availability: Dict[str, Any] = Field(default_factory=dict)
    rolling_windows: Dict[str, Any] = Field(default_factory=dict)
    completed_tasks: int = 0
    active_tasks: int = 0
    total_logged_hours: float = 0.0
    active_working_days: int = 0
    average_hours_per_active_day: float = 0.0
    worklog_records_count: int = 0
    nominal_daily_capacity_hours: float = 6.75
    observed_daily_capacity_hours: float = 0.0
    forecast_daily_capacity_hours: float = 6.75
    active_expected_workload_hours: float = 0.0
    available_capacity_hours: float = 0.0
    capacity_difference_hours: float = 0.0
    forecast_status: RiskLevel = RiskLevel.GREEN
    task_complexity_distribution: Dict[str, int] = Field(default_factory=dict)
    issue_type_distribution: Dict[str, int] = Field(default_factory=dict)
    expected_effort_source_distribution: Dict[str, int] = Field(default_factory=dict)
    expected_effort_confidence_distribution: Dict[str, int] = Field(default_factory=dict)
    blocker_info: Dict[str, Any] = Field(default_factory=dict)
    reopened_tasks_count: int = 0
    reopened_tasks_keys: List[str] = Field(default_factory=list)
    stalled_tasks_count: int = 0
    stalled_tasks_keys: List[str] = Field(default_factory=list)
    overdue_tasks_count: int = 0
    overdue_tasks_keys: List[str] = Field(default_factory=list)
    tasks_due_soon_count: int = 0
    historical_pace_metrics: Dict[str, Any] = Field(default_factory=dict)
    data_completeness: Dict[str, DataCompletenessState] = Field(default_factory=dict)
    anomalies: List[ValidationAnomaly] = Field(default_factory=list)


class DataQualityValidationReport(BaseModel):
    """Comprehensive Data Quality & Analytics Validation Report."""
    validation_id: str
    analysis_run_id: str
    generated_at: str
    team_group: Optional[str] = None
    algorithm_version: str = "1.0.0"
    recommendation: ValidationRecommendation
    executive_summary: Dict[str, Any] = Field(default_factory=dict)
    team_population_validation: Dict[str, Any] = Field(default_factory=dict)
    identity_integrity_audit: List[IdentityAuditRecord] = Field(default_factory=list)
    historical_coverage_validation: Dict[str, Any] = Field(default_factory=dict)
    worklog_quality_validation: Dict[str, Any] = Field(default_factory=dict)
    task_throughput_validation: Dict[str, Any] = Field(default_factory=dict)
    expected_effort_quality_validation: Dict[str, Any] = Field(default_factory=dict)
    role_aware_analysis: List[RoleCategoryValidationSummary] = Field(default_factory=list)
    capacity_validation: Dict[str, Any] = Field(default_factory=dict)
    blocker_validation: Dict[str, Any] = Field(default_factory=dict)
    stalled_reopened_overdue_validation: Dict[str, Any] = Field(default_factory=dict)
    employee_profiles: List[EmployeeDataValidationProfile] = Field(default_factory=list)
    anomalies_requiring_review: List[ValidationAnomaly] = Field(default_factory=list)
    known_limitations: List[str] = Field(default_factory=list)
    foundation_readiness: Dict[str, Any] = Field(default_factory=dict)
