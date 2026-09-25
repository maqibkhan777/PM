"""Comprehensive deterministic evaluation dataset for Phase 4D: AI Planning Evaluation.

Contains 20 diverse, bounded synthetic scenarios (A through T):
A. Healthy planning context
B. Limited historical data
C. No historical data
D. Partial capacity information
E. Multiple resources working in parallel
F. Resource with overloaded queue
G. HARD_BLOCK dependency chain
H. Long dependency chain
I. Dependency cycle
J. Artifact handoff
K. Missing duration evidence
L. Tasks with explicit estimates
M. Tasks relying on deterministic fallback duration
N. Tasks near planning horizon
O. Tasks beyond planning horizon
P. Tight deadlines
Q. Multiple competing priorities
R. Reopened/stale/overdue work
S. Mixed task complexity
T. Truncated PlanningContext
"""

from typing import Any, Dict, List, NamedTuple, Optional
from app.core.models.planning import (
    ArtifactStatus,
    ArtifactType,
    CapacityState,
    ContextTruncationMetadata,
    DependencyClassification,
    PlanningArtifactContext,
    PlanningContext,
    PlanningDependencyContext,
    PlanningResourceContext,
    PlanningScheduleSummary,
    PlanningTaskContext,
    PlanningTeamSummary,
    ResourceCapacitySummary,
    ResourcePaceSummary,
)


class PlanningEvaluationScenario(NamedTuple):
    scenario_id: str
    scenario_name: str
    category: str
    description: str
    context: PlanningContext
    expected_facts: Dict[str, Any]
    expected_validator_constraints: Dict[str, Any]


def _build_context(
    tasks: List[PlanningTaskContext],
    resources: List[PlanningResourceContext],
    dependencies: Optional[List[PlanningDependencyContext]] = None,
    artifacts: Optional[List[PlanningArtifactContext]] = None,
    truncation: Optional[ContextTruncationMetadata] = None,
    anchor_date: str = "2026-09-28",
    horizon_working_days: int = 10,
    horizon_end_date: str = "2026-10-09",
    team_group: str = "Mursaleen Cluster",
) -> PlanningContext:
    deps = dependencies or []
    arts = artifacts or []
    trunc = truncation or ContextTruncationMetadata()

    return PlanningContext(
        context_version="planning-v1",
        generated_at="2026-09-28T00:00:00Z",
        anchor_date=anchor_date,
        planning_horizon_working_days=horizon_working_days,
        horizon_end_date=horizon_end_date,
        team_group=team_group,
        team_summary=PlanningTeamSummary(
            resource_count=len(resources),
            active_task_count=len(tasks),
            total_remaining_effort_hours=sum(t.estimated_remaining_hours for t in tasks),
            total_available_capacity_hours=sum(r.available_capacity_hours for r in resources),
            blocked_task_count=sum(1 for t in tasks if t.is_blocked),
            overdue_task_count=sum(1 for t in tasks if t.is_overdue),
        ),
        resources=resources,
        tasks=tasks,
        dependencies=deps,
        artifacts=arts,
        schedule=PlanningScheduleSummary(
            anchor_date=anchor_date,
            horizon_end_date=horizon_end_date,
            planning_horizon_working_days=horizon_working_days,
            tasks_projected_count=len(tasks),
        ),
        truncation=trunc,
    )


# A. Healthy planning context
SCENARIO_A_HEALTHY = PlanningEvaluationScenario(
    scenario_id="EVAL-SCEN-A",
    scenario_name="Healthy Planning Context",
    category="HEALTHY",
    description="Balanced 2-resource team with complete estimates, known capacity, and valid timeline.",
    context=_build_context(
        tasks=[
            PlanningTaskContext(
                issue_key="HLT-101",
                summary="Build user auth API",
                assigned_resource_id="acc-alice",
                assigned_resource_name="Alice",
                estimated_remaining_hours=8.0,
                duration_evidence_source="jira_estimate",
                duration_confidence="HIGH",
                status="In Progress",
                projected_start_date="2026-09-28",
                projected_completion_date="2026-09-29",
            ),
            PlanningTaskContext(
                issue_key="HLT-102",
                summary="Build user profile UI",
                assigned_resource_id="acc-bob",
                assigned_resource_name="Bob",
                estimated_remaining_hours=12.0,
                duration_evidence_source="jira_estimate",
                duration_confidence="HIGH",
                status="In Progress",
                projected_start_date="2026-09-28",
                projected_completion_date="2026-09-29",
            ),
        ],
        resources=[
            PlanningResourceContext(
                resource_id="acc-alice",
                display_name="Alice",
                available_capacity_hours=67.5,
                remaining_effort_hours=8.0,
                active_task_count=1,
                capacity_state=CapacityState.BALANCED,
                history_completeness="SUFFICIENT_HISTORY",
                capacity_quality="CAPACITY_KNOWN",
            ),
            PlanningResourceContext(
                resource_id="acc-bob",
                display_name="Bob",
                available_capacity_hours=67.5,
                remaining_effort_hours=12.0,
                active_task_count=1,
                capacity_state=CapacityState.BALANCED,
                history_completeness="SUFFICIENT_HISTORY",
                capacity_quality="CAPACITY_KNOWN",
            ),
        ],
    ),
    expected_facts={"resource_count": 2, "task_count": 2, "hard_block_count": 0},
    expected_validator_constraints={"must_be_valid": True, "requires_review": False},
)

