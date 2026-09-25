"""Synthetic scenarios for Phase 4B DeepSeek AI Planning Evaluation.

Scenarios:
1. Balanced team
2. Overloaded resource
3. HARD_BLOCK chain
4. Cross-resource dependency
5. Low-confidence estimates
6. Missing capacity / history
7. Artifact handoff
8. Truncated context
"""

from typing import List, NamedTuple
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


class PlanningScenario(NamedTuple):
    scenario_id: str
    scenario_name: str
    description: str
    context: PlanningContext


def _make_planning_context(
    tasks: List[PlanningTaskContext],
    resources: List[PlanningResourceContext],
    dependencies: List[PlanningDependencyContext] = None,
    artifacts: List[PlanningArtifactContext] = None,
    truncation: ContextTruncationMetadata = None,
    anchor_date: str = "2026-09-26",
) -> PlanningContext:
    deps = dependencies or []
    arts = artifacts or []
    trunc = truncation or ContextTruncationMetadata()

    return PlanningContext(
        context_version="planning-v1",
        generated_at="2026-09-26T00:00:00Z",
        anchor_date=anchor_date,
        planning_horizon_working_days=10,
        horizon_end_date="2026-10-09",
        team_group="Mursaleen Cluster",
        team_summary=PlanningTeamSummary(
            resource_count=len(resources),
            active_task_count=len(tasks),
            total_remaining_effort_hours=sum(t.estimated_remaining_hours for t in tasks),
            total_available_capacity_hours=sum(r.available_capacity_hours for r in resources),
            blocked_task_count=sum(1 for t in tasks if t.is_blocked),
        ),
        resources=resources,
        tasks=tasks,
        dependencies=deps,
        artifacts=arts,
        schedule=PlanningScheduleSummary(
            anchor_date=anchor_date,
            horizon_end_date="2026-10-09",
            planning_horizon_working_days=10,
            tasks_projected_count=len(tasks),
        ),
        truncation=trunc,
    )


# 1. Balanced team
SCENARIO_1_BALANCED_TEAM = PlanningScenario(
    scenario_id="PLAN-SCEN-1",
    scenario_name="Balanced Team Scenario",
    description="Team with well-distributed workload within capacity limits.",
    context=_make_planning_context(
        tasks=[
            PlanningTaskContext(
                issue_key="BAL-101",
                summary="Build user authentication service",
                assigned_resource_id="acc-alice",
                assigned_resource_name="Alice",
                estimated_remaining_hours=16.0,
                status="In Progress",
                projected_start_date="2026-09-26",
                projected_completion_date="2026-09-28",
            ),
            PlanningTaskContext(
                issue_key="BAL-102",
                summary="Build dashboard charts UI",
                assigned_resource_id="acc-bob",
                assigned_resource_name="Bob",
                estimated_remaining_hours=20.0,
                status="In Progress",
                projected_start_date="2026-09-26",
                projected_completion_date="2026-09-29",
            ),
        ],
        resources=[
            PlanningResourceContext(
                resource_id="acc-alice",
                display_name="Alice",
                available_capacity_hours=67.5,
                remaining_effort_hours=16.0,
                active_task_count=1,
                capacity_state=CapacityState.BALANCED,
            ),
            PlanningResourceContext(
                resource_id="acc-bob",
                display_name="Bob",
                available_capacity_hours=67.5,
                remaining_effort_hours=20.0,
                active_task_count=1,
                capacity_state=CapacityState.BALANCED,
            ),
        ],
    ),
)

# 2. Overloaded resource
SCENARIO_2_OVERLOADED_RESOURCE = PlanningScenario(
    scenario_id="PLAN-SCEN-2",
    scenario_name="Overloaded Resource Scenario",
    description="Single resource assigned workload exceeding 10-day available capacity.",
    context=_make_planning_context(
        tasks=[
            PlanningTaskContext(
                issue_key="OVL-201",
                summary="Core payment gateway migration",
                assigned_resource_id="acc-carol",
                assigned_resource_name="Carol",
                estimated_remaining_hours=45.0,
                status="In Progress",
            ),
            PlanningTaskContext(
                issue_key="OVL-202",
                summary="Stripe webhooks retry engine",
                assigned_resource_id="acc-carol",
                assigned_resource_name="Carol",
                estimated_remaining_hours=35.0,
                status="To Do",
            ),
        ],
        resources=[
            PlanningResourceContext(
                resource_id="acc-carol",
                display_name="Carol",
                available_capacity_hours=67.5,
                remaining_effort_hours=80.0,
                active_task_count=2,
                capacity_state=CapacityState.OVERLOADED,
                workload_pressure="HIGH",
            )
        ],
    ),
)

