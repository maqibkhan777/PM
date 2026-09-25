"""Planning domain models, enums, and typed dependency representations.

Phase 3A: Jira Issue Link Normalization and Dependency DAG Foundation.
Deterministic, offline, and provider-agnostic.
"""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator



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


# -------------------------------------------------------------------------
# Phase 3E: Bounded, Provider-Agnostic Planning Context Domain Models
# -------------------------------------------------------------------------

class ContextTruncationMetadata(BaseModel):
    """Deterministic metadata recording context bounds and any truncation."""
    is_truncated: bool = False
    original_resource_count: int = 0
    included_resource_count: int = 0
    original_task_count: int = 0
    included_task_count: int = 0
    original_dependency_count: int = 0
    included_dependency_count: int = 0
    original_artifact_count: int = 0
    included_artifact_count: int = 0
    original_bottleneck_count: int = 0
    included_bottleneck_count: int = 0
    truncation_reasons: List[str] = Field(default_factory=list)

    class Config:
        populate_by_name = True
        extra = "allow"


class PlanningTeamSummary(BaseModel):
    """Bounded, high-level summary of team capacity, queue depth, and schedule state."""
    resource_count: int = 0
    active_task_count: int = 0
    overloaded_resource_count: int = 0
    blocked_task_count: int = 0
    overdue_task_count: int = 0
    tasks_beyond_horizon_count: int = 0
    total_remaining_effort_hours: float = 0.0
    total_available_capacity_hours: float = 0.0
    schedule_valid: bool = True

    class Config:
        populate_by_name = True
        extra = "allow"


class PlanningResourceContext(BaseModel):
    """Bounded, provider-neutral representation of a single resource's planning state."""
    resource_id: str
    display_name: str
    role: Optional[str] = None
    role_category: Optional[str] = None
    team_group: Optional[str] = None
    active_task_count: int = 0
    current_workload_hours: float = 0.0
    remaining_effort_hours: float = 0.0
    available_capacity_hours: float = 0.0
    capacity_state: CapacityState = CapacityState.BALANCED
    workload_pressure: str = "UNKNOWN"
    historical_pace: ResourcePaceSummary = Field(default_factory=ResourcePaceSummary)
    personal_baseline: Optional[Dict[str, Any]] = None
    history_completeness: str = "NO_HISTORY"
    capacity_quality: str = "CAPACITY_UNAVAILABLE"
    queue_completeness: str = "QUEUE_EMPTY"
    data_quality_notes: List[str] = Field(default_factory=list)

    class Config:
        populate_by_name = True
        extra = "allow"


class PlanningTaskContext(BaseModel):
    """Bounded planning facts for an active Jira issue."""
    issue_key: str
    assigned_resource_id: Optional[str] = None
    assigned_resource_name: Optional[str] = None
    summary: str = "Untitled"
    status: str = "Unknown"
    priority: str = "Medium"
    issue_type: str = "Task"
    task_nature: str = "UNKNOWN"
    project_key: str = "UNKNOWN"
    estimated_remaining_hours: float = 0.0
    duration_evidence_source: str = "unavailable"
    duration_confidence: str = "unavailable"
    due_date: Optional[str] = None
    is_overdue: bool = False
    is_stale: bool = False
    is_blocked: bool = False
    is_reopened: bool = False
    projected_start_date: Optional[str] = None
    projected_completion_date: Optional[str] = None
    is_beyond_horizon: bool = False
    predecessor_keys: List[str] = Field(default_factory=list)
    successor_keys: List[str] = Field(default_factory=list)
    produced_artifact_names: List[str] = Field(default_factory=list)
    consumed_artifact_names: List[str] = Field(default_factory=list)

    class Config:
        populate_by_name = True
        extra = "allow"


class PlanningDependencyContext(BaseModel):
    """Normalized, bounded cross-task dependency relationship facts."""
    source_issue_key: str
    target_issue_key: str
    link_type: str
    classification: DependencyClassification
    is_hard_block: bool = True
    is_advisory: bool = False
    provenance: str = "JIRA_ISSUE_LINK"
    confidence: str = "HIGH"

    class Config:
        populate_by_name = True
        extra = "allow"