# B. Limited historical data
SCENARIO_B_LIMITED_HISTORY = PlanningEvaluationScenario(
    scenario_id="EVAL-SCEN-B",
    scenario_name="Limited Historical Data",
    category="UNCERTAINTY",
    description="Resource with only 3 completed tasks; pace confidence is limited.",
    context=_build_context(
        tasks=[
            PlanningTaskContext(
                issue_key="LIM-201",
                summary="Refactor logging pipeline",
                assigned_resource_id="acc-charlie",
                assigned_resource_name="Charlie",
                estimated_remaining_hours=10.0,
                duration_evidence_source="personal_baseline",
                duration_confidence="MEDIUM",
                status="In Progress",
            )
        ],
        resources=[
            PlanningResourceContext(
                resource_id="acc-charlie",
                display_name="Charlie",
                available_capacity_hours=67.5,
                remaining_effort_hours=10.0,
                active_task_count=1,
                history_completeness="LIMITED_HISTORY",
                capacity_quality="CAPACITY_KNOWN",
            )
        ],
    ),
    expected_facts={"resource_id": "acc-charlie", "history_completeness": "LIMITED_HISTORY"},
    expected_validator_constraints={"must_be_valid": True, "requires_review": False},
)

# C. No historical data
SCENARIO_C_NO_HISTORY = PlanningEvaluationScenario(
    scenario_id="EVAL-SCEN-C",
    scenario_name="No Historical Data",
    category="DATA_QUALITY",
    description="New contractor with 0 completed tasks; relying entirely on role defaults.",
    context=_build_context(
        tasks=[
            PlanningTaskContext(
                issue_key="NOH-301",
                summary="Setup tailwind css tokens",
                assigned_resource_id="acc-newbie",
                assigned_resource_name="Newbie",
                estimated_remaining_hours=6.0,
                duration_evidence_source="role_benchmark",
                duration_confidence="LOW",
                status="To Do",
            )
        ],
        resources=[
            PlanningResourceContext(
                resource_id="acc-newbie",
                display_name="Newbie",
                available_capacity_hours=67.5,
                remaining_effort_hours=6.0,
                active_task_count=1,
                history_completeness="NO_HISTORY",
                capacity_quality="CAPACITY_KNOWN",
            )
        ],
    ),
    expected_facts={"history_completeness": "NO_HISTORY"},
    expected_validator_constraints={"must_be_valid": False, "requires_review": True, "expected_code": "RESOURCE_NO_HISTORY"},
)

# D. Partial capacity information
SCENARIO_D_PARTIAL_CAPACITY = PlanningEvaluationScenario(
    scenario_id="EVAL-SCEN-D",
    scenario_name="Partial Capacity Information",
    category="CAPACITY",
    description="Resource with capacity quality marked CAPACITY_UNAVAILABLE.",
    context=_build_context(
        tasks=[
            PlanningTaskContext(
                issue_key="CAP-401",
                summary="Payment retry webhook",
                assigned_resource_id="acc-part",
                assigned_resource_name="Part",
                estimated_remaining_hours=14.0,
                status="In Progress",
            )
        ],
        resources=[
            PlanningResourceContext(
                resource_id="acc-part",
                display_name="Part",
                available_capacity_hours=67.5,
                remaining_effort_hours=14.0,
                capacity_quality="CAPACITY_UNAVAILABLE",
            )
        ],
    ),
    expected_facts={"capacity_quality": "CAPACITY_UNAVAILABLE"},
    expected_validator_constraints={"must_be_valid": False, "requires_review": True, "expected_code": "CAPACITY_UNAVAILABLE"},
)

