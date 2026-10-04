"""Domain models and schemas for Advisory Schedule Generation (Milestone 4B).

Advisory, explainable, read-only schedule proposal models:
- Grounded in Milestone 4A PlanningDataValidationReport
- Purely advisory proposals with tentative sequence and dates
- Clear distinction between explicit estimates, empirical benchmark proxies, and missing data
- Full disclosure of capacity limitations and nominal assumptions
- Zero Jira mutations, zero automated assignments, zero due-date writes
"""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.core.planning.validation_models import (
    CapacityDataSource,
    EstimateType,
    ValidationOutcome,
)


class ScheduleFeasibilityStatus(str, Enum):
    """Feasibility status of a generated advisory schedule proposal."""
    FEASIBLE = "FEASIBLE"                    # Workload fits within horizon and dependencies are acyclic
    PARTIALLY_CONSTRAINED = "PARTIALLY_CONSTRAINED"  # Some tasks blocked or beyond horizon, or proxy estimates used
    UNRELIABLE = "UNRELIABLE"                # Severe gaps (e.g. cycle, unresolved projects, missing critical data) prevent date projection


class ScheduledTaskProposal(BaseModel):
    """Advisory schedule proposal for an individual active Jira issue."""
    action_id: Optional[str] = None         # Deterministic execution action ID
    issue_key: str
    project_key: str
    summary: str
    assignee_name: Optional[str] = None
    assignee_role: Optional[str] = None
    priority: str = "Medium"
    queue_sequence: int = 1                  # 1-indexed deterministic sequence in resource queue
    
    # Effort & Estimation Details
    estimated_effort_hours: Optional[float] = None
    p50_effort_hours: Optional[float] = None
    p90_effort_hours: Optional[float] = None
    estimate_type: EstimateType = EstimateType.MISSING_UNESTIMATED
    is_proxy_estimate: bool = False
    estimate_source_description: str = ""
    benchmark_sample_count: Optional[int] = None
    benchmark_confidence_status: Optional[str] = None
    multiplier_applied: float = 1.0
    multiplier_reason: Optional[str] = None
    
    # Tentative Scheduling (Analytical only, NOT committed Jira due dates)
    dates_available: bool = True
    tentative_start_date: Optional[str] = None       # YYYY-MM-DD
    tentative_completion_date: Optional[str] = None  # YYYY-MM-DD
    working_days_needed: float = 0.0
    is_beyond_horizon: bool = False
    
    # Dependencies & Blockers
    is_blocked: bool = False
    unresolved_predecessor_keys: List[str] = Field(default_factory=list)
    successor_keys: List[str] = Field(default_factory=list)
    blocker_rationale: Optional[str] = None
    
    # Warnings & Disclosures
    warnings: List[str] = Field(default_factory=list)

    class Config:
        populate_by_name = True
        extra = "forbid"


class ResourceScheduleAudit(BaseModel):
    """Advisory queue sequence and timeline summary for a single resource."""
    account_id: str
    display_name: str
    role: Optional[str] = None
    team_group: Optional[str] = None
    
    # Capacity dimensions
    capacity_authority: CapacityDataSource = CapacityDataSource.NOMINAL_BASELINE
    available_capacity_hours: float = 65.0
    nominal_daily_hours: float = 6.50
    total_assigned_remaining_hours: float = 0.0
    allocation_percentage: float = 0.0
    is_overallocated: bool = False
    is_cross_project: bool = False
    projects_involved: List[str] = Field(default_factory=list)
    
    # Scheduled Tasks
    tasks_count: int = 0
    scheduled_tasks: List[ScheduledTaskProposal] = Field(default_factory=list)
    tasks_beyond_horizon: int = 0
    
    # Warnings & Disclosures
    resource_warnings: List[str] = Field(default_factory=list)

    class Config:
        populate_by_name = True
        extra = "forbid"


class AdvisoryScheduleProposal(BaseModel):
    """Root data model for an explainable, advisory schedule proposal across projects."""
    proposal_id: str
    proposal_version: int = 1
    generated_at: str
    anchor_date: str
    planning_horizon_working_days: int = 10
    horizon_end_date: str
    
    # Validation Grounding
    validation_report_id: str
    validation_status: ValidationOutcome
    feasibility_status: ScheduleFeasibilityStatus
    
    # Project Scope
    configured_projects: List[str] = Field(default_factory=list)
    resolved_projects: List[str] = Field(default_factory=list)
    unresolved_projects: List[str] = Field(default_factory=list)
    
    # Summary Metrics
    total_tasks_considered: int = 0
    tasks_scheduled_count: int = 0
    tasks_beyond_horizon_count: int = 0
    blocked_tasks_count: int = 0
    unassigned_tasks_count: int = 0
    
    # Estimation Breakdown
    tasks_explicit_estimates: int = 0
    tasks_benchmark_proxies: int = 0
    tasks_fallback_estimates: int = 0
    tasks_missing_estimates: int = 0
    
    # Resource Schedules & Workload
    resource_schedules: List[ResourceScheduleAudit] = Field(default_factory=list)
    unassigned_tasks: List[ScheduledTaskProposal] = Field(default_factory=list)
    
    # Critical Chains & Bottlenecks
    longest_dependency_chain: List[str] = Field(default_factory=list)
    cross_project_contention_resources: List[str] = Field(default_factory=list)
    
    # Disclosures & Assumptions
    capacity_formula_disclosure: str = "forecast_daily_hours (6.5h) * horizon_working_days (10d) = 65.0h nominal baseline"
    unknown_availability_disclosures: List[str] = Field(default_factory=list)
    sequencing_rules_explanation: List[str] = Field(default_factory=list)
    risks_and_assumptions: List[str] = Field(default_factory=list)
    
    # Safety Governance
    requires_human_review: bool = True
    is_advisory_only: bool = True
    human_review_disclaimer: str = (
        "Advisory proposal only. Tentative sequence and dates are analytical projections, NOT committed Jira delivery dates. "
        "Human review is required before any schedule or allocation decision."
    )

    class Config:
        populate_by_name = True
        extra = "forbid"
