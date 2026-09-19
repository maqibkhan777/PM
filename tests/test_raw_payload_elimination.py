"""Tests for Phase 3: Raw Payload Elimination."""

from datetime import datetime, timezone
import json
import pytest
from app.database.schema import init_db
from app.database.repositories import (
    EventRepository,
    JiraIssueStateRepository,
    PerformanceRepository,
)
from app.connectors.jira.poller import JiraPoller


def test_jira_issue_state_canonical_storage(temp_db):
    """Verify JiraIssueStateRepository stores canonical columns and raw_reference is None."""
    repo = JiraIssueStateRepository(temp_db)
    
    repo.upsert(
        jira_issue_key="PROJ-101",
        summary="Fix payment gateway integration bug",
        status="In Progress",
        assignee="Ahsan Amin",
        priority="High",
        due_date="2026-09-10",
        updated_at="2026-09-02T10:00:00Z",
        project_key="PROJ",
        issue_type="Bug",
        labels=["backend", "urgent"],
        components=["Billing", "API"],
        subtask_count=3,
        original_estimate_seconds=14400,
        time_spent_seconds=7200,
        creator_id="creator-456",
        raw_reference=None,
    )
    
    retrieved = repo.get("PROJ-101")
    assert retrieved is not None
    assert retrieved["jira_issue_key"] == "PROJ-101"
    assert retrieved["issue_type"] == "Bug"
    assert retrieved["labels"] == ["backend", "urgent"]
    assert retrieved["components"] == ["Billing", "API"]
    assert retrieved["subtask_count"] == 3
    assert retrieved["original_estimate_seconds"] == 14400
    assert retrieved["time_spent_seconds"] == 7200
    assert retrieved["creator_id"] == "creator-456"
    assert retrieved["raw_reference"] is None

    # Check directly from DB row
    with temp_db.session() as conn:
        row = conn.execute("SELECT * FROM jira_issue_state WHERE jira_issue_key = 'PROJ-101'").fetchone()
        assert row["issue_type"] == "Bug"
        assert json.loads(row["labels"]) == ["backend", "urgent"]
        assert json.loads(row["components"]) == ["Billing", "API"]
        assert row["subtask_count"] == 3
        assert row["original_estimate_seconds"] == 14400
        assert row["time_spent_seconds"] == 7200
        assert row["creator_id"] == "creator-456"
        assert row["raw_reference"] is None


def test_jira_issue_state_migration_backfill(temp_db):
    """Verify _migrate_jira_issue_state backfills canonical columns from raw_reference if present."""
    # Insert legacy row with raw_reference JSON
    legacy_raw = {
        "fields": {
            "issuetype": {"name": "Story"},
            "labels": ["migration", "test"],
            "components": [{"name": "Core"}],
            "subtasks": [{"id": "1"}, {"id": "2"}],
            "timeoriginalestimate": 28800,
            "timespent": 14400,
            "creator": {"accountId": "legacy-creator-99"},
        }
    }
    with temp_db.session() as conn:
        conn.execute(
            """
            INSERT INTO jira_issue_state (
                jira_issue_key, project_key, summary, status,
                assignee, priority, last_seen_at, last_activity_at, raw_reference
            ) VALUES (
                'LEG-1', 'LEG', 'Legacy Story', 'Open',
                'User One', 'Medium', '2026-08-01T00:00:00Z', '2026-08-01T00:00:00Z', ?
            )
            """,
            (json.dumps(legacy_raw),),
        )

    # Re-run init_db (which runs _migrate_jira_issue_state)
    init_db(temp_db)

    repo = JiraIssueStateRepository(temp_db)
    migrated = repo.get("LEG-1")
    assert migrated is not None
    assert migrated["issue_type"] == "Story"
    assert migrated["labels"] == ["migration", "test"]
    assert migrated["components"] == ["Core"]
    assert migrated["subtask_count"] == 2
    assert migrated["original_estimate_seconds"] == 28800
    assert migrated["time_spent_seconds"] == 14400
    assert migrated["creator_id"] == "legacy-creator-99"


def test_event_payload_stripping_on_processed(temp_db):
    """Verify EventRepository strips payload on PROCESSED but preserves metadata."""
    repo = EventRepository(temp_db)
    
    eid = repo.insert(
        event_type="jira:issue_updated",
        source="jira",
        external_event_id="ext-1001",
        timestamp="2026-09-01T10:00:00Z",
        payload={"massive_json": "x" * 5000, "details": {"nested": "data"}},
        actor_id="actor-1",
        actor_name="Actor One",
        task_id="PROJ-200",
        project_id="PROJ",
        processing_status="RECEIVED",
        event_id="evt-strip-1",
    )
    
    # Check initial state
    saved = repo.get_by_id("evt-strip-1")
    assert saved is not None
    assert saved["processing_status"] == "RECEIVED"
    assert len(json.dumps(saved["payload"])) > 1000

    # Mark as PROCESSED
    repo.update_status("evt-strip-1", "PROCESSED")

    # Check updated state
    updated = repo.get_by_id("evt-strip-1")
    assert updated is not None
    assert updated["processing_status"] == "PROCESSED"
    assert updated["id"] == "evt-strip-1"
    assert updated["source"] == "jira"
    assert updated["event_type"] == "jira:issue_updated"
    assert updated["external_event_id"] == "ext-1001"
    assert updated["actor_id"] == "actor-1"
    assert updated["actor_name"] == "Actor One"
    assert updated["task_id"] == "PROJ-200"
    assert updated["project_id"] == "PROJ"
    # Payload must be stripped to empty dict
    assert updated["payload"] == {}

    # Verify directly in SQLite
    with temp_db.session() as conn:
        row = conn.execute("SELECT payload FROM events WHERE id = 'evt-strip-1'").fetchone()
        assert row[0] == "{}"