# E. Multiple resources working in parallel
SCENARIO_E_PARALLEL_RESOURCES = PlanningEvaluationScenario(
    scenario_id="EVAL-SCEN-E",
    scenario_name="Multiple Resources Parallel Work",
    category="PARALLELISM",
    description="Three independent resources executing parallel tasks simultaneously.",
    context=_build_context(
        tasks=[
            PlanningTaskContext(issue_key="PAR-501", summary="Frontend", assigned_resource_id="acc-1", estimated_remaining_hours=10.0),
            PlanningTaskContext(issue_key="PAR-502", summary="Backend", assigned_resource_id="acc-2", estimated_remaining_hours=12.0),
            PlanningTaskContext(issue_key="PAR-503", summary="QA", assigned_resource_id="acc-3", estimated_remaining_hours=8.0),
        ],
        resources=[
            PlanningResourceContext(resource_id="acc-1", display_name="Dev 1", available_capacity_hours=67.5, remaining_effort_hours=10.0),
            PlanningResourceContext(resource_id="acc-2", display_name="Dev 2", available_capacity_hours=67.5, remaining_effort_hours=12.0),
            PlanningResourceContext(resource_id="acc-3", display_name="QA 1", available_capacity_hours=67.5, remaining_effort_hours=8.0),
        ],
    ),
    expected_facts={"parallel_resources": 3},
    expected_validator_constraints={"must_be_valid": True},
)

# F. Resource with overloaded queue
SCENARIO_F_OVERLOADED_QUEUE = PlanningEvaluationScenario(
    scenario_id="EVAL-SCEN-F",
    scenario_name="Overloaded Resource Queue",
    category="CAPACITY",
    description="Resource carrying 100h of committed workload against 67.5h capacity (1.48x ratio).",
    context=_build_context(
        tasks=[
            PlanningTaskContext(issue_key="OVL-601", summary="Task 1", assigned_resource_id="acc-over", estimated_remaining_hours=50.0),
            PlanningTaskContext(issue_key="OVL-602", summary="Task 2", assigned_resource_id="acc-over", estimated_remaining_hours=50.0),
        ],
        resources=[
            PlanningResourceContext(
                resource_id="acc-over",
                display_name="Overloaded Dev",
                available_capacity_hours=67.5,
                remaining_effort_hours=100.0,
                capacity_state=CapacityState.SATURATED,
            )
        ],
    ),
    expected_facts={"load_ratio": 1.48, "capacity_state": "SATURATED"},
    expected_validator_constraints={"must_be_valid": False, "hard_failure_code": "CAPACITY_EXCEEDED"},
)

# G. HARD_BLOCK dependency chain
SCENARIO_G_HARD_BLOCK_CHAIN = PlanningEvaluationScenario(
    scenario_id="EVAL-SCEN-G",
    scenario_name="HARD_BLOCK Dependency Chain",
    category="DEPENDENCY",
    description="Task A blocks Task B. Task B cannot start before Task A completes.",
    context=_build_context(
        tasks=[
            PlanningTaskContext(
                issue_key="HBC-701",
                summary="DB Schema Migration",
                assigned_resource_id="acc-1",
                estimated_remaining_hours=8.0,
                projected_start_date="2026-09-28",
                projected_completion_date="2026-09-29",
                successor_keys=["HBC-702"],
            ),
            PlanningTaskContext(
                issue_key="HBC-702",
                summary="Backend API implementation",
                assigned_resource_id="acc-1",
                estimated_remaining_hours=14.0,
                is_blocked=True,
                projected_start_date="2026-09-30",
                projected_completion_date="2026-10-01",
                predecessor_keys=["HBC-701"],
            ),
        ],
        resources=[PlanningResourceContext(resource_id="acc-1", display_name="Dev 1", available_capacity_hours=67.5, remaining_effort_hours=22.0)],
        dependencies=[
            PlanningDependencyContext(
                source_issue_key="HBC-701",
                target_issue_key="HBC-702",
                link_type="Blocks",
                classification=DependencyClassification.HARD_BLOCK,
                is_hard_block=True,
            )
        ],
    ),
    expected_facts={"hard_block_edges": 1},
    expected_validator_constraints={"hard_block_violation_if_start_before": "2026-09-29"},
)

