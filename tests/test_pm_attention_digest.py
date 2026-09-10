"""Comprehensive unit and integration tests for the Daily Consolidated PM Attention Digest."""

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
from app.core.reports.attention_report import (
    DailyPMAttentionReportGenerator,
    format_display_date,
    format_date_human,
    calculate_inactive_for,
    resolve_digest_date,
)
from app.connectors.discord.formatter import DiscordFormatter, COLOR_GREEN, COLOR_AMBER
from app.core.actions.base import ActionResult
from app.core.models.enums import ActionStatus
from app.core.rules.builtin import StaleTaskRule
from app.core.events.types import StaleTask
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
def attention_generator(temp_db):
    """Fixture providing a DailyPMAttentionReportGenerator backed by an isolated temporary database."""
    return DailyPMAttentionReportGenerator(manager=temp_db)


# ==============================================================================
# Requirement 1: Inactive / Stalled Candidates Retrieval & Duration Formatting
# ==============================================================================
def test_inactive_stalled_candidates_retrieved_and_formatted(temp_db, attention_generator):
    """Inactive tasks in progress exceeding threshold are retrieved with duration."""
    repo = JiraIssueStateRepository(temp_db)
    two_days_ago = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%S.000+0000")

    repo.upsert(
        jira_issue_key="TASK-101",
        summary="Backend API development",
        status="In Progress",
        assignee="Developer A",
        team_group=settings.JIRA_TEAM_GROUP,
        last_activity_at=two_days_ago,
        updated_at=two_days_ago,
    )

    digest = attention_generator.generate_digest()
    stale_info = digest["categories"]["inactive_stalled"]
    assert stale_info["count"] == 1
    assert stale_info["tickets"][0]["key"] == "TASK-101"
    assert "2 day" in stale_info["tickets"][0]["inactive_for"]
    assert stale_info["tickets"][0]["status"] == "In Progress"
    assert stale_info["tickets"][0]["url"] == settings.get_jira_browse_url("TASK-101")


# ==============================================================================
# Requirement 2: Reopened Candidates Retrieval
# ==============================================================================
def test_reopened_candidates_retrieved(temp_db, attention_generator):
    """Active reopened tickets (via event or status) are included in Category 2."""
    repo = JiraIssueStateRepository(temp_db)
    event_repo = EventRepository(temp_db)
    now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000+0000")

    # Issue 1: Reopened via TaskReopened event
    repo.upsert(
        jira_issue_key="TASK-201",
        summary="Fix auth token bug",
        status="Waiting for customer",
        assignee="Support Agent",
        team_group=settings.JIRA_TEAM_GROUP,
        updated_at=now_str,
    )
    event_repo.insert(
        event_type="TaskReopened",
        source="jira",
        external_event_id=None,
        timestamp=now_str,
        payload={"reason": "Customer replied with further details"},
        actor_name="Customer",
        task_id="TASK-201"
    )

    # Issue 2: Reopened via status string
    repo.upsert(
        jira_issue_key="TASK-202",
        summary="UI Regression issue",
        status="Reopened",
        assignee="QA Engineer",
        team_group=settings.JIRA_TEAM_GROUP,
        updated_at=now_str,
    )

    digest = attention_generator.generate_digest()
    reopened_info = digest["categories"]["reopened"]
    assert reopened_info["count"] == 2
    keys = {t["key"] for t in reopened_info["tickets"]}
    assert keys == {"TASK-201", "TASK-202"}


# ==============================================================================
# Requirement 3: Completed Reopened Tickets are Excluded
# ==============================================================================
def test_reopened_completed_tickets_excluded(temp_db, attention_generator):
    """Reopened tickets that have since been completed/done are safely excluded."""
    repo = JiraIssueStateRepository(temp_db)
    event_repo = EventRepository(temp_db)
    now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000+0000")

    repo.upsert(
        jira_issue_key="TASK-203",
        summary="Completed task that was once reopened",
        status="Done",
        assignee="Lead Dev",
        team_group=settings.JIRA_TEAM_GROUP,
        updated_at=now_str,
    )
    event_repo.insert(
        event_type="TaskReopened",
        source="jira",
        external_event_id=None,
        timestamp=now_str,
        payload={"reason": "Reopened before"},
        actor_name="Customer",
        task_id="TASK-203"
    )

    digest = attention_generator.generate_digest()
    reopened_info = digest["categories"]["reopened"]
    assert reopened_info["count"] == 0


