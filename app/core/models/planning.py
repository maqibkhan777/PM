"""Planning domain models, enums, and typed dependency representations.

Phase 3A: Jira Issue Link Normalization and Dependency DAG Foundation.
Deterministic, offline, and provider-agnostic.
"""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class DependencyClassification(str, Enum):
    """Deterministic classification of Jira issue link types for planning and DAGs.

    Strictly maps the 9 confirmed Jira link types present in production:
    - HARD_BLOCK: Target task cannot proceed until source task is resolved (Blocks).
    - CAUSAL_DEPENDENCY: Causal defect/incident link (Problem/Incident).
    - VERIFICATION_DEPENDENCY: Verification link between QA/test and task (Test).
    - INFORMATIONAL: Symmetric informational association, non-blocking (Relates).
    - NON_DEPENDENCY: Provenance or workflow relation, non-blocking (Duplicate, Cloners, Polaris merge, Discovery).
    - CONTEXTUAL: Originating defect relationship (Defect).
    - UNKNOWN: Unrecognized link type, preserved safely without assuming hard blocking.
    """
    HARD_BLOCK = "HARD_BLOCK"
    CAUSAL_DEPENDENCY = "CAUSAL_DEPENDENCY"
    VERIFICATION_DEPENDENCY = "VERIFICATION_DEPENDENCY"
    INFORMATIONAL = "INFORMATIONAL"
    NON_DEPENDENCY = "NON_DEPENDENCY"
    CONTEXTUAL = "CONTEXTUAL"
    UNKNOWN = "UNKNOWN"


# Mapping from Jira link type name (case-insensitive) to deterministic classification
JIRA_LINK_TYPE_MAP: Dict[str, DependencyClassification] = {
    "blocks": DependencyClassification.HARD_BLOCK,
    "problem/incident": DependencyClassification.CAUSAL_DEPENDENCY,
    "test": DependencyClassification.VERIFICATION_DEPENDENCY,
    "relates": DependencyClassification.INFORMATIONAL,
    "duplicate": DependencyClassification.NON_DEPENDENCY,
    "cloners": DependencyClassification.NON_DEPENDENCY,
    "polaris merge work item link": DependencyClassification.NON_DEPENDENCY,
    "discovery - connected": DependencyClassification.NON_DEPENDENCY,
    "defect": DependencyClassification.CONTEXTUAL,
}


def classify_jira_link_type(link_type_name: Optional[str]) -> DependencyClassification:
    """Classify a Jira issue link type name safely and deterministically.

    Unknown link types default to DependencyClassification.UNKNOWN, never to HARD_BLOCK.
    """
    if not link_type_name or not isinstance(link_type_name, str):
        return DependencyClassification.UNKNOWN
    cleaned = link_type_name.strip().lower()
    return JIRA_LINK_TYPE_MAP.get(cleaned, DependencyClassification.UNKNOWN)


class JiraIssueLinkRecord(BaseModel):
    """Normalized representation of a directed Jira issue link relationship."""
    id: str = Field(..., description="Deterministic unique ID: source_key:target_key:link_type_name")
    source_issue_key: str = Field(..., description="Source/predecessor issue key (e.g., blocking issue)")
    target_issue_key: str = Field(..., description="Target/successor issue key (e.g., blocked issue)")
    link_type_name: str = Field(..., description="Raw Jira link type name (e.g. 'Blocks', 'Relates')")
    inward_description: Optional[str] = Field(None, description="e.g. 'is blocked by'")
    outward_description: Optional[str] = Field(None, description="e.g. 'blocks'")
    classification: DependencyClassification = Field(default=DependencyClassification.UNKNOWN)
    source_issue_id: Optional[str] = None
    target_issue_id: Optional[str] = None
    first_seen_at: str
    last_seen_at: str
    is_active: bool = True

    class Config:
        populate_by_name = True
        extra = "allow"