# H. Long dependency chain
SCENARIO_H_LONG_CHAIN = PlanningEvaluationScenario(
    scenario_id="EVAL-SCEN-H",
    scenario_name="Long Dependency Chain",
    category="DEPENDENCY",
    description="Sequential 4-task hard blocking chain: A -> B -> C -> D.",
    context=_build_context(
        tasks=[
            PlanningTaskContext(issue_key="LNG-801", summary="Step 1", assigned_resource_id="acc-1", estimated_remaining_hours=6.0, successor_keys=["LNG-802"]),
            PlanningTaskContext(issue_key="LNG-802", summary="Step 2", assigned_resource_id="acc-1", estimated_remaining_hours=6.0, predecessor_keys=["LNG-801"], successor_keys=["LNG-803"], is_blocked=True),
            PlanningTaskContext(issue_key="LNG-803", summary="Step 3", assigned_resource_id="acc-1", estimated_remaining_hours=6.0, predecessor_keys=["LNG-802"], successor_keys=["LNG-804"], is_blocked=True),
            PlanningTaskContext(issue_key="LNG-804", summary="Step 4", assigned_resource_id="acc-1", estimated_remaining_hours=6.0, predecessor_keys=["LNG-803"], is_blocked=True),
        ],
        resources=[PlanningResourceContext(resource_id="acc-1", display_name="Dev 1", available_capacity_hours=67.5, remaining_effort_hours=24.0)],
        dependencies=[
            PlanningDependencyContext(source_issue_key="LNG-801", target_issue_key="LNG-802", link_type="Blocks", classification=DependencyClassification.HARD_BLOCK, is_hard_block=True),
            PlanningDependencyContext(source_issue_key="LNG-802", target_issue_key="LNG-803", link_type="Blocks", classification=DependencyClassification.HARD_BLOCK, is_hard_block=True),
            PlanningDependencyContext(source_issue_key="LNG-803", target_issue_key="LNG-804", link_type="Blocks", classification=DependencyClassification.HARD_BLOCK, is_hard_block=True),
        ],
    ),
    expected_facts={"chain_length": 4},
    expected_validator_constraints={"must_respect_chain_order": True},
)

# I. Dependency cycle
SCENARIO_I_DEPENDENCY_CYCLE = PlanningEvaluationScenario(
    scenario_id="EVAL-SCEN-I",
    scenario_name="Dependency Cycle",
    category="DEPENDENCY",
    description="Circular blocking dependency: CYC-901 blocks CYC-902 which blocks CYC-901.",
    context=_build_context(
        tasks=[
            PlanningTaskContext(issue_key="CYC-901", summary="Task 1", assigned_resource_id="acc-1", estimated_remaining_hours=6.0, predecessor_keys=["CYC-902"], successor_keys=["CYC-902"]),
            PlanningTaskContext(issue_key="CYC-902", summary="Task 2", assigned_resource_id="acc-1", estimated_remaining_hours=6.0, predecessor_keys=["CYC-901"], successor_keys=["CYC-901"]),
        ],
        resources=[PlanningResourceContext(resource_id="acc-1", display_name="Dev 1", available_capacity_hours=67.5, remaining_effort_hours=12.0)],
        dependencies=[
            PlanningDependencyContext(source_issue_key="CYC-901", target_issue_key="CYC-902", link_type="Blocks", classification=DependencyClassification.HARD_BLOCK, is_hard_block=True),
            PlanningDependencyContext(source_issue_key="CYC-902", target_issue_key="CYC-901", link_type="Blocks", classification=DependencyClassification.HARD_BLOCK, is_hard_block=True),
        ],
    ),
    expected_facts={"has_cycle": True},
    expected_validator_constraints={"cycle_detected": True},
)

