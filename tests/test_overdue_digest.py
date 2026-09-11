"""Comprehensive unit and integration tests for the Daily Consolidated Overdue Tasks Discord Digest."""

import datetime
import zoneinfo
import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport

from app.config.settings import settings
from app.database.connection import DatabaseManager
from app.database.repositories import (
    JiraIssueStateRepository,
    DailyReportHistoryRepository,
    EventRepository,
)
from app.core.reports.overdue_report import (
    DailyOverdueReportGenerator,
    format_display_date,
    format_date_human,
)
from app.connectors.discord.formatter import DiscordFormatter, COLOR_GREEN, COLOR_RED
from app.core.actions.base import ActionResult
from app.core.models.enums import ActionStatus
from app.core.rules.builtin import OverdueRule
from app.core.events.types import OverdueTask
from app.services.scheduler import PeriodicScheduler
from app.services.orchestrator import SystemOrchestrator
from app.api.app import app


def make_simulated_result(action):
    """Helper to return an ActionResult for action_engine.execute mocks."""
    return ActionResult(
        action_id=getattr(action, "action_id", "act-sim"),
        target_system=getattr(action, "target_system", "discord"),
        target_id=getattr(action, "target_id", "pm-alerts"),
        status=ActionStatus.DRY_RUN_SIMULATED,
        success=True,
        result_data={"simulated": True}
    )


@pytest.fixture
def overdue_generator(temp_db):
    """Fixture providing a DailyOverdueReportGenerator backed by an isolated temporary database."""
    return DailyOverdueReportGenerator(manager=temp_db)


# ==============================================================================
# Requirement 1: Jira polling detects overdue tasks
# ==============================================================================
@pytest.mark.asyncio
async def test_jira_polling_detects_overdue_and_updates_projection(temp_db):
    """Jira polling stores issue with due_date, updated_at, and team_group in local projection."""
    issue_state_repo = JiraIssueStateRepository(temp_db)
    orchestrator = SystemOrchestrator(manager=temp_db)

    # Mock Jira search result with an overdue task
    mock_issues = [
        {
            "id": "1001",
            "key": "WPEP-1592",
            "fields": {
                "summary": "Implement Auth Middleware",
                "status": {"name": "In Progress"},
                "duedate": "2026-09-08",
                "updated": "2026-09-10T08:30:00.000+0500",
                "assignee": {"displayName": "Ali Developer"},
                "project": {"key": "WPEP"},
            },
        }
    ]

    with patch.object(orchestrator.jira_poller.client, "search_issues", new_callable=AsyncMock) as mock_search:
        mock_search.return_value = {"issues": mock_issues, "total": 1}
        await orchestrator.jira_poller.poll()

    saved = issue_state_repo.get("WPEP-1592")
    assert saved is not None
    assert saved["jira_issue_key"] == "WPEP-1592"
    assert saved["due_date"] == "2026-09-08"
    assert saved["updated_at"] == "2026-09-10T08:30:00.000+0500"
    assert saved["status"] == "In Progress"
    assert saved["team_group"] == settings.JIRA_TEAM_GROUP


# ==============================================================================
# Requirement 2 & 16: Jira polling does NOT send individual Discord overdue notifications
# ==============================================================================
@pytest.mark.asyncio
async def test_jira_polling_does_not_send_discord_overdue_notifications(temp_db):
    """Jira polling cycle updates state but never dispatches individual overdue Discord alerts."""
    orchestrator = SystemOrchestrator(manager=temp_db)
    mock_issues = [
        {
            "id": "1002",
            "key": "PP-843",
            "fields": {
                "summary": "Bug in Payment Flow",
                "status": {"name": "To Do"},
                "duedate": "2026-09-07",
                "updated": "2026-09-09T10:00:00.000+0500",
                "assignee": {"displayName": "Sara QA"},
                "project": {"key": "PP"},
            },
        }
    ]

    dispatched_actions = []
    with patch.object(orchestrator.jira_poller.client, "search_issues", new_callable=AsyncMock) as mock_search, \
         patch("app.core.actions.engine.action_engine.execute", new_callable=AsyncMock) as mock_exec:
        mock_search.return_value = {"issues": mock_issues, "total": 1}
        mock_exec.side_effect = lambda act: dispatched_actions.append(act) or make_simulated_result(act)

        prev_overdue = settings.OVERDUE_NOTIFY_PM
        settings.OVERDUE_NOTIFY_PM = False
        try:
            await orchestrator.jira_poller.poll()
        finally:
            settings.OVERDUE_NOTIFY_PM = prev_overdue

    # Verify no individual overdue alerts were dispatched to Discord
    overdue_alerts = [
        a for a in dispatched_actions
        if getattr(a, "target_system", None) == "discord" and "Overdue" in getattr(a, "parameters", {}).get("title", "")
    ]
    assert len(overdue_alerts) == 0