class PlanningArtifactContext(BaseModel):
    """Normalized, bounded work product handoff facts."""
    artifact_name: str
    project_key: str
    artifact_type: ArtifactType = ArtifactType.GENERIC
    status: ArtifactStatus = ArtifactStatus.PLANNED
    producer_issue_key: Optional[str] = None
    producer_resource_id: Optional[str] = None
    consumer_issue_keys: List[str] = Field(default_factory=list)
    consumer_resource_ids: List[str] = Field(default_factory=list)
    relationship_type: str = "PRODUCES"
    is_inferred: bool = False
    is_advisory: bool = False
    provenance: str = "EXPLICIT_JIRA_LABEL"
    confidence: str = "HIGH"

    class Config:
        populate_by_name = True
        extra = "allow"


class PlanningScheduleSummary(BaseModel):
    """Bounded team timeline forecast summary and deterministic bottlenecks."""
    schedule_valid: bool = True
    anchor_date: str
    planning_horizon_working_days: int = 10
    horizon_end_date: str
    tasks_projected_count: int = 0
    tasks_beyond_horizon_count: int = 0
    longest_dependency_chain: List[str] = Field(default_factory=list)
    bottlenecks: List[Bottleneck] = Field(default_factory=list)
    data_quality_summary: Dict[str, Any] = Field(default_factory=dict)
    error_message: Optional[str] = None

    class Config:
        populate_by_name = True
        extra = "allow"


class PlanningContext(BaseModel):
    """Provider-agnostic, bounded, typed planning context.
    
    Composes deterministic evidence from:
    - ResourceQueueSnapshots (Phase 3C)
    - DependencyGraph (Phase 3A)
    - ArtifactEngine (Phase 3B)
    - TeamScheduleProjection (Phase 3D)
    
    Strictly bounded, sanitized, and provider-agnostic.
    Contains ZERO LLM calls, ZERO Jira mutations, and ZERO automated planning decisions.
    """
    context_version: str = "planning-v1"
    generated_at: str
    anchor_date: str
    planning_horizon_working_days: int = 10
    horizon_end_date: str
    team_group: Optional[str] = None

    # Summaries & Bounded Contexts
    team_summary: PlanningTeamSummary = Field(default_factory=PlanningTeamSummary)
    resources: List[PlanningResourceContext] = Field(default_factory=list)
    tasks: List[PlanningTaskContext] = Field(default_factory=list)
    dependencies: List[PlanningDependencyContext] = Field(default_factory=list)
    artifacts: List[PlanningArtifactContext] = Field(default_factory=list)
    schedule: PlanningScheduleSummary = Field(
        default_factory=lambda: PlanningScheduleSummary(anchor_date="", horizon_end_date="")
    )

    # Context Bounds & Truncation Metadata
    truncation: ContextTruncationMetadata = Field(default_factory=ContextTruncationMetadata)

    class Config:
        populate_by_name = True
        extra = "allow"


# -------------------------------------------------------------------------
# Phase 4A: Typed AI Planning Proposal Contracts
# -------------------------------------------------------------------------

import re


def _validate_planning_date(v: Optional[str]) -> Optional[str]:
    """Validate that a date string is formatted strictly as YYYY-MM-DD."""
    if v is None:
        return None
    if not isinstance(v, str):
        raise ValueError("Date must be a string formatted as YYYY-MM-DD")
    v_clean = v.strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", v_clean):
        raise ValueError(f"Date '{v}' must match format YYYY-MM-DD")
    return v_clean


def _validate_planning_confidence(v: float) -> float:
    """Validate that numeric confidence is bounded in [0.0, 1.0]."""
    if v is None:
        raise ValueError("Confidence cannot be None")
    try:
        val = float(v)
    except (ValueError, TypeError):
        raise ValueError("Confidence must be a numeric float between 0.0 and 1.0")
    if not (0.0 <= val <= 1.0):
        raise ValueError(f"Confidence {val} out of bounds: must be between 0.0 and 1.0 inclusive")
    return round(val, 4)


def _validate_non_empty_issue_key(v: str) -> str:
    """Validate that an issue key is a non-empty string."""
    if not v or not isinstance(v, str) or not v.strip():
        raise ValueError("issue_key must be a non-empty string")
    return v.strip().upper()