# 3. HARD_BLOCK chain
SCENARIO_3_HARD_BLOCK_CHAIN = PlanningScenario(
    scenario_id="PLAN-SCEN-3",
    scenario_name="HARD_BLOCK Chain Scenario",
    description="Sequential dependency chain: A blocks B, which blocks C.",
    context=_make_planning_context(
        tasks=[
            PlanningTaskContext(
                issue_key="BLK-301",
                summary="Database schema migration",
                assigned_resource_id="acc-dave",
                assigned_resource_name="Dave",
                estimated_remaining_hours=8.0,
                status="In Progress",
                successor_keys=["BLK-302"],
            ),
            PlanningTaskContext(
                issue_key="BLK-302",
                summary="REST API backend implementation",
                assigned_resource_id="acc-dave",
                assigned_resource_name="Dave",
                estimated_remaining_hours=14.0,
                status="To Do",
                is_blocked=True,
                predecessor_keys=["BLK-301"],
                successor_keys=["BLK-303"],
            ),
            PlanningTaskContext(
                issue_key="BLK-303",
                summary="End-to-end integration tests",
                assigned_resource_id="acc-dave",
                assigned_resource_name="Dave",
                estimated_remaining_hours=10.0,
                status="To Do",
                is_blocked=True,
                predecessor_keys=["BLK-302"],
            ),
        ],
        resources=[
            PlanningResourceContext(
                resource_id="acc-dave",
                display_name="Dave",
                available_capacity_hours=67.5,
                remaining_effort_hours=32.0,
                active_task_count=3,
            )
        ],
        dependencies=[
            PlanningDependencyContext(
                source_issue_key="BLK-301",
                target_issue_key="BLK-302",
                link_type="Blocks",
                classification=DependencyClassification.HARD_BLOCK,
                is_hard_block=True,
            ),
            PlanningDependencyContext(
                source_issue_key="BLK-302",
                target_issue_key="BLK-303",
                link_type="Blocks",
                classification=DependencyClassification.HARD_BLOCK,
                is_hard_block=True,
            ),
        ],
    ),
)

# 4. Cross-resource dependency
SCENARIO_4_CROSS_RESOURCE_DEPENDENCY = PlanningScenario(
    scenario_id="PLAN-SCEN-4",
    scenario_name="Cross-Resource Dependency Scenario",
    description="Frontend task assigned to Bob blocked by Backend task assigned to Alice.",
    context=_make_planning_context(
        tasks=[
            PlanningTaskContext(
                issue_key="XRES-401",
                summary="Backend GraphQL resolver",
                assigned_resource_id="acc-alice",
                assigned_resource_name="Alice",
                estimated_remaining_hours=10.0,
                status="In Progress",
                successor_keys=["XRES-402"],
            ),
            PlanningTaskContext(
                issue_key="XRES-402",
                summary="Frontend UI consumer component",
                assigned_resource_id="acc-bob",
                assigned_resource_name="Bob",
                estimated_remaining_hours=12.0,
                status="To Do",
                is_blocked=True,
                predecessor_keys=["XRES-401"],
            ),
        ],
        resources=[
            PlanningResourceContext(
                resource_id="acc-alice",
                display_name="Alice",
                available_capacity_hours=67.5,
                remaining_effort_hours=10.0,
            ),
            PlanningResourceContext(
                resource_id="acc-bob",
                display_name="Bob",
                available_capacity_hours=67.5,
                remaining_effort_hours=12.0,
            ),
        ],
        dependencies=[
            PlanningDependencyContext(
                source_issue_key="XRES-401",
                target_issue_key="XRES-402",
                link_type="Blocks",
                classification=DependencyClassification.HARD_BLOCK,
                is_hard_block=True,
            )
        ],
    ),
)

# 5. Low-confidence estimates
SCENARIO_5_LOW_CONFIDENCE_ESTIMATES = PlanningScenario(
    scenario_id="PLAN-SCEN-5",
    scenario_name="Low-Confidence Estimates Scenario",
    description="Tasks with missing duration evidence or fallback benchmark estimates.",
    context=_make_planning_context(
        tasks=[
            PlanningTaskContext(
                issue_key="UNC-501",
                summary="Explore third-party ML model API",
                assigned_resource_id="acc-eve",
                assigned_resource_name="Eve",
                estimated_remaining_hours=15.0,
                duration_evidence_source="fallback_benchmark",
                duration_confidence="low",
                status="In Progress",
            ),
            PlanningTaskContext(
                issue_key="UNC-502",
                summary="Legacy codebase discovery and spike",
                assigned_resource_id="acc-eve",
                assigned_resource_name="Eve",
                estimated_remaining_hours=20.0,
                duration_evidence_source="unavailable",
                duration_confidence="unavailable",
                status="To Do",
            ),
        ],
        resources=[
            PlanningResourceContext(
                resource_id="acc-eve",
                display_name="Eve",
                available_capacity_hours=67.5,
                remaining_effort_hours=35.0,
                history_completeness="LIMITED_HISTORY",
            )
        ],
    ),
)