# ==============================================================================
# Requirement 3 & 17 & 18: The 09:00 scheduler generates the overdue digest in Asia/Karachi
# ==============================================================================
@pytest.mark.asyncio
async def test_scheduler_0900_generates_overdue_digest(temp_db):
    """PeriodicScheduler evaluates and generates overdue digest at 09:00 Asia/Karachi."""
    repo = JiraIssueStateRepository(temp_db)
    repo.upsert(
        jira_issue_key="WPEP-1592",
        summary="Task 1",
        status="In Progress",
        due_date="2026-09-08",
        updated_at="2026-09-10T08:00:00.000+0500",
        team_group=settings.JIRA_TEAM_GROUP,
    )

    scheduler = PeriodicScheduler(manager=temp_db)

    # Set config to enabled, 09:00, Asia/Karachi
    prev_enabled = settings.OVERDUE_DIGEST_ENABLED
    prev_time = settings.OVERDUE_DIGEST_TIME
    prev_tz = settings.OVERDUE_DIGEST_TIMEZONE
    settings.OVERDUE_DIGEST_ENABLED = True
    settings.OVERDUE_DIGEST_TIME = "09:00"
    settings.OVERDUE_DIGEST_TIMEZONE = "Asia/Karachi"

    # Mock datetime.now to 09:05 Asia/Karachi
    mock_now = datetime.datetime(2026, 9, 10, 9, 5, tzinfo=zoneinfo.ZoneInfo("Asia/Karachi"))

    try:
        with patch("app.services.scheduler.datetime") as mock_dt, \
             patch("app.core.actions.engine.action_engine.execute", new_callable=AsyncMock) as mock_exec:
            mock_dt.now.return_value = mock_now
            mock_exec.side_effect = make_simulated_result

            cycle_res = await scheduler.run_cycle()
            assert cycle_res["daily_overdue_status"] == ActionStatus.DRY_RUN_SIMULATED.value
            assert mock_exec.called
    finally:
        settings.OVERDUE_DIGEST_ENABLED = prev_enabled
        settings.OVERDUE_DIGEST_TIME = prev_time
        settings.OVERDUE_DIGEST_TIMEZONE = prev_tz


# ==============================================================================
# Requirement 4 & 5: Multiple overdue tickets produce exactly ONE Discord message
# ==============================================================================
@pytest.mark.asyncio
async def test_multiple_overdue_tickets_produce_exactly_one_discord_message(temp_db, overdue_generator):
    """10 overdue tickets produce exactly ONE consolidated Discord message."""
    repo = JiraIssueStateRepository(temp_db)
    team_group = settings.JIRA_TEAM_GROUP

    for i in range(1, 11):
        repo.upsert(
            jira_issue_key=f"PP-{800 + i}",
            summary=f"Ticket {i}",
            status="In Progress",
            due_date="2026-09-05",
            updated_at="2026-09-09T10:00:00.000+0500",
            team_group=team_group,
        )

    dispatched = []
    with patch("app.core.actions.engine.action_engine.execute", new_callable=AsyncMock) as mock_exec:
        mock_exec.side_effect = lambda act: dispatched.append(act) or make_simulated_result(act)

        result = await overdue_generator.send_digest_to_discord(target_date="2026-09-10", force=True)

    assert result["status"] == ActionStatus.DRY_RUN_SIMULATED.value
    assert result["overdue_count"] == 10
    # Exactly ONE Discord message dispatched
    assert len(dispatched) == 1
    action = dispatched[0]
    assert action.target_system == "discord"
    assert "🚨" in action.parameters.get("title", "")
    assert "Mursaleen Cluster — Overdue Tasks" in action.parameters.get("title", "")


