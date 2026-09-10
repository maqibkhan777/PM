"""Unit tests for local Jira issue state projection and stale/overdue detection."""

import pytest
from datetime import datetime, timezone, timedelta
from app.database.repositories import JiraIssueStateRepository
from app.services.scheduler import PeriodicScheduler
from app.utils.time import format_iso, utc_now
from app.config.settings import settings


@pytest.mark.asyncio
async def test_jira_issue_state_meaningful_activity_semantics(temp_db):
    """Test that meaningful activities update last_activity_at while non-meaningful ones do not."""
    repo = JiraIssueStateRepository(temp_db)

    initial_time = "2026-09-07T10:00:00+00:00"
    repo.upsert(
        jira_issue_key="TEST-1",
        summary="Initial Task",
        status="In Progress",
        assignee="Developer A",
        last_seen_at=initial_time,
        last_activity_at=initial_time
    )

    state = repo.get("TEST-1")
    assert state["last_activity_at"] == initial_time

    # Non-meaningful update (e.g. system background check or label sync) passes last_activity_at=None
    later_seen_time = "2026-09-08T12:00:00+00:00"
    repo.upsert(
        jira_issue_key="TEST-1",
        summary="Initial Task",
        status="In Progress",
        assignee="Developer A",
        last_seen_at=later_seen_time,
        last_activity_at=None  # Preserves existing!
    )
    state_after = repo.get("TEST-1")
    assert state_after["last_seen_at"] == later_seen_time
    assert state_after["last_activity_at"] == initial_time  # Inactivity timer not reset!

    # Meaningful activity (e.g. status change or comment added)
    activity_time = "2026-09-08T14:00:00+00:00"
    repo.upsert(
        jira_issue_key="TEST-1",
        summary="Initial Task",
        status="In Progress",
        assignee="Developer A",
        last_seen_at=activity_time,
        last_activity_at=activity_time
    )
    state_active = repo.get("TEST-1")
    assert state_active["last_activity_at"] == activity_time


@pytest.mark.asyncio
async def test_stale_task_evaluated_from_projection(temp_db):
    """Test that PeriodicScheduler evaluates stale tasks directly from the local projection."""
    repo = JiraIssueStateRepository(temp_db)
    scheduler = PeriodicScheduler(manager=temp_db)

    # Insert a task whose meaningful activity was 30 hours ago
    thirty_hours_ago = format_iso(datetime.now(timezone.utc) - timedelta(hours=30))
    repo.upsert(
        jira_issue_key="STALE-PROJ-1",
        summary="Critical Backend Task",
        status="In Progress",
        assignee="Sara",
        last_seen_at=thirty_hours_ago,
        last_activity_at=thirty_hours_ago,
        team_group=settings.JIRA_TEAM_GROUP
    )

    prev = settings.STALE_TASK_NOTIFY_PM
    settings.STALE_TASK_NOTIFY_PM = True
    try:
        result = await scheduler.run_cycle()
        assert result["stale_actions"] >= 1
        assert result["actions_dispatched"] >= 1
    finally:
        settings.STALE_TASK_NOTIFY_PM = prev


@pytest.mark.asyncio
async def test_overdue_task_evaluated_from_projection(temp_db):
    """Test that PeriodicScheduler evaluates overdue tasks directly from the local projection."""
    repo = JiraIssueStateRepository(temp_db)
    scheduler = PeriodicScheduler(manager=temp_db)

    yesterday_date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    repo.upsert(
        jira_issue_key="OVERDUE-PROJ-2",
        summary="Release Feature",
        status="In Progress",
        assignee="Bob",
        due_date=yesterday_date,
        last_seen_at=format_iso(datetime.now(timezone.utc)),
        last_activity_at=format_iso(datetime.now(timezone.utc)),
        team_group=settings.JIRA_TEAM_GROUP
    )

    prev = settings.OVERDUE_NOTIFY_PM
    settings.OVERDUE_NOTIFY_PM = True
    try:
        result = await scheduler.run_cycle()
        assert result["overdue_actions"] >= 1
        assert result["actions_dispatched"] >= 1
    finally:
        settings.OVERDUE_NOTIFY_PM = prev
