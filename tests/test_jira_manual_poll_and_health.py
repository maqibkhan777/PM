"""Integration tests for POST /jira/poll, health status, and end-to-end polling pipeline."""

import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, patch
from app.api.app import app
from app.config.settings import Settings
from app.utils.time import utc_now_iso


@pytest.mark.asyncio
async def test_manual_jira_poll_endpoint():
    """Test POST /jira/poll returns stats and does not leak secrets."""
    mock_result = {
        "status": "completed",
        "issues_scanned": 12,
        "events_generated": 3,
        "duplicates_skipped": 2
    }

    with patch("app.services.orchestrator.orchestrator.jira_poller.poll", new_callable=AsyncMock) as mock_poll:
        mock_poll.return_value = mock_result
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post("/jira/poll")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"
        assert data["issues_scanned"] == 12
        assert data["events_generated"] == 3
        assert data["duplicates_skipped"] == 2
        assert "token" not in response.text.lower()
        assert "secret" not in response.text.lower()


@pytest.mark.asyncio
async def test_health_connectors_distinction():
    """Test GET /health/connectors clearly distinguishes Jira, Mattermost, and Discord states."""
    test_settings = Settings(
        MATTERMOST_URL=None,
        MATTERMOST_TOKEN=None,
        JIRA_POLLING_ENABLED=True
    )
    with patch("app.config.settings.settings", test_settings), \
         patch("app.connectors.mattermost.connector.settings", test_settings), \
         patch("app.api.routes.health.settings", test_settings):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/health/connectors")

        assert response.status_code == 200
        data = response.json()

        # Jira section
        assert "jira" in data
        assert data["jira"]["polling_enabled"] is True
        assert "polling_status" in data["jira"]

        # Mattermost section
        assert "mattermost" in data
        assert data["mattermost"]["configured"] is False
        assert data["mattermost"]["connected"] is False
        assert data["mattermost"]["status"] == "not_configured"

        # Discord section
        assert "discord" in data


@pytest.mark.asyncio
async def test_end_to_end_polling_to_action_pipeline(temp_db):
    """End-to-End Test:

    Mock Jira API
      ↓
    Jira Poller
      ↓
    Change Detection
      ↓
    Normalized Event
      ↓
    Event Bus
      ↓
    Rules Engine
      ↓
    Action Engine (Dry Run)
      ↓
    Discord simulated + Mattermost safely skipped
    """
    now_iso = utc_now_iso()
    test_settings = Settings(
        DRY_RUN=True,
        JIRA_BASE_URL="https://company.atlassian.net",
        JIRA_EMAIL="pm@company.com",
        JIRA_API_TOKEN="token-xyz",
        MATTERMOST_URL=None,
        MATTERMOST_TOKEN=None,
        DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/valid-webhook",
        JIRA_TEAM_GROUP="Engineering Team"
    )

    # Issue with comment on 'To Do' status (triggers ActiveWorkRule!)
    issue_payload = {
        "id": "2001",
        "key": "E2E-10",
        "fields": {
            "summary": "Implement Billing Flow",
            "status": {"name": "To Do"},
            "updated": now_iso,
            "project": {"key": "E2E", "id": "proj-e2e"},
            "comment": {
                "comments": [
                    {
                        "id": "c-901",
                        "created": now_iso,
                        "author": {"displayName": "Dev Bob", "accountId": "acc-bob"},
                        "body": "Started working on billing database models"
                    }
                ]
            }
        }
    }

    with patch("app.connectors.jira.poller.settings", test_settings), \
         patch("app.core.actions.engine.settings", test_settings), \
         patch("app.connectors.mattermost.connector.settings", test_settings):

        from app.services.orchestrator import SystemOrchestrator
        from app.connectors.jira.client import JiraClient

        mock_client = JiraClient()
        mock_client.search_issues = AsyncMock(return_value={"issues": [issue_payload], "total": 1})

        import time
        unique_key = f"E2E-{int(time.time() * 1000)}"
        issue_payload["key"] = unique_key

        from app.database.repositories import ActionRepository, NotificationRepository
        from app.services.notification_deduplication import notification_dedup_service
        orch = SystemOrchestrator(manager=temp_db)
        orch.jira_poller.client = mock_client

        with patch("app.services.orchestrator.orchestrator", orch), \
             patch("app.core.actions.engine.action_engine.mgr", temp_db), \
             patch("app.core.actions.engine.action_engine.action_repo", ActionRepository(temp_db)), \
             patch.object(notification_dedup_service, "repo", NotificationRepository(temp_db)):
            await orch.initialize()

            # Run poller
            poll_result = await orch.jira_poller.poll()

            assert poll_result["status"] == "completed"
            assert poll_result["issues_scanned"] == 1
            assert poll_result["events_generated"] == 1

            # Check action repository for resulting actions dispatched by ActiveWorkRule
            with temp_db.session() as conn:
                cursor = conn.execute(f"SELECT * FROM actions WHERE target_id = '{unique_key}' OR target_id = 'pm-alerts'")
                rows = cursor.fetchall()
                assert len(rows) >= 1
                status = rows[0]["status"]
                assert status in ("DRY_RUN_SIMULATED", "COMPLETED", "APPROVED")