# ==============================================================================
# Requirement 6: Only the configured team is included
# ==============================================================================
@pytest.mark.asyncio
async def test_digest_scopes_strictly_to_configured_team(temp_db, overdue_generator):
    """Only tickets belonging to the configured JIRA_TEAM_GROUP are included."""
    repo = JiraIssueStateRepository(temp_db)
    target_team = settings.JIRA_TEAM_GROUP  # e.g. Mursaleen Cluster

    # Ticket for configured team
    repo.upsert(
        jira_issue_key="TEAM-1",
        summary="Team Task",
        status="In Progress",
        due_date="2026-09-01",
        updated_at="2026-09-08T12:00:00.000+0500",
        team_group=target_team,
    )
    # Ticket for different team
    repo.upsert(
        jira_issue_key="OTHER-2",
        summary="Other Team Task",
        status="In Progress",
        due_date="2026-09-01",
        updated_at="2026-09-08T12:00:00.000+0500",
        team_group="Other Cluster",
    )
    # Ticket with NULL team
    repo.upsert(
        jira_issue_key="NULL-3",
        summary="Unassigned Team Task",
        status="In Progress",
        due_date="2026-09-01",
        updated_at="2026-09-08T12:00:00.000+0500",
        team_group=None,
    )

    digest = overdue_generator.generate_digest(target_date="2026-09-10")
    keys = [t["key"] for t in digest["tickets"]]
    assert "TEAM-1" in keys
    assert "OTHER-2" not in keys
    assert "NULL-3" not in keys
    assert digest["overdue_count"] == 1


# ==============================================================================
# Requirement 7: Each ticket is hyperlinked to its Jira issue
# ==============================================================================
def test_each_ticket_is_hyperlinked_to_jira_issue():
    """Overdue digest description includes clickable Jira link to view the overdue tasks."""
    base_url = settings.JIRA_BASE_URL.rstrip("/")
    report_data = {
        "team_name": "Mursaleen Cluster",
        "formatted_date": "September 10, 2026",
        "tickets": [
            {
                "key": "WPEP-1592",
                "url": f"{base_url}/browse/WPEP-1592",
                "due_date": "Sep 08, 2026",
                "updated_at": "Sep 10, 2026",
            }
        ],
    }
    embed = DiscordFormatter.format_overdue_digest(report_data)
    desc = embed["embeds"][0]["description"]

    # Verify link syntax in description
    assert "Open 1 Overdue Tasks in Jira" in desc
    assert "https://objectsws.atlassian.net" in desc



# ==============================================================================
# Requirement 8 & 9: Due Date and Last Updated formatting
# ==============================================================================
@pytest.mark.asyncio
async def test_due_date_and_last_updated_formatting(temp_db, overdue_generator):
    """Due Date and Last Updated are formatted as '%b %d, %Y' using Jira's actual timestamps."""
    repo = JiraIssueStateRepository(temp_db)
    repo.upsert(
        jira_issue_key="HFCF-757",
        summary="Integration",
        status="In Progress",
        due_date="2026-09-05",
        updated_at="2026-09-08T14:22:15.000+0500",
        team_group=settings.JIRA_TEAM_GROUP,
    )

    digest = overdue_generator.generate_digest(target_date="2026-09-10")
    assert digest["overdue_count"] == 1
    ticket = digest["tickets"][0]

    assert ticket["due_date"] == "Sep 05, 2026"
    assert ticket["updated_at"] == "Sep 08, 2026"


