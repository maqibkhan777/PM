"""Deterministic unit tests for Phase 3D: Deterministic Team Timeline & Bottleneck Forecaster.

Covers all required scenarios A through AA plus AB:
A. Single resource, single task
B. Multiple independent resources working in parallel
C. A -> B HARD_BLOCK
D. A -> B -> C dependency chain
E. Cross-resource dependency
F. Multiple independent dependency chains
G. Dependency cycle graceful failure
H. Unknown dependency does not hard-block
I. Informational relationship ignored for scheduling
J. Verification relationship preserved but non-blocking
K. Explicit artifact producer -> consumer
L. Inferred artifact remains advisory
M. Overloaded resource
N. Insufficient capacity
O. Missing duration evidence
P. Strong duration evidence
Q. Weak duration evidence
R. Working-day boundary (Friday to Monday)
S. Weekend handling
T. Planning horizon boundary (is_beyond_horizon)
U. Deterministic tie-breaking
V. Empty team
W. Resource with no queue
X. Multiple bottlenecks
Y. No Jira API calls
Z. No DeepSeek calls
AA. No Action Engine calls
AB. Input-order independence
"""

from datetime import datetime, date, timezone, timedelta
import json
import pytest
from typing import Any, Dict, List

from app.core.models.planning import (
    BottleneckType,
    CapacityState,
    DependencyClassification,
    QueueTaskDetail,
    ResourceCapacitySummary,
    ResourcePaceSummary,
    ResourceQueueSnapshot,
    TeamScheduleProjection,
)
from app.core.planning.dag import DependencyGraph
from app.core.planning.forecaster import TeamScheduleForecaster
from app.database.connection import DatabaseManager
from app.database.repositories import (
    ArtifactRepository,
    JiraIssueLinkRepository,
)
from app.database.schema import init_db
from app.utils.time import format_iso, utc_now_iso


@pytest.fixture
def test_db_manager(tmp_path):
    """Provide isolated database for Phase 3D tests."""
    db_file = str(tmp_path / "test_pm_phase3d.db")
    mgr = DatabaseManager(db_path=db_file)
    init_db(mgr)
    return mgr


@pytest.fixture
def forecaster(test_db_manager):
    """Instantiate TeamScheduleForecaster connected to isolated test database."""
    return TeamScheduleForecaster(manager=test_db_manager)


def _make_snapshot(
    resource_id: str,
    display_name: str,
    tasks: List[QueueTaskDetail],
    daily_capacity: float = 6.75,
    committed_hours: float = 0.0,
    available_hours: float = 67.5,
    cap_state: CapacityState = CapacityState.BALANCED,
    cap_quality: str = "CAPACITY_KNOWN",
    history_completeness: str = "SUFFICIENT_HISTORY",
) -> ResourceQueueSnapshot:
    """Helper to build a deterministic ResourceQueueSnapshot for testing."""
    total_remaining = sum(t.remaining_hours for t in tasks) if tasks else 0.0
    effective_committed = committed_hours if committed_hours > 0 else total_remaining

    return ResourceQueueSnapshot(
        resource_id=resource_id,
        display_name=display_name,
        snapshot_timestamp="2026-09-21T09:00:00+00:00",
        active_tasks=tasks,
        active_task_count=len(tasks),
        total_remaining_workload_hours=total_remaining,
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
        queue_completeness="QUEUE_COMPLETE" if all(t.expected_effort_source == "jira_estimate" for t in tasks) else "QUEUE_PARTIAL",
    )


def _make_task(
    issue_key: str,
    expected_effort_hours: float = 6.75,
    priority: str = "Medium",
    due_date: str = None,
    hard_blockers: List[str] = None,
    produced_artifacts: List[str] = None,
    consumed_artifacts: List[str] = None,
    effort_source: str = "jira_estimate",
    effort_conf: str = "HIGH",
) -> QueueTaskDetail:
    """Helper to build a deterministic QueueTaskDetail for testing."""
    return QueueTaskDetail(
        issue_key=issue_key,
        summary=f"Task {issue_key}",
        status="In Progress",
        priority=priority,
        issue_type="Task",
        due_date=due_date,
        expected_effort_hours=expected_effort_hours,
        remaining_hours=expected_effort_hours,
        expected_effort_source=effort_source,
        expected_effort_confidence=effort_conf,
        hard_blocker_keys=hard_blockers or [],
        produced_artifact_names=produced_artifacts or [],
        consumed_artifact_names=consumed_artifacts or [],
    )