# J. Artifact handoff
SCENARIO_J_ARTIFACT_HANDOFF = PlanningEvaluationScenario(
    scenario_id="EVAL-SCEN-J",
    scenario_name="Explicit Artifact Handoff",
    category="ARTIFACT",
    description="Task ART-1001 produces OpenAPI spec consumed by Task ART-1002 across resources.",
    context=_build_context(
        tasks=[
            PlanningTaskContext(issue_key="ART-1001", summary="Write API Spec", assigned_resource_id="acc-alice", estimated_remaining_hours=6.0, projected_completion_date="2026-09-29", produced_artifact_names=["openapi-spec"]),
            PlanningTaskContext(issue_key="ART-1002", summary="Generate Client SDK", assigned_resource_id="acc-bob", estimated_remaining_hours=8.0, projected_start_date="2026-09-30", consumed_artifact_names=["openapi-spec"]),
        ],
        resources=[
            PlanningResourceContext(resource_id="acc-alice", display_name="Alice", available_capacity_hours=67.5, remaining_effort_hours=6.0),
            PlanningResourceContext(resource_id="acc-bob", display_name="Bob", available_capacity_hours=67.5, remaining_effort_hours=8.0),
        ],
        artifacts=[
            PlanningArtifactContext(
                artifact_name="openapi-spec",
                project_key="WSSS",
                artifact_type=ArtifactType.API_CONTRACT,
                status=ArtifactStatus.PLANNED,
                producer_issue_key="ART-1001",
                consumer_issue_keys=["ART-1002"],
                is_inferred=False,
            )
        ],
    ),
    expected_facts={"artifact_count": 1, "is_inferred": False},
    expected_validator_constraints={"explicit_artifact_handoff": True},
)

# K. Missing duration evidence
SCENARIO_K_MISSING_EVIDENCE = PlanningEvaluationScenario(
    scenario_id="EVAL-SCEN-K",
    scenario_name="Missing Duration Evidence",
    category="ESTIMATE",
    description="Task lacks estimate and historical evidence; duration is unavailable.",
    context=_build_context(
        tasks=[
            PlanningTaskContext(
                issue_key="MSE-1101",
                summary="Investigate memory spike",
                assigned_resource_id="acc-1",
                estimated_remaining_hours=0.0,
                duration_evidence_source="unavailable",
                duration_confidence="UNKNOWN",
            )
        ],
        resources=[PlanningResourceContext(resource_id="acc-1", display_name="Dev 1", available_capacity_hours=67.5, remaining_effort_hours=0.0)],
    ),
    expected_facts={"duration_evidence_source": "unavailable"},
    expected_validator_constraints={"requires_review": True, "expected_code": "MISSING_DURATION_EVIDENCE"},
)

# L. Tasks with explicit estimates
SCENARIO_L_EXPLICIT_ESTIMATES = PlanningEvaluationScenario(
    scenario_id="EVAL-SCEN-L",
    scenario_name="Explicit Jira Estimates",
    category="ESTIMATE",
    description="Tasks with authoritative high-confidence Jira estimates.",
    context=_build_context(
        tasks=[
            PlanningTaskContext(issue_key="EXP-1201", summary="Task 1", assigned_resource_id="acc-1", estimated_remaining_hours=16.0, duration_evidence_source="jira_estimate", duration_confidence="HIGH"),
            PlanningTaskContext(issue_key="EXP-1202", summary="Task 2", assigned_resource_id="acc-1", estimated_remaining_hours=8.0, duration_evidence_source="jira_estimate", duration_confidence="HIGH"),
        ],
        resources=[PlanningResourceContext(resource_id="acc-1", display_name="Dev 1", available_capacity_hours=67.5, remaining_effort_hours=24.0)],
    ),
    expected_facts={"explicit_estimate_count": 2},
    expected_validator_constraints={"must_be_valid": True},
)

# M. Tasks relying on deterministic fallback duration
SCENARIO_M_FALLBACK_ESTIMATES = PlanningEvaluationScenario(
    scenario_id="EVAL-SCEN-M",
    scenario_name="Fallback Complexity Benchmark Estimates",
    category="ESTIMATE",
    description="Tasks with medium duration confidence estimated from role benchmarks.",
    context=_build_context(
        tasks=[
            PlanningTaskContext(issue_key="FLB-1301", summary="Generic bugfix", assigned_resource_id="acc-1", estimated_remaining_hours=8.0, duration_evidence_source="role_benchmark", duration_confidence="MEDIUM")
        ],
        resources=[PlanningResourceContext(resource_id="acc-1", display_name="Dev 1", available_capacity_hours=67.5, remaining_effort_hours=8.0)],
    ),
    expected_facts={"duration_evidence_source": "role_benchmark"},
    expected_validator_constraints={"must_be_valid": True},
)

