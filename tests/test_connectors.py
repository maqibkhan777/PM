"""Unit tests for Jira, Discord, and Mattermost connectors with mocked HTTP clients."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.connectors.jira import JiraConnector, JiraClient
from app.connectors.discord import DiscordWebhookConnector, DiscordBotConnector, DiscordFormatter
from app.connectors.mattermost import MattermostConnector, MattermostClient
from app.core.actions.base import BaseAction
from app.core.models.enums import ActionType, Capability, SecurityLevel


@pytest.mark.asyncio
async def test_jira_connector_mock_actions():
    """Test JiraConnector action dispatch with mocked JiraClient."""
    mock_client = MagicMock(spec=JiraClient)
    mock_client.transition_issue = AsyncMock(return_value={"status": "transitioned"})
    mock_client.assign_issue = AsyncMock(return_value={"status": "assigned"})
    mock_client.add_comment = AsyncMock(return_value={"id": "c-123"})
    mock_client.get_transitions = AsyncMock(return_value=[{"id": "31", "name": "In Progress"}])

    connector = JiraConnector(client=mock_client)

    # Test Transition action
    action = BaseAction(
        action_type=ActionType.TRANSITION_TASK,
        target_system="jira",
        target_id="CF7-421",
        parameters={"status": "In Progress"}
    )
    res = await connector.execute_action(action)
    assert res == {"status": "transitioned"}
    mock_client.transition_issue.assert_called_once_with("CF7-421", "31")

    # Test Add Comment action
    comment_action = BaseAction(
        action_type=ActionType.ADD_COMMENT,
        target_system="jira",
        target_id="CF7-421",
        parameters={"comment": "Test Comment"}
    )
    comment_res = await connector.execute_action(comment_action)
    assert comment_res == {"id": "c-123"}
    mock_client.add_comment.assert_called_once_with("CF7-421", "Test Comment")


@pytest.mark.asyncio
async def test_discord_webhook_formatter():
    """Test Discord rich embed formatter structures."""
    embed = DiscordFormatter.format_workflow_violation(
        task_key="CF7-421",
        task_title="Payment Gateway",
        resource_name="Ahsan Amin",
        project_name="CF7 Apps",
        details="Activity on To Do task"
    )
    assert "embeds" in embed
    emb_data = embed["embeds"][0]
    assert "🚨 Jira Workflow Alert" in emb_data["title"]
    assert len(emb_data["fields"]) >= 4


@pytest.mark.asyncio
async def test_mattermost_connector_mock_dm():
    """Test MattermostConnector direct message action with mocked MattermostClient."""
    mock_client = MagicMock(spec=MattermostClient)
    mock_client.send_direct_message = AsyncMock(return_value={"id": "post-999", "message": "hello"})

    connector = MattermostConnector(client=mock_client)

    action = BaseAction(
        action_type=ActionType.SEND_MESSAGE,
        target_system="mattermost",
        target_id="mm-user-123",
        parameters={"text": "Hey Ahsan, update your task please.", "recipient_id": "mm-user-123"}
    )

    res = await connector.execute_action(action)
    assert res == {"id": "post-999", "message": "hello"}
    mock_client.send_direct_message.assert_called_once_with(
        target_user_id="mm-user-123",
        message="Hey Ahsan, update your task please."
    )


def test_capabilities_and_security_levels():
    """Test that all connector capabilities map to proper security levels."""
    jira = JiraConnector()
    caps = jira.get_capabilities()
    assert Capability.READ_TASK in caps
    assert Capability.TRANSITION_TASK in caps

    # Read should be READ, Transition should be WRITE
    assert jira.get_security_level_for_capability(Capability.READ_TASK) == SecurityLevel.READ
    assert jira.get_security_level_for_capability(Capability.TRANSITION_TASK) == SecurityLevel.WRITE
    assert jira.get_security_level_for_capability(Capability.DELETE_TASK) == SecurityLevel.DESTRUCTIVE