class DependencyEdge(BaseModel):
    """An edge in the dependency DAG from predecessor (source) to successor (target)."""
    source_key: str = Field(..., description="Predecessor issue key that must complete first")
    target_key: str = Field(..., description="Successor issue key that is blocked or dependent")
    link_type: str = Field(..., description="Raw Jira link type name")
    classification: DependencyClassification = Field(..., description="Dependency classification")
    is_hard_block: bool = Field(default=True, description="True if this edge represents a hard scheduling block")


class CycleDetectionResult(BaseModel):
    """Result of cycle detection on a dependency graph."""
    has_cycle: bool
    cycle_nodes: List[str] = Field(default_factory=list, description="Cycle path if detected e.g. ['A', 'B', 'C', 'A']")
    error_message: Optional[str] = None


class TopologicalSortResult(BaseModel):
    """Result of topological sorting on a dependency graph."""
    is_acyclic: bool
    ordered_keys: List[str] = Field(default_factory=list, description="Deterministically ordered issue keys")
    cycle_details: Optional[CycleDetectionResult] = None


# -------------------------------------------------------------------------
# Phase 3B: Artifact & Work Product Domain Models
# -------------------------------------------------------------------------

class ArtifactType(str, Enum):
    """Standard categorized work product types."""
    SPECIFICATION = "SPECIFICATION"
    DESIGN_ASSET = "DESIGN_ASSET"
    API_CONTRACT = "API_CONTRACT"
    DATABASE_MIGRATION = "DATABASE_MIGRATION"
    BUILD_PACKAGE = "BUILD_PACKAGE"
    TEST_SUITE = "TEST_SUITE"
    DOCUMENTATION = "DOCUMENTATION"
    GENERIC = "GENERIC"


class ArtifactProvenance(str, Enum):
    """Authoritative source or method through which the artifact/relationship was identified."""
    EXPLICIT_JIRA_LABEL = "EXPLICIT_JIRA_LABEL"
    EXPLICIT_JIRA_COMPONENT = "EXPLICIT_JIRA_COMPONENT"
    EXPLICIT_CONFIGURATION = "EXPLICIT_CONFIGURATION"
    TASK_NATURE_INFERENCE = "TASK_NATURE_INFERENCE"
    MANUAL = "MANUAL"


class ArtifactStatus(str, Enum):
    """Operational status of a work artifact."""
    PLANNED = "PLANNED"
    IN_PROGRESS = "IN_PROGRESS"
    AVAILABLE = "AVAILABLE"
    SUPERSEDED = "SUPERSEDED"
    UNKNOWN = "UNKNOWN"


class ArtifactRecord(BaseModel):
    """Canonical model for a tracked work product or deliverable."""
    id: str = Field(..., description="Deterministic unique ID: project_key:normalized_artifact_name")
    name: str = Field(..., description="Normalized artifact name e.g. 'api-spec', 'figma-design'")
    project_key: str = Field(..., description="Scoping Jira project key e.g. 'WSSS', 'GLOBAL'")
    artifact_type: ArtifactType = Field(default=ArtifactType.GENERIC)
    status: ArtifactStatus = Field(default=ArtifactStatus.PLANNED)
    producer_issue_key: Optional[str] = Field(None, description="Jira issue key that produces this artifact")
    provenance: ArtifactProvenance = Field(default=ArtifactProvenance.EXPLICIT_JIRA_LABEL)
    confidence: str = Field(default="HIGH", description="Confidence level: HIGH, MEDIUM, LOW")
    first_seen_at: str
    last_seen_at: str
    is_active: bool = True

    class Config:
        populate_by_name = True
        extra = "allow"


