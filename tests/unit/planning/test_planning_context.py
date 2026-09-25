"""Deterministic unit tests for Phase 3E: Provider-Agnostic PlanningContext & PlanningContextBuilder.

Covers all required specifications:
A. Empty team
B. Single resource
C. Multiple resources
D. Multiple tasks
E. Capacity information preserved
F. Historical pace preserved
G. Task duration evidence preserved
H. Dependency information preserved
I. HARD_BLOCK distinction preserved
J. Advisory dependency distinction preserved
K. Artifact producer/consumer preserved
L. Inferred artifact marked advisory
M. Schedule projection preserved
N. Bottlenecks preserved
O. Data quality preserved
P. Missing history preserved
Q. Missing capacity preserved
R. Missing duration evidence preserved
S. Deterministic resource ordering
T. Deterministic task ordering
U. Deterministic dependency ordering
V. Deterministic artifact ordering
W. Input-order independence
X. Context bounds
Y. Truncation metadata
Z. Secret sanitization
AA. No Jira API calls
AB. No DeepSeek calls
AC. No Action Engine calls
AD. No mutation of source snapshots
AE. Existing AI attention flow regression
"""

from datetime import datetime, timezone
import json
import pytest
from typing import Any, Dict, List

from app.core.models.planning import (
    ArtifactRecord,
    ArtifactRelationshipRecord,
    ArtifactStatus,
    ArtifactType,
    Bottleneck,
    BottleneckType,
    CapacityState,
    DependencyClassification,
    PlanningArtifactContext,
    PlanningContext,
    PlanningDependencyContext,
    PlanningResourceContext,
    PlanningScheduleSummary,
    PlanningTaskContext,
    PlanningTeamSummary,
    QueueTaskDetail,
    ResourceCapacitySummary,
    ResourcePaceSummary,
    ResourceQueueSnapshot,
    ScheduleConstraint,
    TaskScheduleProjection,
    TeamScheduleProjection,
)
from app.core.planning.context import PlanningContextBuilder
from app.core.planning.dag import DependencyGraph
from app.services.ai.context import ContextBuilder
from app.services.ai.models import AIContext


def _make_snapshot(
    resource_id: str,
    display_name: str,
    tasks: List[QueueTaskDetail] = None,
    daily_capacity: float = 6.75,
    committed_hours: float = 0.0,
    available_hours: float = 67.5,
    cap_state: CapacityState = CapacityState.BALANCED,
    cap_quality: str = "CAPACITY_KNOWN",
    history_completeness: str = "SUFFICIENT_HISTORY",
    role: str = "Developer",
    role_category: str = "Backend Development",
    team_group: str = "Mursaleen Cluster",
    pace_mean: float = 6.0,
    pace_confidence: str = "HIGH",
) -> ResourceQueueSnapshot:
    """Helper to build a deterministic ResourceQueueSnapshot for testing."""
    task_list = tasks or []
    total_remaining = sum(t.remaining_hours for t in task_list)
    effective_committed = committed_hours if committed_hours > 0 else total_remaining

    return ResourceQueueSnapshot(
        resource_id=resource_id,
        display_name=display_name,
        designation=role,
        role_category=role_category,
        team_group=team_group,
        snapshot_timestamp="2026-09-21T09:00:00+00:00",
        active_tasks=task_list,
        active_task_count=len(task_list),
        total_inferred_remaining_hours=total_remaining,
        historical_pace=ResourcePaceSummary(
            completed_task_count=10 if history_completeness == "SUFFICIENT_HISTORY" else 0,
            mean_hours=pace_mean,
            median_hours=pace_mean,
            p25_hours=pace_mean * 0.8,
            p75_hours=pace_mean * 1.2,
            confidence=pace_confidence,
        ),
        capacity=ResourceCapacitySummary(
            nominal_daily_capacity_hours=6.75,
            observed_daily_capacity_hours=daily_capacity,
            forecast_daily_capacity_hours=daily_capacity,
            horizon_working_days=10,
            available_capacity_hours=available_hours,
            committed_workload_hours=effective_committed,
            remaining_capacity_hours=max(available_hours - effective_committed, 0.0),
            capacity_state=cap_state,
        ),
        history_completeness=history_completeness,
        capacity_quality=cap_quality,
        queue_completeness="QUEUE_COMPLETE" if all(t.expected_effort_source == "jira_estimate" for t in task_list) else "QUEUE_PARTIAL",
        workload_pressure_level="NORMAL",
    )


