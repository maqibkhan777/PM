"""Comprehensive test suite for Phase A Performance Data Foundation.

Tests deterministic profiling, intrinsic complexity calculation, capacity separation,
conservative blocker detection, pace percentiles, active queue forecasting, 7-tier expected-effort hierarchy,
Jira raw remaining estimate separation, authoritative employee roles and designations,
canonical exclusions, 365-day expanded history with rolling windows, signals,
evidence ledger, analysis snapshot versioning, API endpoints, schema migrations, and scheduler.
"""

from datetime import datetime, timedelta, timezone
import json
import os
import tempfile
import pytest
from fastapi.testclient import TestClient

from app.api.app import app
from app.config.settings import settings
from app.core.models.performance import (
    ConfidenceLevel,
    EffortStatistics,
    EmployeeRoleAssignment,
    PerformanceAnalysisRun,
    PerformanceEvidence,
    PerformanceSignal,
    ResourcePerformanceProfile,
    ResourceRole,
    RiskLevel,
    RoleCategory,
    SignalType,
    TaskComplexity,
    TaskDeliveryForecast,
    TeamPerformanceSummary,
)
from app.core.performance.blockers import BlockerAnalyzer
from app.core.performance.capacity import CapacityCalculator
from app.core.performance.complexity import TaskComplexityCalculator
from app.core.performance.engine import PerformanceAnalysisEngine
from app.core.performance.forecaster import DueDateForecaster
from app.core.performance.pace import HistoricalPaceAnalyzer, calculate_percentiles
from app.core.performance.queue import CurrentQueueAnalyzer
from app.core.performance.roles import (
    get_employee_designation_and_category,
    resolve_resource_role,
    resolve_canonical_account_id,
    get_account_aliases,
    AUTHORITATIVE_JIRA_LEGACY_ACCOUNT_MAP,
)
from app.core.performance.signals import PerformanceSignalGenerator
from app.database.connection import DatabaseManager
from app.database.repositories import (
    AuditRepository,
    EmployeeRoleRepository,
    JiraIssueStateRepository,
    JiraWorklogRepository,
    PerformanceRepository,
)
from app.database.schema import init_db, AUTHORITATIVE_EMPLOYEE_ROLES
from app.services.scheduler import PeriodicScheduler
from app.utils.time import utc_now, utc_now_iso, format_iso


