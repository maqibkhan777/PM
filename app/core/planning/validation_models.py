"""Domain models and schemas for Planning Data and Cross-Project Capacity Validation (Milestone 4A).

Advisory, explainable, read-only validation structures:
- Resource cross-project workload analysis
- Capacity data completeness and reliability auditing
- Cross-project over-allocation risk detection
- Dependency and blocker feasibility analysis
"""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ValidationOutcome(str, Enum):
    """Overall validation status for planning data and capacity."""
    VALID = "VALID"            # All critical dimensions present and reliable
    PARTIAL = "PARTIAL"        # Usable with known data quality disclosures / fallback
    UNRELIABLE = "UNRELIABLE"  # Severe gaps (e.g. missing calendar, missing assignees) prevent definitive totals


class EstimateType(str, Enum):
    """Categorization of task remaining duration evidence."""
    EXPLICIT_REMAINING = "EXPLICIT_REMAINING"      # Jira remainingEstimate (> 0)
    EXPLICIT_ZERO = "EXPLICIT_ZERO"                # Jira remainingEstimate == 0 (explicitly marked completed/0)
    HISTORICAL_BENCHMARK = "HISTORICAL_BENCHMARK"  # Empirical benchmark estimate (Milestone 2/3)
    COMPLEXITY_FALLBACK = "COMPLEXITY_FALLBACK"    # Heuristic fallback based on issue type / complexity
    MISSING_UNESTIMATED = "MISSING_UNESTIMATED"    # No estimate provided in Jira, no benchmark available


class CapacityDataSource(str, Enum):
    """Authority status for capacity calculation dimensions."""
    AUTHORITATIVE = "AUTHORITATIVE"  # Authoritative source present (e.g. employee role assignment)
    NOMINAL_BASELINE = "NOMINAL_BASELINE"  # Default 6.75h nominal workday assumption
    UNKNOWN = "UNKNOWN"              # No authoritative calendar/leave/absence source in repository


class ValidatedTaskWorkload(BaseModel):
    """Validated task state for planning and workload analysis."""
    issue_key: str
    project_key: str
    summary: str
    status: str
    status_category: str = "In Progress"
    assignee_account_id: Optional[str] = None
    assignee_name: Optional[str] = None
    priority: str = "Medium"
    issue_type: str = "Task"
    due_date: Optional[str] = None
    
    # Estimate breakdown
    original_estimate_hours: Optional[float] = None
    time_spent_hours: Optional[float] = None
    remaining_estimate_hours: Optional[float] = None
    estimate_type: EstimateType = EstimateType.MISSING_UNESTIMATED
    is_proxy_estimate: bool = False
    estimate_rationale: str = ""
    
    # Dependencies & Blockers
    is_blocked: bool = False
    hard_blocker_keys: List[str] = Field(default_factory=list)
    has_unresolved_predecessors: bool = False
    inaccessible_link_keys: List[str] = Field(default_factory=list)
    
    # Source tracking
    data_source: str = "jira_issue_state"
    retrieval_timestamp: str = ""

    class Config:
        populate_by_name = True
        extra = "forbid"


class ResourceCapacityAudit(BaseModel):
    """Audit of a single resource's capacity and cross-project workload."""
    account_id: str
    display_name: str
    role: Optional[str] = None
    team_group: Optional[str] = None
    
    # Planning Window & Formula
    planning_horizon_working_days: int = 10
    nominal_daily_hours: float = 6.75
    forecast_daily_hours: float = 6.50
    capacity_formula: str = "forecast_daily_hours * horizon_working_days"
    available_capacity_hours: float = 65.0
    
    # Capacity Data Completeness
    working_hours_source: CapacityDataSource = CapacityDataSource.NOMINAL_BASELINE
    holidays_calendar_source: CapacityDataSource = CapacityDataSource.UNKNOWN
    leave_absence_source: CapacityDataSource = CapacityDataSource.UNKNOWN
    part_time_schedule_source: CapacityDataSource = CapacityDataSource.UNKNOWN
    focus_factor_source: CapacityDataSource = CapacityDataSource.UNKNOWN
    
    # Workload across projects
    assigned_tasks_count: int = 0
    projects_involved: List[str] = Field(default_factory=list)
    project_workload_hours: Dict[str, float] = Field(default_factory=dict)
    total_assigned_remaining_hours: float = 0.0
    
    # Cross-project over-allocation
    workload_pressure_ratio: float = 0.0  # remaining / available
    is_overallocated: bool = False
    cross_project_conflict_detected: bool = False
    
    # Audit Notes & Explanations
    capacity_reliability: ValidationOutcome = ValidationOutcome.PARTIAL
    unreliable_reasons: List[str] = Field(default_factory=list)
    data_quality_disclosures: List[str] = Field(default_factory=list)

    class Config:
        populate_by_name = True
        extra = "forbid"


class DependencyValidationFinding(BaseModel):
    """Finding related to task dependency and blocker feasibility."""
    source_issue_key: str
    target_issue_key: str
    link_type: str
    is_hard_block: bool
    is_target_resolved: bool
    is_cycle_detected: bool = False
    is_cross_project: bool = False
    source_project: str = ""
    target_project: str = ""
    notes: str = ""

    class Config:
        populate_by_name = True
        extra = "forbid"


class PlanningDataValidationReport(BaseModel):
    """Deterministic validation report for planning data across multiple projects."""
    report_id: str
    generated_at: str
    anchor_date: str
    planning_horizon_working_days: int = 10
    
    # Configured & Resolved Projects
    configured_projects: List[str] = Field(default_factory=list)
    resolved_projects: List[str] = Field(default_factory=list)
    unresolved_projects: List[str] = Field(default_factory=list)
    
    # Workload & Capacity Audit
    total_active_tasks: int = 0
    tasks_with_explicit_remaining: int = 0
    tasks_with_benchmark_estimates: int = 0
    tasks_with_missing_estimates: int = 0
    tasks_missing_assignee: int = 0
    
    # Resource Summaries
    resources_audited: List[ResourceCapacityAudit] = Field(default_factory=list)
    overallocated_resources: List[str] = Field(default_factory=list)
    cross_project_contention_resources: List[str] = Field(default_factory=list)
    
    # Dependencies & Blockers
    blocked_tasks_count: int = 0
    dependency_findings: List[DependencyValidationFinding] = Field(default_factory=list)
    
    # Scope and Data Integrity
    validated_tasks: List[ValidatedTaskWorkload] = Field(default_factory=list)
    deduplicated_issue_keys: List[str] = Field(default_factory=list)
    exclusions_and_reasons: Dict[str, str] = Field(default_factory=dict)
    
    # Overall Validation Outcome
    overall_status: ValidationOutcome = ValidationOutcome.PARTIAL
    executive_summary: str = ""
    capacity_limitations_summary: List[str] = Field(default_factory=list)

    class Config:
        populate_by_name = True
        extra = "forbid"
