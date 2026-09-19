"""Tests for Phase 2: Centralized Retention Service."""

import json
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import pytest

from app.database.schema import init_db
from app.core.retention.models import RetentionScope
from app.core.retention.policy import get_retention_policies, get_policy_for_table
from app.core.retention.repository import RetentionRepository
from app.core.retention.service import RetentionService


def test_monday_raw_boundary_calculation():
    """Verify compute_monday_raw_boundary calculates exact Monday 00:00 Asia/Karachi in UTC."""
    service = RetentionService()
    karachi_tz = ZoneInfo("Asia/Karachi")

    # Scenario 1: Reference time is Wednesday 2026-09-16 15:30:00 PKT
    ref_wed = datetime(2026, 9, 16, 15, 30, 0, tzinfo=karachi_tz)
    # Target should be Monday 7 days prior: Monday 2026-09-07 00:00:00 PKT -> 2026-09-06 19:00:00 UTC
    boundary_wed = service.compute_monday_raw_boundary(ref_wed, days=7)
    assert boundary_wed == "2026-09-06T19:00:00+00:00"

    # Scenario 2: Reference time is Monday 2026-09-21 04:00:00 PKT
    ref_mon = datetime(2026, 9, 21, 4, 0, 0, tzinfo=karachi_tz)
    # Target should be Monday 7 days prior: Monday 2026-09-14 00:00:00 PKT -> 2026-09-13 19:00:00 UTC
    boundary_mon = service.compute_monday_raw_boundary(ref_mon, days=7)
    assert boundary_mon == "2026-09-13T19:00:00+00:00"

    # Scenario 3: Reference time is Sunday 2026-09-20 23:59:59 PKT
    ref_sun = datetime(2026, 9, 20, 23, 59, 59, tzinfo=karachi_tz)
    # Target should be Monday 7 days prior to the current week's Monday: Monday 2026-09-07 00:00:00 PKT
    boundary_sun = service.compute_monday_raw_boundary(ref_sun, days=7)
    assert boundary_sun == "2026-09-06T19:00:00+00:00"


def test_retention_policy_registry_integrity():
    """Verify retention policy registry covers required tables and enforces hierarchy."""
    policies = get_retention_policies()
    assert len(policies) > 0

    table_names = [p.table_name for p in policies]
    
    # Critical child-before-parent ordering check
    if "performance_analysis_runs" in table_names:
        idx_parent = table_names.index("performance_analysis_runs")
        if "resource_performance_profiles" in table_names:
            idx_child = table_names.index("resource_performance_profiles")
            assert idx_child < idx_parent, "Child resource_performance_profiles must be pruned before parent performance_analysis_runs"
        if "resource_task_classifications" in table_names:
            idx_child = table_names.index("resource_task_classifications")
            assert idx_child < idx_parent, "Child resource_task_classifications must be pruned before parent performance_analysis_runs"

    # Check protected tables policy
    worklog_policy = get_policy_for_table("jira_worklogs")
    assert worklog_policy is not None
    assert worklog_policy.is_protected is True
    assert worklog_policy.retention_days == 365, "jira_worklogs must be protected for 365 days"

    issue_state_policy = get_policy_for_table("jira_issue_state")
    assert issue_state_policy is not None
    assert issue_state_policy.is_protected is True, "jira_issue_state is strictly protected"


def test_retention_dry_run_leaves_data_intact(temp_db):
    """Verify retention execution in dry_run mode identifies eligible rows without mutating DB."""
    repo = RetentionRepository(temp_db)
    service = RetentionService(manager=temp_db, repository=repo)

    # Insert test events: 2 old processed events, 1 recent processed event
    old_utc = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
    recent_utc = datetime.now(timezone.utc).isoformat()

    with temp_db.session() as conn:
        conn.execute(
            """
            INSERT INTO events (id, event_type, source, processing_status, timestamp, payload, created_at)
            VALUES 
                ('evt-old-1', 'jira:issue_updated', 'jira', 'PROCESSED', ?, '{}', ?),
                ('evt-old-2', 'jira:issue_updated', 'jira', 'PROCESSED', ?, '{}', ?),
                ('evt-rec-1', 'jira:issue_updated', 'jira', 'PROCESSED', ?, '{}', ?)
            """,
            (old_utc, old_utc, old_utc, old_utc, recent_utc, recent_utc)
        )

    # Run DRY RUN weekly monday scope
    result = service.execute(scope=RetentionScope.WEEKLY_MONDAY, dry_run=True)
    assert result.dry_run is True
    assert result.status == "COMPLETED"
    assert result.total_deleted_rows == 0

    # Verify rows still exist in database
    with temp_db.session() as conn:
        count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        assert count == 3

    # Check retention run history record
    last_run = repo.get_last_run()
    assert last_run is not None
    assert bool(last_run["dry_run"]) is True
    assert last_run["rows_deleted"] == 0