# ==============================================================================
# Requirement 4: Unassigned Candidates Retrieval
# ==============================================================================
def test_unassigned_candidates_retrieved(temp_db, attention_generator):
    """Active unassigned tickets (None or empty string or 'Unassigned') are included in Category 3."""
    repo = JiraIssueStateRepository(temp_db)
    now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000+0000")

    repo.upsert(
        jira_issue_key="TASK-301",
        summary="Unassigned Task A",
        status="To Do",
        assignee=None,
        team_group=settings.JIRA_TEAM_GROUP,
        updated_at=now_str,
    )
    repo.upsert(
        jira_issue_key="TASK-302",
        summary="Unassigned Task B",
        status="Open",
        assignee="  ",
        team_group=settings.JIRA_TEAM_GROUP,
        updated_at=now_str,
    )
    repo.upsert(
        jira_issue_key="TASK-303",
        summary="Unassigned Task C",
        status="In Review",
        assignee="Unassigned",
        team_group=settings.JIRA_TEAM_GROUP,
        updated_at=now_str,
    )

    digest = attention_generator.generate_digest()
    unassigned_info = digest["categories"]["unassigned"]
    assert unassigned_info["count"] == 3
    keys = {t["key"] for t in unassigned_info["tickets"]}
    assert keys == {"TASK-301", "TASK-302", "TASK-303"}


# ==============================================================================
# Requirement 5: Completed Unassigned Tickets are Excluded
# ==============================================================================
def test_unassigned_completed_tickets_excluded(temp_db, attention_generator):
    """Unassigned tickets that are completed/closed are excluded."""
    repo = JiraIssueStateRepository(temp_db)
    now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000+0000")

    repo.upsert(
        jira_issue_key="TASK-304",
        summary="Cancelled unassigned task",
        status="Cancelled",
        assignee=None,
        team_group=settings.JIRA_TEAM_GROUP,
        updated_at=now_str,
    )

    digest = attention_generator.generate_digest()
    unassigned_info = digest["categories"]["unassigned"]
    assert unassigned_info["count"] == 0


# ==============================================================================
# Requirement 6: Team Scope Strictly Enforced Across All Categories
# ==============================================================================
def test_team_scope_strictly_enforced(temp_db, attention_generator):
    """Tickets belonging to another team/group are excluded from the digest."""
    repo = JiraIssueStateRepository(temp_db)
    event_repo = EventRepository(temp_db)
    two_days_ago = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%S.000+0000")

    # Stale candidate from Other Team
    repo.upsert(
        jira_issue_key="OTHER-101",
        summary="Other team stale task",
        status="In Progress",
        assignee="Other Dev",
        team_group="Other Cluster",
        last_activity_at=two_days_ago,
        updated_at=two_days_ago,
    )
    # Reopened candidate from Other Team
    repo.upsert(
        jira_issue_key="OTHER-201",
        summary="Other team reopened task",
        status="In Progress",
        assignee="Other Dev",
        team_group="Other Cluster",
        updated_at=two_days_ago,
    )
    event_repo.insert(
        event_type="TaskReopened",
        source="jira",
        external_event_id=None,
        timestamp=two_days_ago,
        payload={"reason": "Customer reopened"},
        actor_name="Customer",
        task_id="OTHER-201"
    )
    # Unassigned candidate from Other Team
    repo.upsert(
        jira_issue_key="OTHER-301",
        summary="Other team unassigned task",
        status="In Progress",
        assignee=None,
        team_group="Other Cluster",
        updated_at=two_days_ago,
    )

    digest = attention_generator.generate_digest()
    assert digest["total_count"] == 0
    assert digest["categories"]["inactive_stalled"]["count"] == 0
    assert digest["categories"]["reopened"]["count"] == 0
    assert digest["categories"]["unassigned"]["count"] == 0