class ArtifactRelationshipRecord(BaseModel):
    """Relationship indicating that a task produces or consumes an artifact."""
    id: str = Field(..., description="Deterministic unique ID: artifact_id:issue_key:relationship_type")
    artifact_id: str = Field(..., description="ID of the associated artifact")
    issue_key: str = Field(..., description="Jira issue key")
    relationship_type: str = Field(..., description="'PRODUCES' or 'CONSUMES'")
    provenance: ArtifactProvenance = Field(default=ArtifactProvenance.EXPLICIT_JIRA_LABEL)
    confidence: str = Field(default="HIGH")
    is_inferred: bool = Field(default=False, description="True if inferred rather than explicitly declared")
    first_seen_at: str
    last_seen_at: str
    is_active: bool = True

    class Config:
        populate_by_name = True
        extra = "allow"


# -------------------------------------------------------------------------
# Phase 3C: Resource Queue & Capacity Intelligence Composition Models
# -------------------------------------------------------------------------

class CapacityState(str, Enum):
    """Deterministic capacity utilization classification."""
    UNDER_UTILIZED = "UNDER_UTILIZED"  # committed < 0.6 * available
    BALANCED = "BALANCED"              # 0.6 <= committed <= 1.0 * available
    OVERLOADED = "OVERLOADED"          # 1.0 < committed <= 1.4 * available
    SATURATED = "SATURATED"            # committed > 1.4 * available
    UNKNOWN = "UNKNOWN"


class QueueTaskDetail(BaseModel):
    """Deterministic, bounded representation of an active assigned Jira task."""
    issue_key: str
    summary: str = "Untitled"
    status: str = "Unknown"
    priority: str = "Medium"
    issue_type: str = "Task"
    task_nature: str = "UNKNOWN"
    project_key: str = "UNKNOWN"
    due_date: Optional[str] = None
    complexity_score: int = 3
    complexity_confidence: str = "medium"
    original_estimate_hours: Optional[float] = None
    time_spent_hours: float = 0.0
    remaining_hours: float = 0.0
    expected_effort_hours: float = 0.0
    expected_effort_source: str = "unavailable"
    expected_effort_confidence: str = "unavailable"
    is_overdue: bool = False
    is_stale: bool = False
    is_blocked: bool = False
    is_reopened: bool = False
    hard_blocker_keys: List[str] = Field(default_factory=list)
    produced_artifact_names: List[str] = Field(default_factory=list)
    consumed_artifact_names: List[str] = Field(default_factory=list)

    class Config:
        populate_by_name = True
        extra = "allow"


class ResourcePaceSummary(BaseModel):
    """Bounded historical pace metrics for a resource."""
    completed_task_count: int = 0
    mean_hours: float = 0.0
    median_hours: float = 0.0
    p25_hours: float = 0.0
    p75_hours: float = 0.0
    min_hours: float = 0.0
    max_hours: float = 0.0
    pace_factor: float = 1.0
    confidence: str = "INSUFFICIENT"
    is_fallback: bool = False


class ResourceCapacitySummary(BaseModel):
    """Deterministic capacity metrics for an operational planning horizon."""
    nominal_daily_capacity_hours: float = 6.75
    observed_daily_capacity_hours: float = 6.75
    forecast_daily_capacity_hours: float = 6.75
    horizon_working_days: int = 10  # Standard 2-week planning horizon
    available_capacity_hours: float = 67.5
    committed_workload_hours: float = 0.0
    remaining_capacity_hours: float = 67.5
    capacity_state: CapacityState = CapacityState.BALANCED
    capacity_method: str = "nominal_baseline_default"
    confidence: str = "LOW"


class ResourceDependencyContext(BaseModel):
    """Compact summary of dependency constraints involving this resource's tasks."""
    total_dependencies: int = 0
    hard_blocker_count: int = 0
    blocked_issue_keys: List[str] = Field(default_factory=list)
    downstream_dependent_keys: List[str] = Field(default_factory=list)


class ResourceArtifactContext(BaseModel):
    """Compact summary of work product handoffs involving this resource's tasks."""
    total_artifacts: int = 0
    produced_artifact_ids: List[str] = Field(default_factory=list)
    consumed_artifact_ids: List[str] = Field(default_factory=list)


