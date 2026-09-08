"""Unit tests for the Daily Activity Report generator."""

import pytest
from app.core.reports.daily_report import DailyActivityReportGenerator
from app.database.repositories import EventRepository
from app.utils.time import utc_now


def test_daily_activity_report_generation(temp_db):
    """Test generating daily activity metrics from recorded events."""
    event_repo = EventRepository(temp_db)
    report_gen = DailyActivityReportGenerator(manager=temp_db)

    today = utc_now().strftime("%Y-%m-%d")
    now_iso = utc_now().isoformat()

    # Insert sample events
    event_repo.insert(
        event_type="TaskCreated",
        source="jira",
        external_event_id="e1",
        timestamp=now_iso,
        actor_name="Ahsan Amin",
        task_id="CF7-1",
        payload={"title": "Task 1"}
    )
    event_repo.insert(
        event_type="TaskStatusChanged",
        source="jira",
        external_event_id="e2",
        timestamp=now_iso,
        actor_name="Ahsan Amin",
        task_id="CF7-1",
        payload={"old_status": "To Do", "new_status": "In Progress"}
    )
    event_repo.insert(
        event_type="TaskCommentAdded",
        source="jira",
        external_event_id="e3",
        timestamp=now_iso,
        actor_name="Sara Connor",
        task_id="CF7-2",
        payload={"comment": "Reviewing PR"}
    )

    report = report_gen.generate_report(target_date=today)

    assert report["date"] == today
    assert report["total_activities"] == 3
    assert report["tasks_created"] == 1
    assert report["status_transitions"] == 1
    assert report["comments_added"] == 1
    assert report["activities_by_resource"]["Ahsan Amin"] == 2
    assert report["activities_by_resource"]["Sara Connor"] == 1
