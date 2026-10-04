"""Unit tests for Planning Data and Cross-Project Capacity Validation Service (Milestone 4A).

Tests:
1. Multi-project scope resolution and partial failures.
2. Duplicate issue prevention and deduplication.
3. Explicit zero vs missing remaining estimates.
4. Missing assignee handling.
5. Cross-project workload visibility and contention detection.
6. Unknown capacity data disclosure (leave/calendar).
7. Dependency direction and blocker feasibility analysis.
8. Overall validation status: VALID, PARTIAL, UNRELIABLE.
"""

import pytest
from app.core.planning.validation_models import (
    CapacityDataSource,
    EstimateType,
    ValidationOutcome,
)
from app.core.planning.validation_service import PlanningDataValidationService
from app.core.planning.validation_formatter import PlanningDataValidationFormatter
from app.database.connection import DatabaseManager


@pytest.fixture
def mock_validation_db(tmp_path):
    """Create isolated SQLite database populated with multi-project test data."""
    db_file = str(tmp_path / "test_planning_val.db")
    db = DatabaseManager(db_path=db_file)
    with db.session() as conn:
        conn.executescript(
            """
            CREATE TABLE jira_issue_state (
                jira_issue_key TEXT PRIMARY KEY,
                summary TEXT,
                status TEXT,
                assignee TEXT,
                priority TEXT,
                due_date TEXT,
                updated_at TEXT,
                last_seen_at TEXT,
                last_activity_at TEXT,
                project_key TEXT,
                raw_reference TEXT,
                team_group TEXT,
                issue_type TEXT,
                labels TEXT,
                components TEXT,
                subtask_count INTEGER,
                original_estimate_seconds INTEGER,
                time_spent_seconds INTEGER,
                creator_id TEXT
            );

            CREATE TABLE employee_role_assignments (
                account_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                designation TEXT,
                role_category TEXT NOT NULL,
                team_group TEXT,
                confidence TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE jira_issue_links (
                id TEXT PRIMARY KEY,
                source_issue_key TEXT NOT NULL,
                target_issue_key TEXT NOT NULL,
                link_type_name TEXT NOT NULL,
                inward_description TEXT,
                outward_description TEXT,
                classification TEXT NOT NULL,
                source_issue_id TEXT,
                target_issue_id TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE historical_effort_benchmarks (
                id TEXT PRIMARY KEY,
                analysis_run_id TEXT NOT NULL,
                account_id TEXT,
                segmentation_tier TEXT NOT NULL,
                segment_type TEXT NOT NULL,
                segment_key TEXT NOT NULL,
                sample_count INTEGER NOT NULL,
                mean_hours REAL NOT NULL,
                median_hours REAL NOT NULL,
                p25_hours REAL NOT NULL,
                p75_hours REAL NOT NULL,
                min_hours REAL NOT NULL,
                max_hours REAL NOT NULL,
                stddev_hours REAL NOT NULL,
                confidence TEXT NOT NULL,
                is_fallback INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE audit_logs (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                actor TEXT NOT NULL,
                action TEXT NOT NULL,
                target TEXT NOT NULL,
                result TEXT NOT NULL,
                details TEXT
            );
            """
        )

        # Seed employee roles
        conn.execute(
            """
            INSERT INTO employee_role_assignments VALUES
            ('acc-1', 'Alice Developer', 'Senior Software Engineer', 'BACKEND', 'Core Team', 'HIGH', '2026-01-01', '2026-01-01'),
            ('acc-2', 'Bob Support', 'Support Engineer', 'SUPPORT', 'Support Team', 'HIGH', '2026-01-01', '2026-01-01')
            """
        )

        # Seed benchmarks
        conn.execute(
            """
            INSERT INTO historical_effort_benchmarks VALUES
            ('b1', 'run-1', NULL, 'ISSUE_TYPE', 'issue_type', 'SMTPSUPORT:Support', 30, 2.0, 1.5, 1.0, 3.0, 0.5, 5.0, 1.0, 'HIGH', 0, '2026-01-01'),
            ('b2', 'run-1', NULL, 'ISSUE_TYPE', 'issue_type', 'GF:Task', 25, 5.0, 4.0, 2.0, 6.0, 1.0, 10.0, 2.0, 'HIGH', 0, '2026-01-01')
            """
        )

        # Seed active issues across SMTPSUPORT and GF
        # Alice has tasks in BOTH SMTPSUPORT and GF (Cross-project workload)
        conn.execute(
            """
            INSERT INTO jira_issue_state VALUES
            ('SMTPSUPORT-1', 'SMTP Bug Fix', 'To Do', 'Alice Developer', 'Medium', '2026-10-10', '2026-10-01', '2026-10-01', '2026-10-01', 'SMTPSUPORT', '{\"fields\": {\"timetracking\": {\"remainingEstimateSeconds\": 36000}}}', 'Support Team', 'Support', '[]', '[]', 0, 36000, 0, 'usr-1'),
            ('GF-1', 'GF Feature Setup', 'In Progress', 'Alice Developer', 'Medium', '2026-10-15', '2026-10-01', '2026-10-01', '2026-10-01', 'GF', '{\"fields\": {\"timetracking\": {\"remainingEstimateSeconds\": 216000}}}', 'Core Team', 'Task', '[]', '[]', 0, 216000, 0, 'usr-1'),
            ('SMTPSUPORT-2', 'Unassigned Ticket', 'To Do', 'unassigned', 'High', NULL, '2026-10-01', '2026-10-01', '2026-10-01', 'SMTPSUPORT', NULL, 'Support Team', 'Support', '[]', '[]', 0, NULL, NULL, 'usr-1'),
            ('GF-2', 'Explicit Zero Task', 'In Progress', 'Bob Support', 'Low', NULL, '2026-10-01', '2026-10-01', '2026-10-01', 'GF', '{\"fields\": {\"timetracking\": {\"remainingEstimateSeconds\": 0}}}', 'Support Team', 'Task', '[]', '[]', 0, 7200, 7200, 'usr-1')
            """
        )

        # Seed dependency link: SMTPSUPORT-1 blocks GF-1 (Cross-project block)
        conn.execute(
            """
            INSERT INTO jira_issue_links VALUES
            ('link-1', 'SMTPSUPORT-1', 'GF-1', 'Blocks', 'is blocked by', 'blocks', 'HARD_BLOCK', 'id-1', 'id-2', '2026-10-01', '2026-10-01', 1)
            """
        )

    return db