# ==============================================================================
# Requirement 7: Strictly Maximum of 3 Sections in V1 (Categories 4 & 5 Deferred)
# ==============================================================================
def test_v1_categories_strictly_three_sections(attention_generator):
    """V1 digest contains ONLY inactive_stalled, reopened, and unassigned."""
    digest = attention_generator.generate_digest()
    categories = digest["categories"]
    assert set(categories.keys()) == {"inactive_stalled", "reopened", "unassigned"}
    assert "routing_anomalies" not in categories
    assert "worklog_inactivity" not in categories


# ==============================================================================
# Requirement 8: Zero Attention Items Produces Concise Empty State
# ==============================================================================
def test_zero_attention_items_empty_state():
    """When no items require attention, title is green checkmark and no tables are rendered."""
    report_data = {
        "team_name": "Mursaleen Cluster",
        "date": "2026-09-11",
        "formatted_date": "September 11, 2026",
        "total_count": 0,
        "categories": {
            "inactive_stalled": {"count": 0, "tickets": []},
            "reopened": {"count": 0, "tickets": []},
            "unassigned": {"count": 0, "tickets": []},
        }
    }
    payload = DiscordFormatter.format_pm_attention_digest(report_data)
    embed = payload["embeds"][0]
    assert embed["title"] == "✅ Mursaleen Cluster — PM Attention Digest"
    assert embed["color"] == COLOR_GREEN
    assert "No items requiring attention." in embed["description"]
    assert "|" not in embed["description"]  # No tables rendered


# ==============================================================================
# Requirement 9 & 10: Monospace Tables with Clickable Links
# ==============================================================================
def test_discord_table_monospace_and_clickable_links():
    """Embed table rows keep fixed width via inline code while hyperlinked keys remain clickable."""
    report_data = {
        "team_name": "Mursaleen Cluster",
        "date": "2026-09-11",
        "formatted_date": "September 11, 2026",
        "total_count": 3,
        "categories": {
            "inactive_stalled": {
                "title": "🟠 Inactive / Stalled",
                "count": 1,
                "tickets": [{
                    "key": "TASK-101",
                    "url": "https://objectsws.atlassian.net/browse/TASK-101",
                    "status": "In Progress",
                    "updated_at": "Sep 09, 2026",
                    "inactive_for": "2 days"
                }]
            },
            "reopened": {
                "title": "🔁 Reopened",
                "count": 1,
                "tickets": [{
                    "key": "TASK-201",
                    "url": "https://objectsws.atlassian.net/browse/TASK-201",
                    "status": "Waiting for customer",
                    "updated_at": "Sep 10, 2026"
                }]
            },
            "unassigned": {
                "title": "📌 Unassigned",
                "count": 1,
                "tickets": [{
                    "key": "TASK-301",
                    "url": "https://objectsws.atlassian.net/browse/TASK-301",
                    "status": "Unknown",
                    "updated_at": "N/A"
                }]
            },
        }
    }
    payload = DiscordFormatter.format_pm_attention_digest(report_data)
    embed = payload["embeds"][0]
    assert embed["title"] == "⚠️ Mursaleen Cluster — PM Attention Digest"
    assert embed["color"] == COLOR_AMBER
    desc = embed["description"]

    # Verify all 3 section headers present
    assert "🟠 Inactive / Stalled" in desc
    assert "🔁 Reopened" in desc
    assert "📌 Unassigned" in desc

    # Verify clickable Jira links with monospace wrapper
    assert "[TASK-101](https://objectsws.atlassian.net/browse/TASK-101)" in desc
    assert "[TASK-201](https://objectsws.atlassian.net/browse/TASK-201)" in desc
    assert "[TASK-301](https://objectsws.atlassian.net/browse/TASK-301)" in desc