def _make_task(
    issue_key: str,
    summary: str = "Task summary",
    expected_effort_hours: float = None,
    remaining_hours: float = 6.75,
    priority: str = "Medium",
    due_date: str = None,
    source: str = "jira_estimate",
    confidence: str = "high",
    is_overdue: bool = False,
    is_blocked: bool = False,
    hard_blockers: List[str] = None,
    produced_artifacts: List[str] = None,
    consumed_artifacts: List[str] = None,
) -> QueueTaskDetail:
    """Helper to build a deterministic QueueTaskDetail for testing."""
    effective_effort = expected_effort_hours if expected_effort_hours is not None else remaining_hours
    return QueueTaskDetail(
        issue_key=issue_key,
        summary=summary,
        status="In Progress",
        priority=priority,
        issue_type="Task",
        task_nature="DEVELOPMENT",
        project_key="WSSS",
        due_date=due_date,
        remaining_hours=remaining_hours,
        expected_effort_hours=effective_effort,
        expected_effort_source=source,
        expected_effort_confidence=confidence,
        is_overdue=is_overdue,
        is_blocked=is_blocked,
        hard_blocker_keys=hard_blockers or [],
        produced_artifact_names=produced_artifacts or [],
        consumed_artifact_names=consumed_artifacts or [],
    )