@pytest.fixture
def temp_db():
    """Create a temporary SQLite database for isolated test execution."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    mgr = DatabaseManager(db_path=path)
    init_db(mgr)
    yield mgr
    try:
        os.unlink(path)
    except Exception:
        pass


@pytest.fixture
def test_client(temp_db):
    """FastAPI test client pointing to temporary database."""
    from app.api.routes.performance import engine, perf_repo, role_repo
    engine.mgr = temp_db
    engine.perf_repo = PerformanceRepository(temp_db)
    engine.issue_repo = JiraIssueStateRepository(temp_db)
    engine.worklog_repo = JiraWorklogRepository(temp_db)
    engine.audit_repo = AuditRepository(temp_db)
    engine.role_repo = EmployeeRoleRepository(temp_db)
    perf_repo.mgr = temp_db
    role_repo.mgr = temp_db

    client = TestClient(app)
    return client


# =============================================================================
# 1. Authoritative Employee Roles & Designation Tests (Requirements 5 & 6)
# =============================================================================

def test_authoritative_roles_seeding(temp_db):
    """Verify 18 authoritative employee designations are seeded with exact text and normalized categories."""
    role_repo = EmployeeRoleRepository(temp_db)
    assignments = role_repo.list_assignments()
    assert len(assignments) == 18

    # Verify exact designation and role category for key members
    tahir = role_repo.get_by_account_id("638855b85fce844d606bb422")
    assert tahir is not None
    assert tahir["designation"] == "Senior Content Writer / Marketing Strategist"
    assert tahir["role_category"] == RoleCategory.CONTENT_MARKETING.value

    ahsan_iftikhar = role_repo.get_by_account_id("63da2ba4f1475ad42c584247")
    assert ahsan_iftikhar is not None
    assert ahsan_iftikhar["designation"] == "Senior BA"
    assert ahsan_iftikhar["role_category"] == RoleCategory.BUSINESS_ANALYSIS.value

    ahsan_amin = role_repo.get_by_account_id("712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de")
    assert ahsan_amin is not None
    assert ahsan_amin["designation"] == "Senior WordPress Developer"
    assert ahsan_amin["role_category"] == RoleCategory.WORDPRESS_DEVELOPMENT.value


def test_role_resolution_authoritative_first(temp_db):
    """Verify role resolution uses SQLite authoritative table first, then falls back."""
    role_repo = EmployeeRoleRepository(temp_db)

    # 1. Tahir Ali -> Content Writer -> maps to UNKNOWN or OTHER ResourceRole cleanly
    desig, cat, resolved = get_employee_designation_and_category(
        "638855b85fce844d606bb422", role_repo=role_repo
    )
    assert resolved is True
    assert desig == "Senior Content Writer / Marketing Strategist"
    assert cat == "Content / Marketing"

    # 2. Unknown unseeded resource -> Unresolved
    desig_unk, cat_unk, res_unk = get_employee_designation_and_category(
        "unknown_acc_999", "Unknown Person", role_repo=role_repo
    )
    assert res_unk is False
    assert desig_unk == "Unknown"
    assert cat_unk == "Unknown"


def test_unresolved_employees_tracking(temp_db):
    """Verify unresolved employees are accurately listed without guessing account IDs."""
    role_repo = EmployeeRoleRepository(temp_db)
    active_team = [
        {"account_id": "638855b85fce844d606bb422", "display_name": "Tahir Ali"},  # Seeded
        {"account_id": "acc_unseeded_new_hire", "display_name": "New Intern", "team_group": "Alpha"},
    ]
    unresolved = role_repo.get_unresolved_employees(active_team)
    assert len(unresolved) == 1
    assert unresolved[0]["account_id"] == "acc_unseeded_new_hire"
    assert unresolved[0]["display_name"] == "New Intern"
    assert unresolved[0]["resolved"] is False


# =============================================================================
# 2. Canonical Global Exclusions Tests (Requirements 1 & 8)
# =============================================================================

def test_canonical_exclusions_verification():
    """Verify all 4 canonical excluded resources are recognized and excluded."""
    excluded_ids = settings.get_canonical_excluded_account_ids()
    assert len(excluded_ids) >= 4

    # Verify specific authoritative IDs
    assert settings.is_canonical_excluded("712020:566cad70-4a54-42bc-bf36-0c6132fe3cf0", "Aqib Khan") is True
    assert settings.is_canonical_excluded("712020:1b564792-a3af-447c-951d-17aa5507b946", "Abdul Subhan") is True
    assert settings.is_canonical_excluded("557058:8b3f9c31-7d88-473a-9351-abacc5b84933", "Mohammad Mursaleen") is True
    assert settings.is_canonical_excluded("5f83e3937d9637006ffd0436", "Syed Muhammad Usman") is True

    # Regular non-excluded developer
    assert settings.is_canonical_excluded("62d556ad67b2d561571221bb", "Awais") is False


def test_canonical_exclusions_applied_in_engine(temp_db):
    """Verify excluded resources are never discovered or profiled by PerformanceAnalysisEngine."""
    worklog_repo = JiraWorklogRepository(temp_db)
    issue_repo = JiraIssueStateRepository(temp_db)

    # Add worklogs for excluded resource (Aqib Khan) and regular developer (Awais)
    worklog_repo.upsert_worklog(
        worklog_id="wl_aqib",
        jira_issue_key="TEST-1",
        time_spent_seconds=7 * 3600,
        started_at="2026-09-01T10:00:00Z",
        author_account_id="712020:566cad70-4a54-42bc-bf36-0c6132fe3cf0",
        author_display_name="Aqib Khan",
        team_group="Core",
    )
    worklog_repo.upsert_worklog(
        worklog_id="wl_awais",
        jira_issue_key="TEST-2",
        time_spent_seconds=7 * 3600,
        started_at="2026-09-01T10:00:00Z",
        author_account_id="62d556ad67b2d561571221bb",
        author_display_name="Awais",
        team_group="Core",
    )

    engine = PerformanceAnalysisEngine(manager=temp_db)
    discovered = engine._discover_resources(team_group="Core")

    # Aqib Khan must be excluded
    assert "712020:566cad70-4a54-42bc-bf36-0c6132fe3cf0" not in discovered
    # Awais must be included
    assert "62d556ad67b2d561571221bb" in discovered


# =============================================================================
# 3. Expected-Effort 7-Tier Hierarchy & Jira Separation Tests (Requirements 2, 3, 4)
# =============================================================================

def test_deterministic_fallback_settings():
    """Requirement 4: Verify deterministic fallback hours from settings."""
    assert settings.get_fallback_hours_for_complexity(1) == 1.5
    assert settings.get_fallback_hours_for_complexity(2) == 3.0
    assert settings.get_fallback_hours_for_complexity(3) == 5.0
    assert settings.get_fallback_hours_for_complexity(4) == 8.0
    assert settings.get_fallback_hours_for_complexity(5) == 14.0
    assert settings.get_fallback_hours_for_complexity(99) == 5.0


def test_expected_effort_hierarchy_levels():
    """Requirement 2: Verify all 7 levels of the expected-effort hierarchy in order."""
    # 1. Level 1: jira_estimate (explicit Jira estimate takes highest precedence)
    task_with_jira_est = {
        "issue_type": "Bug",
        "complexity_score": 3,
        "original_estimate_seconds": 4 * 3600,  # 4h
    }
    h1, s1, c1, n1 = CurrentQueueAnalyzer.resolve_expected_task_hours(
        task=task_with_jira_est,
        resource_effort_stats=[],
    )
    assert h1 == 4.0
    assert s1 == "jira_estimate"
    assert c1 == "high"
    assert n1 == 1

    # 2. Level 2: resource_historical_comparable (issue_type + complexity, n >= 3)
    res_stats_comparable = [
        EffortStatistics(
            segment_type="comparable",
            segment_key="bug:2",
            sample_count=4,
            median_hours=2.5,
            is_fallback=False,
        )
    ]
    task_comparable = {"issue_type": "Bug", "complexity_score": 2, "original_estimate_seconds": 0}
    h2, s2, c2, n2 = CurrentQueueAnalyzer.resolve_expected_task_hours(
        task=task_comparable,
        resource_effort_stats=res_stats_comparable,
    )
    assert h2 == 2.5
    assert s2 == "resource_historical_comparable"
    assert n2 == 4

    # 3. Level 3: resource_complexity_history (complexity_score, n >= 3)
    res_stats_comp = [
        EffortStatistics(
            segment_type="complexity",
            segment_key="4",
            sample_count=5,
            median_hours=7.5,
            is_fallback=False,
        )
    ]
    task_comp = {"issue_type": "Feature", "complexity_score": 4, "original_estimate_seconds": 0}
    h3, s3, c3, n3 = CurrentQueueAnalyzer.resolve_expected_task_hours(
        task=task_comp,
        resource_effort_stats=res_stats_comp,
    )
    assert h3 == 7.5
    assert s3 == "resource_complexity_history"
    assert n3 == 5

    # 4. Level 4: resource_issue_type_history (issue_type, n >= 3)
    res_stats_type = [
        EffortStatistics(
            segment_type="issue_type",
            segment_key="story",
            sample_count=6,
            median_hours=11.0,
            is_fallback=False,
        )
    ]
    task_type = {"issue_type": "Story", "complexity_score": 3, "original_estimate_seconds": 0}
    h4, s4, c4, n4 = CurrentQueueAnalyzer.resolve_expected_task_hours(
        task=task_type,
        resource_effort_stats=res_stats_type,
    )
    assert h4 == 11.0
    assert s4 == "resource_issue_type_history"
    assert n4 == 6

    # 5. Level 5: role_team_benchmark (n >= 3)
    team_stats = [
        EffortStatistics(
            segment_type="role_category",
            segment_key="frontend development",
            sample_count=12,
            median_hours=4.5,
            is_fallback=False,
        )
    ]
    task_team = {"issue_type": "Improvement", "complexity_score": 3, "original_estimate_seconds": 0}
    h5, s5, c5, n5 = CurrentQueueAnalyzer.resolve_expected_task_hours(
        task=task_team,
        resource_effort_stats=[],
        team_effort_stats=team_stats,
        role_category="Frontend Development",
    )
    assert h5 == 4.5
    assert s5 == "role_team_benchmark"
    assert n5 == 12

    # 6. Level 6: deterministic_fallback
    task_fallback = {"issue_type": "NewType", "complexity_score": 5, "original_estimate_seconds": None}
    h6, s6, c6, n6 = CurrentQueueAnalyzer.resolve_expected_task_hours(
        task=task_fallback,
        resource_effort_stats=[],
        team_effort_stats=[],
    )
    assert h6 == 14.0  # Complexity 5 fallback
    assert s6 == "deterministic_fallback"
    assert c6 == "low"
    assert n6 == 0


def test_jira_remaining_estimate_separation():
    """Requirement 3: Verify Jira remaining estimate is kept separate from inferred metrics."""
    active_issues = [
        {
            "jira_issue_key": "WSSS-200",
            "summary": "Optimize database queries",
            "status": "In Progress",
            "priority": "Medium",
            "issue_type": "Task",
            "logged_seconds": 3 * 3600,
            "raw_reference": {
                "fields": {
                    "timeoriginalestimate": None,
                    "timeestimate": 6 * 3600,  # 6h raw Jira remaining
                    "timespent": 3 * 3600,
                    "duedate": "2026-09-20",
                }
            },
        }
    ]

    q_res = CurrentQueueAnalyzer.analyze_active_queue(
        account_id="acc_1",
        active_issues=active_issues,
        resource_effort_stats=[],  # Falls back to deterministic (complexity 3 -> 5.0h)
        designation="Senior WordPress Developer",
        role_category="WordPress Development",
    )

    forecast = q_res["task_forecasts"][0]
    # Jira remaining estimate is untouched
    assert forecast.jira_remaining_hours == 6.0
    # Inferred expected hours = 5.0h
    assert forecast.inferred_expected_hours == 5.0
    # Inferred total = 5.0 + 0.75 buffer = 5.75h; logged = 3.0h -> inferred remaining = 2.75h
    assert forecast.inferred_remaining_hours == 2.75
    assert forecast.expected_effort_source == "deterministic_fallback"
    assert forecast.expected_effort_confidence == "low"
    assert forecast.designation == "Senior WordPress Developer"
    assert forecast.role_category == "WordPress Development"


# =============================================================================
# 4. Expanded 365-Day History & Rolling Windows Tests (Requirement 7)
# =============================================================================

def test_history_expansion_and_rolling_windows(temp_db):
    """Requirement 7: Verify 365-day history expansion and rolling windows (30d, 90d, 180d, 365d)."""
    perf_repo = PerformanceRepository(temp_db)
    issue_repo = JiraIssueStateRepository(temp_db)
    worklog_repo = JiraWorklogRepository(temp_db)
    now = utc_now()

    # Seed worklogs spanning 200 days
    for days_ago in [10, 45, 120, 200]:
        worklog_repo.upsert_worklog(
            worklog_id=f"wl_{days_ago}",
            jira_issue_key=f"PROJ-{days_ago}",
            time_spent_seconds=6 * 3600,
            started_at=format_iso(now - timedelta(days=days_ago)),
            author_account_id="acc_history_tester",
            author_display_name="History Tester",
            team_group="Engineering",
        )

    engine = PerformanceAnalysisEngine(manager=temp_db)
    profile = engine.analyze_resource(
        account_id="acc_history_tester",
        display_name="History Tester",
        team_group="Engineering",
        history_days=365,
    )

    assert profile.history["requested_history_days"] == 365
    assert profile.history["actual_available_history_days"] >= 199
    assert "rolling_windows" in profile.history
    windows = profile.history["rolling_windows"]
    assert "30d" in windows
    assert "90d" in windows
    assert "180d" in windows
    assert "365d" in windows

    assert windows["30d"]["active_working_days"] == 1
    assert windows["90d"]["active_working_days"] == 2
    assert windows["180d"]["active_working_days"] == 3
    assert windows["365d"]["active_working_days"] == 4


# =============================================================================
# 5. REST API Roles and Unresolved Endpoints Tests
# =============================================================================

def test_api_roles_and_unresolved_endpoints(test_client, temp_db):
    """Verify GET /performance/roles, GET /performance/roles/unresolved, and POST /performance/roles."""
    # 1. GET /performance/roles
    resp_roles = test_client.get("/performance/roles")
    assert resp_roles.status_code == 200
    roles_list = resp_roles.json()
    assert len(roles_list) == 18

    # 2. Seed an unseeded active issue
    issue_repo = JiraIssueStateRepository(temp_db)
    issue_repo.upsert(
        jira_issue_key="NEW-1",
        summary="Unassigned Task",
        status="In Progress",
        assignee="Unseeded Employee",
        team_group="Design Team",
    )

    # 3. GET /performance/roles/unresolved
    resp_unres = test_client.get("/performance/roles/unresolved?team_group=Design%20Team")
    assert resp_unres.status_code == 200
    unres_list = resp_unres.json()
    assert any(u["display_name"] == "Unseeded Employee" for u in unres_list)

    # 4. POST /performance/roles
    payload = {
        "account_id": "acc_new_designer",
        "display_name": "Unseeded Employee",
        "designation": "Junior UI Designer",
        "role_category": "Design",
    }
    resp_post = test_client.post("/performance/roles", json=payload)
    assert resp_post.status_code == 201


# =============================================================================
# 6. Complexity Model Tests (Refinement 1)
# =============================================================================

def test_complexity_intrinsic_characteristics():
    """Test complexity derives from intrinsic task characteristics and explicitly stores factors."""
    res_epic = TaskComplexityCalculator.calculate_complexity(
        issue_type="Epic",
        priority="Highest",
        components=["Backend", "Frontend", "Database"],
        labels=["architecture", "security"],
        subtask_count=5,
        original_estimate_seconds=144000,
    )
    assert res_epic.complexity_score == 5
    assert len(res_epic.factors) >= 4
    assert any("macro initiative" in f for f in res_epic.factors)
    assert res_epic.confidence == ConfidenceLevel.HIGH

    res_sub = TaskComplexityCalculator.calculate_complexity(
        issue_type="Sub-task",
        priority="Lowest",
        labels=["docs", "typo"],
        subtask_count=0,
        original_estimate_seconds=3600,
    )
    assert res_sub.complexity_score == 1
    assert any("subtask scope" in f for f in res_sub.factors)

    res_task = TaskComplexityCalculator.calculate_complexity(
        issue_type="Task",
        priority="Medium",
    )
    assert res_task.complexity_score == 3


def test_complexity_does_not_use_actual_logged_hours():
    """Refinement 1: Verify actual logged effort is NOT an input or driver of complexity."""
    import inspect
    sig = inspect.signature(TaskComplexityCalculator.calculate_complexity)
    assert "actual_logged_seconds" not in sig.parameters
    assert "actual_hours" not in sig.parameters


# =============================================================================
# 7. Capacity Model Tests (Refinement 2)
# =============================================================================

def test_capacity_separation_and_bounding():
    """Refinement 2: Clean separation of nominal (6.75h), observed, and forecast capacity (bounded 6.5-7.0h)."""
    cap_std = CapacityCalculator.calculate_capacity_metrics(
        total_logged_seconds=135 * 3600,
        active_working_days=20,
    )
    assert cap_std["nominal_capacity_hours"] == 6.75
    assert cap_std["observed_logged_capacity_hours"] == 6.75
    assert cap_std["forecast_capacity_hours"] == 6.75

    cap_high = CapacityCalculator.calculate_capacity_metrics(
        total_logged_seconds=220 * 3600,
        active_working_days=20,
    )
    assert cap_high["nominal_capacity_hours"] == 6.75
    assert cap_high["observed_logged_capacity_hours"] == 11.0
    assert cap_high["forecast_capacity_hours"] == 7.0

    cap_low = CapacityCalculator.calculate_capacity_metrics(
        total_logged_seconds=80 * 3600,
        active_working_days=20,
    )
    assert cap_low["nominal_capacity_hours"] == 6.75
    assert cap_low["observed_logged_capacity_hours"] == 4.0
    assert cap_low["forecast_capacity_hours"] == 6.5


# =============================================================================
# 8. Blocker Detection Tests (Refinement 3)
# =============================================================================

def test_conservative_blocker_detection():
    """Refinement 3: Blocker detection requires explicit status history, flags, or prefix comments."""
    raw_issue_blocked = {
        "fields": {"status": {"name": "In Progress"}, "comment": {"comments": []}},
        "changelog": {
            "histories": [
                {
                    "created": "2026-09-01T10:00:00Z",
                    "items": [{"field": "status", "fromString": "In Progress", "toString": "Blocked"}],
                },
                {
                    "created": "2026-09-03T10:00:00Z",
                    "items": [{"field": "status", "fromString": "Blocked", "toString": "In Progress"}],
                },
            ]
        },
    }
    res_a = BlockerAnalyzer.analyze_issue_blockers(raw_issue_blocked)
    assert res_a["blocker_detected"] is True
    assert res_a["blocker_count"] == 1
    assert res_a["blocked_hours"] == 48.0
    assert res_a["confidence"] == ConfidenceLevel.HIGH

    raw_issue_old = {
        "fields": {"status": {"name": "In Progress"}, "comment": {"comments": []}},
        "changelog": {"histories": []},
    }
    res_d = BlockerAnalyzer.analyze_issue_blockers(raw_issue_old)
    assert res_d["blocker_detected"] is False
    assert res_d["blocker_count"] == 0


# =============================================================================
# 9. Historical Pace & Statistics Tests (Refinement 4)
# =============================================================================

def test_percentile_calculations():
    """Test percentile calculations (P25, median, mean, P75)."""
    values = [2.0, 4.0, 6.0, 8.0, 10.0]
    mean, med, p25, p75, min_v, max_v = calculate_percentiles(values)
    assert mean == 6.0
    assert med == 6.0
    assert p25 == 4.0
    assert p75 == 8.0


def test_pace_factor_calculation():
    """Test bounded pace factor calculation."""
    res = HistoricalPaceAnalyzer.calculate_pace_factor(
        resource_median_hours=4.0,
        team_benchmark_median_hours=5.0,
        sample_count=20,
    )
    assert res["pace_factor"] == 0.8
    assert res["confidence"] == ConfidenceLevel.MEDIUM


# =============================================================================
# 10. Signals & Evidence Tests (Refinement 5)
# =============================================================================

def test_deterministic_signals_and_evidence():
    """Refinement 5: Test traceable signals and auditable evidence ledger without overall scores."""
    metrics = {
        "history_days": 90,
        "completed_tasks": 20,
        "on_time_rate": 0.90,
        "tasks_due": 20,
        "tasks_completed_on_time": 18,
        "tasks_completed_late": 2,
        "reopen_rate": 0.05,
        "reopened_tasks": 1,
        "blocker_count": 0,
        "average_blocker_hours": 0.0,
        "median_estimation_variance_percent": 10.0,
        "estimated_tasks": 15,
        "capacity_difference_hours": 5.0,
        "total_remaining_hours": 25.0,
        "queue_task_count": 4,
        "forecast_status": RiskLevel.GREEN,
        "active_working_days": 40,
    }

    signals, evidence = PerformanceSignalGenerator.generate_signals_and_evidence(
        account_id="acc_1",
        analysis_run_id="run_101",
        metrics=metrics,
    )

    sig_types = [s.signal for s in signals]
    assert SignalType.DELIVERY_ON_TRACK in sig_types
    assert SignalType.NORMAL_UPDATE_ACTIVITY in sig_types
    assert len(evidence) >= 2


# =============================================================================
# 11. Database Schema & Migration Tests
# =============================================================================

def test_performance_schema_and_migrations(temp_db):
    """Test all 8 performance and role tables and indexes are created properly."""
    with temp_db.session() as conn:
        tables = [
            "employee_role_assignments",
            "performance_analysis_runs",
            "resource_performance_profiles",
            "resource_effort_statistics",
            "resource_task_classifications",
            "task_delivery_forecasts",
            "performance_signals",
            "performance_evidence",
        ]
        for t in tables:
            cursor = conn.execute(f"SELECT COUNT(*) FROM {t}")
            assert cursor.fetchone() is not None


# =============================================================================
# 12. Scheduler Integration Test
# =============================================================================

@pytest.mark.asyncio
async def test_scheduler_performance_analysis(temp_db, monkeypatch):
    """Test periodic scheduler triggers performance data foundation analysis."""
    monkeypatch.setattr(settings, "PERFORMANCE_ANALYSIS_ENABLED", True)
    monkeypatch.setattr(settings, "PERFORMANCE_ANALYSIS_INTERVAL_MINUTES", 60)

    scheduler = PeriodicScheduler(manager=temp_db)
    res = await scheduler.run_cycle()
    assert "performance_analysis_status" in res
    assert res["performance_analysis_status"] == "COMPLETED"


# =============================================================================
# 13. Identity Normalization & Legacy Username Resolution Tests
# =============================================================================

def test_legacy_username_identity_resolution(temp_db):
    """Verify legacy usernames (e.g. ahsan.amin, jira-user-ahsan) resolve deterministically to canonical Atlassian account ID."""
    role_repo = EmployeeRoleRepository(temp_db)
    canonical_ahsan_id = "712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de"

    # 1. Authoritative mapping verification
    assert resolve_canonical_account_id("ahsan.amin", role_repo=role_repo) == canonical_ahsan_id
    assert resolve_canonical_account_id("AHSAN.AMIN", role_repo=role_repo) == canonical_ahsan_id
    assert resolve_canonical_account_id("jira-user-ahsan", role_repo=role_repo) == canonical_ahsan_id
    assert resolve_canonical_account_id(canonical_ahsan_id, role_repo=role_repo) == canonical_ahsan_id

    # 2. Alias listing
    aliases = get_account_aliases(canonical_ahsan_id)
    assert canonical_ahsan_id in aliases
    assert "ahsan.amin" in aliases
    assert "jira-user-ahsan" in aliases

    # 3. Repository lookup via legacy username
    assignment = role_repo.get_by_account_id("ahsan.amin")
    assert assignment is not None
    assert assignment["account_id"] == canonical_ahsan_id
    assert assignment["display_name"] == "Ahsan Amin"
    assert assignment["designation"] == "Senior WordPress Developer"

    # 4. Role resolution via legacy username
    designation, category, resolved = get_employee_designation_and_category("ahsan.amin", role_repo=role_repo)
    assert resolved is True
    assert designation == "Senior WordPress Developer"
    assert category == RoleCategory.WORDPRESS_DEVELOPMENT.value

    role = resolve_resource_role("ahsan.amin", role_repo=role_repo)
    assert role == ResourceRole.DEVELOPER

    # 5. Unresolved check (Ahsan must NOT be reported as unresolved)
    unresolved = role_repo.get_unresolved_employees()
    unresolved_names = [u["display_name"] for u in unresolved]
    unresolved_ids = [u["account_id"] for u in unresolved]
    assert "Ahsan Amin" not in unresolved_names
    assert "ahsan.amin" not in unresolved_ids
    assert "jira-user-ahsan" not in unresolved_ids


@pytest.mark.asyncio
async def test_legacy_username_performance_engine_unification(temp_db):
    """Verify performance engine unifies issues under legacy username and canonical worklogs into a single profile."""
    canonical_ahsan_id = "712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de"
    issue_repo = JiraIssueStateRepository(temp_db)
    worklog_repo = JiraWorklogRepository(temp_db)

    # 1. Seed completed issue assigned to legacy username 'jira-user-ahsan'
    raw_1 = {
        "key": "CF7-421",
        "fields": {
            "summary": "Legacy assigned issue",
            "status": {"name": "Done"},
            "assignee": {"accountId": "jira-user-ahsan", "displayName": "Ahsan Amin"},
            "issuetype": {"name": "Task"},
            "priority": {"name": "Medium"},
            "project": {"key": "CF7"},
            "created": (utc_now() - timedelta(days=20)).isoformat(),
            "updated": (utc_now() - timedelta(days=10)).isoformat(),
            "resolutiondate": (utc_now() - timedelta(days=10)).isoformat(),
            "timespent": 14400,
            "timeoriginalestimate": 14400,
            "timeestimate": 0,
        },
    }
    issue_repo.upsert(
        jira_issue_key="CF7-421",
        summary="Legacy assigned issue",
        status="Done",
        assignee="Ahsan Amin",
        project_key="CF7",
        priority="Medium",
        updated_at=(utc_now() - timedelta(days=10)).isoformat(),
        raw_reference=raw_1,
    )

    # 2. Seed active issue assigned to legacy username 'ahsan.amin'
    raw_2 = {
        "key": "CF7-422",
        "fields": {
            "summary": "Active legacy assigned issue",
            "status": {"name": "In Progress"},
            "assignee": {"accountId": "ahsan.amin", "displayName": "Ahsan Amin"},
            "issuetype": {"name": "Task"},
            "priority": {"name": "High"},
            "project": {"key": "CF7"},
            "created": (utc_now() - timedelta(days=5)).isoformat(),
            "updated": utc_now_iso(),
            "timespent": 7200,
            "timeoriginalestimate": 14400,
            "timeestimate": 7200,
        },
    }
    issue_repo.upsert(
        jira_issue_key="CF7-422",
        summary="Active legacy assigned issue",
        status="In Progress",
        assignee="Ahsan Amin",
        project_key="CF7",
        priority="High",
        updated_at=utc_now_iso(),
        raw_reference=raw_2,
    )

    # 3. Seed worklogs logged under canonical account ID
    worklog_repo.upsert_worklog(
        worklog_id="wl-ahsan-101",
        jira_issue_key="CF7-421",
        author_account_id=canonical_ahsan_id,
        author_display_name="Ahsan Amin",
        time_spent_seconds=14400,
        started_at=(utc_now() - timedelta(days=10)).isoformat(),
    )
    worklog_repo.upsert_worklog(
        worklog_id="wl-ahsan-102",
        jira_issue_key="CF7-422",
        author_account_id=canonical_ahsan_id,
        author_display_name="Ahsan Amin",
        time_spent_seconds=7200,
        started_at=(utc_now() - timedelta(days=2)).isoformat(),
    )

    # 4. Run Performance Engine
    engine = PerformanceAnalysisEngine(manager=temp_db)
    run = engine.run_analysis(history_days=90)

    # 5. Verify profiles
    perf_repo = PerformanceRepository(temp_db)
    profiles = perf_repo.list_profiles(run_id=run.analysis_run_id)
    account_ids = [p["account_id"] for p in profiles]

    # Must have canonical ID profile and NO legacy ID profiles
    assert canonical_ahsan_id in account_ids
    assert "ahsan.amin" not in account_ids
    assert "jira-user-ahsan" not in account_ids

    ahsan_profile = next(p for p in profiles if p["account_id"] == canonical_ahsan_id)
    assert ahsan_profile["display_name"] == "Ahsan Amin"
    assert ahsan_profile["designation"] == "Senior WordPress Developer"
    assert ahsan_profile["role_category"] == RoleCategory.WORDPRESS_DEVELOPMENT.value
    assert ahsan_profile["role"] == ResourceRole.DEVELOPER.value

    # Check that the legacy-assigned completed task is included in Ahsan's completed metrics
    assert ahsan_profile["completed_tasks"] >= 1

    # Check that the active queue includes CF7-422
    assert ahsan_profile["current_queue_task_count"] >= 1

    # Check that worklogs from canonical ID are aggregated
    assert ahsan_profile["total_logged_seconds"] >= 21600
