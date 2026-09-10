"""Comprehensive tests for Optional Mattermost integration."""

import pytest
from unittest.mock import AsyncMock, patch
from app.config.settings import Settings, settings
from app.connectors.mattermost import MattermostConnector
from app.core.actions.engine import ActionEngine
from app.core.actions.types import create_send_message_action, create_send_notification_action
from app.core.models.enums import ActionStatus
from app.core.rules.builtin import StaleTaskRule
from app.core.events.types import StaleTask
from app.services.user_mapping_service import UserMappingService
from app.connectors.discord import DiscordWebhookConnector


@pytest.mark.asyncio
async def test_mattermost_unconfigured_startup():
    """Test 1: When no credentials exist, application starts cleanly with not_configured status."""
    test_settings = Settings(
        MATTERMOST_URL=None,
        MATTERMOST_TOKEN=None,
        MATTERMOST_TEAM_NAME=None
    )
    with patch("app.connectors.mattermost.connector.settings", test_settings):
        connector = MattermostConnector()
        assert connector.is_configured is False

        # Connect should return False without making any HTTP calls
        with patch.object(connector.client, "get_me", new_callable=AsyncMock) as mock_get_me:
            connected = await connector.connect()
            assert connected is False
            mock_get_me.assert_not_called()

        health = await connector.health_check()
        assert health.status == "NOT_CONFIGURED"
        assert health.is_connected is False
        assert health.details["status"] == "not_configured"
        assert health.details["configured"] is False


@pytest.mark.asyncio
async def test_mattermost_action_skipped_when_unconfigured(temp_db):
    """Test 2: A Mattermost action executed without credentials is safely SKIPPED with reason."""
    test_settings = Settings(
        MATTERMOST_URL=None,
        MATTERMOST_TOKEN=None
    )
    with patch("app.core.actions.engine.settings", test_settings), \
         patch("app.connectors.mattermost.connector.settings", test_settings):
        engine = ActionEngine(manager=temp_db)
        connector = MattermostConnector()
        engine.register_connector(connector)

        action = create_send_message_action(
            target_system="mattermost",
            target_id="jira-user-999",
            text="Hello from PM Agent",
            recipient_name="Developer"
        )

        result = await engine.execute(action)

        assert result.success is True
        assert result.status == ActionStatus.SKIPPED
        assert result.error_message == "connector_not_configured"
        assert result.result_data.get("skipped") is True
        assert result.result_data.get("reason") == "connector_not_configured"

        # Verify in DB
        db_action = engine.action_repo.get_by_id(action.action_id)
        assert db_action["status"] == ActionStatus.SKIPPED.value
        assert db_action["last_error"] == "connector_not_configured"


@pytest.mark.asyncio
async def test_discord_and_mattermost_rule_pipeline(temp_db):
    """Test 3: StaleTaskRule produces Discord + Mattermost actions; Discord succeeds while Mattermost is skipped."""
    test_settings = Settings(
        DRY_RUN=True,
        MATTERMOST_URL=None,
        MATTERMOST_TOKEN=None,
        DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/mocked",
        STALE_TASK_NOTIFY_PM=True,
        STALE_TASK_NOTIFY_ASSIGNEE=True
    )
    with patch("app.core.actions.engine.settings", test_settings), \
         patch("app.connectors.mattermost.connector.settings", test_settings), \
         patch("app.core.rules.builtin.settings", test_settings):
        engine = ActionEngine(manager=temp_db)
        mm_conn = MattermostConnector()
        discord_conn = DiscordWebhookConnector()
        engine.register_connector(mm_conn)
        engine.register_connector(discord_conn)

        rule = StaleTaskRule()
        stale_event = StaleTask(
            source="scheduler",
            task_key="PROJ-101",
            task_title="Important Feature",
            assignee_id="jira-user-123",
            assignee_name="Alice Developer",
            hours_inactive=36.0,
            threshold_hours=24
        )

        actions = rule.evaluate(stale_event)
        assert len(actions) == 2  # 1 Discord + 1 Mattermost

        results = []
        for act in actions:
            res = await engine.execute(act)
            results.append(res)

        # Discord action simulated in dry-run
        discord_res = next(r for r in results if r.target_system == "discord")
        assert discord_res.success is True
        assert discord_res.status == ActionStatus.DRY_RUN_SIMULATED

        # Mattermost action cleanly skipped
        mm_res = next(r for r in results if r.target_system == "mattermost")
        assert mm_res.success is True
        assert mm_res.status == ActionStatus.SKIPPED
        assert mm_res.error_message == "connector_not_configured"


@pytest.mark.asyncio
async def test_mattermost_configured_later():
    """Test 4: Adding credentials allows Mattermost connector to initialize without code changes."""
    configured_settings = Settings(
        MATTERMOST_URL="https://mattermost.corporate.internal",
        MATTERMOST_TOKEN="real-token-abc",
        MATTERMOST_TEAM_NAME="engineering"
    )
    with patch("app.connectors.mattermost.connector.settings", configured_settings):
        connector = MattermostConnector()
        assert connector.is_configured is True

        with patch.object(connector.client, "get_me", new_callable=AsyncMock) as mock_get_me:
            mock_get_me.return_value = {"id": "mm-bot-1", "username": "pm-bot"}
            connected = await connector.connect()
            assert connected is True
            assert connector.is_connected is True

            health = await connector.health_check()
            assert health.status == "OK"
            assert health.is_connected is True
            assert health.details["status"] == "connected"
            assert health.details["configured"] is True


def test_user_mapping_never_guesses(temp_db):
    """Test 5: User mapping service never guesses unmapped users."""
    service = UserMappingService(manager=temp_db)
    resolved_id, method = service.resolve_jira_to_mattermost(
        jira_user_id="jira-random-unmapped",
        display_name="Random Person",
        email="nobody@nowhere.com"
    )
    assert resolved_id is None
    assert method == "UNRESOLVED"