# 6. Missing capacity / history
SCENARIO_6_MISSING_CAPACITY_HISTORY = PlanningScenario(
    scenario_id="PLAN-SCEN-6",
    scenario_name="Missing Capacity / History Scenario",
    description="New contractor with no historical pace data and unconfigured working schedule.",
    context=_make_planning_context(
        tasks=[
            PlanningTaskContext(
                issue_key="NEW-601",
                summary="Implement CSS theme switcher",
                assigned_resource_id="acc-frank",
                assigned_resource_name="Frank",
                estimated_remaining_hours=8.0,
                status="To Do",
            )
        ],
        resources=[
            PlanningResourceContext(
                resource_id="acc-frank",
                display_name="Frank",
                available_capacity_hours=67.5,
                remaining_effort_hours=8.0,
                history_completeness="NO_HISTORY",
                capacity_quality="CAPACITY_UNAVAILABLE",
                capacity_state=CapacityState.UNKNOWN,
            )
        ],
    ),
)

# 7. Artifact handoff
SCENARIO_7_ARTIFACT_HANDOFF = PlanningScenario(
    scenario_id="PLAN-SCEN-7",
    scenario_name="Artifact Handoff Scenario",
    description="Task produces api-spec consumed by another task across resources.",
    context=_make_planning_context(
        tasks=[
            PlanningTaskContext(
                issue_key="ART-701",
                summary="Draft OpenAPI v3 specification",
                assigned_resource_id="acc-alice",
                assigned_resource_name="Alice",
                estimated_remaining_hours=6.0,
                status="In Progress",
                produced_artifact_names=["api-spec"],
            ),
            PlanningTaskContext(
                issue_key="ART-702",
                summary="Generate TypeScript client SDK from spec",
                assigned_resource_id="acc-bob",
                assigned_resource_name="Bob",
                estimated_remaining_hours=10.0,
                status="To Do",
                consumed_artifact_names=["api-spec"],
            ),
        ],
        resources=[
            PlanningResourceContext(
                resource_id="acc-alice",
                display_name="Alice",
                available_capacity_hours=67.5,
                remaining_effort_hours=6.0,
            ),
            PlanningResourceContext(
                resource_id="acc-bob",
                display_name="Bob",
                available_capacity_hours=67.5,
                remaining_effort_hours=10.0,
            ),
        ],
        artifacts=[
            PlanningArtifactContext(
                artifact_name="api-spec",
                project_key="WSSS",
                artifact_type=ArtifactType.API_CONTRACT,
                status=ArtifactStatus.PLANNED,
                producer_issue_key="ART-701",
                producer_resource_id="acc-alice",
                consumer_issue_keys=["ART-702"],
                consumer_resource_ids=["acc-bob"],
                is_advisory=True,
            )
        ],
    ),
)

# 8. Truncated context
SCENARIO_8_TRUNCATED_CONTEXT = PlanningScenario(
    scenario_id="PLAN-SCEN-8",
    scenario_name="Truncated Context Scenario",
    description="Bounded planning context where task and resource limits were capped.",
    context=_make_planning_context(
        tasks=[
            PlanningTaskContext(
                issue_key="TRN-801",
                summary="Critical hotfix item 1",
                assigned_resource_id="acc-alice",
                assigned_resource_name="Alice",
                estimated_remaining_hours=5.0,
                status="In Progress",
            ),
            PlanningTaskContext(
                issue_key="TRN-802",
                summary="Critical hotfix item 2",
                assigned_resource_id="acc-alice",
                assigned_resource_name="Alice",
                estimated_remaining_hours=7.0,
                status="To Do",
            ),
        ],
        resources=[
            PlanningResourceContext(
                resource_id="acc-alice",
                display_name="Alice",
                available_capacity_hours=67.5,
                remaining_effort_hours=12.0,
            )
        ],
        truncation=ContextTruncationMetadata(
            is_truncated=True,
            original_resource_count=10,
            included_resource_count=1,
            original_task_count=50,
            included_task_count=2,
            truncation_reasons=[
                "Resource count truncated to 1",
                "Task count truncated to 2",
            ],
        ),
    ),
)

PLANNING_EVALUATION_SCENARIOS = [
    SCENARIO_1_BALANCED_TEAM,
    SCENARIO_2_OVERLOADED_RESOURCE,
    SCENARIO_3_HARD_BLOCK_CHAIN,
    SCENARIO_4_CROSS_RESOURCE_DEPENDENCY,
    SCENARIO_5_LOW_CONFIDENCE_ESTIMATES,
    SCENARIO_6_MISSING_CAPACITY_HISTORY,
    SCENARIO_7_ARTIFACT_HANDOFF,
    SCENARIO_8_TRUNCATED_CONTEXT,
]