# N. Tasks near planning horizon
SCENARIO_N_NEAR_HORIZON = PlanningEvaluationScenario(
    scenario_id="EVAL-SCEN-N",
    scenario_name="Tasks Near Horizon Boundary",
    category="HORIZON",
    description="Task scheduled to complete on the 10th working day (horizon boundary).",
    context=_build_context(
        tasks=[
            PlanningTaskContext(issue_key="NRH-1401", summary="Final release audit", assigned_resource_id="acc-1", estimated_remaining_hours=6.0, projected_start_date="2026-10-09", projected_completion_date="2026-10-09")
        ],
        resources=[PlanningResourceContext(resource_id="acc-1", display_name="Dev 1", available_capacity_hours=67.5, remaining_effort_hours=6.0)],
    ),
    expected_facts={"horizon_end_date": "2026-10-09"},
    expected_validator_constraints={"must_be_valid": True},
)

# O. Tasks beyond planning horizon
SCENARIO_O_BEYOND_HORIZON = PlanningEvaluationScenario(
    scenario_id="EVAL-SCEN-O",
    scenario_name="Tasks Beyond Planning Horizon",
    category="HORIZON",
    description="Task projected to complete on 2026-10-15 (after 10-day horizon ending 2026-10-09).",
    context=_build_context(
        tasks=[
            PlanningTaskContext(issue_key="BYH-1501", summary="Long term migration", assigned_resource_id="acc-1", estimated_remaining_hours=40.0, projected_start_date="2026-10-05", projected_completion_date="2026-10-15", is_beyond_horizon=True)
        ],
        resources=[PlanningResourceContext(resource_id="acc-1", display_name="Dev 1", available_capacity_hours=67.5, remaining_effort_hours=40.0)],
    ),
    expected_facts={"is_beyond_horizon": True},
    expected_validator_constraints={"requires_review": True, "expected_code": "PROPOSAL_BEYOND_HORIZON"},
)

# P. Tight deadlines
SCENARIO_P_TIGHT_DEADLINES = PlanningEvaluationScenario(
    scenario_id="EVAL-SCEN-P",
    scenario_name="Tight Deadlines Scenario",
    category="SCHEDULE",
    description="Tasks with due dates within 2 working days of anchor date.",
    context=_build_context(
        tasks=[
            PlanningTaskContext(issue_key="TGT-1601", summary="Critical patch", assigned_resource_id="acc-1", estimated_remaining_hours=6.0, due_date="2026-09-29", projected_start_date="2026-09-28", projected_completion_date="2026-09-29")
        ],
        resources=[PlanningResourceContext(resource_id="acc-1", display_name="Dev 1", available_capacity_hours=67.5, remaining_effort_hours=6.0)],
    ),
    expected_facts={"due_date": "2026-09-29"},
    expected_validator_constraints={"must_be_valid": True},
)

# Q. Multiple competing priorities
SCENARIO_Q_COMPETING_PRIORITIES = PlanningEvaluationScenario(
    scenario_id="EVAL-SCEN-Q",
    scenario_name="Multiple Competing Priorities",
    category="SEQUENCING",
    description="Resource carrying multiple Highest and High priority tasks.",
    context=_build_context(
        tasks=[
            PlanningTaskContext(issue_key="PR-1701", summary="P0 Outage fix", priority="Highest", assigned_resource_id="acc-1", estimated_remaining_hours=6.0),
            PlanningTaskContext(issue_key="PR-1702", summary="P1 Security patch", priority="High", assigned_resource_id="acc-1", estimated_remaining_hours=8.0),
            PlanningTaskContext(issue_key="PR-1703", summary="P2 Feature work", priority="Medium", assigned_resource_id="acc-1", estimated_remaining_hours=10.0),
        ],
        resources=[PlanningResourceContext(resource_id="acc-1", display_name="Dev 1", available_capacity_hours=67.5, remaining_effort_hours=24.0)],
    ),
    expected_facts={"priority_distribution": {"Highest": 1, "High": 1, "Medium": 1}},
    expected_validator_constraints={"must_be_valid": True},
)