# ==============================================================================
# Requirement 11 & 12: Send Digest Dispatch & Idempotency
# ==============================================================================
@pytest.mark.asyncio
async def test_send_digest_dispatch_and_idempotency(temp_db, attention_generator):
    """Digest dispatches to Discord and respects daily idempotency."""
    repo = JiraIssueStateRepository(temp_db)
    now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000+0000")
    repo.upsert(
        jira_issue_key="TASK-1",
        summary="Unassigned task",
        status="To Do",
        assignee=None,
        team_group=settings.JIRA_TEAM_GROUP,
        updated_at=now_str,
    )

    with patch("app.core.reports.attention_report.action_engine.execute", new_callable=AsyncMock) as mock_exec:
        mock_exec.side_effect = make_simulated_result

        # First run: dispatches successfully
        res1 = await attention_generator.send_digest_to_discord(target_date="2026-09-11", force=False, record_history=True)
        assert res1["status"].lower() in ("dry_run_simulated", "success")
        assert res1["total_count"] == 1
        assert res1["recorded_history"] is True
        assert mock_exec.call_count == 1

        # Second run without force: skipped due to idempotency
        res2 = await attention_generator.send_digest_to_discord(target_date="2026-09-11", force=False)
        assert res2["status"] == "skipped"
        assert res2["reason"] == "already_sent_today"
        assert mock_exec.call_count == 1  # Not called again

        # Third run WITH force: bypasses idempotency
        res3 = await attention_generator.send_digest_to_discord(target_date="2026-09-11", force=True)
        assert res3["status"].lower() in ("dry_run_simulated", "success")
        assert mock_exec.call_count == 2


# ==============================================================================
# Requirement 13: Idempotency Survives Restart (SQLite persistence)
# ==============================================================================
@pytest.mark.asyncio
async def test_idempotency_persists_in_database(temp_db):
    """Idempotency is stored in daily_report_history table and recognized by a new instance."""
    history_repo = DailyReportHistoryRepository(temp_db)
    history_repo.record_report_sent(
        team_group="Mursaleen Cluster",
        report_date="2026-09-11",
        payload={"total_count": 0},
        report_type="pm_attention_digest"
    )

    # Instantiate a brand-new generator to simulate process restart
    new_generator = DailyPMAttentionReportGenerator(manager=temp_db)
    res = await new_generator.send_digest_to_discord(target_date="2026-09-11", force=False)
    assert res["status"] == "skipped"
    assert res["reason"] == "already_sent_today"


# ==============================================================================
# Requirement 14: record_history=False Does Not Consume Schedule
# ==============================================================================
@pytest.mark.asyncio
async def test_send_digest_record_history_false(temp_db, attention_generator):
    """Manual send with record_history=False leaves the scheduled run unconsumed."""
    with patch("app.core.reports.attention_report.action_engine.execute", new_callable=AsyncMock) as mock_exec:
        mock_exec.side_effect = make_simulated_result
        res = await attention_generator.send_digest_to_discord(
            target_date="2026-09-11",
            force=True,
            record_history=False
        )
        assert res["recorded_history"] is False

    history_repo = DailyReportHistoryRepository(temp_db)
    assert not history_repo.has_report_been_sent("Mursaleen Cluster", "2026-09-11", report_type="pm_attention_digest")