class ResourceQueueSnapshot(BaseModel):
    """Deterministic, unified snapshot of a resource's active queue, capacity, and performance.
    
    Combines Phase A & B intelligence engines with Phase 3A & 3B dependencies.
    Strictly operational and analytical: contains ZERO employee rankings, scores, or due-date assignments.
    """
    # 1. Identity & Scope
    resource_id: str = Field(..., description="Authoritative canonical Atlassian account ID")
    display_name: str
    designation: Optional[str] = None
    role_category: Optional[str] = None
    team_group: Optional[str] = None
    snapshot_timestamp: str

    # 2. Current Active Queue Facts
    active_tasks: List[QueueTaskDetail] = Field(default_factory=list)
    active_task_count: int = 0
    overdue_task_count: int = 0
    stale_task_count: int = 0
    blocked_task_count: int = 0
    reopened_task_count: int = 0
    unestimated_task_count: int = 0  # Tasks lacking Jira original_estimate
    priority_summary: Dict[str, int] = Field(default_factory=dict)
    task_type_summary: Dict[str, int] = Field(default_factory=dict)
    task_nature_summary: Dict[str, int] = Field(default_factory=dict)
    total_inferred_remaining_hours: float = 0.0
    total_logged_hours: float = 0.0
    total_review_buffer_hours: float = 0.0

    # 3. Historical Performance Metrics
    historical_completed_tasks: int = 0
    historical_active_working_days: int = 0
    historical_pace: ResourcePaceSummary = Field(default_factory=ResourcePaceSummary)
    personal_baseline_summary: Optional[Dict[str, Any]] = None

    # 4. Capacity Model
    capacity: ResourceCapacitySummary = Field(default_factory=ResourceCapacitySummary)

    # 5. Workload Pressure & Signals
    workload_pressure_level: str = "UNKNOWN"
    workload_pressure_explanation: str = ""
    tasks_due_within_7_days: int = 0
    high_complexity_tasks_count: int = 0

    # 6. Dependency & Artifact Reference Context
    dependency_context: ResourceDependencyContext = Field(default_factory=ResourceDependencyContext)
    artifact_context: ResourceArtifactContext = Field(default_factory=ResourceArtifactContext)

    # 7. Data Quality & Readiness
    history_completeness: str = "NO_HISTORY"  # "SUFFICIENT_HISTORY", "LIMITED_HISTORY", "NO_HISTORY"
    capacity_quality: str = "CAPACITY_UNAVAILABLE"  # "CAPACITY_KNOWN", "CAPACITY_PARTIAL", "CAPACITY_UNAVAILABLE"
    queue_completeness: str = "QUEUE_EMPTY"  # "QUEUE_COMPLETE", "QUEUE_PARTIAL", "QUEUE_EMPTY"
    data_quality_notes: List[str] = Field(default_factory=list)

    class Config:
        populate_by_name = True
        extra = "allow"


class TeamWorkloadSnapshot(BaseModel):
    """Deterministic collection of ResourceQueueSnapshots for a team or project group.
    
    Provides foundational evidence for future TeamScheduleForecaster and PlanningContextBuilder.
    Does NOT score, rank, or compare employees hierarchically.
    """
    snapshot_timestamp: str
    team_group: Optional[str] = None
    resources_count: int = 0
    total_active_tasks: int = 0
    total_remaining_workload_hours: float = 0.0
    total_available_capacity_hours: float = 0.0
    total_overdue_tasks: int = 0
    total_blocked_tasks: int = 0
    resource_snapshots: List[ResourceQueueSnapshot] = Field(default_factory=list)

    class Config:
        populate_by_name = True
        extra = "allow"


# -------------------------------------------------------------------------
# Phase 3D: Deterministic Team Timeline & Bottleneck Forecasting Models
# -------------------------------------------------------------------------

