"""Unit and integration tests for the /test/discord-notification testing endpoint."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import AsyncClient, ASGITransport
from app.api.app import app
from app.config.settings import Settings, settings
from app.database.repositories import ActionRepository


@pytest.mark.asyncio
async def test_discord_test_notification_dry_run(temp_db):
    """Test POST /test/discord-notification under DRY_RUN=True simulates cleanly."""
    dry_run_settings = Settings(
        DRY_RUN=True,
        PM_DISCORD_CHANNEL="test-alerts",
        DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/mock"
    )

    with patch("app.api.routes.test_notifications.settings", dry_run_settings), \
         patch("app.core.actions.engine.settings", dry_run_settings), \
         patch("app.core.actions.engine.action_engine.mgr", temp_db), \
         patch("app.core.actions.engine.action_engine.action_repo", ActionRepository(temp_db)):

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.post("/test/discord-notification", json={
                "title": "Dry Run Test Alert",
                "message": "Testing ActionEngine pipeline in dry-run mode.",
                "level": "INFO"
            })

        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "success"
        assert data["dry_run"] is True
        assert data["action_status"] == "DRY_RUN_SIMULATED"
        assert data["target_system"] == "discord"
        assert "simulated" in data["message"].lower()

        # Verify action record was persisted in the ActionRepository
        with temp_db.session() as conn:
            cursor = conn.execute("SELECT * FROM actions WHERE action_id = ?", (data["action_id"],))
            row = cursor.fetchone()
            assert row is not None
            assert row["action_type"] == "SendNotification"
            assert row["status"] == "DRY_RUN_SIMULATED"
            assert row["dry_run"] == 1


@pytest.mark.asyncio
async def test_discord_test_notification_live_dispatch(temp_db):
    """Test POST /test/discord-notification under DRY_RUN=False calls Discord connector."""
    live_settings = Settings(
        DRY_RUN=False,
        PM_DISCORD_CHANNEL="test-alerts",
        DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/valid-webhook"
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 204
    mock_http_client = AsyncMock()
    mock_http_client.post = AsyncMock(return_value=mock_resp)

    with patch("app.api.routes.test_notifications.settings", live_settings), \
         patch("app.core.actions.engine.settings", live_settings), \
         patch("app.core.actions.engine.action_engine.mgr", temp_db), \
         patch("app.core.actions.engine.action_engine.action_repo", ActionRepository(temp_db)), \
         patch("app.connectors.discord.webhook_connector.DiscordWebhookConnector._get_client", return_value=mock_http_client):

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.post("/test/discord-notification", json={
                "title": "Live Test Alert",
                "message": "Testing real Discord webhook execution.",
                "level": "WARNING"
            })

        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "success"
        assert data["dry_run"] is False
        assert data["action_status"] == "COMPLETED"
        assert data["target_system"] == "discord"
        assert mock_http_client.post.called


@pytest.mark.asyncio
async def test_discord_test_notification_default_body(temp_db):
    """Test POST /test/discord-notification with empty/omitted payload uses safe defaults."""
    dry_run_settings = Settings(
        DRY_RUN=True,
        PM_DISCORD_CHANNEL="pm-alerts",
        DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/mock"
    )

    with patch("app.api.routes.test_notifications.settings", dry_run_settings), \
         patch("app.core.actions.engine.settings", dry_run_settings), \
         patch("app.core.actions.engine.action_engine.mgr", temp_db), \
         patch("app.core.actions.engine.action_engine.action_repo", ActionRepository(temp_db)):

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.post("/test/discord-notification")

        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "success"
        assert data["target_id"] == "pm-alerts"
        assert data["dry_run"] is True


@pytest.mark.asyncio
async def test_discord_test_notification_does_not_modify_jira(temp_db):
    """Ensure POST /test/discord-notification has no side-effects on Jira state or poller."""
    dry_run_settings = Settings(
        DRY_RUN=True,
        PM_DISCORD_CHANNEL="test-alerts"
    )

    with patch("app.api.routes.test_notifications.settings", dry_run_settings), \
         patch("app.core.actions.engine.settings", dry_run_settings), \
         patch("app.core.actions.engine.action_engine.mgr", temp_db), \
         patch("app.core.actions.engine.action_engine.action_repo", ActionRepository(temp_db)):

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            await ac.post("/test/discord-notification", json={"title": "No Jira Impact"})

        # Verify no jira_issue_state rows were written
        with temp_db.session() as conn:
            cursor = conn.execute("SELECT COUNT(*) as count FROM jira_issue_state")
            row = cursor.fetchone()
            assert row["count"] == 0


@pytest.mark.asyncio
async def test_scheduler_scoping_diagnostic_endpoint(temp_db):
    """Test GET /test/diagnostic/scheduler-scoping correctly filters candidates to configured team."""
    import datetime
    from app.utils.time import format_iso, utc_now
    from app.database.repositories import JiraIssueStateRepository

    diagnostic_settings = Settings(
        JIRA_TEAM_GROUP="Mursaleen Cluster",
        STALE_TASK_HOURS=24
    )

    repo = JiraIssueStateRepository(temp_db)
    thirty_hours_ago = format_iso(utc_now() - datetime.timedelta(hours=30))
    yesterday = (utc_now() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")

    # 1. Matching team stale candidate
    repo.upsert(
        jira_issue_key="TEAM-STALE-1",
        summary="Team Stale Issue",
        status="In Progress",
        assignee="Dev A",
        last_seen_at=thirty_hours_ago,
        last_activity_at=thirty_hours_ago,
        team_group="Mursaleen Cluster"
    )

    # 2. Other team stale issue (should NOT be included in candidates)
    repo.upsert(
        jira_issue_key="OTHER-STALE-2",
        summary="Other Team Stale Issue",
        status="In Progress",
        assignee="Dev B",
        last_seen_at=thirty_hours_ago,
        last_activity_at=thirty_hours_ago,
        team_group="Other Cluster"
    )

    # 3. NULL team stale issue (should NOT be included in candidates)
    repo.upsert(
        jira_issue_key="LEGACY-STALE-3",
        summary="Legacy Stale Issue",
        status="In Progress",
        assignee="Dev C",
        last_seen_at=thirty_hours_ago,
        last_activity_at=thirty_hours_ago,
        team_group=None
    )

    # 4. Matching team overdue candidate
    repo.upsert(
        jira_issue_key="TEAM-OVERDUE-4",
        summary="Team Overdue Issue",
        status="To Do",
        assignee="Dev D",
        due_date=yesterday,
        last_seen_at=format_iso(utc_now()),
        last_activity_at=format_iso(utc_now()),
        team_group="Mursaleen Cluster"
    )

    # 5. Other team overdue issue (should NOT be included in candidates)
    repo.upsert(
        jira_issue_key="OTHER-OVERDUE-5",
        summary="Other Overdue Issue",
        status="To Do",
        assignee="Dev E",
        due_date=yesterday,
        last_seen_at=format_iso(utc_now()),
        last_activity_at=format_iso(utc_now()),
        team_group="Other Cluster"
    )

    # 6. NULL team overdue issue (should NOT be included in candidates)
    repo.upsert(
        jira_issue_key="LEGACY-OVERDUE-6",
        summary="Legacy Overdue Issue",
        status="To Do",
        assignee="Dev F",
        due_date=yesterday,
        last_seen_at=format_iso(utc_now()),
        last_activity_at=format_iso(utc_now()),
        team_group=None
    )

    from app.api.routes.test_notifications import get_scheduler_scoping_diagnostic

    with patch("app.api.routes.test_notifications.settings", diagnostic_settings), \
         patch("app.api.routes.test_notifications.db_manager", temp_db):

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.get("/test/diagnostic/scheduler-scoping")

        assert res.status_code == 200
        data = res.json()

        # Check configuration & summary
        assert data["configured_jira_team_group"] == "Mursaleen Cluster"
        assert data["team_scoping_enabled"] is True

        summary = data["database_projection_summary"]
        assert summary["total_rows"] == 6
        assert summary["rows_matching_team_group"] == 2
        assert summary["rows_with_different_team_group"] == 2
        assert summary["rows_with_null_team_group"] == 2

        # Check stale candidate filtering: only TEAM-STALE-1
        assert data["stale_candidates_count"] == 1
        stale_keys = [c["jira_issue_key"] for c in data["stale_candidates"]]
        assert stale_keys == ["TEAM-STALE-1"]
        assert data["stale_candidates"][0]["team_group"] == "Mursaleen Cluster"
        assert "qualification_reason" in data["stale_candidates"][0]

        # Check overdue candidate filtering: only TEAM-OVERDUE-4
        assert data["overdue_candidates_count"] == 1
        overdue_keys = [c["jira_issue_key"] for c in data["overdue_candidates"]]
        assert overdue_keys == ["TEAM-OVERDUE-4"]
        assert data["overdue_candidates"][0]["team_group"] == "Mursaleen Cluster"
        assert "qualification_reason" in data["overdue_candidates"][0]


@pytest.mark.asyncio
async def test_get_daily_worklog_report_endpoint(temp_db):
    """Test read-only preview endpoint GET /test/report/daily-worklog."""
    from app.database.repositories import JiraWorklogRepository
    from app.core.reports.worklog_report import daily_worklog_report_generator

    repo = JiraWorklogRepository(temp_db)
    repo.upsert_worklog(
        worklog_id="wl-endpoint-1",
        jira_issue_key="WSSS-326",
        time_spent_seconds=7200,
        started_at="2026-09-10T09:00:00Z",
        author_account_id="acc-1",
        author_display_name="Mubashir Butt",
        team_group="Mursaleen Cluster"
    )

    with patch.object(daily_worklog_report_generator, "mgr", temp_db), \
         patch.object(daily_worklog_report_generator, "worklog_repo", repo):

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.get("/test/report/daily-worklog?date=2026-09-10&sync_jira=false")

        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "success"
        assert data["diagnostic_mode"] == "DEVELOPMENT_TESTING_READ_ONLY"
        rep = data["report"]
        assert rep["report_date"] == "2026-09-10"
        assert rep["total_time_human"] == "2h"
        assert rep["members_logged_count"] == 1
        assert rep["tickets_worked_count"] == 1


@pytest.mark.asyncio
async def test_send_daily_worklog_report_endpoint(temp_db):
    """Test dispatch endpoint POST /test/report/daily-worklog/send."""
    from app.database.repositories import JiraWorklogRepository, DailyReportHistoryRepository
    from app.core.reports.worklog_report import daily_worklog_report_generator

    repo = JiraWorklogRepository(temp_db)
    hist_repo = DailyReportHistoryRepository(temp_db)
    repo.upsert_worklog(
        worklog_id="wl-endpoint-2",
        jira_issue_key="WSSS-301",
        time_spent_seconds=3600,
        started_at="2026-09-10T10:00:00Z",
        author_account_id="acc-2",
        author_display_name="Ahmed Raza",
        team_group="Mursaleen Cluster"
    )

    prev_dry_run = settings.DRY_RUN
    settings.DRY_RUN = True

    with patch.object(daily_worklog_report_generator, "mgr", temp_db), \
         patch.object(daily_worklog_report_generator, "worklog_repo", repo), \
         patch.object(daily_worklog_report_generator, "history_repo", hist_repo):

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.post("/test/report/daily-worklog/send", json={
                "date": "2026-09-10",
                "force": False,
                "sync_jira": False
            })

        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "sent"
        assert data["report"]["report_date"] == "2026-09-10"
        assert data["report"]["total_time_human"] == "1h"

        # Verify idempotency via endpoint
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res_repeat = await ac.post("/test/report/daily-worklog/send", json={
                "date": "2026-09-10",
                "force": False,
                "sync_jira": False
            })

        assert res_repeat.status_code == 200
        data_repeat = res_repeat.json()
        assert data_repeat["status"] == "skipped"
        assert data_repeat["reason"] == "already_sent_today"

    settings.DRY_RUN = prev_dry_run