# ==============================================================================
# Requirement 15 & 16: Scheduler Timing & Enablement
# ==============================================================================
@pytest.mark.asyncio
async def test_scheduler_evaluates_when_enabled_and_time_reached(temp_db):
    """Scheduler dispatches digest when enabled and current time >= 09:00 Asia/Karachi."""
    scheduler = PeriodicScheduler(manager=temp_db)

    with patch("app.services.scheduler.settings.PM_ATTENTION_DIGEST_ENABLED", True), \
         patch("app.services.scheduler.settings.PM_ATTENTION_DIGEST_TIME", "09:00"), \
         patch("app.core.reports.attention_report.action_engine.execute", new_callable=AsyncMock) as mock_exec:
        mock_exec.side_effect = make_simulated_result

        # Mock current time in Asia/Karachi to 09:15
        fake_now = datetime.datetime(2026, 9, 11, 9, 15, tzinfo=zoneinfo.ZoneInfo("Asia/Karachi"))
        with patch("app.services.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            res = await scheduler._evaluate_daily_pm_attention_digest()
            assert res is not None
            assert res["status"].lower() in ("dry_run_simulated", "success")
            assert res["team_name"] == "Mursaleen Cluster"


@pytest.mark.asyncio
async def test_scheduler_does_not_evaluate_when_disabled(temp_db):
    """Scheduler returns None when PM_ATTENTION_DIGEST_ENABLED is False."""
    scheduler = PeriodicScheduler(manager=temp_db)
    with patch("app.services.scheduler.settings.PM_ATTENTION_DIGEST_ENABLED", False):
        res = await scheduler._evaluate_daily_pm_attention_digest()
        assert res is None


@pytest.mark.asyncio
async def test_scheduler_does_not_evaluate_before_time(temp_db):
    """Scheduler returns None when current time < 09:00 Asia/Karachi."""
    scheduler = PeriodicScheduler(manager=temp_db)
    with patch("app.services.scheduler.settings.PM_ATTENTION_DIGEST_ENABLED", True), \
         patch("app.services.scheduler.settings.PM_ATTENTION_DIGEST_TIME", "09:00"):
        fake_now = datetime.datetime(2026, 9, 11, 8, 45, tzinfo=zoneinfo.ZoneInfo("Asia/Karachi"))
        with patch("app.services.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            res = await scheduler._evaluate_daily_pm_attention_digest()
            assert res is None


# ==============================================================================
# Requirement 17 & 18: Notification Suppression (Polling & Periodic cycle never send individual alerts)
# ==============================================================================
@pytest.mark.asyncio
async def test_stale_task_rule_suppresses_individual_discord_notification(temp_db):
    """StaleTaskRule does not produce Discord actions when STALE_TASK_NOTIFY_PM=False."""
    rule = StaleTaskRule()
    event = StaleTask(
        task_id="TASK-999",
        task_key="TASK-999",
        project_key="TASK",
        hours_inactive=48.0,
        current_status="In Progress",
        assignee_id=None,
        threshold_hours=24
    )
    with patch("app.core.rules.builtin.settings.STALE_TASK_NOTIFY_PM", False):
        actions = rule.evaluate(event)
        # Should not generate any Discord PM alert actions
        discord_actions = [a for a in actions if getattr(a, "target_system", None) == "discord"]
        assert len(discord_actions) == 0


# ==============================================================================
# Requirement 19 & 20: API Endpoints (GET read-only preview, POST manual send)
# ==============================================================================
@pytest.mark.asyncio
async def test_api_get_pm_attention_preview(temp_db):
    """GET /test/report/pm-attention returns JSON preview without sending notifications."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/test/report/pm-attention?date=2026-09-11")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["diagnostic_mode"] == "DEVELOPMENT_TESTING_READ_ONLY"
        assert "digest" in data
        assert "categories" in data["digest"]
        assert set(data["digest"]["categories"].keys()) == {"inactive_stalled", "reopened", "unassigned"}


@pytest.mark.asyncio
async def test_api_post_pm_attention_send(temp_db):
    """POST /test/report/pm-attention/send triggers manual send via ActionEngine."""
    with patch("app.core.reports.attention_report.action_engine.execute", new_callable=AsyncMock) as mock_exec:
        mock_exec.side_effect = make_simulated_result
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post(
                "/test/report/pm-attention/send",
                json={"date": "2026-09-11", "force": True, "record_history": False}
            )
            assert response.status_code == 200
            data = response.json()
            assert data["status"].lower() in ("dry_run_simulated", "success")
            assert data["recorded_history"] is False


# ==============================================================================
# Requirement 21: calculate_inactive_for duration logic
# ==============================================================================
def test_calculate_inactive_for():
    """Inactive duration calculation handles various deltas properly."""
    ref = datetime.datetime(2026, 9, 11, 10, 0, tzinfo=datetime.timezone.utc)

    # 1 day ago
    dt1 = "2026-09-10T10:00:00.000+0000"
    assert calculate_inactive_for(dt1, reference_dt=ref) == "1 day"

    # 5 days ago
    dt5 = "2026-09-06T10:00:00.000+0000"
    assert calculate_inactive_for(dt5, reference_dt=ref) == "5 days"

    # 12 hours ago
    dt12h = "2026-09-10T22:00:00.000+0000"
    assert calculate_inactive_for(dt12h, reference_dt=ref) == "12h"

    # None or invalid
    assert calculate_inactive_for(None) == "N/A"
    assert calculate_inactive_for("") == "N/A"
    assert calculate_inactive_for("invalid-date") == "N/A"