# ==============================================================================
# Requirement 10: Ticket summary and Assignee are included in overdue digest
# ==============================================================================
@pytest.mark.asyncio
async def test_digest_includes_summary_and_assignee(temp_db, overdue_generator):
    """Digest includes summary and assignee in tickets and Discord embed table."""
    repo = JiraIssueStateRepository(temp_db)
    repo.upsert(
        jira_issue_key="PRIV-999",
        summary="Payment Gateway Timeout",
        status="In Progress",
        assignee="Sara QA",
        priority="High Priority",
        due_date="2026-09-01",
        updated_at="2026-09-08T10:00:00.000+0500",
        team_group=settings.JIRA_TEAM_GROUP,
    )

    digest = overdue_generator.generate_digest(target_date="2026-09-10")
    t = digest["tickets"][0]

    # Verify summary and assignee are included in ticket dictionary
    assert t["summary"] == "Payment Gateway Timeout"
    assert t["assignee"] == "Sara QA"
    assert t["due_date"] == "Sep 01, 2026"

    embed = DiscordFormatter.format_overdue_digest(digest)
    desc = embed["embeds"][0]["description"]
    assert "Payment Gateway Timeout" in desc
    assert "Sara QA" in desc
    assert "Summary" in desc
    assert "Assignee" in desc