class TestTeamScheduleForecaster:
    """Comprehensive deterministic test suite covering Phase 3D scenarios."""

    def test_scenario_a_single_resource_single_task(self, forecaster):
        """A. Single resource, single task."""
        # Anchor on a Monday: 2026-09-21
        anchor = date(2026, 9, 21)
        task = _make_task("TASK-1", expected_effort_hours=6.75)
        snap = _make_snapshot("acc-1", "Alice", [task])

        proj = forecaster.forecast_team_schedule(
            team_snapshots=[snap],
            anchor_date=anchor,
        )

        assert proj.schedule_valid is True
        assert proj.tasks_projected_count == 1
        p1 = proj.task_projections[0]
        assert p1.issue_key == "TASK-1"
        assert p1.projected_start_date == "2026-09-21"
        assert p1.projected_completion_date == "2026-09-21"
        assert p1.working_days_needed == 1.0
        assert p1.is_beyond_horizon is False

    def test_scenario_b_multiple_independent_resources_in_parallel(self, forecaster):
        """B. Multiple independent resources working in parallel."""
        anchor = date(2026, 9, 21)
        task_a = _make_task("TASK-A", expected_effort_hours=6.75)
        task_b = _make_task("TASK-B", expected_effort_hours=6.75)
        snap_a = _make_snapshot("acc-alice", "Alice", [task_a])
        snap_b = _make_snapshot("acc-bob", "Bob", [task_b])

        proj = forecaster.forecast_team_schedule(
            team_snapshots=[snap_a, snap_b],
            anchor_date=anchor,
        )

        assert proj.tasks_projected_count == 2
        p_a = next(p for p in proj.task_projections if p.issue_key == "TASK-A")
        p_b = next(p for p in proj.task_projections if p.issue_key == "TASK-B")
        # Both start on anchor concurrently without serialization
        assert p_a.projected_start_date == "2026-09-21"
        assert p_b.projected_start_date == "2026-09-21"

    def test_scenario_c_hard_block_dependency(self, forecaster):
        """C. A -> B HARD_BLOCK (completion of A gates B)."""
        anchor = date(2026, 9, 21)  # Monday
        task_a = _make_task("TASK-A", expected_effort_hours=6.75)
        task_b = _make_task("TASK-B", expected_effort_hours=6.75, hard_blockers=["TASK-A"])

        snap_a = _make_snapshot("acc-1", "Alice", [task_a])
        snap_b = _make_snapshot("acc-2", "Bob", [task_b])

        dag = DependencyGraph(include_only_hard_blocks=True)
        dag.add_edge("TASK-A", "TASK-B", "Blocks", DependencyClassification.HARD_BLOCK)

        proj = forecaster.forecast_team_schedule(
            team_snapshots=[snap_a, snap_b],
            dependency_graph=dag,
            anchor_date=anchor,
        )

        p_a = next(p for p in proj.task_projections if p.issue_key == "TASK-A")
        p_b = next(p for p in proj.task_projections if p.issue_key == "TASK-B")

        assert p_a.projected_completion_date == "2026-09-21"
        # B cannot start before A completes (starts at or after completion of A)
        assert p_b.projected_start_date >= p_a.projected_completion_date
        assert p_b.is_blocked_by_dependency is True

    def test_scenario_d_dependency_chain(self, forecaster):
        """D. A -> B -> C dependency chain."""
        anchor = date(2026, 9, 21)
        task_a = _make_task("TASK-A", expected_effort_hours=6.75)
        task_b = _make_task("TASK-B", expected_effort_hours=6.75, hard_blockers=["TASK-A"])
        task_c = _make_task("TASK-C", expected_effort_hours=6.75, hard_blockers=["TASK-B"])

        snap = _make_snapshot("acc-1", "Alice", [task_a, task_b, task_c])

        dag = DependencyGraph(include_only_hard_blocks=True)
        dag.add_edge("TASK-A", "TASK-B", "Blocks", DependencyClassification.HARD_BLOCK)
        dag.add_edge("TASK-B", "TASK-C", "Blocks", DependencyClassification.HARD_BLOCK)

        proj = forecaster.forecast_team_schedule(
            team_snapshots=[snap],
            dependency_graph=dag,
            anchor_date=anchor,
        )

        assert proj.longest_dependency_chain == ["TASK-A", "TASK-B", "TASK-C"]
        chain_bottleneck = next((b for b in proj.bottlenecks if b.type == BottleneckType.DEPENDENCY_CHAIN), None)
        assert chain_bottleneck is not None
        assert "TASK-A -> TASK-B -> TASK-C" in chain_bottleneck.evidence

    def test_scenario_e_cross_resource_dependency(self, forecaster):
        """E. Cross-resource dependency: Alice(Task-A) -> Bob(Task-B)."""
        anchor = date(2026, 9, 21)  # Mon
        task_a = _make_task("TASK-A", expected_effort_hours=6.75)  # Completes Mon
        task_b = _make_task("TASK-B", expected_effort_hours=6.75, hard_blockers=["TASK-A"])

        snap_alice = _make_snapshot("acc-alice", "Alice", [task_a])
        snap_bob = _make_snapshot("acc-bob", "Bob", [task_b])

        dag = DependencyGraph(include_only_hard_blocks=True)
        dag.add_edge("TASK-A", "TASK-B", "Blocks", DependencyClassification.HARD_BLOCK)

        proj = forecaster.forecast_team_schedule(
            team_snapshots=[snap_alice, snap_bob],
            dependency_graph=dag,
            anchor_date=anchor,
        )

        p_a = next(p for p in proj.task_projections if p.issue_key == "TASK-A")
        p_b = next(p for p in proj.task_projections if p.issue_key == "TASK-B")

        assert p_a.resource_id == "acc-alice"
        assert p_b.resource_id == "acc-bob"
        assert p_b.projected_start_date >= p_a.projected_completion_date

    def test_scenario_f_multiple_independent_dependency_chains(self, forecaster):
        """F. Multiple independent dependency chains (Chain 1: A1 -> B1, Chain 2: A2 -> B2)."""
        anchor = date(2026, 9, 21)
        t_a1 = _make_task("A1", expected_effort_hours=6.75)
        t_b1 = _make_task("B1", expected_effort_hours=6.75, hard_blockers=["A1"])
        t_a2 = _make_task("A2", expected_effort_hours=6.75)
        t_b2 = _make_task("B2", expected_effort_hours=6.75, hard_blockers=["A2"])

        snap1 = _make_snapshot("acc-1", "Alice", [t_a1, t_b1])
        snap2 = _make_snapshot("acc-2", "Bob", [t_a2, t_b2])

        dag = DependencyGraph(include_only_hard_blocks=True)
        dag.add_edge("A1", "B1", "Blocks", DependencyClassification.HARD_BLOCK)
        dag.add_edge("A2", "B2", "Blocks", DependencyClassification.HARD_BLOCK)

        proj = forecaster.forecast_team_schedule(
            team_snapshots=[snap1, snap2],
            dependency_graph=dag,
            anchor_date=anchor,
        )

        assert proj.schedule_valid is True
        assert proj.tasks_projected_count == 4

    def test_scenario_g_dependency_cycle_graceful_failure(self, forecaster):
        """G. Dependency cycle graceful failure (A -> B -> A)."""
        anchor = date(2026, 9, 21)
        t_a = _make_task("TASK-A", expected_effort_hours=6.75, hard_blockers=["TASK-B"])
        t_b = _make_task("TASK-B", expected_effort_hours=6.75, hard_blockers=["TASK-A"])

        snap = _make_snapshot("acc-1", "Alice", [t_a, t_b])

        dag = DependencyGraph(include_only_hard_blocks=True)
        dag.add_edge("TASK-A", "TASK-B", "Blocks", DependencyClassification.HARD_BLOCK)
        dag.add_edge("TASK-B", "TASK-A", "Blocks", DependencyClassification.HARD_BLOCK)

        proj = forecaster.forecast_team_schedule(
            team_snapshots=[snap],
            dependency_graph=dag,
            anchor_date=anchor,
        )

        assert proj.schedule_valid is False
        assert "Dependency cycle detected" in proj.error_message
        cycle_b = next((b for b in proj.bottlenecks if b.type == BottleneckType.DEPENDENCY_CYCLE), None)
        assert cycle_b is not None
        assert cycle_b.severity == "HIGH"

    def test_scenario_h_unknown_dependency_does_not_hard_block(self, forecaster, test_db_manager):
        """H. Unknown dependency does not hard-block."""
        link_repo = JiraIssueLinkRepository(test_db_manager)
        link_repo.upsert_link(
            source_issue_key="TASK-A",
            target_issue_key="TASK-B",
            link_type_name="UnknownLink",
            classification=DependencyClassification.UNKNOWN.value,
        )

        anchor = date(2026, 9, 21)
        t_a = _make_task("TASK-A", expected_effort_hours=6.75)
        t_b = _make_task("TASK-B", expected_effort_hours=6.75)

        snap1 = _make_snapshot("acc-1", "Alice", [t_a])
        snap2 = _make_snapshot("acc-2", "Bob", [t_b])

        proj = forecaster.forecast_team_schedule(
            team_snapshots=[snap1, snap2],
            anchor_date=anchor,
        )

        assert proj.schedule_valid is True
        p_b = next(p for p in proj.task_projections if p.issue_key == "TASK-B")
        # Task B is not blocked by unknown link, starts concurrently on anchor
        assert p_b.projected_start_date == "2026-09-21"

    def test_scenario_i_informational_relationship_ignored_for_scheduling(self, forecaster, test_db_manager):
        """I. Informational relationship ignored for scheduling."""
        link_repo = JiraIssueLinkRepository(test_db_manager)
        link_repo.upsert_link(
            source_issue_key="TASK-A",
            target_issue_key="TASK-B",
            link_type_name="Relates",
            classification=DependencyClassification.INFORMATIONAL.value,
        )

        anchor = date(2026, 9, 21)
        t_a = _make_task("TASK-A", expected_effort_hours=6.75)
        t_b = _make_task("TASK-B", expected_effort_hours=6.75)

        snap = _make_snapshot("acc-1", "Alice", [t_a, t_b])

        proj = forecaster.forecast_team_schedule(
            team_snapshots=[snap],
            anchor_date=anchor,
        )

        constraint = next((c for c in proj.dependency_constraints if c.constraint_type == "INFORMATIONAL"), None)
        assert constraint is not None
        assert constraint.is_hard_block is False

    def test_scenario_j_verification_relationship_preserved_but_non_blocking(self, forecaster, test_db_manager):
        """J. Verification relationship preserved but not hard-blocking."""
        link_repo = JiraIssueLinkRepository(test_db_manager)
        link_repo.upsert_link(
            source_issue_key="TASK-QA",
            target_issue_key="TASK-DEV",
            link_type_name="Test",
            classification=DependencyClassification.VERIFICATION_DEPENDENCY.value,
        )

        anchor = date(2026, 9, 21)
        t_qa = _make_task("TASK-QA", expected_effort_hours=6.75)
        t_dev = _make_task("TASK-DEV", expected_effort_hours=6.75)

        snap1 = _make_snapshot("acc-qa", "QA Tester", [t_qa])
        snap2 = _make_snapshot("acc-dev", "Developer", [t_dev])

        proj = forecaster.forecast_team_schedule(
            team_snapshots=[snap1, snap2],
            anchor_date=anchor,
        )

        p_dev = next(p for p in proj.task_projections if p.issue_key == "TASK-DEV")
        assert p_dev.is_blocked_by_dependency is False
        constraint = next((c for c in proj.dependency_constraints if c.constraint_type == "VERIFICATION_DEPENDENCY"), None)
        assert constraint is not None
        assert constraint.is_hard_block is False

    def test_scenario_k_explicit_artifact_producer_consumer(self, forecaster, test_db_manager):
        """K. Explicit artifact producer -> consumer surfaced as constraint & advisory bottleneck."""
        art_repo = ArtifactRepository(test_db_manager)
        now_iso = utc_now_iso()
        art_id = art_repo.upsert_artifact(
            name="payment-api-spec",
            project_key="PAY",
            artifact_type="API_CONTRACT",
            producer_issue_key="PAY-1",
            observed_at=now_iso,
        )
        art_repo.upsert_relationship(
            artifact_id=art_id,
            issue_key="PAY-1",
            relationship_type="PRODUCES",
            observed_at=now_iso,
        )
        art_repo.upsert_relationship(
            artifact_id=art_id,
            issue_key="PAY-2",
            relationship_type="CONSUMES",
            observed_at=now_iso,
        )

        anchor = date(2026, 9, 21)
        t1 = _make_task("PAY-1", expected_effort_hours=6.75, produced_artifacts=["payment-api-spec"])
        t2 = _make_task("PAY-2", expected_effort_hours=6.75, consumed_artifacts=["payment-api-spec"])

        snap1 = _make_snapshot("acc-1", "Designer", [t1])
        snap2 = _make_snapshot("acc-2", "Developer", [t2])

        proj = forecaster.forecast_team_schedule(
            team_snapshots=[snap1, snap2],
            anchor_date=anchor,
        )

        art_constraint = next((c for c in proj.dependency_constraints if c.constraint_type == "ARTIFACT_HANDOFF"), None)
        assert art_constraint is not None
        assert art_constraint.is_hard_block is False
        assert "payment-api-spec" in art_constraint.description

        art_bottleneck = next((b for b in proj.bottlenecks if b.type == BottleneckType.ARTIFACT_HANDOFF), None)
        assert art_bottleneck is not None
        assert art_bottleneck.severity == "LOW"

    def test_scenario_l_inferred_artifact_remains_advisory(self, forecaster, test_db_manager):
        """L. Inferred artifact remains advisory and never becomes HARD_BLOCK."""
        art_repo = ArtifactRepository(test_db_manager)
        now_iso = utc_now_iso()
        art_id = art_repo.upsert_artifact(
            name="design-asset",
            project_key="PAY",
            artifact_type="DESIGN_ASSET",
            producer_issue_key="PAY-10",
            provenance="TASK_NATURE_INFERENCE",
            confidence="MEDIUM",
            observed_at=now_iso,
        )
        art_repo.upsert_relationship(
            artifact_id=art_id,
            issue_key="PAY-10",
            relationship_type="PRODUCES",
            is_inferred=True,
            confidence="MEDIUM",
            observed_at=now_iso,
        )
        art_repo.upsert_relationship(
            artifact_id=art_id,
            issue_key="PAY-11",
            relationship_type="CONSUMES",
            is_inferred=True,
            confidence="MEDIUM",
            observed_at=now_iso,
        )

        anchor = date(2026, 9, 21)
        t1 = _make_task("PAY-10", expected_effort_hours=6.75, produced_artifacts=["design-asset"])
        t2 = _make_task("PAY-11", expected_effort_hours=6.75, consumed_artifacts=["design-asset"])

        snap1 = _make_snapshot("acc-1", "Alice", [t1])
        snap2 = _make_snapshot("acc-2", "Bob", [t2])

        proj = forecaster.forecast_team_schedule(
            team_snapshots=[snap1, snap2],
            anchor_date=anchor,
        )

        p2 = next(p for p in proj.task_projections if p.issue_key == "PAY-11")
        assert p2.is_blocked_by_dependency is False
        # Inferred artifact constraint is NOT hard block
        constraint = next((c for c in proj.dependency_constraints if c.constraint_type == "ARTIFACT_HANDOFF"), None)
        assert constraint is not None
        assert constraint.is_hard_block is False

    def test_scenario_m_overloaded_resource(self, forecaster):
        """M. Overloaded resource detection."""
        anchor = date(2026, 9, 21)
        # 100 hours committed workload vs 67.5h available capacity (ratio > 1.4 -> HIGH)
        t = _make_task("TASK-HEAVY", expected_effort_hours=100.0)
        snap = _make_snapshot(
            "acc-overloaded",
            "Alice",
            [t],
            committed_hours=100.0,
            available_hours=67.5,
            cap_state=CapacityState.OVERLOADED,
        )

        proj = forecaster.forecast_team_schedule(
            team_snapshots=[snap],
            anchor_date=anchor,
        )

        b = next((b for b in proj.bottlenecks if b.type == BottleneckType.OVERLOADED_RESOURCE), None)
        assert b is not None
        assert b.affected_resource_id == "acc-overloaded"

    def test_scenario_n_insufficient_capacity(self, forecaster):
        """N. Insufficient capacity bottleneck detection."""
        anchor = date(2026, 9, 21)
        t = _make_task("TASK-1", expected_effort_hours=6.75)
        snap = _make_snapshot(
            "acc-zero-cap",
            "Alice",
            [t],
            daily_capacity=0.0,
            cap_quality="CAPACITY_UNAVAILABLE",
        )

        proj = forecaster.forecast_team_schedule(
            team_snapshots=[snap],
            anchor_date=anchor,
        )

        b = next((b for b in proj.bottlenecks if b.type == BottleneckType.INSUFFICIENT_CAPACITY), None)
        assert b is not None
        assert b.affected_resource_id == "acc-zero-cap"

    def test_scenario_o_missing_duration_evidence(self, forecaster):
        """O. Missing duration evidence bottleneck."""
        anchor = date(2026, 9, 21)
        t = _make_task(
            "TASK-UNESTIMATED",
            expected_effort_hours=6.75,
            effort_source="unavailable",
            effort_conf="unavailable",
        )
        snap = _make_snapshot("acc-1", "Alice", [t])

        proj = forecaster.forecast_team_schedule(
            team_snapshots=[snap],
            anchor_date=anchor,
        )

        b = next((b for b in proj.bottlenecks if b.type == BottleneckType.MISSING_DURATION_EVIDENCE), None)
        assert b is not None
        assert b.affected_issue_key == "TASK-UNESTIMATED"

    def test_scenario_p_strong_duration_evidence(self, forecaster):
        """P. Strong duration evidence preserves high confidence."""
        anchor = date(2026, 9, 21)
        t = _make_task(
            "TASK-STRONG",
            expected_effort_hours=13.5,
            effort_source="jira_estimate",
            effort_conf="HIGH",
        )
        snap = _make_snapshot("acc-1", "Alice", [t])

        proj = forecaster.forecast_team_schedule(
            team_snapshots=[snap],
            anchor_date=anchor,
        )

        p = proj.task_projections[0]
        assert p.duration_evidence_source == "jira_estimate"
        assert p.duration_confidence == "HIGH"
        # No missing evidence bottleneck
        b = next((b for b in proj.bottlenecks if b.type == BottleneckType.MISSING_DURATION_EVIDENCE), None)
        assert b is None

    def test_scenario_q_weak_duration_evidence(self, forecaster):
        """Q. Weak duration evidence surfaces low confidence."""
        anchor = date(2026, 9, 21)
        t = _make_task(
            "TASK-FALLBACK",
            expected_effort_hours=5.0,
            effort_source="deterministic_fallback",
            effort_conf="LOW",
        )
        snap = _make_snapshot("acc-1", "Alice", [t])

        proj = forecaster.forecast_team_schedule(
            team_snapshots=[snap],
            anchor_date=anchor,
        )

        p = proj.task_projections[0]
        assert p.duration_evidence_source == "deterministic_fallback"
        assert p.duration_confidence == "LOW"

    def test_scenario_r_working_day_boundary(self, forecaster):
        """R. Working-day boundary: Friday effort finishes Monday."""
        # 2026-09-25 is Friday
        anchor_friday = date(2026, 9, 25)
        # 13.5 hours = exactly 2 working days (6.75h/day)
        t = _make_task("TASK-FRI", expected_effort_hours=13.5)
        snap = _make_snapshot("acc-1", "Alice", [t], daily_capacity=6.75)

        proj = forecaster.forecast_team_schedule(
            team_snapshots=[snap],
            anchor_date=anchor_friday,
        )

        p = proj.task_projections[0]
        assert p.projected_start_date == "2026-09-25"  # Friday
        assert p.projected_completion_date == "2026-09-28"  # Monday (weekend Saturday 26 and Sunday 27 skipped)

    def test_scenario_s_weekend_handling(self, forecaster):
        """S. Weekend anchor advances cleanly to Monday."""
        # 2026-09-26 is Saturday
        anchor_saturday = date(2026, 9, 26)
        t = _make_task("TASK-1", expected_effort_hours=6.75)
        snap = _make_snapshot("acc-1", "Alice", [t], daily_capacity=6.75)

        proj = forecaster.forecast_team_schedule(
            team_snapshots=[snap],
            anchor_date=anchor_saturday,
        )

        assert proj.anchor_date == "2026-09-28"  # Advanced to Monday
        p = proj.task_projections[0]
        assert p.projected_start_date == "2026-09-28"
        assert p.projected_completion_date == "2026-09-28"

    def test_scenario_t_planning_horizon_boundary(self, forecaster):
        """T. Planning horizon boundary: tasks exceeding horizon flagged with is_beyond_horizon=True."""
        # 2026-09-21 (Monday), 10 working days horizon ends 2026-10-02 (Friday)
        anchor = date(2026, 9, 21)
        # Task needing 15 working days = 101.25 hours
        t = _make_task("TASK-LONG", expected_effort_hours=101.25)
        snap = _make_snapshot("acc-1", "Alice", [t], daily_capacity=6.75)

        proj = forecaster.forecast_team_schedule(
            team_snapshots=[snap],
            horizon_working_days=10,
            anchor_date=anchor,
        )

        assert proj.tasks_beyond_horizon_count == 1
        p = proj.task_projections[0]
        assert p.is_beyond_horizon is True
        assert p.projected_completion_date > proj.horizon_end_date

    def test_scenario_u_deterministic_tie_breaking(self, forecaster):
        """U. Deterministic tie-breaking: priority rank -> due date -> issue key."""
        anchor = date(2026, 9, 21)
        # Same resource, same priority, different due dates and keys
        t1 = _make_task("TASK-Z", expected_effort_hours=6.75, priority="High", due_date="2026-09-25")
        t2 = _make_task("TASK-A", expected_effort_hours=6.75, priority="High", due_date="2026-09-22")
        t3 = _make_task("TASK-M", expected_effort_hours=6.75, priority="Highest", due_date="2026-09-30")

        snap = _make_snapshot("acc-1", "Alice", [t1, t2, t3])

        proj = forecaster.forecast_team_schedule(
            team_snapshots=[snap],
            anchor_date=anchor,
        )

        # Scheduled order must be: TASK-M (Highest), then TASK-A (High, due 22), then TASK-Z (High, due 25)
        keys_ordered = [p.issue_key for p in proj.task_projections]
        assert keys_ordered == ["TASK-M", "TASK-A", "TASK-Z"]

    def test_scenario_v_empty_team(self, forecaster):
        """V. Empty team snapshot produces valid empty projection without crash."""
        anchor = date(2026, 9, 21)
        proj = forecaster.forecast_team_schedule(
            team_snapshots=[],
            anchor_date=anchor,
        )

        assert proj.schedule_valid is True
        assert proj.tasks_projected_count == 0
        assert proj.tasks_beyond_horizon_count == 0
        assert len(proj.task_projections) == 0

    def test_scenario_w_resource_with_no_queue(self, forecaster):
        """W. Resource with no active tasks produces clean empty task projections."""
        anchor = date(2026, 9, 21)
        snap = _make_snapshot("acc-idle", "Idle Alice", [])

        proj = forecaster.forecast_team_schedule(
            team_snapshots=[snap],
            anchor_date=anchor,
        )

        assert proj.schedule_valid is True
        assert proj.tasks_projected_count == 0

    def test_scenario_x_multiple_bottlenecks(self, forecaster):
        """X. Multiple concurrent bottlenecks detected cleanly."""
        anchor = date(2026, 9, 21)
        # 1. Overloaded resource
        # 2. Blocked task
        # 3. Missing duration evidence
        t_blocked = _make_task(
            "TASK-B",
            expected_effort_hours=30.0,
            hard_blockers=["TASK-A"],
            effort_source="unavailable",
            effort_conf="unavailable",
        )
        t_a = _make_task("TASK-A", expected_effort_hours=60.0)

        snap = _make_snapshot(
            "acc-1",
            "Alice",
            [t_a, t_blocked],
            committed_hours=90.0,
            available_hours=67.5,
            cap_state=CapacityState.OVERLOADED,
        )

        dag = DependencyGraph(include_only_hard_blocks=True)
        dag.add_edge("TASK-A", "TASK-B", "Blocks", DependencyClassification.HARD_BLOCK)

        proj = forecaster.forecast_team_schedule(
            team_snapshots=[snap],
            dependency_graph=dag,
            anchor_date=anchor,
        )

        b_types = {b.type for b in proj.bottlenecks}
        assert BottleneckType.OVERLOADED_RESOURCE in b_types
        assert BottleneckType.BLOCKED_TASK in b_types
        assert BottleneckType.LONG_DURATION_TASK in b_types

    def test_scenario_y_no_jira_api_calls(self, forecaster, monkeypatch):
        """Y. Zero external Jira API invocations."""
        def _forbidden_jira_call(*args, **kwargs):
            raise AssertionError("Forbidden Jira API call executed during deterministic forecasting!")

        # Monkeypatch common HTTP/Jira caller points if present
        monkeypatch.setattr("app.connectors.jira.JiraConnector.get_issue", _forbidden_jira_call, raising=False)
        monkeypatch.setattr("app.connectors.jira.JiraConnector.search_issues", _forbidden_jira_call, raising=False)

        anchor = date(2026, 9, 21)
        task = _make_task("TASK-1", expected_effort_hours=6.75)
        snap = _make_snapshot("acc-1", "Alice", [task])

        proj = forecaster.forecast_team_schedule(
            team_snapshots=[snap],
            anchor_date=anchor,
        )
        assert proj.schedule_valid is True

    def test_scenario_z_no_deepseek_calls(self, forecaster, monkeypatch):
        """Z. Zero DeepSeek / AI provider invocations."""
        def _forbidden_deepseek_call(*args, **kwargs):
            raise AssertionError("Forbidden DeepSeek call executed during deterministic forecasting!")

        monkeypatch.setattr("app.services.ai.providers.deepseek.DeepSeekAIProvider.generate_decision", _forbidden_deepseek_call, raising=False)

        anchor = date(2026, 9, 21)
        task = _make_task("TASK-1", expected_effort_hours=6.75)
        snap = _make_snapshot("acc-1", "Alice", [task])

        proj = forecaster.forecast_team_schedule(
            team_snapshots=[snap],
            anchor_date=anchor,
        )
        assert proj.schedule_valid is True

    def test_scenario_aa_no_action_engine_calls(self, forecaster, monkeypatch):
        """AA. Zero Action Engine executions."""
        def _forbidden_action_engine(*args, **kwargs):
            raise AssertionError("Forbidden Action Engine call executed during deterministic forecasting!")

        monkeypatch.setattr("app.core.actions.engine.ActionEngine.execute_action", _forbidden_action_engine, raising=False)

        anchor = date(2026, 9, 21)
        task = _make_task("TASK-1", expected_effort_hours=6.75)
        snap = _make_snapshot("acc-1", "Alice", [task])

        proj = forecaster.forecast_team_schedule(
            team_snapshots=[snap],
            anchor_date=anchor,
        )
        assert proj.schedule_valid is True

    def test_scenario_ab_input_order_independence(self, forecaster):
        """AB. Input-order independence: different resource or task list order yields identical projection."""
        anchor = date(2026, 9, 21)
        t1 = _make_task("TASK-1", expected_effort_hours=6.75, priority="High")
        t2 = _make_task("TASK-2", expected_effort_hours=13.5, priority="Medium")
        t3 = _make_task("TASK-3", expected_effort_hours=6.75, priority="Low")

        snap_a = _make_snapshot("acc-1", "Alice", [t1, t2])
        snap_b = _make_snapshot("acc-2", "Bob", [t3])

        # Run 1: snap_a, snap_b with [t1, t2]
        proj1 = forecaster.forecast_team_schedule(
            team_snapshots=[snap_a, snap_b],
            anchor_date=anchor,
        )

        # Run 2: snap_b, snap_a with reversed [t2, t1]
        snap_a_reversed = _make_snapshot("acc-1", "Alice", [t2, t1])
        proj2 = forecaster.forecast_team_schedule(
            team_snapshots=[snap_b, snap_a_reversed],
            anchor_date=anchor,
        )

        # Confirm identical serializations
        p1_tasks = [(p.issue_key, p.projected_start_date, p.projected_completion_date) for p in proj1.task_projections]
        p2_tasks = [(p.issue_key, p.projected_start_date, p.projected_completion_date) for p in proj2.task_projections]
        assert p1_tasks == p2_tasks

        p1_bottlenecks = [(b.type.value, b.severity, b.affected_issue_key) for b in proj1.bottlenecks]
        p2_bottlenecks = [(b.type.value, b.severity, b.affected_issue_key) for b in proj2.bottlenecks]
        assert p1_bottlenecks == p2_bottlenecks
