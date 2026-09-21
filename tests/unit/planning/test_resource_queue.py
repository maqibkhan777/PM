"""Deterministic unit tests for Phase 3C: Resource Queue & Capacity Intelligence Composition.

Covers all required scenarios A through X:
A. Resource with healthy queue
B. Resource with overdue tasks
C. Resource with stale tasks
D. Resource with blocked tasks
E. Resource with reopened tasks
F. Resource with no active tasks
G. Resource with no historical data
H. Resource with limited historical data
I. Resource with sufficient history
J. Capacity known
K. Capacity partially known
L. Capacity unavailable
M. Explicit task estimate
N. Historical effort fallback
O. Insufficient effort evidence
P. Workload pressure composition
Q. Dependency summary
R. Artifact handoff summary
S. Multiple resources
T. Stable resource identity
U. Snapshot determinism
V. No Jira API calls
W. No DeepSeek calls
X. No Action Engine calls
"""

from datetime import datetime, timezone, timedelta
import json
import pytest
from typing import Any, Dict, List

from app.core.models.planning import (
    CapacityState,
    DependencyClassification,
    ResourceQueueSnapshot,
    TeamWorkloadSnapshot,
)
from app.core.planning.artifacts import ArtifactEngine
from app.core.planning.queue_composer import ResourceQueueComposer
from app.database.connection import DatabaseManager
from app.database.repositories import (
    ArtifactRepository,
    EmployeeRoleRepository,
    JiraIssueLinkRepository,
    JiraIssueStateRepository,
    JiraWorklogRepository,
    PerformanceRepository,
)
from app.database.schema import init_db
from app.utils.time import format_iso, utc_now_iso


@pytest.fixture
def test_db_manager(tmp_path):
    """Provide isolated in-memory or temp-file database for Phase 3C tests."""
    db_file = str(tmp_path / "test_pm_phase3c.db")
    mgr = DatabaseManager(db_path=db_file)
    init_db(mgr)
    return mgr


@pytest.fixture
def composer(test_db_manager):
    """Instantiate ResourceQueueComposer connected to the isolated test database."""
    return ResourceQueueComposer(manager=test_db_manager)


def _seed_completed_tasks(mgr: DatabaseManager, account_id: str, count: int, hours_each: float = 4.0):
    """Helper to seed historical completed tasks and worklogs."""
    issue_repo = JiraIssueStateRepository(mgr)
    worklog_repo = JiraWorklogRepository(mgr)
    now = datetime(2026, 9, 21, 10, 0, 0, tzinfo=timezone.utc)

    for i in range(count):
        t_key = f"HIST-{i+1}"
        d_str = format_iso(now - timedelta(days=i + 1))
        spent_sec = int(hours_each * 3600)

        issue_repo.upsert(
            jira_issue_key=t_key,
            summary=f"Completed Task {i+1}",
            status="Done",
            assignee=account_id,
            priority="Medium",
            issue_type="Task",
            original_estimate_seconds=spent_sec,
            time_spent_seconds=spent_sec,
            updated_at=d_str,
            last_activity_at=d_str,
        )
        worklog_repo.upsert_worklog(
            worklog_id=f"w-{account_id}-{i+1}",
            jira_issue_key=t_key,
            time_spent_seconds=spent_sec,
            started_at=d_str,
            author_account_id=account_id,
        )


