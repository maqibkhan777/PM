"""Unit tests for the PeriodicScheduler worker."""

import pytest
from app.services.scheduler import PeriodicScheduler
from app.database.repositories import EventRepository
from app.services.notification_deduplication import notification_dedup_service
from app.utils.time import utc_now, format_iso
from app.config.settings import settings
from datetime import datetime, timezone, timedelta


@pytest.fixture(autouse=True)
def clean_dedup():
    """Ensure clean notification history for scheduler tests."""
    notification_dedup_service.clear_all()
    yield
    notification_dedup_service.clear_all()


@pytest.mark.asyncio
async def test_scheduler_evaluates_stale_task(temp_db):
    """Test scheduler discovers inactive in-progress tasks."""
    scheduler = PeriodicScheduler(manager=temp_db)
    event_repo = EventRepository(temp_db)

    # Insert an In Progress event from 30 hours ago
    thirty_hours_ago = format_iso(datetime.now(timezone.utc) - timedelta(hours=30))
    event_repo.insert(
        event_type="TaskStatusChanged",
        source="jira",
        external_event_id="e-stale-unique-101",
        timestamp=thirty_hours_ago,
        actor_id="jira-user-ahsan",
        actor_name="Ahsan Amin",
        task_id="CF7-StaleSched-1",
        payload={
            "issue": {
                "key": "CF7-StaleSched-1",
                "fields": {
                    "summary": "Stale Feature",
                    "status": {"name": "In Progress"},
                    "assignee": {"accountId": "jira-user-ahsan", "displayName": "Ahsan Amin"}
                }
            }
        },
        processing_status="PROCESSED"
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
async def test_scheduler_evaluates_overdue_task(temp_db):
    """Test scheduler discovers tasks past their due date."""
    scheduler = PeriodicScheduler(manager=temp_db)
    event_repo = EventRepository(temp_db)

    # Insert an event with overdue date
    now_iso = utc_now().isoformat()
    yesterday_iso = format_iso(datetime.now(timezone.utc) - timedelta(days=1))

    event_repo.insert(
        event_type="TaskCreated",
        source="jira",
        external_event_id="e-overdue-unique-102",
        timestamp=now_iso,
        actor_name="Manager",
        task_id="CF7-OverdueSched-1",
        payload={
            "issue": {
                "key": "CF7-OverdueSched-1",
                "fields": {
                    "summary": "Urgent Release",
                    "status": {"name": "To Do"},
                    "duedate": yesterday_iso,
                    "assignee": {"displayName": "Developer"}
                }
            }
        },
        processing_status="PROCESSED"
    )

    prev = settings.OVERDUE_NOTIFY_PM
    settings.OVERDUE_NOTIFY_PM = True
    try:
        result = await scheduler.run_cycle()
        assert result["overdue_actions"] >= 1
        assert result["actions_dispatched"] >= 1
    finally:
        settings.OVERDUE_NOTIFY_PM = prev