# R. Reopened / stale / overdue work
SCENARIO_R_STALE_OVERDUE = PlanningEvaluationScenario(
    scenario_id="EVAL-SCEN-R",
    scenario_name="Reopened, Stale, and Overdue Work",
    category="ATTENTION",
    description="Resource queue containing stale and overdue issues needing PM attention.",
    context=_build_context(
        tasks=[
            PlanningTaskContext(issue_key="STL-1801", summary="Overdue payment fix", assigned_resource_id="acc-1", estimated_remaining_hours=8.0, is_overdue=True, due_date="2026-09-25"),
            PlanningTaskContext(issue_key="STL-1802", summary="Stale migration ticket", assigned_resource_id="acc-1", estimated_remaining_hours=10.0, is_stale=True),
            PlanningTaskContext(issue_key="STL-1803", summary="Reopened regression bug", assigned_resource_id="acc-1", estimated_remaining_hours=6.0, is_reopened=True),
        ],
        resources=[PlanningResourceContext(resource_id="acc-1", display_name="Dev 1", available_capacity_hours=67.5, remaining_effort_hours=24.0)],
    ),
    expected_facts={"overdue_count": 1, "stale_count": 1, "reopened_count": 1},
    expected_validator_constraints={"must_be_valid": True},
)

# S. Mixed task complexity
SCENARIO_S_MIXED_COMPLEXITY = PlanningEvaluationScenario(
    scenario_id="EVAL-SCEN-S",
    scenario_name="Mixed Task Complexity",
    category="COMPLEXITY",
    description="Queue containing tasks across 1 (Trivial) through 5 (Very Large) complexity scores.",
    context=_build_context(
        tasks=[
            PlanningTaskContext(issue_key="CPX-1901", summary="Trivial typo fix", assigned_resource_id="acc-1", estimated_remaining_hours=2.0),
            PlanningTaskContext(issue_key="CPX-1902", summary="Complex DB rewrite", assigned_resource_id="acc-1", estimated_remaining_hours=24.0),
        ],
        resources=[PlanningResourceContext(resource_id="acc-1", display_name="Dev 1", available_capacity_hours=67.5, remaining_effort_hours=26.0)],
    ),
    expected_facts={"task_count": 2},
    expected_validator_constraints={"must_be_valid": True},
)

# T. Truncated PlanningContext
SCENARIO_T_TRUNCATED_CONTEXT = PlanningEvaluationScenario(
    scenario_id="EVAL-SCEN-T",
    scenario_name="Truncated Planning Context",
    category="TRUNCATION",
    description="Context where task limit was capped and truncation reasons recorded.",
    context=_build_context(
        tasks=[
            PlanningTaskContext(issue_key="TRN-2001", summary="Task 1", assigned_resource_id="acc-1", estimated_remaining_hours=5.0),
            PlanningTaskContext(issue_key="TRN-2002", summary="Task 2", assigned_resource_id="acc-1", estimated_remaining_hours=7.0),
        ],
        resources=[PlanningResourceContext(resource_id="acc-1", display_name="Dev 1", available_capacity_hours=67.5, remaining_effort_hours=12.0)],
        truncation=ContextTruncationMetadata(
            is_truncated=True,
            original_task_count=40,
            included_task_count=2,
            truncation_reasons=["Context bounded to top 2 tasks"],
        ),
    ),
    expected_facts={"is_truncated": True},
    expected_validator_constraints={"requires_review": True, "expected_code": "TRUNCATED_CONTEXT_UNCERTAINTY"},
)

# Master list of all 20 Phase 4D scenarios (A through T)
PHASE_4D_EVALUATION_DATASET: List[PlanningEvaluationScenario] = [
    SCENARIO_A_HEALTHY,
    SCENARIO_B_LIMITED_HISTORY,
    SCENARIO_C_NO_HISTORY,
    SCENARIO_D_PARTIAL_CAPACITY,
    SCENARIO_E_PARALLEL_RESOURCES,
    SCENARIO_F_OVERLOADED_QUEUE,
    SCENARIO_G_HARD_BLOCK_CHAIN,
    SCENARIO_H_LONG_CHAIN,
    SCENARIO_I_DEPENDENCY_CYCLE,
    SCENARIO_J_ARTIFACT_HANDOFF,
    SCENARIO_K_MISSING_EVIDENCE,
    SCENARIO_L_EXPLICIT_ESTIMATES,
    SCENARIO_M_FALLBACK_ESTIMATES,
    SCENARIO_N_NEAR_HORIZON,
    SCENARIO_O_BEYOND_HORIZON,
    SCENARIO_P_TIGHT_DEADLINES,
    SCENARIO_Q_COMPETING_PRIORITIES,
    SCENARIO_R_STALE_OVERDUE,
    SCENARIO_S_MIXED_COMPLEXITY,
    SCENARIO_T_TRUNCATED_CONTEXT,
]