def test_event_payload_preserved_on_failure(temp_db):
    """Verify EventRepository preserves payload intact on FAILED status."""
    repo = EventRepository(temp_db)
    
    eid = repo.insert(
        event_type="jira:issue_updated",
        source="jira",
        external_event_id="ext-1002",
        timestamp="2026-09-01T10:00:00Z",
        payload={"important_debug_data": "must_preserve", "error_context": 12345},
        processing_status="RECEIVED",
        event_id="evt-fail-1",
    )

    # Mark as FAILED
    repo.update_status("evt-fail-1", "FAILED", last_error="Simulated downstream timeout")

    failed = repo.get_by_id("evt-fail-1")
    assert failed is not None
    assert failed["processing_status"] == "FAILED"
    assert failed["last_error"] == "Simulated downstream timeout"
    # Payload MUST NOT be stripped
    assert failed["payload"] == {"important_debug_data": "must_preserve", "error_context": 12345}


def test_performance_profile_raw_json_elimination(temp_db):
    """Verify PerformanceRepository stops writing raw_profile_json."""
    repo = PerformanceRepository(temp_db)
    
    profile_data = {
        "account_id": "acc-test-1",
        "analysis_run_id": "run-test-1",
        "display_name": "Developer One",
        "role": "Developer",
        "designation": "Software Engineer",
        "role_category": "Engineering",
        "team_group": "Backend",
        "analysis_start": "2026-08-20T00:00:00Z",
        "analysis_end": "2026-09-19T00:00:00Z",
        "history_days": 30,
        "requested_history_days": 30,
        "actual_available_history_days": 30,
        "completed_tasks": 10,
        "active_working_days": 20,
        "total_logged_seconds": 36000,
        "average_logged_hours_per_active_day": 4.0,
        "median_logged_hours_per_active_day": 4.0,
        "tasks_due": 10,
        "tasks_completed_on_time": 9,
        "tasks_completed_late": 1,
        "on_time_rate": 0.9,
        "average_days_late": 0.5,
        "median_days_late": 0.0,
        "average_task_hours": 3.6,
        "median_task_hours": 3.5,
        "p25_task_hours": 2.0,
        "p75_task_hours": 5.0,
        "estimated_tasks": 8,
        "average_estimated_hours": 4.0,
        "average_actual_hours": 3.6,
        "estimation_variance_percent": 10.0,
        "median_estimation_variance_percent": 10.0,
        "reopened_tasks": 0,
        "reopen_rate": 0.0,
        "blocker_count": 0,
        "blocked_seconds": 0,
        "average_blocker_hours": 0.0,
        "nominal_daily_capacity_hours": 6.75,
        "observed_daily_capacity_hours": 6.75,
        "forecast_daily_capacity_hours": 6.75,
        "current_queue_task_count": 2,
        "current_queue_expected_hours": 8.0,
        "current_queue_review_buffer_hours": 2.0,
        "current_queue_total_expected_hours": 10.0,
        "available_capacity_hours": 15.0,
        "capacity_difference_hours": 5.0,
        "forecast_status": "GREEN",
        "forecast_reason": "On track",
        "projected_queue_completion_date": "2026-09-22",
        "confidence_level": "HIGH",
        "raw_profile_json": None,
    }
    
    repo.upsert_profile(profile_data)

    # Check raw_profile_json in DB
    with temp_db.session() as conn:
        row = conn.execute("SELECT raw_profile_json FROM resource_performance_profiles WHERE account_id = 'acc-test-1'").fetchone()
        assert row["raw_profile_json"] is None


@pytest.mark.asyncio
async def test_jira_poller_canonical_projection(temp_db):
    """Verify JiraPoller extracts canonical fields and does not set raw_reference."""
    poller = JiraPoller(manager=temp_db)
    
    raw_issue = {
        "id": "10050",
        "key": "DEV-50",
        "fields": {
            "summary": "Implement high-throughput ingest",
            "status": {"name": "In Progress", "statusCategory": {"key": "indeterminate", "name": "In Progress"}},
            "project": {"key": "DEV"},
            "assignee": {"accountId": "dev-user-1", "displayName": "Developer One"},
            "created": "2026-09-10T08:00:00.000+0000",
            "updated": "2026-09-12T09:00:00.000+0000",
            "resolutiondate": None,
            "duedate": "2026-09-20",
            "priority": {"name": "Critical"},
            "issuetype": {"name": "Task"},
            "labels": ["performance", "v1.2.3"],
            "components": [{"name": "Pipeline"}],
            "subtasks": [{"id": "10051"}, {"id": "10052"}],
            "timeoriginalestimate": 72000,
            "timespent": 36000,
            "creator": {"accountId": "creator-77"},
        }
    }
    
    await poller._process_issue(raw_issue, query_start_dt=datetime.now(timezone.utc))

    state_repo = JiraIssueStateRepository(temp_db)
    saved = state_repo.get("DEV-50")
    assert saved is not None
    assert saved["jira_issue_key"] == "DEV-50"
    assert saved["issue_type"] == "Task"
    assert saved["labels"] == ["performance", "v1.2.3"]
    assert saved["components"] == ["Pipeline"]
    assert saved["subtask_count"] == 2
    assert saved["original_estimate_seconds"] == 72000
    assert saved["time_spent_seconds"] == 36000
    assert saved["creator_id"] == "creator-77"
    assert saved["raw_reference"] is None