# ==============================================================================
# Requirement 11: Multiple polls or scheduler cycles before 09:00 do not send digest
# ==============================================================================
@pytest.mark.asyncio
async def test_polls_before_0900_do_not_send_digest(temp_db):
    """Cycles before 09:00 AM do not send the digest."""
    scheduler = PeriodicScheduler(manager=temp_db)

    prev_enabled = settings.OVERDUE_DIGEST_ENABLED
    prev_time = settings.OVERDUE_DIGEST_TIME
    prev_tz = settings.OVERDUE_DIGEST_TIMEZONE
    settings.OVERDUE_DIGEST_ENABLED = True
    settings.OVERDUE_DIGEST_TIME = "09:00"
    settings.OVERDUE_DIGEST_TIMEZONE = "Asia/Karachi"

    # Mock time at 08:45 AM Asia/Karachi
    mock_early = datetime.datetime(2026, 9, 10, 8, 45, tzinfo=zoneinfo.ZoneInfo("Asia/Karachi"))

    try:
        with patch("app.services.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = mock_early
            res = await scheduler.run_cycle()
            assert res["daily_overdue_status"] is None
    finally:
        settings.OVERDUE_DIGEST_ENABLED = prev_enabled
        settings.OVERDUE_DIGEST_TIME = prev_time
        settings.OVERDUE_DIGEST_TIMEZONE = prev_tz


# ==============================================================================
# Requirement 12 & 13: Daily idempotency and persistence across restarts
# ==============================================================================
@pytest.mark.asyncio
async def test_daily_idempotency_and_restart_persistence(temp_db):
    """Digest is sent only once per day and restarts do not re-send."""
    generator1 = DailyOverdueReportGenerator(manager=temp_db)
    team_group = settings.JIRA_TEAM_GROUP
    target_date = "2026-09-10"

    with patch("app.core.actions.engine.action_engine.execute", new_callable=AsyncMock) as mock_exec:
        mock_exec.side_effect = make_simulated_result

        # First send: should succeed and record history
        res1 = await generator1.send_digest_to_discord(target_date=target_date, force=False, record_history=True)
        assert res1["status"] == ActionStatus.DRY_RUN_SIMULATED.value
        assert res1["recorded_history"] is True

        # Second send on same date: should be skipped
        res2 = await generator1.send_digest_to_discord(target_date=target_date, force=False, record_history=True)
        assert res2["status"] == "skipped"
        assert res2["reason"] == "already_sent_today"

        # Simulate app restart with a new generator instance pointing to same DB
        generator2 = DailyOverdueReportGenerator(manager=temp_db)
        res3 = await generator2.send_digest_to_discord(target_date=target_date, force=False, record_history=True)
        assert res3["status"] == "skipped"
        assert res3["reason"] == "already_sent_today"


# ==============================================================================
# Requirement 14: No-overdue condition produces concise "No overdue tasks" message
# ==============================================================================
@pytest.mark.asyncio
async def test_no_overdue_tasks_produces_single_concise_message(temp_db, overdue_generator):
    """When zero overdue tasks exist, send 'No overdue tasks.' with green embed and no table."""
    digest = overdue_generator.generate_digest(target_date="2026-09-10")
    assert digest["overdue_count"] == 0

    embed = DiscordFormatter.format_overdue_digest(digest)
    embed_obj = embed["embeds"][0]

    assert embed_obj["title"] == f"✅ {settings.JIRA_TEAM_GROUP} — Overdue Tasks"
    assert embed_obj["color"] == COLOR_GREEN
    assert "No overdue tasks." in embed_obj["description"]
    assert "**Date:** September 10, 2026" in embed_obj["description"]
    # No markdown table syntax in description
    assert "| Ticket |" not in embed_obj["description"]


# ==============================================================================
# Requirement 15: Existing overdue detection logic remains intact
# ==============================================================================
@pytest.mark.asyncio
async def test_existing_overdue_detection_definition_remains_intact(temp_db, overdue_generator):
    """Tasks only count as overdue if due_date is in past and status is not Done."""
    repo = JiraIssueStateRepository(temp_db)
    team_group = settings.JIRA_TEAM_GROUP

    # Overdue task (past due + In Progress)
    repo.upsert("OD-1", "OD", "In Progress", due_date="2026-09-01", updated_at="2026-09-05", team_group=team_group)
    # Completed task (past due + Done) -> should NOT be included
    repo.upsert("OD-2", "Done", "Done", due_date="2026-09-01", updated_at="2026-09-05", team_group=team_group)
    # Future task (future due + In Progress) -> should NOT be included
    repo.upsert("OD-3", "Future", "In Progress", due_date="2099-01-01", updated_at="2026-09-05", team_group=team_group)

    candidates = repo.get_overdue_candidates(team_group=team_group)
    keys = [c["jira_issue_key"] for c in candidates]
    assert "OD-1" in keys
    assert "OD-2" not in keys
    assert "OD-3" not in keys


# ==============================================================================
# Requirement 19: Jira base URL is taken from configuration
# ==============================================================================
def test_jira_base_url_taken_from_configuration():
    """Verify that Jira URLs use settings.JIRA_BASE_URL dynamically."""
    prev_url = settings.JIRA_BASE_URL
    settings.JIRA_BASE_URL = "https://custom-jira-instance.example.com"
    try:
        url = settings.get_jira_browse_url("TASK-42")
        assert url == "https://custom-jira-instance.example.com/browse/TASK-42"
    finally:
        settings.JIRA_BASE_URL = prev_url


# ==============================================================================
# Requirement 20 / Manual Test Endpoints: GET and POST
# ==============================================================================
@pytest.mark.asyncio
async def test_manual_preview_endpoint(temp_db):
    """GET /test/report/overdue-digest returns read-only preview without sending Discord notifications."""
    repo = JiraIssueStateRepository(temp_db)
    repo.upsert("PREV-1", "Preview", "In Progress", due_date="2026-09-01", updated_at="2026-09-05", team_group=settings.JIRA_TEAM_GROUP)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("app.core.reports.overdue_report.daily_overdue_report_generator.issue_state_repo", repo):
            resp = await client.get("/test/report/overdue-digest?date=2026-09-10")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "success"
            assert data["diagnostic_mode"] == "DEVELOPMENT_TESTING_READ_ONLY"
            assert data["digest"]["overdue_count"] >= 1


@pytest.mark.asyncio
async def test_manual_send_endpoint_does_not_block_scheduled_run_by_default(temp_db):
    """POST /test/report/overdue-digest/send sends immediately with record_history=False by default."""
    repo = JiraIssueStateRepository(temp_db)
    repo.upsert("SEND-1", "Send", "In Progress", due_date="2026-09-01", updated_at="2026-09-05", team_group=settings.JIRA_TEAM_GROUP)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("app.core.reports.overdue_report.daily_overdue_report_generator.issue_state_repo", repo), \
             patch("app.core.actions.engine.action_engine.execute", new_callable=AsyncMock) as mock_exec:
            mock_exec.side_effect = make_simulated_result
            resp = await client.post("/test/report/overdue-digest/send", json={"date": "2026-09-10", "force": True})
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == ActionStatus.DRY_RUN_SIMULATED.value
            # Crucial check: record_history is False by default so scheduled 09:00 run is NOT blocked
            assert data["recorded_history"] is False
