"""Regression tests verifying strict Mattermost self-only recipient guard and channel blocking."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.config.settings import Settings, settings
from app.connectors.mattermost.client import MattermostClient
from app.connectors.mattermost.connector import MattermostConnector
from app.core.actions.engine import ActionEngine
from app.core.actions.types import create_send_message_action
from app.core.models.enums import ActionStatus, ActionType
from app.core.actions.base import BaseAction
from app.database.repositories import UserMappingRepository, UserRepository


SELF_USER_ID = "yp7quu37tf8p5enxr9rr8gsqay"
OTHER_USER_ID = "mm-other-user-999"


# ------------------------------------------------------------------------------
# 1. MattermostClient Boundary Tests
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_client_send_dm_to_self_allowed():
    """Client allows DM to verified self account and creates direct post."""
    client = MattermostClient()
    client._bot_user_id = "bot-id-1"
    
    with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
        mock_req.side_effect = [
            {"id": "direct-channel-123"},  # create_direct_channel response
            {"id": "post-456", "message": "hello self"}  # create_post response
        ]
        
        result = await client.send_direct_message(target_user_id=SELF_USER_ID, message="hello self")
        assert result == {"id": "post-456", "message": "hello self"}
        assert mock_req.call_count == 2
        
        # Verify call arguments
        channel_call = mock_req.call_args_list[0]
        assert channel_call.args == ("POST", "/channels/direct")
        assert channel_call.kwargs["json_data"] == ["bot-id-1", SELF_USER_ID]
        
        post_call = mock_req.call_args_list[1]
        assert post_call.args == ("POST", "/posts")
        assert post_call.kwargs["json_data"]["channel_id"] == "direct-channel-123"
        assert post_call.kwargs["json_data"]["message"] == "hello self"


@pytest.mark.asyncio
async def test_client_send_dm_to_other_user_rejected_before_network():
    """Client rejects DM to any other user ID with PermissionError and makes ZERO network requests."""
    client = MattermostClient()
    
    with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
        with pytest.raises(PermissionError) as exc_info:
            await client.send_direct_message(target_user_id=OTHER_USER_ID, message="unauthorized message")
            
        assert "blocked" in str(exc_info.value)
        assert SELF_USER_ID in str(exc_info.value)
        mock_req.assert_not_called()


@pytest.mark.asyncio
async def test_client_create_post_channel_message_blocked():
    """Client rejects all channel messages with PermissionError and makes ZERO network requests."""
    client = MattermostClient()
    
    with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
        with pytest.raises(PermissionError) as exc_info:
            await client.create_post(channel_id="town-square", message="broadcast announcement")
            
        assert "channel messaging is blocked" in str(exc_info.value)
        mock_req.assert_not_called()


# ------------------------------------------------------------------------------
# 2. MattermostConnector Boundary Tests
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_connector_execute_dm_to_self_success():
    """Connector executes DM to self using underlying client."""
    mock_client = MagicMock(spec=MattermostClient)
    mock_client.send_direct_message = AsyncMock(return_value={"id": "post-789", "message": "reminder"})
    
    connector = MattermostConnector(client=mock_client)
    
    action = BaseAction(
        action_type=ActionType.SEND_MESSAGE,
        target_system="mattermost",
        target_id=SELF_USER_ID,
        parameters={"text": "reminder", "recipient_id": SELF_USER_ID}
    )
    
    res = await connector.execute_action(action)
    assert res == {"id": "post-789", "message": "reminder"}
    mock_client.send_direct_message.assert_called_once_with(
        target_user_id=SELF_USER_ID,
        message="reminder"
    )


@pytest.mark.asyncio
async def test_connector_execute_dm_to_other_user_rejected():
    """Connector rejects DM targeting any other recipient with PermissionError."""
    mock_client = MagicMock(spec=MattermostClient)
    connector = MattermostConnector(client=mock_client)
    
    action = BaseAction(
        action_type=ActionType.SEND_MESSAGE,
        target_system="mattermost",
        target_id=OTHER_USER_ID,
        parameters={"text": "reminder", "recipient_id": OTHER_USER_ID}
    )
    
    with pytest.raises(PermissionError) as exc_info:
        await connector.execute_action(action)
        
    assert "blocked" in str(exc_info.value)
    mock_client.send_direct_message.assert_not_called()
    mock_client.create_post.assert_not_called()


@pytest.mark.asyncio
async def test_connector_execute_channel_post_rejected():
    """Connector rejects channel post with PermissionError."""
    mock_client = MagicMock(spec=MattermostClient)
    connector = MattermostConnector(client=mock_client)
    
    action = BaseAction(
        action_type=ActionType.SEND_MESSAGE,
        target_system="mattermost",
        target_id="general-channel",
        parameters={"text": "channel broadcast", "channel_id": "general-channel"}
    )
    
    with pytest.raises(PermissionError) as exc_info:
        await connector.execute_action(action)
        
    assert "channel messaging is blocked" in str(exc_info.value)
    mock_client.send_direct_message.assert_not_called()
    mock_client.create_post.assert_not_called()


# ------------------------------------------------------------------------------
# 3. ActionEngine Pipeline Integration Tests
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_engine_dm_to_mapped_self_simulates_in_dry_run(temp_db):
    """When a Jira user maps to self ID, ActionEngine resolves mapping and safely simulates in dry-run."""
    mapping_repo = UserMappingRepository(temp_db)
    mapping_repo.upsert_mapping(
        jira_user_id="jira-aqib",
        mattermost_user_id=SELF_USER_ID,
        display_name="Aqib Khan"
    )
    
    test_settings = Settings(
        DRY_RUN=True,
        MATTERMOST_URL="https://chat.objects.ws",
        MATTERMOST_TOKEN="mock-token-123",
        MATTERMOST_SELF_ONLY_USER_ID=SELF_USER_ID
    )
    
    with patch("app.core.actions.engine.settings", test_settings), \
         patch("app.connectors.mattermost.connector.settings", test_settings), \
         patch("app.connectors.mattermost.client.settings", test_settings):
        
        mock_client = MagicMock(spec=MattermostClient)
        connector = MattermostConnector(client=mock_client)
        
        engine = ActionEngine(manager=temp_db)
        engine.register_connector(connector)
        
        action = create_send_message_action(
            target_system="mattermost",
            target_id="jira-aqib",
            text="Your task is stale",
            recipient_name="Aqib Khan"
        )
        
        result = await engine.execute(action)
        assert result.success is True
        assert result.status == ActionStatus.DRY_RUN_SIMULATED
        assert result.dry_run is True
        
        # Zero network calls made in dry run
        mock_client.send_direct_message.assert_not_called()
        mock_client.create_post.assert_not_called()


@pytest.mark.asyncio
async def test_engine_dm_to_other_mapped_user_is_rejected(temp_db):
    """When a Jira user maps to someone else's MM ID, ActionEngine REJECTS the action before execution."""
    mapping_repo = UserMappingRepository(temp_db)
    mapping_repo.upsert_mapping(
        jira_user_id="jira-other-dev",
        mattermost_user_id=OTHER_USER_ID,
        display_name="Other Developer"
    )
    
    test_settings = Settings(
        DRY_RUN=False,
        MATTERMOST_URL="https://chat.objects.ws",
        MATTERMOST_TOKEN="mock-token-123",
        MATTERMOST_SELF_ONLY_USER_ID=SELF_USER_ID
    )
    
    with patch("app.core.actions.engine.settings", test_settings), \
         patch("app.connectors.mattermost.connector.settings", test_settings), \
         patch("app.connectors.mattermost.client.settings", test_settings):
        
        mock_client = MagicMock(spec=MattermostClient)
        connector = MattermostConnector(client=mock_client)
        
        engine = ActionEngine(manager=temp_db)
        engine.register_connector(connector)
        
        action = create_send_message_action(
            target_system="mattermost",
            target_id="jira-other-dev",
            text="Your task is stale",
            recipient_name="Other Developer"
        )
        
        result = await engine.execute(action)
        assert result.success is False
        assert result.status == ActionStatus.REJECTED
        assert "blocked" in result.error_message
        assert SELF_USER_ID in result.error_message
        
        # Zero outbound network calls
        mock_client.send_direct_message.assert_not_called()
        mock_client.create_post.assert_not_called()