def test_retention_events_live_execution_and_failed_event_safety(temp_db):
    """Verify LIVE retention deletes old processed events while preserving failed events and recent events."""
    repo = RetentionRepository(temp_db)
    service = RetentionService(manager=temp_db, repository=repo)

    old_utc = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    recent_utc = datetime.now(timezone.utc).isoformat()

    with temp_db.session() as conn:
        conn.execute(
            """
            INSERT INTO events (id, event_type, source, processing_status, timestamp, payload, created_at)
            VALUES 
                ('evt-old-proc', 'jira:issue_updated', 'jira', 'PROCESSED', ?, '{}', ?),
                ('evt-old-fail', 'jira:issue_updated', 'jira', 'FAILED', ?, '{"error": "critical"}', ?),
                ('evt-rec-proc', 'jira:issue_updated', 'jira', 'PROCESSED', ?, '{}', ?)
            """,
            (old_utc, old_utc, old_utc, old_utc, recent_utc, recent_utc)
        )

    # Run LIVE weekly monday retention
    result = service.execute(scope=RetentionScope.WEEKLY_MONDAY, dry_run=False)
    assert result.dry_run is False
    assert result.status == "COMPLETED"
    assert result.total_deleted_rows >= 1

    # Verify results in SQLite
    with temp_db.session() as conn:
        remaining_ids = [r[0] for r in conn.execute("SELECT id FROM events").fetchall()]
        # Old processed event MUST be deleted
        assert "evt-old-proc" not in remaining_ids
        # Old failed event MUST be preserved for troubleshooting
        assert "evt-old-fail" in remaining_ids
        # Recent processed event MUST be preserved
        assert "evt-rec-proc" in remaining_ids


def test_retention_action_idempotency_protection(temp_db):
    """Verify actions retention deletes only old terminal actions and preserves active/pending actions."""
    repo = RetentionRepository(temp_db)
    service = RetentionService(manager=temp_db, repository=repo)

    old_utc = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
    recent_utc = datetime.now(timezone.utc).isoformat()

    with temp_db.session() as conn:
        conn.execute(
            """
            INSERT INTO actions (
                id, action_id, action_type, target_system, target_id, parameters, status, idempotency_key, created_at
            ) VALUES 
                ('act-old-done', 'act-old-done', 'AssignTask', 'jira', 'TREN-1', '{}', 'COMPLETED', 'idem-1', ?),
                ('act-old-pending', 'act-old-pending', 'AssignTask', 'jira', 'TREN-2', '{}', 'PENDING_APPROVAL', 'idem-2', ?),
                ('act-old-exec', 'act-old-exec', 'AssignTask', 'jira', 'TREN-3', '{}', 'EXECUTING', 'idem-3', ?),
                ('act-rec-done', 'act-rec-done', 'AssignTask', 'jira', 'TREN-4', '{}', 'COMPLETED', 'idem-4', ?)
            """,
            (old_utc, old_utc, old_utc, recent_utc)
        )

    # Run LIVE daily maintenance pass (analytical + actions + audit)
    result = service.execute(scope=RetentionScope.DAILY, dry_run=False)
    assert result.status == "COMPLETED"

    with temp_db.session() as conn:
        remaining_ids = [r[0] for r in conn.execute("SELECT id FROM actions").fetchall()]
        # Old completed action is deleted
        assert "act-old-done" not in remaining_ids
        # Old pending action MUST NOT be deleted
        assert "act-old-pending" in remaining_ids
        # Old executing action MUST NOT be deleted
        assert "act-old-exec" in remaining_ids
        # Recent completed action MUST NOT be deleted
        assert "act-rec-done" in remaining_ids


def test_chunked_deletion_limit(temp_db):
    """Verify delete_chunked handles large datasets with bounded batches (LIMIT 500)."""
    repo = RetentionRepository(temp_db)
    policy = get_policy_for_table("events")
    assert policy is not None

    old_utc = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    
    # Insert 1,200 old processed events
    with temp_db.session() as conn:
        conn.executemany(
            """
            INSERT INTO events (id, event_type, source, processing_status, timestamp, payload, created_at)
            VALUES (?, 'jira:issue_updated', 'jira', 'PROCESSED', ?, '{}', ?)
            """,
            [(f"evt-bulk-{i}", old_utc, old_utc) for i in range(1200)]
        )

    # Count eligible rows
    eligible = repo.count_eligible_rows(policy, "2099-01-01T00:00:00Z")
    assert eligible == 1200

    # Execute chunked deletion
    deleted = repo.delete_chunked(policy, "2099-01-01T00:00:00Z", batch_size=500)
    assert deleted == 1200

    # Verify 0 rows remaining
    with temp_db.session() as conn:
        count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        assert count == 0
