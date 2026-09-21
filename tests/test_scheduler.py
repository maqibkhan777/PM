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


@pytest.mark.asyncio
async def test_scheduler_daily_activity_report_disabled(temp_db):
    """Test scheduler does not evaluate or dispatch daily activity report when disabled."""
    scheduler = PeriodicScheduler(manager=temp_db)
    prev = settings.DAILY_ACTIVITY_REPORT_ENABLED
    settings.DAILY_ACTIVITY_REPORT_ENABLED = False
    try:
        res = await scheduler._evaluate_daily_activity_report()
        assert res is None
    finally:
        settings.DAILY_ACTIVITY_REPORT_ENABLED = prev


@pytest.mark.asyncio
async def test_scheduler_daily_activity_report_before_scheduled_time(temp_db, monkeypatch):
    """Test scheduler skips daily activity report when current time is before scheduled time."""
    scheduler = PeriodicScheduler(manager=temp_db)
    prev_enabled = settings.DAILY_ACTIVITY_REPORT_ENABLED
    prev_time = settings.DAILY_ACTIVITY_REPORT_TIME
    prev_tz = settings.DAILY_ACTIVITY_REPORT_TIMEZONE

    settings.DAILY_ACTIVITY_REPORT_ENABLED = True
    settings.DAILY_ACTIVITY_REPORT_TIME = "08:40"
    settings.DAILY_ACTIVITY_REPORT_TIMEZONE = "Asia/Karachi"

    import zoneinfo
    tz = zoneinfo.ZoneInfo("Asia/Karachi")
    fake_now = datetime(2026, 9, 20, 8, 15, tzinfo=tz)

    class MockDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fake_now

    monkeypatch.setattr("app.services.scheduler.datetime", MockDatetime)

    try:
        res = await scheduler._evaluate_daily_activity_report()
        assert res is None
    finally:
        settings.DAILY_ACTIVITY_REPORT_ENABLED = prev_enabled
        settings.DAILY_ACTIVITY_REPORT_TIME = prev_time
        settings.DAILY_ACTIVITY_REPORT_TIMEZONE = prev_tz


@pytest.mark.asyncio
async def test_scheduler_daily_activity_report_at_or_after_scheduled_time(temp_db, monkeypatch):
    """Test scheduler triggers daily activity report when current time reaches scheduled time."""
    from app.core.actions.base import ActionResult
    from app.core.models.enums import ActionStatus
    from app.core.actions.engine import action_engine

    async def fake_execute(action):
        return ActionResult(
            action_id=getattr(action, "action_id", "act-sim-1"),
            target_system="discord",
            target_id="pm-alerts",
            status=ActionStatus.DRY_RUN_SIMULATED,
            success=True,
            result_data={"simulated": True}
        )

    monkeypatch.setattr(action_engine, "execute", fake_execute)

    scheduler = PeriodicScheduler(manager=temp_db)
    event_repo = EventRepository(temp_db)
    now_iso = "2026-09-20T04:00:00Z"

    event_repo.insert(
        event_type="TaskCreated",
        source="jira",
        external_event_id="sched-act-1",
        timestamp=now_iso,
        actor_name="Ahsan Amin",
        task_id="CF7-200",
        payload={"title": "Scheduled Activity"}
    )

    prev_enabled = settings.DAILY_ACTIVITY_REPORT_ENABLED
    prev_time = settings.DAILY_ACTIVITY_REPORT_TIME
    prev_tz = settings.DAILY_ACTIVITY_REPORT_TIMEZONE

    settings.DAILY_ACTIVITY_REPORT_ENABLED = True
    settings.DAILY_ACTIVITY_REPORT_TIME = "08:40"
    settings.DAILY_ACTIVITY_REPORT_TIMEZONE = "Asia/Karachi"

    import zoneinfo
    tz = zoneinfo.ZoneInfo("Asia/Karachi")
    fake_now = datetime(2026, 9, 20, 8, 45, tzinfo=tz)

    class MockDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fake_now

    monkeypatch.setattr("app.services.scheduler.datetime", MockDatetime)

    try:
        res = await scheduler._evaluate_daily_activity_report()
        assert res is not None
        assert res.get("status").lower() in ("success", "simulated", "dry_run_simulated")
        assert res.get("date") == "2026-09-19"
        assert res.get("recorded_history") is True

        # Second evaluation should be skipped due to persistent idempotency
        res2 = await scheduler._evaluate_daily_activity_report()
        assert res2 is None
    finally:
        settings.DAILY_ACTIVITY_REPORT_ENABLED = prev_enabled
        settings.DAILY_ACTIVITY_REPORT_TIME = prev_time
        settings.DAILY_ACTIVITY_REPORT_TIMEZONE = prev_tz


@pytest.mark.asyncio
async def test_scheduler_run_cycle_includes_daily_activity_status(temp_db):
    """Test scheduler run_cycle returns daily_activity_status field."""
    scheduler = PeriodicScheduler(manager=temp_db)
    res = await scheduler.run_cycle()
    assert "daily_activity_status" in res
    assert res["daily_activity_status"] is None or isinstance(res["daily_activity_status"], str)


@pytest.mark.asyncio
async def test_scheduler_daily_activity_failure_isolation(temp_db, monkeypatch):
    """Test that an error in _evaluate_daily_activity_report does not break run_cycle."""
    scheduler = PeriodicScheduler(manager=temp_db)

    async def raise_err():
        raise RuntimeError("Simulated daily activity error")

    monkeypatch.setattr(scheduler, "_evaluate_daily_activity_report", raise_err)

    # run_cycle must complete cleanly without throwing
    res = await scheduler.run_cycle()
    assert "daily_activity_status" in res
    assert res["daily_activity_status"] is None


@pytest.mark.asyncio
async def test_existing_report_schedules_unaffected(temp_db):
    """Test that existing report evaluation methods in PeriodicScheduler remain unaffected."""
    scheduler = PeriodicScheduler(manager=temp_db)
    assert hasattr(scheduler, "_evaluate_daily_worklog_report")
    assert hasattr(scheduler, "_evaluate_daily_overdue_digest")
    assert hasattr(scheduler, "_evaluate_daily_pm_attention_digest")
    assert hasattr(scheduler, "_evaluate_mubashir_automation_report")
    assert hasattr(scheduler, "_evaluate_daily_activity_report")
    assert hasattr(scheduler, "_evaluate_performance_analysis")
    assert hasattr(scheduler, "_evaluate_daily_retention")
    assert hasattr(scheduler, "_evaluate_monday_retention")