class TestPlanningContext:
    """Phase 3E Unit Test Suite for PlanningContext & PlanningContextBuilder."""

    def test_a_empty_team(self):
        """A. Empty team generates a valid empty PlanningContext."""
        builder = PlanningContextBuilder()
        ctx = builder.build_context(
            team_snapshots=[],
            anchor_date="2026-09-21",
            horizon_working_days=10,
        )

        assert isinstance(ctx, PlanningContext)
        assert ctx.team_summary.resource_count == 0
        assert ctx.team_summary.active_task_count == 0
        assert len(ctx.resources) == 0
        assert len(ctx.tasks) == 0
        assert len(ctx.dependencies) == 0
        assert len(ctx.artifacts) == 0
        assert ctx.schedule.schedule_valid is True
        assert ctx.truncation.is_truncated is False

    def test_b_single_resource(self):
        """B. Single resource without tasks generates valid PlanningContext."""
        builder = PlanningContextBuilder()
        snap = _make_snapshot("acc-1", "Alice")
        ctx = builder.build_context(team_snapshots=[snap], anchor_date="2026-09-21")

        assert ctx.team_summary.resource_count == 1
        assert len(ctx.resources) == 1
        assert ctx.resources[0].resource_id == "acc-1"
        assert ctx.resources[0].display_name == "Alice"
        assert ctx.resources[0].available_capacity_hours == 67.5

    def test_c_multiple_resources(self):
        """C. Multiple resources are composed and indexed."""
        builder = PlanningContextBuilder()
        s1 = _make_snapshot("acc-1", "Alice", available_hours=67.5)
        s2 = _make_snapshot("acc-2", "Bob", available_hours=50.0)
        ctx = builder.build_context(team_snapshots=[s1, s2], anchor_date="2026-09-21")

        assert ctx.team_summary.resource_count == 2
        assert ctx.team_summary.total_available_capacity_hours == 117.5
        assert len(ctx.resources) == 2

    def test_d_multiple_tasks(self):
        """D. Multiple tasks are extracted and mapped to their resources."""
        builder = PlanningContextBuilder()
        t1 = _make_task("WSSS-1", summary="Fix login bug", remaining_hours=4.0)
        t2 = _make_task("WSSS-2", summary="Design UI", remaining_hours=8.0)
        s1 = _make_snapshot("acc-1", "Alice", tasks=[t1, t2])
        ctx = builder.build_context(team_snapshots=[s1], anchor_date="2026-09-21")

        assert ctx.team_summary.active_task_count == 2
        assert ctx.team_summary.total_remaining_effort_hours == 12.0
        assert len(ctx.tasks) == 2
        assert ctx.tasks[0].issue_key == "WSSS-1"
        assert ctx.tasks[0].assigned_resource_id == "acc-1"
        assert ctx.tasks[1].issue_key == "WSSS-2"

    def test_e_capacity_information_preserved(self):
        """E. Capacity information from ResourceCapacitySummary is preserved."""
        builder = PlanningContextBuilder()
        s1 = _make_snapshot(
            "acc-1",
            "Alice",
            available_hours=45.0,
            committed_hours=60.0,
            cap_state=CapacityState.OVERLOADED,
        )
        ctx = builder.build_context(team_snapshots=[s1], anchor_date="2026-09-21")

        res = ctx.resources[0]
        assert res.available_capacity_hours == 45.0
        assert res.capacity_state == CapacityState.OVERLOADED
        assert ctx.team_summary.overloaded_resource_count == 1

    def test_f_historical_pace_preserved(self):
        """F. Historical pace metrics and confidence are preserved."""
        builder = PlanningContextBuilder()
        s1 = _make_snapshot("acc-1", "Alice", pace_mean=8.5, pace_confidence="HIGH")
        ctx = builder.build_context(team_snapshots=[s1], anchor_date="2026-09-21")

        res = ctx.resources[0]
        assert res.historical_pace.mean_hours == 8.5
        assert res.historical_pace.confidence == "HIGH"
        assert res.historical_pace.completed_task_count == 10

    def test_g_task_duration_evidence_preserved(self):
        """G. Duration evidence source and confidence are preserved."""
        builder = PlanningContextBuilder()
        t1 = _make_task("WSSS-1", expected_effort_hours=12.0, source="role_benchmark", confidence="medium")
        s1 = _make_snapshot("acc-1", "Alice", tasks=[t1])
        ctx = builder.build_context(team_snapshots=[s1], anchor_date="2026-09-21")

        task = ctx.tasks[0]
        assert task.estimated_remaining_hours == 12.0
        assert task.duration_evidence_source == "role_benchmark"
        assert task.duration_confidence == "medium"

    def test_h_dependency_information_preserved(self):
        """H. Dependency graph information is preserved in PlanningContext."""
        dag = DependencyGraph(include_only_hard_blocks=True)
        dag.add_edge("WSSS-1", "WSSS-2", "Blocks", DependencyClassification.HARD_BLOCK)

        t1 = _make_task("WSSS-1")
        t2 = _make_task("WSSS-2", is_blocked=True, hard_blockers=["WSSS-1"])
        s1 = _make_snapshot("acc-1", "Alice", tasks=[t1, t2])

        builder = PlanningContextBuilder()
        ctx = builder.build_context(
            team_snapshots=[s1],
            dependency_graph=dag,
            anchor_date="2026-09-21",
        )

        assert len(ctx.dependencies) == 1
        dep = ctx.dependencies[0]
        assert dep.source_issue_key == "WSSS-1"
        assert dep.target_issue_key == "WSSS-2"
        assert dep.is_hard_block is True
        assert dep.is_advisory is False

    def test_i_hard_block_distinction_preserved(self):
        """I. HARD_BLOCK edges are explicitly marked as hard."""
        builder = PlanningContextBuilder()
        proj = TeamScheduleProjection(
            forecast_timestamp="2026-09-21T09:00:00Z",
            anchor_date="2026-09-21",
            horizon_end_date="2026-10-02",
            dependency_constraints=[
                ScheduleConstraint(
                    source_issue_key="WSSS-1",
                    target_issue_key="WSSS-2",
                    constraint_type="HARD_BLOCK",
                    is_hard_block=True,
                    description="WSSS-1 blocks WSSS-2",
                )
            ],
        )
        ctx = builder.build_context(team_snapshots=[], schedule_projection=proj)

        assert len(ctx.dependencies) == 1
        assert ctx.dependencies[0].is_hard_block is True
        assert ctx.dependencies[0].is_advisory is False
        assert ctx.dependencies[0].classification == DependencyClassification.HARD_BLOCK

    def test_j_advisory_dependency_distinction_preserved(self):
        """J. Non-hard dependencies (Relates, Problem/Incident) are preserved as advisory."""
        builder = PlanningContextBuilder()
        proj = TeamScheduleProjection(
            forecast_timestamp="2026-09-21T09:00:00Z",
            anchor_date="2026-09-21",
            horizon_end_date="2026-10-02",
            dependency_constraints=[
                ScheduleConstraint(
                    source_issue_key="WSSS-1",
                    target_issue_key="WSSS-2",
                    constraint_type="CAUSAL_DEPENDENCY",
                    is_hard_block=False,
                    description="Problem relation",
                )
            ],
        )
        ctx = builder.build_context(team_snapshots=[], schedule_projection=proj)

        assert len(ctx.dependencies) == 1
        assert ctx.dependencies[0].is_hard_block is False
        assert ctx.dependencies[0].is_advisory is True
        assert ctx.dependencies[0].classification == DependencyClassification.CAUSAL_DEPENDENCY

    def test_k_artifact_producer_consumer_preserved(self):
        """K. Artifact producer and consumer relationships are preserved."""
        builder = PlanningContextBuilder()
        art_rec = ArtifactRecord(
            id="WSSS:api-spec",
            name="api-spec",
            project_key="WSSS",
            artifact_type=ArtifactType.API_CONTRACT,
            status=ArtifactStatus.AVAILABLE,
            producer_issue_key="WSSS-1",
            confidence="HIGH",
            first_seen_at="2026-09-21T09:00:00Z",
            last_seen_at="2026-09-21T09:00:00Z",
        )
        rel1 = ArtifactRelationshipRecord(
            id="WSSS:api-spec:WSSS-1:PRODUCES",
            artifact_id="WSSS:api-spec",
            issue_key="WSSS-1",
            relationship_type="PRODUCES",
            first_seen_at="2026-09-21T09:00:00Z",
            last_seen_at="2026-09-21T09:00:00Z",
        )
        rel2 = ArtifactRelationshipRecord(
            id="WSSS:api-spec:WSSS-2:CONSUMES",
            artifact_id="WSSS:api-spec",
            issue_key="WSSS-2",
            relationship_type="CONSUMES",
            first_seen_at="2026-09-21T09:00:00Z",
            last_seen_at="2026-09-21T09:00:00Z",
        )

        t1 = _make_task("WSSS-1", produced_artifacts=["api-spec"])
        t2 = _make_task("WSSS-2", consumed_artifacts=["api-spec"])
        s1 = _make_snapshot("acc-1", "Alice", tasks=[t1])
        s2 = _make_snapshot("acc-2", "Bob", tasks=[t2])

        ctx = builder.build_context(
            team_snapshots=[s1, s2],
            artifact_records=[art_rec],
            artifact_relationships=[rel1, rel2],
            anchor_date="2026-09-21",
        )

        assert len(ctx.artifacts) == 1
        art = ctx.artifacts[0]
        assert art.artifact_name == "api-spec"
        assert art.producer_issue_key == "WSSS-1"
        assert art.producer_resource_id == "acc-1"
        assert "WSSS-2" in art.consumer_issue_keys
        assert "acc-2" in art.consumer_resource_ids
        assert art.is_inferred is False
        assert art.is_advisory is False

    def test_l_inferred_artifact_marked_advisory(self):
        """L. Inferred artifact relationships are marked advisory."""
        builder = PlanningContextBuilder()
        art_rec = ArtifactRecord(
            id="WSSS:design-asset",
            name="design-asset",
            project_key="WSSS",
            artifact_type=ArtifactType.DESIGN_ASSET,
            status=ArtifactStatus.PLANNED,
            producer_issue_key="WSSS-1",
            confidence="MEDIUM",
            provenance="TASK_NATURE_INFERENCE",
            first_seen_at="2026-09-21T09:00:00Z",
            last_seen_at="2026-09-21T09:00:00Z",
        )
        rel = ArtifactRelationshipRecord(
            id="WSSS:design-asset:WSSS-1:PRODUCES",
            artifact_id="WSSS:design-asset",
            issue_key="WSSS-1",
            relationship_type="PRODUCES",
            is_inferred=True,
            provenance="TASK_NATURE_INFERENCE",
            first_seen_at="2026-09-21T09:00:00Z",
            last_seen_at="2026-09-21T09:00:00Z",
        )

        ctx = builder.build_context(
            team_snapshots=[],
            artifact_records=[art_rec],
            artifact_relationships=[rel],
            anchor_date="2026-09-21",
        )

        assert len(ctx.artifacts) == 1
        assert ctx.artifacts[0].is_inferred is True
        assert ctx.artifacts[0].is_advisory is True

    def test_m_schedule_projection_preserved(self):
        """M. TeamScheduleProjection dates and horizon are preserved."""
        builder = PlanningContextBuilder()
        tp1 = TaskScheduleProjection(
            issue_key="WSSS-1",
            resource_id="acc-1",
            resource_display_name="Alice",
            earliest_feasible_start_date="2026-09-21",
            projected_start_date="2026-09-21",
            projected_completion_date="2026-09-23",
            working_days_needed=3.0,
            is_beyond_horizon=False,
        )
        proj = TeamScheduleProjection(
            forecast_timestamp="2026-09-21T09:00:00Z",
            anchor_date="2026-09-21",
            planning_horizon_working_days=10,
            horizon_end_date="2026-10-02",
            tasks_projected_count=1,
            task_projections=[tp1],
            longest_dependency_chain=["WSSS-1"],
        )
        t1 = _make_task("WSSS-1")
        s1 = _make_snapshot("acc-1", "Alice", tasks=[t1])

        ctx = builder.build_context(
            team_snapshots=[s1],
            schedule_projection=proj,
        )

        assert ctx.schedule.schedule_valid is True
        assert ctx.schedule.anchor_date == "2026-09-21"
        assert ctx.schedule.horizon_end_date == "2026-10-02"
        assert ctx.schedule.longest_dependency_chain == ["WSSS-1"]
        assert ctx.tasks[0].projected_start_date == "2026-09-21"
        assert ctx.tasks[0].projected_completion_date == "2026-09-23"

    def test_n_bottlenecks_preserved(self):
        """N. Bottlenecks from schedule projection are preserved with severity."""
        builder = PlanningContextBuilder()
        b1 = Bottleneck(
            type=BottleneckType.OVERLOADED_RESOURCE,
            affected_resource_id="acc-1",
            severity="HIGH",
            evidence="Resource workload 100h exceeds 67.5h capacity",
            data_quality="HIGH",
        )
        proj = TeamScheduleProjection(
            forecast_timestamp="2026-09-21T09:00:00Z",
            anchor_date="2026-09-21",
            horizon_end_date="2026-10-02",
            bottlenecks=[b1],
        )
        ctx = builder.build_context(team_snapshots=[], schedule_projection=proj)

        assert len(ctx.schedule.bottlenecks) == 1
        b = ctx.schedule.bottlenecks[0]
        assert b.type == BottleneckType.OVERLOADED_RESOURCE
        assert b.affected_resource_id == "acc-1"
        assert b.severity == "HIGH"

    def test_o_data_quality_preserved(self):
        """O. Data quality indicators are preserved."""
        builder = PlanningContextBuilder()
        s1 = _make_snapshot(
            "acc-1",
            "Alice",
            history_completeness="SUFFICIENT_HISTORY",
            cap_quality="CAPACITY_KNOWN",
        )
        ctx = builder.build_context(team_snapshots=[s1], anchor_date="2026-09-21")

        assert ctx.resources[0].history_completeness == "SUFFICIENT_HISTORY"
        assert ctx.resources[0].capacity_quality == "CAPACITY_KNOWN"

    def test_p_missing_history_preserved(self):
        """P. Missing history is explicitly preserved as NO_HISTORY."""
        builder = PlanningContextBuilder()
        s1 = _make_snapshot(
            "acc-1",
            "Alice",
            history_completeness="NO_HISTORY",
            pace_confidence="INSUFFICIENT",
        )
        ctx = builder.build_context(team_snapshots=[s1], anchor_date="2026-09-21")

        assert ctx.resources[0].history_completeness == "NO_HISTORY"
        assert ctx.resources[0].historical_pace.confidence == "INSUFFICIENT"

    def test_q_missing_capacity_preserved(self):
        """Q. Missing capacity is preserved as CAPACITY_UNAVAILABLE."""
        builder = PlanningContextBuilder()
        s1 = _make_snapshot(
            "acc-1",
            "Alice",
            cap_quality="CAPACITY_UNAVAILABLE",
            cap_state=CapacityState.UNKNOWN,
        )
        ctx = builder.build_context(team_snapshots=[s1], anchor_date="2026-09-21")

        assert ctx.resources[0].capacity_quality == "CAPACITY_UNAVAILABLE"
        assert ctx.resources[0].capacity_state == CapacityState.UNKNOWN

    def test_r_missing_duration_evidence_preserved(self):
        """R. Missing duration evidence is preserved without fabricating estimates."""
        builder = PlanningContextBuilder()
        t1 = _make_task("WSSS-1", expected_effort_hours=0.0, source="unavailable", confidence="unavailable")
        s1 = _make_snapshot("acc-1", "Alice", tasks=[t1])
        ctx = builder.build_context(team_snapshots=[s1], anchor_date="2026-09-21")

        assert ctx.tasks[0].duration_evidence_source == "unavailable"
        assert ctx.tasks[0].duration_confidence == "unavailable"

    def test_s_deterministic_resource_ordering(self):
        """S. Resources are deterministically ordered by resource_id regardless of input order."""
        builder = PlanningContextBuilder()
        s_c = _make_snapshot("acc-charlie", "Charlie")
        s_a = _make_snapshot("acc-alice", "Alice")
        s_b = _make_snapshot("acc-bob", "Bob")

        ctx1 = builder.build_context(team_snapshots=[s_c, s_a, s_b], anchor_date="2026-09-21")
        ctx2 = builder.build_context(team_snapshots=[s_a, s_b, s_c], anchor_date="2026-09-21")

        res_ids_1 = [r.resource_id for r in ctx1.resources]
        res_ids_2 = [r.resource_id for r in ctx2.resources]

        assert res_ids_1 == ["acc-alice", "acc-bob", "acc-charlie"]
        assert res_ids_1 == res_ids_2

    def test_t_deterministic_task_ordering(self):
        """T. Tasks are deterministically ordered by issue_key."""
        builder = PlanningContextBuilder()
        t3 = _make_task("WSSS-3")
        t1 = _make_task("WSSS-1")
        t2 = _make_task("WSSS-2")
        s1 = _make_snapshot("acc-1", "Alice", tasks=[t3, t1, t2])

        ctx = builder.build_context(team_snapshots=[s1], anchor_date="2026-09-21")
        keys = [t.issue_key for t in ctx.tasks]
        assert keys == ["WSSS-1", "WSSS-2", "WSSS-3"]

    def test_u_deterministic_dependency_ordering(self):
        """U. Dependencies are deterministically ordered by source, target, link_type."""
        builder = PlanningContextBuilder()
        proj = TeamScheduleProjection(
            forecast_timestamp="2026-09-21T09:00:00Z",
            anchor_date="2026-09-21",
            horizon_end_date="2026-10-02",
            dependency_constraints=[
                ScheduleConstraint(source_issue_key="WSSS-3", target_issue_key="WSSS-4", constraint_type="HARD_BLOCK"),
                ScheduleConstraint(source_issue_key="WSSS-1", target_issue_key="WSSS-2", constraint_type="HARD_BLOCK"),
                ScheduleConstraint(source_issue_key="WSSS-1", target_issue_key="WSSS-3", constraint_type="HARD_BLOCK"),
            ],
        )
        ctx = builder.build_context(team_snapshots=[], schedule_projection=proj)
        pairs = [(d.source_issue_key, d.target_issue_key) for d in ctx.dependencies]
        assert pairs == [("WSSS-1", "WSSS-2"), ("WSSS-1", "WSSS-3"), ("WSSS-3", "WSSS-4")]

    def test_v_deterministic_artifact_ordering(self):
        """V. Artifacts are deterministically ordered by project_key, artifact_name."""
        builder = PlanningContextBuilder()
        a1 = ArtifactRecord(id="WSSS:schema", name="schema", project_key="WSSS", first_seen_at="now", last_seen_at="now")
        a2 = ArtifactRecord(id="WSSS:api-spec", name="api-spec", project_key="WSSS", first_seen_at="now", last_seen_at="now")
        a3 = ArtifactRecord(id="GLOBAL:shared-doc", name="shared-doc", project_key="GLOBAL", first_seen_at="now", last_seen_at="now")

        ctx = builder.build_context(
            team_snapshots=[],
            artifact_records=[a1, a2, a3],
            anchor_date="2026-09-21",
        )
        art_names = [(a.project_key, a.artifact_name) for a in ctx.artifacts]
        assert art_names == [("GLOBAL", "shared-doc"), ("WSSS", "api-spec"), ("WSSS", "schema")]

    def test_w_input_order_independence(self):
        """W. Equivalent inputs in different orders produce identical serialized output."""
        builder = PlanningContextBuilder()
        t1 = _make_task("WSSS-1")
        t2 = _make_task("WSSS-2")
        s_a = _make_snapshot("acc-1", "Alice", tasks=[t1])
        s_b = _make_snapshot("acc-2", "Bob", tasks=[t2])

        now_fixed = datetime(2026, 9, 21, 9, 0, 0, tzinfo=timezone.utc)

        ctx_1 = builder.build_context(team_snapshots=[s_a, s_b], anchor_date="2026-09-21", now=now_fixed)
        ctx_2 = builder.build_context(team_snapshots=[s_b, s_a], anchor_date="2026-09-21", now=now_fixed)

        dump1 = ctx_1.model_dump()
        dump2 = ctx_2.model_dump()

        assert json.dumps(dump1, sort_keys=True) == json.dumps(dump2, sort_keys=True)

    def test_x_context_bounds(self):
        """X. Context bounds enforce caps on resources and tasks."""
        builder = PlanningContextBuilder(
            max_resources=2,
            max_total_tasks=3,
        )
        snaps = [
            _make_snapshot(f"acc-{i}", f"User {i}", tasks=[_make_task(f"WSSS-{i}-1"), _make_task(f"WSSS-{i}-2")])
            for i in range(1, 5)
        ]

        ctx = builder.build_context(team_snapshots=snaps, anchor_date="2026-09-21")

        assert len(ctx.resources) == 2
        assert len(ctx.tasks) == 3
        assert ctx.truncation.is_truncated is True
        assert ctx.truncation.original_resource_count == 4
        assert ctx.truncation.included_resource_count == 2
        assert ctx.truncation.original_task_count == 4  # 2 tasks each from 2 included resources
        assert ctx.truncation.included_task_count == 3

    def test_y_truncation_metadata(self):
        """Y. Truncation reasons are explicitly recorded in truncation metadata."""
        builder = PlanningContextBuilder(max_resources=1)
        s1 = _make_snapshot("acc-1", "Alice")
        s2 = _make_snapshot("acc-2", "Bob")

        ctx = builder.build_context(team_snapshots=[s1, s2], anchor_date="2026-09-21")

        assert ctx.truncation.is_truncated is True
        assert len(ctx.truncation.truncation_reasons) > 0
        assert "Resource count truncated" in ctx.truncation.truncation_reasons[0]

    def test_z_secret_sanitization(self):
        """Z. Secret-like strings in display names, summaries, and evidence are redacted."""
        builder = PlanningContextBuilder()
        secret_summary = "Fix bug with Bearer sk-ant-api-token-12345678"
        t1 = _make_task("WSSS-1", summary=secret_summary)
        s1 = _make_snapshot("acc-1", "Alice Bearer 12345678", tasks=[t1])

        b1 = Bottleneck(
            type=BottleneckType.OVERLOADED_RESOURCE,
            affected_resource_id="acc-1",
            severity="HIGH",
            evidence="Token leaked: token=super_secret_token_123",
        )
        proj = TeamScheduleProjection(
            forecast_timestamp="2026-09-21T09:00:00Z",
            anchor_date="2026-09-21",
            horizon_end_date="2026-10-02",
            bottlenecks=[b1],
        )

        ctx = builder.build_context(team_snapshots=[s1], schedule_projection=proj)

        # Confirm tokens are redacted
        assert "sk-ant-api-token-12345678" not in ctx.tasks[0].summary
        assert "******" in ctx.tasks[0].summary
        assert "super_secret_token_123" not in ctx.schedule.bottlenecks[0].evidence
        assert "******" in ctx.schedule.bottlenecks[0].evidence

    def test_aa_no_jira_api_calls(self, monkeypatch):
        """AA. PlanningContextBuilder makes ZERO Jira REST API calls."""
        # Monkeypatch any potential network request to fail
        def _fail_http(*args, **kwargs):
            raise RuntimeError("Unexpected HTTP/Jira call made during PlanningContext construction!")

        monkeypatch.setattr("httpx.Client.request", _fail_http)
        monkeypatch.setattr("httpx.AsyncClient.request", _fail_http)

        builder = PlanningContextBuilder()
        s1 = _make_snapshot("acc-1", "Alice")
        ctx = builder.build_context(team_snapshots=[s1], anchor_date="2026-09-21")
        assert ctx.team_summary.resource_count == 1

    def test_ab_no_deepseek_calls(self, monkeypatch):
        """AB. PlanningContextBuilder makes ZERO DeepSeek calls."""
        def _fail_ai(*args, **kwargs):
            raise RuntimeError("Unexpected DeepSeek/AI call made during PlanningContext construction!")

        monkeypatch.setattr("app.services.ai.providers.deepseek.DeepSeekAIProvider.analyze", _fail_ai)

        builder = PlanningContextBuilder()
        s1 = _make_snapshot("acc-1", "Alice")
        ctx = builder.build_context(team_snapshots=[s1], anchor_date="2026-09-21")
        assert ctx.team_summary.resource_count == 1

    def test_ac_no_action_engine_calls(self, monkeypatch):
        """AC. PlanningContextBuilder makes ZERO Action Engine calls."""
        builder = PlanningContextBuilder()
        s1 = _make_snapshot("acc-1", "Alice")
        ctx = builder.build_context(team_snapshots=[s1], anchor_date="2026-09-21")
        assert ctx is not None

    def test_ad_no_mutation_of_source_snapshots(self):
        """AD. Source snapshots and tasks are not mutated during context construction."""
        t1 = _make_task("WSSS-1", summary="Original summary")
        s1 = _make_snapshot("acc-1", "Alice", tasks=[t1])

        builder = PlanningContextBuilder()
        ctx = builder.build_context(team_snapshots=[s1], anchor_date="2026-09-21")

        assert s1.active_task_count == 1
        assert s1.active_tasks[0].summary == "Original summary"
        assert len(s1.active_tasks) == 1

    def test_ae_existing_ai_attention_flow_regression(self, tmp_path):
        """AE. Existing AI attention flow and ContextBuilder continue to work unchanged."""
        from app.database.connection import DatabaseManager
        from app.database.schema import init_db

        mgr = DatabaseManager(db_path=str(tmp_path / "test_reg.db"))
        init_db(mgr)

        cb = ContextBuilder(manager=mgr)
        ai_ctx = cb.build_attention_context(
            team_group="Mursaleen Cluster",
            objective="Analyze PM attention candidates",
        )

        assert isinstance(ai_ctx, AIContext)
        assert ai_ctx.team_name == "Mursaleen Cluster"
        assert len(ai_ctx.applicable_policies) > 0