class TestResourceQueueComposition:
    """Deterministic offline unit test suite for Phase 3C scenarios."""

    def test_resource_with_healthy_queue(self, test_db_manager, composer):
        """A. Resource with healthy queue within capacity."""
        issue_repo = JiraIssueStateRepository(test_db_manager)
        now = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)

        # Seed 20 historical tasks to establish history
        _seed_completed_tasks(test_db_manager, "acc-alice", 20, 4.0)

        # Active tasks with total 16 hours (< 67.5h capacity)
        future_due = format_iso(now + timedelta(days=5))[:10]
        issue_repo.upsert(
            jira_issue_key="HEALTHY-1",
            summary="Feature A",
            status="In Progress",
            assignee="acc-alice",
            priority="Medium",
            due_date=future_due,
            original_estimate_seconds=8 * 3600,
            updated_at=format_iso(now),
            last_activity_at=format_iso(now),
        )
        issue_repo.upsert(
            jira_issue_key="HEALTHY-2",
            summary="Feature B",
            status="To Do",
            assignee="acc-alice",
            priority="Low",
            due_date=future_due,
            original_estimate_seconds=8 * 3600,
            updated_at=format_iso(now),
            last_activity_at=format_iso(now),
        )

        snap = composer.compose_snapshot("acc-alice", display_name="Alice", horizon_working_days=10, now=now)

        assert snap.resource_id == "acc-alice"
        assert snap.active_task_count == 2
        assert snap.overdue_task_count == 0
        assert snap.stale_task_count == 0
        assert snap.blocked_task_count == 0
        assert snap.capacity.capacity_state == CapacityState.UNDER_UTILIZED
        assert snap.workload_pressure_level in ("LOW", "NORMAL")
        assert snap.history_completeness == "LIMITED_HISTORY" or snap.history_completeness == "SUFFICIENT_HISTORY"

    def test_resource_with_overdue_tasks(self, test_db_manager, composer):
        """B. Resource with overdue tasks."""
        issue_repo = JiraIssueStateRepository(test_db_manager)
        now = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)

        past_due = "2026-09-15"
        issue_repo.upsert(
            jira_issue_key="OVERDUE-1",
            summary="Critical Bug",
            status="In Progress",
            assignee="acc-bob",
            due_date=past_due,
            updated_at=format_iso(now),
            last_activity_at=format_iso(now),
        )

        snap = composer.compose_snapshot("acc-bob", display_name="Bob", now=now)

        assert snap.active_task_count == 1
        assert snap.overdue_task_count == 1
        assert snap.active_tasks[0].is_overdue is True
        assert any("passed their stated due date" in n.lower() or "overdue" in n.lower() for n in snap.data_quality_notes)

    def test_resource_with_stale_tasks(self, test_db_manager, composer):
        """C. Resource with stale tasks (>48 hours inactivity)."""
        issue_repo = JiraIssueStateRepository(test_db_manager)
        now = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)
        stale_time = format_iso(now - timedelta(hours=72))

        issue_repo.upsert(
            jira_issue_key="STALE-1",
            summary="Abandoned Review",
            status="In Progress",
            assignee="acc-carol",
            updated_at=stale_time,
            last_activity_at=stale_time,
        )

        snap = composer.compose_snapshot("acc-carol", display_name="Carol", now=now)

        assert snap.active_task_count == 1
        assert snap.stale_task_count == 1
        assert snap.active_tasks[0].is_stale is True

    def test_resource_with_blocked_tasks(self, test_db_manager, composer):
        """D. Resource with blocked tasks (via Phase 3A HARD_BLOCK link)."""
        issue_repo = JiraIssueStateRepository(test_db_manager)
        link_repo = JiraIssueLinkRepository(test_db_manager)
        now = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)

        issue_repo.upsert(
            jira_issue_key="BLOCKED-1",
            summary="Blocked Task",
            status="In Progress",
            assignee="acc-dave",
            updated_at=format_iso(now),
            last_activity_at=format_iso(now),
        )

        link_repo.upsert_link(
            source_issue_key="BLOCKER-99",
            target_issue_key="BLOCKED-1",
            link_type_name="Blocks",
            classification=DependencyClassification.HARD_BLOCK.value,
        )

        snap = composer.compose_snapshot("acc-dave", display_name="Dave", now=now)

        assert snap.blocked_task_count == 1
        assert snap.active_tasks[0].is_blocked is True
        assert "BLOCKER-99" in snap.active_tasks[0].hard_blocker_keys
        assert snap.dependency_context.hard_blocker_count == 1

    def test_resource_with_reopened_tasks(self, test_db_manager, composer):
        """E. Resource with reopened tasks."""
        issue_repo = JiraIssueStateRepository(test_db_manager)
        now = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)

        issue_repo.upsert(
            jira_issue_key="REOPEN-1",
            summary="Reopened Defect",
            status="Reopened",
            assignee="acc-eve",
            updated_at=format_iso(now),
            last_activity_at=format_iso(now),
        )

        snap = composer.compose_snapshot("acc-eve", display_name="Eve", now=now)

        assert snap.reopened_task_count == 1
        assert snap.active_tasks[0].is_reopened is True

    def test_resource_with_no_active_tasks(self, test_db_manager, composer):
        """F. Resource with no active tasks."""
        now = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)
        snap = composer.compose_snapshot("acc-frank", display_name="Frank", now=now)

        assert snap.active_task_count == 0
        assert snap.active_tasks == []
        assert snap.queue_completeness == "QUEUE_EMPTY"
        assert snap.capacity.committed_workload_hours == 0.0
        assert snap.capacity.capacity_state == CapacityState.UNDER_UTILIZED

    def test_resource_with_no_historical_data(self, test_db_manager, composer):
        """G. Resource with no historical data."""
        now = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)
        snap = composer.compose_snapshot("acc-newbie", display_name="Newbie", now=now)

        assert snap.historical_completed_tasks == 0
        assert snap.historical_active_working_days == 0
        assert snap.history_completeness == "NO_HISTORY"
        assert snap.historical_pace.confidence == "INSUFFICIENT"
        assert snap.historical_pace.is_fallback is True

    def test_resource_with_limited_historical_data(self, test_db_manager, composer):
        """H. Resource with limited historical data (<15 tasks)."""
        _seed_completed_tasks(test_db_manager, "acc-grace", 4, 3.0)
        now = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)

        snap = composer.compose_snapshot("acc-grace", display_name="Grace", now=now)

        assert snap.historical_completed_tasks == 4
        assert snap.history_completeness == "LIMITED_HISTORY"

    def test_resource_with_sufficient_history(self, test_db_manager, composer):
        """I. Resource with sufficient history (>=15 tasks, >=30 active days)."""
        worklog_repo = JiraWorklogRepository(test_db_manager)
        issue_repo = JiraIssueStateRepository(test_db_manager)
        now = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)

        # Seed 35 tasks across 35 distinct days
        for i in range(35):
            d_str = format_iso(now - timedelta(days=i + 1))
            t_key = f"SUFF-{i+1}"
            issue_repo.upsert(
                jira_issue_key=t_key,
                summary=f"Task {i+1}",
                status="Done",
                assignee="acc-heidi",
                time_spent_seconds=4 * 3600,
                updated_at=d_str,
            )
            worklog_repo.upsert_worklog(
                worklog_id=f"w-heidi-{i+1}",
                jira_issue_key=t_key,
                time_spent_seconds=4 * 3600,
                started_at=d_str,
                author_account_id="acc-heidi",
            )

        snap = composer.compose_snapshot("acc-heidi", display_name="Heidi", now=now)

        assert snap.historical_completed_tasks == 35
        assert snap.historical_active_working_days >= 30
        assert snap.history_completeness == "SUFFICIENT_HISTORY"

    def test_capacity_known(self, test_db_manager, composer):
        """J. Capacity known (>=15 active working days observed)."""
        worklog_repo = JiraWorklogRepository(test_db_manager)
        now = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)

        for i in range(16):
            d_str = format_iso(now - timedelta(days=i + 1))
            worklog_repo.upsert_worklog(
                worklog_id=f"w-ivan-{i+1}",
                jira_issue_key=f"IVAN-{i+1}",
                time_spent_seconds=int(6.75 * 3600),
                started_at=d_str,
                author_account_id="acc-ivan",
            )

        snap = composer.compose_snapshot("acc-ivan", display_name="Ivan", now=now)

        assert snap.capacity_quality == "CAPACITY_KNOWN"
        assert snap.capacity.capacity_method == "calibrated_bounded_worklog"

    def test_capacity_partially_known(self, test_db_manager, composer):
        """K. Capacity partially known (5 to 14 active working days)."""
        worklog_repo = JiraWorklogRepository(test_db_manager)
        now = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)

        for i in range(8):
            d_str = format_iso(now - timedelta(days=i + 1))
            worklog_repo.upsert_worklog(
                worklog_id=f"w-judy-{i+1}",
                jira_issue_key=f"JUDY-{i+1}",
                time_spent_seconds=6 * 3600,
                started_at=d_str,
                author_account_id="acc-judy",
            )

        snap = composer.compose_snapshot("acc-judy", display_name="Judy", now=now)

        assert snap.capacity_quality == "CAPACITY_PARTIAL"

    def test_capacity_unavailable(self, test_db_manager, composer):
        """L. Capacity unavailable (<5 active days)."""
        now = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)
        snap = composer.compose_snapshot("acc-kevin", display_name="Kevin", now=now)

        assert snap.capacity_quality == "CAPACITY_UNAVAILABLE"
        assert snap.capacity.nominal_daily_capacity_hours == 6.75

    def test_explicit_task_estimate(self, test_db_manager, composer):
        """M. Explicit task estimate is preserved without mutation."""
        issue_repo = JiraIssueStateRepository(test_db_manager)
        now = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)

        issue_repo.upsert(
            jira_issue_key="EST-1",
            summary="Estimated Task",
            status="In Progress",
            assignee="acc-leo",
            original_estimate_seconds=12 * 3600,
            updated_at=format_iso(now),
            last_activity_at=format_iso(now),
        )

        snap = composer.compose_snapshot("acc-leo", display_name="Leo", now=now)

        task = snap.active_tasks[0]
        assert task.original_estimate_hours == 12.0
        assert task.expected_effort_source == "jira_estimate"
        assert task.expected_effort_confidence == "high"

    def test_historical_effort_fallback(self, test_db_manager, composer):
        """N. Unestimated task falls back to deterministic complexity ladder."""
        issue_repo = JiraIssueStateRepository(test_db_manager)
        now = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)

        # Unestimated task with medium priority -> complexity score 3 -> fallback 5.0h in settings
        issue_repo.upsert(
            jira_issue_key="UNEST-1",
            summary="Bug fix without estimate",
            status="In Progress",
            assignee="acc-mike",
            priority="Medium",
            updated_at=format_iso(now),
            last_activity_at=format_iso(now),
        )

        snap = composer.compose_snapshot("acc-mike", display_name="Mike", now=now)

        task = snap.active_tasks[0]
        assert task.original_estimate_hours is None
        assert task.expected_effort_hours > 0.0
        assert task.expected_effort_source == "deterministic_fallback"

    def test_insufficient_effort_evidence(self, test_db_manager, composer):
        """O. Insufficient effort evidence marks queue partial."""
        issue_repo = JiraIssueStateRepository(test_db_manager)
        now = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)

        issue_repo.upsert(
            jira_issue_key="NO-EST-1",
            summary="Unestimated item",
            status="To Do",
            assignee="acc-nancy",
            updated_at=format_iso(now),
            last_activity_at=format_iso(now),
        )

        snap = composer.compose_snapshot("acc-nancy", display_name="Nancy", now=now)

        assert snap.unestimated_task_count == 1
        assert snap.queue_completeness == "QUEUE_PARTIAL"
        assert any("lack explicit Jira estimates" in n for n in snap.data_quality_notes)

    def test_workload_pressure_composition(self, test_db_manager, composer):
        """P. Workload pressure composition correctly flags elevated/high load."""
        issue_repo = JiraIssueStateRepository(test_db_manager)
        now = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)

        # Seed 12 tasks of 8 hours = 96h workload (> 67.5h capacity)
        for i in range(12):
            issue_repo.upsert(
                jira_issue_key=f"OVERLOAD-{i+1}",
                summary=f"Big Feature {i+1}",
                status="In Progress",
                assignee="acc-oscar",
                original_estimate_seconds=8 * 3600,
                updated_at=format_iso(now),
                last_activity_at=format_iso(now),
            )

        snap = composer.compose_snapshot("acc-oscar", display_name="Oscar", horizon_working_days=10, now=now)

        assert snap.capacity.capacity_state in (CapacityState.OVERLOADED, CapacityState.SATURATED)
        assert snap.workload_pressure_level in ("ELEVATED", "HIGH")
        assert len(snap.workload_pressure_explanation) > 0

    def test_dependency_summary(self, test_db_manager, composer):
        """Q. Dependency summary references hard blockers and downstream consumers."""
        issue_repo = JiraIssueStateRepository(test_db_manager)
        link_repo = JiraIssueLinkRepository(test_db_manager)
        now = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)

        issue_repo.upsert(
            jira_issue_key="DEP-MID",
            summary="Middle Task",
            status="In Progress",
            assignee="acc-pat",
            updated_at=format_iso(now),
            last_activity_at=format_iso(now),
        )

        link_repo.upsert_link(
            source_issue_key="DEP-UPSTREAM",
            target_issue_key="DEP-MID",
            link_type_name="Blocks",
            classification=DependencyClassification.HARD_BLOCK.value,
        )
        link_repo.upsert_link(
            source_issue_key="DEP-MID",
            target_issue_key="DEP-DOWNSTREAM",
            link_type_name="Blocks",
            classification=DependencyClassification.HARD_BLOCK.value,
        )

        snap = composer.compose_snapshot("acc-pat", display_name="Pat", now=now)

        dep_ctx = snap.dependency_context
        assert dep_ctx.hard_blocker_count == 1
        assert "DEP-MID" in dep_ctx.blocked_issue_keys
        assert "DEP-DOWNSTREAM" in dep_ctx.downstream_dependent_keys

    def test_artifact_handoff_summary(self, test_db_manager, composer):
        """R. Artifact handoff summary references produced and consumed deliverables."""
        issue_repo = JiraIssueStateRepository(test_db_manager)
        art_engine = ArtifactEngine(test_db_manager)
        now = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)

        issue_repo.upsert(
            jira_issue_key="ART-101",
            summary="Backend API",
            status="In Progress",
            assignee="acc-quinn",
            labels=["produces:api-spec", "consumes:figma-mockup"],
            updated_at=format_iso(now),
            last_activity_at=format_iso(now),
        )

        art_engine.extract_artifacts_from_labels(
            issue_key="ART-101",
            project_key="ART",
            labels=["produces:api-spec", "consumes:figma-mockup"],
        )

        snap = composer.compose_snapshot("acc-quinn", display_name="Quinn", now=now)

        art_ctx = snap.artifact_context
        assert art_ctx.total_artifacts == 2
        assert any("api-spec" in p for p in art_ctx.produced_artifact_ids)
        assert any("figma-mockup" in c for c in art_ctx.consumed_artifact_ids)
        assert "api-spec" in snap.active_tasks[0].produced_artifact_names
        assert "figma-mockup" in snap.active_tasks[0].consumed_artifact_names

    def test_multiple_resources_composition(self, test_db_manager, composer):
        """S. Team composition produces multi-resource snapshot without ranking."""
        issue_repo = JiraIssueStateRepository(test_db_manager)
        role_repo = EmployeeRoleRepository(test_db_manager)
        now = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)

        role_repo.upsert_assignment(
            account_id="acc-team-1",
            display_name="Dev One",
            designation="Software Engineer",
            role_category="WordPress Development",
        )
        role_repo.upsert_assignment(
            account_id="acc-team-2",
            display_name="Dev Two",
            designation="Frontend Developer",
            role_category="Frontend Development",
        )

        issue_repo.upsert(
            jira_issue_key="TEAM-1",
            summary="Work item 1",
            status="In Progress",
            assignee="acc-team-1",
            original_estimate_seconds=4 * 3600,
            updated_at=format_iso(now),
            last_activity_at=format_iso(now),
        )
        issue_repo.upsert(
            jira_issue_key="TEAM-2",
            summary="Work item 2",
            status="In Progress",
            assignee="acc-team-2",
            original_estimate_seconds=6 * 3600,
            updated_at=format_iso(now),
            last_activity_at=format_iso(now),
        )

        team_snap = composer.compose_team_snapshots(
            account_ids=["acc-team-1", "acc-team-2"],
            horizon_working_days=10,
            now=now,
        )

        assert isinstance(team_snap, TeamWorkloadSnapshot)
        assert team_snap.resources_count == 2
        assert team_snap.total_active_tasks == 2
        assert len(team_snap.resource_snapshots) == 2
        # Deterministic sorting on account_id
        assert team_snap.resource_snapshots[0].resource_id == "acc-team-1"
        assert team_snap.resource_snapshots[1].resource_id == "acc-team-2"

    def test_stable_resource_identity(self, test_db_manager, composer):
        """T. Resource identity remains stable via canonical alias resolution."""
        role_repo = EmployeeRoleRepository(test_db_manager)
        now = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)

        # "ahsan.amin" maps to "712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de" in roles.py
        snap = composer.compose_snapshot("ahsan.amin", now=now)
        assert snap.resource_id == "712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de"

    def test_snapshot_determinism(self, test_db_manager, composer):
        """U. Snapshot produces identical output when executed repeatedly."""
        issue_repo = JiraIssueStateRepository(test_db_manager)
        now = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)

        issue_repo.upsert(
            jira_issue_key="DET-1",
            summary="Deterministic Task",
            status="In Progress",
            assignee="acc-det",
            original_estimate_seconds=5 * 3600,
            updated_at=format_iso(now),
            last_activity_at=format_iso(now),
        )

        snap1 = composer.compose_snapshot("acc-det", now=now)
        snap2 = composer.compose_snapshot("acc-det", now=now)

        assert snap1.model_dump() == snap2.model_dump()

    def test_no_jira_api_calls(self, monkeypatch, test_db_manager, composer):
        """V. Zero Jira API requests made during snapshot composition."""
        def fail_jira(*args, **kwargs):
            raise AssertionError("Jira API must not be called during Phase 3C composition!")

        monkeypatch.setattr("app.connectors.jira.client.JiraClient.get_issue", fail_jira, raising=False)
        monkeypatch.setattr("app.connectors.jira.client.JiraClient.search_issues", fail_jira, raising=False)

        now = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)
        snap = composer.compose_snapshot("acc-offline", now=now)
        assert snap is not None

    def test_no_deepseek_calls(self, monkeypatch, test_db_manager, composer):
        """W. Zero DeepSeek calls made during snapshot composition."""
        def fail_deepseek(*args, **kwargs):
            raise AssertionError("DeepSeek must not be called during Phase 3C!")

        monkeypatch.setattr("app.services.ai.providers.deepseek.DeepSeekAIProvider.complete", fail_deepseek, raising=False)
        monkeypatch.setattr("app.services.ai.providers.deepseek.DeepSeekAIProvider.analyze", fail_deepseek, raising=False)

        now = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)
        snap = composer.compose_snapshot("acc-no-ai", now=now)
        assert snap is not None

    def test_no_action_engine_calls(self, monkeypatch, test_db_manager, composer):
        """X. Zero Action Engine calls made during snapshot composition."""
        def fail_action(*args, **kwargs):
            raise AssertionError("Action Engine must not be invoked during Phase 3C!")

        monkeypatch.setattr("app.core.actions.engine.ActionEngine.execute", fail_action, raising=False)

        now = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)
        snap = composer.compose_snapshot("acc-no-action", now=now)
        assert snap is not None