class BottleneckType(str, Enum):
    """Deterministic categorization of operational and schedule bottlenecks."""
    OVERLOADED_RESOURCE = "OVERLOADED_RESOURCE"
    DEPENDENCY_CHAIN = "DEPENDENCY_CHAIN"
    BLOCKED_TASK = "BLOCKED_TASK"
    ARTIFACT_HANDOFF = "ARTIFACT_HANDOFF"
    INSUFFICIENT_CAPACITY = "INSUFFICIENT_CAPACITY"
    LONG_DURATION_TASK = "LONG_DURATION_TASK"
    DEPENDENCY_CYCLE = "DEPENDENCY_CYCLE"
    MISSING_DURATION_EVIDENCE = "MISSING_DURATION_EVIDENCE"


class Bottleneck(BaseModel):
    """Deterministic, rule-based operational bottleneck signal.
    
    Contains strictly operational and analytical planning facts.
    Contains ZERO employee scoring, rankings, or productivity ratings.
    """
    type: BottleneckType
    affected_issue_key: Optional[str] = None
    affected_resource_id: Optional[str] = None
    severity: str = "MEDIUM"  # "HIGH", "MEDIUM", "LOW"
    evidence: str = ""
    data_quality: str = "UNKNOWN"  # "HIGH", "MEDIUM", "LOW", "UNKNOWN"

    class Config:
        populate_by_name = True
        extra = "allow"


class ScheduleConstraint(BaseModel):
    """A directed dependency or handoff constraint between two tasks in the timeline."""
    source_issue_key: str
    target_issue_key: str
    constraint_type: str  # "HARD_BLOCK", "ARTIFACT_HANDOFF", "CAUSAL_DEPENDENCY", "VERIFICATION_DEPENDENCY", etc.
    is_hard_block: bool = True
    description: str = ""

    class Config:
        populate_by_name = True
        extra = "allow"


class TaskScheduleProjection(BaseModel):
    """Deterministic projected schedule timeline for an individual active task.
    
    All dates are analytical projections, NOT committed Jira due dates or Jira mutations.
    """
    issue_key: str
    resource_id: str
    resource_display_name: str
    current_status: str = "Unknown"
    estimated_effort_hours: float = 0.0
    duration_evidence_source: str = "unavailable"
    duration_confidence: str = "UNKNOWN"
    dependency_predecessors: List[str] = Field(default_factory=list)
    dependency_successors: List[str] = Field(default_factory=list)
    earliest_feasible_start_date: str  # YYYY-MM-DD
    projected_start_date: str          # YYYY-MM-DD
    projected_completion_date: str     # YYYY-MM-DD
    working_days_needed: float = 0.0
    is_blocked_by_dependency: bool = False
    is_beyond_horizon: bool = False
    critical_chain_position: Optional[int] = None

    class Config:
        populate_by_name = True
        extra = "allow"


class TeamScheduleProjection(BaseModel):
    """Deterministic team-level timeline projection and bottleneck analysis.
    
    Composes ResourceQueueSnapshots, DependencyDAG, and Artifact handoffs.
    Does NOT write to Jira, reschedule tasks, or call AI/DeepSeek.
    """
    forecast_timestamp: str
    anchor_date: str  # YYYY-MM-DD
    planning_horizon_working_days: int = 10
    horizon_end_date: str  # YYYY-MM-DD
    schedule_valid: bool = True
    tasks_projected_count: int = 0
    tasks_beyond_horizon_count: int = 0
    task_projections: List[TaskScheduleProjection] = Field(default_factory=list)
    longest_dependency_chain: List[str] = Field(default_factory=list)
    dependency_constraints: List[ScheduleConstraint] = Field(default_factory=list)
    bottlenecks: List[Bottleneck] = Field(default_factory=list)
    data_quality_summary: Dict[str, Any] = Field(default_factory=dict)
    error_message: Optional[str] = None

    class Config:
        populate_by_name = True
        extra = "allow"