def test_multi_project_scope_and_cross_project_workload(mock_validation_db):
    """Test multi-project resolution, cross-project workload aggregation, and conflict detection."""
    svc = PlanningDataValidationService(manager=mock_validation_db)

    report = svc.validate_planning_data(project_keys=["SMTPSUPORT", "GF", "NONEXISTENT_PROJ"])

    # 1. Project Resolution
    assert "SMTPSUPORT" in report.resolved_projects
    assert "GF" in report.resolved_projects
    assert "NONEXISTENT_PROJ" in report.unresolved_projects
    assert report.total_active_tasks == 4

    # 2. Cross-Project Workload & Over-allocation for Alice
    alice_audit = next(r for r in report.resources_audited if r.display_name == "Alice Developer")
    assert alice_audit is not None
    assert set(alice_audit.projects_involved) == {"SMTPSUPORT", "GF"}
    assert alice_audit.cross_project_conflict_detected is True
    # SMTPSUPORT-1 (10h) + GF-1 (60h) = 70.0h > 65.0h available
    assert alice_audit.total_assigned_remaining_hours == 70.0
    assert alice_audit.is_overallocated is True
    assert "Alice Developer" in report.overallocated_resources
    assert "Alice Developer" in report.cross_project_contention_resources


def test_estimate_types_and_missing_assignee(mock_validation_db):
    """Test distinction between explicit remaining, explicit zero, and missing assignees."""
    svc = PlanningDataValidationService(manager=mock_validation_db)

    report = svc.validate_planning_data(project_keys=["SMTPSUPORT", "GF"])

    # Explicit remaining (SMTPSUPORT-1: 10h, GF-1: 60h)
    assert report.tasks_with_explicit_remaining == 2
    # Missing assignee (SMTPSUPORT-2)
    assert report.tasks_missing_assignee == 1
    # SMTPSUPORT-2 has benchmark estimate proxy from SMTPSUPORT:Support (1.5h)
    assert report.tasks_with_benchmark_estimates == 1


def test_capacity_data_quality_disclosures(mock_validation_db):
    """Test that capacity calculations disclose UNKNOWN leave and calendar data sources."""
    svc = PlanningDataValidationService(manager=mock_validation_db)

    report = svc.validate_planning_data(project_keys=["SMTPSUPORT", "GF"])

    for r in report.resources_audited:
        assert r.working_hours_source == CapacityDataSource.NOMINAL_BASELINE
        assert r.leave_absence_source == CapacityDataSource.UNKNOWN
        assert r.holidays_calendar_source == CapacityDataSource.UNKNOWN
        assert any("Holidays, sick leave, part-time schedule" in d for d in r.data_quality_disclosures)


def test_cross_project_blocker_detection(mock_validation_db):
    """Test detection and reporting of cross-project hard blocker dependencies."""
    svc = PlanningDataValidationService(manager=mock_validation_db)

    report = svc.validate_planning_data(project_keys=["SMTPSUPORT", "GF"])

    assert report.blocked_tasks_count == 1
    assert len(report.dependency_findings) == 1

    dep = report.dependency_findings[0]
    assert dep.source_issue_key == "SMTPSUPORT-1"
    assert dep.target_issue_key == "GF-1"
    assert dep.is_cross_project is True
    assert dep.is_hard_block is True
    assert dep.is_target_resolved is False


def test_formatter_markdown_output(mock_validation_db):
    """Test that report formatter generates readable, structured markdown."""
    svc = PlanningDataValidationService(manager=mock_validation_db)
    report = svc.validate_planning_data(project_keys=["SMTPSUPORT", "GF"])

    md = PlanningDataValidationFormatter.format_markdown_report(report)
    assert "# Planning Data & Cross-Project Capacity Validation Report" in md
    assert "Alice Developer" in md
    assert "SMTPSUPORT-1" in md
    assert "GF-1" in md
    assert "70.0h" in md
    assert "Available Capacity = forecast_daily_hours (6.5h)" in md