class EvidenceType(str, Enum):
    """Bounded categorization of planning evidence sources."""
    RESOURCE_HISTORY = "RESOURCE_HISTORY"
    CURRENT_QUEUE = "CURRENT_QUEUE"
    CAPACITY = "CAPACITY"
    TASK_ESTIMATE = "TASK_ESTIMATE"
    TASK_COMPLEXITY = "TASK_COMPLEXITY"
    DEPENDENCY = "DEPENDENCY"
    ARTIFACT = "ARTIFACT"
    TEAM_SCHEDULE = "TEAM_SCHEDULE"
    BOTTLENECK = "BOTTLENECK"
    DATA_QUALITY = "DATA_QUALITY"
    OTHER = "OTHER"


class EvidenceReference(BaseModel):
    """Structured, bounded evidence reference supporting an AI proposal.
    
    Contains ZERO raw Jira payloads, raw tokens, or credentials.
    """
    evidence_type: EvidenceType = EvidenceType.OTHER
    source_identifier: str = Field(..., description="Canonical reference key (e.g. 'acc-123', 'WSSS-1', 'capacity.available')")
    description: str = Field(default="", description="Explanation of how this evidence supports the proposal")
    relevance: str = Field(default="DIRECT", description="Relevance level: DIRECT, CONTEXTUAL, CORROBORATING")

    @field_validator("source_identifier")
    @classmethod
    def validate_source_identifier(cls, v: str) -> str:
        if not v or not isinstance(v, str) or not v.strip():
            raise ValueError("source_identifier must be a non-empty string")
        return v.strip()

    class Config:
        populate_by_name = True
        extra = "forbid"


class EstimateUnit(str, Enum):
    """Explicit units for task duration estimates.
    
    Ambiguous units like 'days' or 'story_points' without explicit calibration are prohibited.
    """
    HOURS = "hours"


class PlanningEstimate(BaseModel):
    """Typed estimate representation proposed by AI.
    
    All estimates are advisory proposals and must be explicitly quantified in hours.
    """
    value: float = Field(..., ge=0.0, description="Estimated duration effort in specified units")
    unit: EstimateUnit = Field(default=EstimateUnit.HOURS, description="Explicit measurement unit (e.g. 'hours')")
    confidence: float = Field(default=0.8, ge=0.0, le=1.0, description="Confidence in [0.0, 1.0]")
    rationale: str = Field(default="", description="Justification grounded in historical pace / complexity")
    evidence_references: List[EvidenceReference] = Field(default_factory=list)

    @field_validator("confidence")
    @classmethod
    def check_confidence(cls, v: float) -> float:
        return _validate_planning_confidence(v)

    class Config:
        populate_by_name = True
        extra = "forbid"


class PlanningAssumption(BaseModel):
    """Explicitly stated assumption made by the AI planning engine.
    
    Assumptions are NOT confirmed facts and require human awareness.
    """
    statement: str = Field(..., description="Clear description of the underlying assumption")
    evidence_references: List[EvidenceReference] = Field(default_factory=list)
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    requires_human_review: bool = True

    @field_validator("statement")
    @classmethod
    def validate_statement(cls, v: str) -> str:
        if not v or not isinstance(v, str) or not v.strip():
            raise ValueError("statement must be a non-empty string")
        return v.strip()

    @field_validator("confidence")
    @classmethod
    def check_confidence(cls, v: float) -> float:
        return _validate_planning_confidence(v)

    class Config:
        populate_by_name = True
        extra = "forbid"


class SequencingProposal(BaseModel):
    """Advisory sequencing recommendation for a task in a resource's queue.
    
    Does NOT override HARD_BLOCK dependencies and does NOT imply reassignment.
    """
    issue_key: str = Field(..., description="Target Jira issue key")
    position: int = Field(..., ge=1, description="1-indexed suggested queue position")
    rationale: str = Field(default="", description="Reasoning for suggested sequence position")
    evidence_references: List[EvidenceReference] = Field(default_factory=list)
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)

    @field_validator("issue_key")
    @classmethod
    def check_issue_key(cls, v: str) -> str:
        return _validate_non_empty_issue_key(v)

    @field_validator("confidence")
    @classmethod
    def check_confidence(cls, v: float) -> float:
        return _validate_planning_confidence(v)

    class Config:
        populate_by_name = True
        extra = "forbid"