@pytest.mark.asyncio
async def test_engine_dm_to_email_matched_other_user_is_rejected(temp_db):
    """When a user matches by email to another MM user, ActionEngine rejects it before execution."""
    user_repo = UserRepository(temp_db)
    user_repo.upsert(
        external_system="mattermost",
        external_user_id=OTHER_USER_ID,
        display_name="Bob Worker",
        email="bob@example.com"
    )
    
    test_settings = Settings(
        DRY_RUN=False,
        MATTERMOST_URL="https://chat.objects.ws",
        MATTERMOST_TOKEN="mock-token-123",
        MATTERMOST_SELF_ONLY_USER_ID=SELF_USER_ID
    )
    
    with patch("app.core.actions.engine.settings", test_settings), \
         patch("app.connectors.mattermost.connector.settings", test_settings), \
         patch("app.connectors.mattermost.client.settings", test_settings):
        
        mock_client = MagicMock(spec=MattermostClient)
        connector = MattermostConnector(client=mock_client)
        
        engine = ActionEngine(manager=temp_db)
        engine.register_connector(connector)
        
        action = create_send_message_action(
            target_system="mattermost",
            target_id="jira-bob-99",
            text="Please update ticket",
            recipient_name="Bob Worker"
        )
        # Pass email in parameters for resolution
        action.parameters["email"] = "bob@example.com"
        
        result = await engine.execute(action)
        assert result.success is False
        assert result.status == ActionStatus.REJECTED
        assert "blocked" in result.error_message
        
        mock_client.send_direct_message.assert_not_called()


@pytest.mark.asyncio
async def test_engine_unresolved_user_stops_with_user_mapping_required(temp_db):
    """Unmapped user causes ActionEngine to return USER_MAPPING_REQUIRED and make zero network calls."""
    test_settings = Settings(
        DRY_RUN=False,
        MATTERMOST_URL="https://chat.objects.ws",
        MATTERMOST_TOKEN="mock-token-123",
        MATTERMOST_SELF_ONLY_USER_ID=SELF_USER_ID
    )
    
    with patch("app.core.actions.engine.settings", test_settings), \
         patch("app.connectors.mattermost.connector.settings", test_settings), \
         patch("app.connectors.mattermost.client.settings", test_settings):
        
        mock_client = MagicMock(spec=MattermostClient)
        connector = MattermostConnector(client=mock_client)
        
        engine = ActionEngine(manager=temp_db)
        engine.register_connector(connector)
        
        action = create_send_message_action(
            target_system="mattermost",
            target_id="jira-unmapped-user-999",
            text="Please update ticket",
            recipient_name="Unmapped Contractor"
        )
        
        result = await engine.execute(action)
        assert result.success is False
        assert result.status == ActionStatus.USER_MAPPING_REQUIRED
        
        mock_client.send_direct_message.assert_not_called()


@pytest.mark.asyncio
async def test_fail_closed_when_self_only_setting_is_empty():
    """If MATTERMOST_SELF_ONLY_USER_ID is empty/misconfigured, all DM sending fails closed."""
    test_settings = Settings(
        MATTERMOST_SELF_ONLY_USER_ID=""
    )
    assert test_settings.is_mattermost_recipient_allowed(SELF_USER_ID) is False
    assert test_settings.is_mattermost_recipient_allowed(None) is False
    assert test_settings.is_mattermost_recipient_allowed("") is False


# ------------------------------------------------------------------------------
# 4. Legacy Trigger Disablement Tests
# ------------------------------------------------------------------------------

def test_stale_task_rule_legacy_mattermost_dm_disabled_by_default():
    """Verify that StaleTaskRule does not generate any Mattermost DM actions by default."""
    from app.core.rules.builtin import StaleTaskRule
    from app.core.events.types import StaleTask
    
    rule = StaleTaskRule()
    stale_event = StaleTask(
        source="scheduler",
        task_key="PROJ-200",
        task_title="Stale Feature",
        assignee_id="jira-assignee-1",
        assignee_name="Alice Dev",
        hours_inactive=48.0,
        threshold_hours=24
    )
    
    actions = rule.evaluate(stale_event)
    # Confirm zero Mattermost actions are produced
    mm_actions = [a for a in actions if a.target_system == "mattermost"]
    assert len(mm_actions) == 0


@pytest.mark.asyncio
async def test_scheduler_stale_evaluation_produces_zero_mattermost_actions(temp_db):
    """Verify that PeriodicScheduler._evaluate_stale_tasks produces zero Mattermost actions."""
    from app.services.scheduler import PeriodicScheduler
    from app.database.repositories import JiraIssueStateRepository
    
    repo = JiraIssueStateRepository(temp_db)
    repo.upsert(
        jira_issue_key="PROJ-300",
        summary="Old Task",
        status="In Progress",
        assignee="Bob Dev",
        due_date=None,
        last_seen_at="2020-01-01T00:00:00Z",
        last_activity_at="2020-01-01T00:00:00Z"
    )
    
    sched = PeriodicScheduler(manager=temp_db)
    actions = await sched._evaluate_stale_tasks()
    mm_actions = [a for a in actions if getattr(a, "target_system", "") == "mattermost"]
    assert len(mm_actions) == 0