class PlanningRiskType(str, Enum):
    """Bounded, deterministic risk signal categories.
    
    Strictly operational: contains ZERO employee personality or competence judgments.
    """
    CAPACITY_RISK = "CAPACITY_RISK"
    DEPENDENCY_RISK = "DEPENDENCY_RISK"
    DEADLINE_RISK = "DEADLINE_RISK"
    ESTIMATION_UNCERTAINTY = "ESTIMATION_UNCERTAINTY"
    DATA_QUALITY_RISK = "DATA_QUALITY_RISK"
    ARTIFACT_HANDOFF_RISK = "ARTIFACT_HANDOFF_RISK"
    SCHEDULE_DRIFT_RISK = "SCHEDULE_DRIFT_RISK"
    OTHER = "OTHER"


class PlanningRiskSignal(BaseModel):
    """Deterministic, bounded risk signal identified by the planning engine."""
    risk_type: PlanningRiskType
    issue_key: Optional[str] = None
    severity: str = Field(default="MEDIUM", description="Severity level: HIGH, MEDIUM, LOW")
    explanation: str = Field(..., description="Operational explanation of the identified risk")
    evidence_references: List[EvidenceReference] = Field(default_factory=list)
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    requires_human_review: bool = True

    @field_validator("issue_key")
    @classmethod
    def check_issue_key(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        return _validate_non_empty_issue_key(v)

    @field_validator("explanation")
    @classmethod
    def check_explanation(cls, v: str) -> str:
        if not v or not isinstance(v, str) or not v.strip():
            raise ValueError("explanation must be a non-empty string")
        return v.strip()

    @field_validator("confidence")
    @classmethod
    def check_confidence(cls, v: float) -> float:
        return _validate_planning_confidence(v)

    class Config:
        populate_by_name = True
        extra = "forbid"


class TaskPlanningProposal(BaseModel):
    """Advisory planning proposal for a SINGLE existing Jira issue.
    
    References an existing task in PlanningContext.
    Does NOT support arbitrary issue creation or direct Jira field mutations.
    """
    issue_key: str = Field(..., description="Authoritative Jira issue key")
    
    # Duration & Estimate Proposal
    proposed_estimate: Optional[PlanningEstimate] = None
    
    # Scheduling & Date Proposals (Proposals only, NOT committed Jira dates)
    proposed_start_date: Optional[str] = None
    proposed_due_date: Optional[str] = None
    date_confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    
    # Sequencing & Dependency Proposals
    sequencing_position: Optional[int] = Field(None, ge=1)
    proposed_predecessors: List[str] = Field(default_factory=list)
    proposed_successors: List[str] = Field(default_factory=list)
    
    # Risk, Assumptions, Evidence
    risk_level: str = Field(default="LOW", description="Risk level: HIGH, MEDIUM, LOW")
    risk_reason: Optional[str] = None
    evidence_references: List[EvidenceReference] = Field(default_factory=list)
    assumptions: List[PlanningAssumption] = Field(default_factory=list)
    
    # Safety Gate
    requires_human_review: bool = True

    @field_validator("issue_key")
    @classmethod
    def check_issue_key(cls, v: str) -> str:
        return _validate_non_empty_issue_key(v)

    @field_validator("proposed_start_date", "proposed_due_date")
    @classmethod
    def check_dates(cls, v: Optional[str]) -> Optional[str]:
        return _validate_planning_date(v)

    @field_validator("date_confidence")
    @classmethod
    def check_confidence(cls, v: float) -> float:
        return _validate_planning_confidence(v)

    @field_validator("proposed_predecessors", "proposed_successors")
    @classmethod
    def check_linked_keys(cls, v: List[str]) -> List[str]:
        if not v:
            return []
        cleaned: List[str] = []
        for item in v:
            if not item or not isinstance(item, str) or not item.strip():
                raise ValueError("Linked task keys must be non-empty strings")
            cleaned.append(item.strip().upper())
        return cleaned

    class Config:
        populate_by_name = True
        extra = "forbid"


class PlanningProposal(BaseModel):
    """Root typed contract for an AI-generated Planning Proposal.
    
    Represents the structured planning recommendations proposed by an AI planning engine
    after evaluating a PlanningContext.
    
    CRITICAL ARCHITECTURAL BOUNDARIES:
    - Pure advisory data contract: AI output != Jira mutation.
    - NEVER directly executes Jira mutations, Action Engine actions, or notifications.
    - Requires validation by the deterministic Proposal Validator (Phase 4C) and explicit Human Approval.
    - Default requires_human_review = True.
    """
    proposal_version: str = "proposal-v1"
    generated_at: str
    context_version: str = "planning-v1"
    anchor_date: str
    planning_horizon_working_days: int = 10
    
    # Core Summary & Safety
    requires_human_review: bool = True
    overall_confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    summary: str = Field(..., description="High-level executive summary of proposed planning decisions")
    
    # Detailed Proposals
    task_proposals: List[TaskPlanningProposal] = Field(default_factory=list)
    sequencing_proposals: List[SequencingProposal] = Field(default_factory=list)
    risk_signals: List[PlanningRiskSignal] = Field(default_factory=list)
    assumptions: List[PlanningAssumption] = Field(default_factory=list)
    evidence_references: List[EvidenceReference] = Field(default_factory=list)

    @field_validator("anchor_date")
    @classmethod
    def check_anchor_date(cls, v: str) -> str:
        val = _validate_planning_date(v)
        if val is None:
            raise ValueError("anchor_date must be provided")
        return val

    @field_validator("overall_confidence")
    @classmethod
    def check_confidence(cls, v: float) -> float:
        return _validate_planning_confidence(v)

    @field_validator("summary")
    @classmethod
    def check_summary(cls, v: str) -> str:
        if not v or not isinstance(v, str) or not v.strip():
            raise ValueError("summary must be a non-empty string")
        return v.strip()

    class Config:
        populate_by_name = True
        extra = "forbid"


# -------------------------------------------------------------------------
# Phase 4C: Deterministic AI Planning Proposal Validation Models
# -------------------------------------------------------------------------

class ProposalValidationStatus(str, Enum):
    """Deterministic validation outcome status for an AI planning proposal."""
    VALID = "VALID"
    INVALID = "INVALID"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class ValidationIssueSeverity(str, Enum):
    """Severity classification of validation findings."""
    ERROR = "ERROR"      # Hard constraint violation -> INVALID
    WARNING = "WARNING"  # Advisory discrepancy or data quality limitation -> NEEDS_REVIEW
    INFO = "INFO"        # Informational alignment note


class ProposalValidationCategory(str, Enum):
    """Categorization of validation checks."""
    STRUCTURE = "STRUCTURE"
    GROUNDING = "GROUNDING"
    RESOURCE = "RESOURCE"
    ESTIMATE = "ESTIMATE"
    DATE = "DATE"
    DEPENDENCY = "DEPENDENCY"
    CAPACITY = "CAPACITY"
    SCHEDULE = "SCHEDULE"
    HORIZON = "HORIZON"
    DATA_QUALITY = "DATA_QUALITY"
    ARTIFACT = "ARTIFACT"
    SEQUENCING = "SEQUENCING"
    SAFETY = "SAFETY"


class ProposalValidationIssue(BaseModel):
    """Deterministic finding or violation recorded during proposal validation."""
    code: str = Field(..., description="Machine-readable issue code e.g. HARD_BLOCK_VIOLATION")
    severity: ValidationIssueSeverity = Field(default=ValidationIssueSeverity.ERROR)
    category: ProposalValidationCategory = Field(default=ProposalValidationCategory.STRUCTURE)
    issue_key: Optional[str] = Field(None, description="Affected Jira issue key if task-specific")
    resource_id: Optional[str] = Field(None, description="Affected resource ID if resource-specific")
    field: Optional[str] = Field(None, description="Affected proposal field name")
    message: str = Field(..., description="Human-readable explanation of the validation issue")
    evidence: str = Field(default="", description="Deterministic factual evidence supporting this finding")

    class Config:
        populate_by_name = True
        extra = "forbid"


class ProposalValidationResult(BaseModel):
    """Deterministic validation outcome for an AI PlanningProposal against PlanningContext."""
    status: ProposalValidationStatus
    proposal_version: str
    context_version: str
    validated_at: str
    proposal_accepted: bool = False
    
    # Issue findings
    issues: List[ProposalValidationIssue] = Field(default_factory=list)
    
    # Task Counts
    validated_task_count: int = 0
    valid_task_count: int = 0
    invalid_task_count: int = 0
    needs_review_task_count: int = 0
    
    # Executive Summary & Checks
    summary: str = Field(..., description="Summary of deterministic validation outcome")
    deterministic_checks: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        populate_by_name = True
        extra = "forbid"


# -------------------------------------------------------------------------
# Phase 4E: Human Planning Approval Domain Models
# -------------------------------------------------------------------------

class PlanningApprovalState(str, Enum):
    """Deterministic lifecycle states for human planning approval requests."""
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class ReviewerIdentity(BaseModel):
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


class PlanningApprovalDecision(BaseModel):
    """Immutable, typed record of an authorized human approval or rejection decision."""
    decision_id: str = Field(..., description="Deterministic unique decision record identifier")
    approval_request_id: str = Field(..., description="Target PlanningApprovalRequest ID")
    proposal_id: str = Field(..., description="Target PlanningProposal ID")
    proposal_version: str = Field(..., description="Target proposal version")
    context_version: str = Field(..., description="Target PlanningContext version")
    decision: PlanningApprovalState = Field(..., description="APPROVED or REJECTED")
    reviewer: ReviewerIdentity = Field(..., description="Authenticated reviewer identity")
    decided_at: str = Field(..., description="ISO-8601 UTC timestamp of human decision")
    acknowledged_validation_issue_codes: List[str] = Field(
        default_factory=list,
        description="Explicitly acknowledged warning/review issue codes (mandatory for NEEDS_REVIEW)",
    )
    comments: Optional[str] = Field(None, description="Optional human reviewer notes or explanation")

    @field_validator("decision")
    @classmethod
    def check_decision_value(cls, v: PlanningApprovalState) -> PlanningApprovalState:
        if v not in (PlanningApprovalState.APPROVED, PlanningApprovalState.REJECTED):
            raise ValueError("Decision must be either APPROVED or REJECTED")
        return v

    class Config:
        populate_by_name = True
        extra = "forbid"


class PlanningApprovalRequest(BaseModel):
    """Deterministic, immutable container for human planning review and approval gate."""
    approval_request_id: str = Field(..., description="Unique approval request ID (e.g. apr-uuid)")
    proposal_id: str = Field(..., description="Unique proposal identifier")
    proposal_version: str = Field(..., description="Proposal version (e.g. proposal-v1)")
    context_version: str = Field(..., description="Context version (e.g. planning-v1)")
    anchor_date: str = Field(..., description="Anchor date for planning timeline")
    
    # State & Timeline
    state: PlanningApprovalState = Field(default=PlanningApprovalState.PENDING)
    created_at: str = Field(..., description="ISO-8601 UTC timestamp of creation")
    expires_at: str = Field(..., description="ISO-8601 UTC timestamp when pending approval expires")
    
    # Snapshot facts for transparent review
    proposal_summary: str = Field(..., description="Executive summary of the proposal")
    affected_issue_keys: List[str] = Field(default_factory=list)
    affected_resource_ids: List[str] = Field(default_factory=list)
    
    # Phase 4C Deterministic Validation Finding Snapshot
    validation_status: ProposalValidationStatus
    validation_result: ProposalValidationResult
    
    # Proposal content snapshots
    task_proposals: List[TaskPlanningProposal] = Field(default_factory=list)
    sequencing_proposals: List[SequencingProposal] = Field(default_factory=list)
    risk_signals: List[PlanningRiskSignal] = Field(default_factory=list)
    assumptions: List[PlanningAssumption] = Field(default_factory=list)
    evidence_references: List[EvidenceReference] = Field(default_factory=list)
    
    # Terminal decision record if finalized
    decision: Optional[PlanningApprovalDecision] = None

    class Config:
        populate_by_name = True
        extra = "forbid"


# -------------------------------------------------------------------------
# Phase 4F: Approved Planning Execution Domain Models
# -------------------------------------------------------------------------

class PlanningExecutionState(str, Enum):
    """Deterministic lifecycle state for approved planning execution."""
    PENDING = "PENDING"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    ALREADY_EXECUTED = "ALREADY_EXECUTED"


class PlanningExecutionFailureCategory(str, Enum):
    """Deterministic failure classification for planning execution."""
    APPROVAL_INVALID = "APPROVAL_INVALID"
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
    VERSION_MISMATCH = "VERSION_MISMATCH"
    VALIDATION_INVALID = "VALIDATION_INVALID"
    LIVE_STATE_CONFLICT = "LIVE_STATE_CONFLICT"
    UNSUPPORTED_ACTION = "UNSUPPORTED_ACTION"
    AUTHORIZATION_FAILURE = "AUTHORIZATION_FAILURE"
    JIRA_NOT_FOUND = "JIRA_NOT_FOUND"
    JIRA_PERMISSION_FAILURE = "JIRA_PERMISSION_FAILURE"
    JIRA_API_FAILURE = "JIRA_API_FAILURE"
    CONCURRENCY_FAILURE = "CONCURRENCY_FAILURE"
    DRY_RUN = "DRY_RUN"
    ALREADY_EXECUTED = "ALREADY_EXECUTED"
    UNKNOWN_FAILURE = "UNKNOWN_FAILURE"


class PlanningExecutionActionType(str, Enum):
    """Explicitly supported Jira planning mutation action types."""
    UPDATE_DUE_DATE = "UPDATE_DUE_DATE"


class PlanningExecutionAction(BaseModel):
    """Deterministic, immutable unit of planning mutation intent extracted from an approved proposal."""
    action_id: str = Field(..., description="Unique deterministic action ID")
    issue_key: str = Field(..., description="Target Jira issue key")
    action_type: PlanningExecutionActionType = Field(..., description="Allowed mutation action type")
    field_name: str = Field(..., description="Target Jira field name (e.g. duedate)")
    approved_value: Any = Field(..., description="Approved value from human-approved proposal")
    current_value: Optional[Any] = Field(None, description="Live value in Jira prior to execution")
    state: str = Field(default="PENDING", description="Action state: PENDING, SUCCEEDED, FAILED, BLOCKED, SKIPPED")
    error_message: Optional[str] = None
    failure_category: Optional[PlanningExecutionFailureCategory] = None
    action_engine_action_id: Optional[str] = None

    class Config:
        populate_by_name = True
        extra = "forbid"


class PlanningExecutionFailure(BaseModel):
    """Deterministic structured record of an execution error or blockage."""
    failure_category: PlanningExecutionFailureCategory
    message: str
    issue_key: Optional[str] = None
    action_type: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        populate_by_name = True
        extra = "forbid"


class PlanningExecutionRequest(BaseModel):
    """Typed execution request initiated by an authorized human for an APPROVED proposal."""
    execution_id: str = Field(..., description="Unique execution instance identifier")
    approval_request_id: str = Field(..., description="Approved approval request ID")
    proposal_id: str = Field(..., description="Approved proposal ID")
    proposal_version: str = Field(..., description="Approved proposal version")
    context_version: str = Field(..., description="Approved context version")
    executor: ReviewerIdentity = Field(..., description="Authenticated human executor identity")
    dry_run: bool = Field(default=False, description="Whether to simulate execution without mutating Jira")
    requested_at: str = Field(..., description="ISO-8601 UTC timestamp of execution request")

    class Config:
        populate_by_name = True
        extra = "forbid"


class PlanningExecutionResult(BaseModel):
    """Deterministic, immutable outcome of executing an approved planning proposal."""
    execution_id: str
    approval_request_id: str
    proposal_id: str
    proposal_version: str
    context_version: str
    state: PlanningExecutionState
    dry_run: bool
    started_at: str
    completed_at: str
    total_actions: int = 0
    successful_actions: int = 0
    failed_actions: int = 0
    blocked_actions: int = 0
    skipped_actions: int = 0
    actions: List[PlanningExecutionAction] = Field(default_factory=list)
    failures: List[PlanningExecutionFailure] = Field(default_factory=list)
    summary: str = Field(default="", description="Summary of execution outcome")

    class Config:
        populate_by_name = True
        extra = "forbid"







