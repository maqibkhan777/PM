"""Unit tests for strict user mapping and unresolved PM alert handling."""

import pytest
from app.services.user_mapping_service import UserMappingService
from app.database.repositories import UserRepository, UserMappingRepository
from app.core.actions.engine import ActionEngine
from app.core.actions.types import create_send_message_action
from app.core.models.enums import ActionStatus
from app.connectors.mattermost import MattermostConnector
from app.connectors.discord import DiscordWebhookConnector


def test_explicit_mapping_resolution(temp_db):
    """Test resolution via explicit database mapping table."""
    service = UserMappingService(manager=temp_db)
    mapping_repo = UserMappingRepository(temp_db)

    mapping_repo.upsert_mapping(
        jira_user_id="jira-123",
        mattermost_user_id="mm-456",
        display_name="Ahsan Amin"
    )

    mm_id, method = service.resolve_jira_to_mattermost("jira-123")
    assert mm_id == "mm-456"
    assert method == "EXPLICIT_MAPPING"


def test_email_matching_resolution(temp_db):
    """Test resolution via verified email match."""
    service = UserMappingService(manager=temp_db)
    user_repo = UserRepository(temp_db)

    user_repo.upsert(
        external_system="mattermost",
        external_user_id="mm-789",
        display_name="Sara Connor",
        email="sara@example.com"
    )

    mm_id, method = service.resolve_jira_to_mattermost(
        jira_user_id="jira-999",
        display_name="Sara Connor",
        email="sara@example.com"
    )
    assert mm_id == "mm-789"
    assert method == "EXACT_EMAIL_MATCH"


def test_unresolved_user_never_guesses(temp_db):
    """Test that an unmapped user is unresolved and never guessed."""
    service = UserMappingService(manager=temp_db)
    mm_id, method = service.resolve_jira_to_mattermost(
        jira_user_id="jira-unknown-000",
        display_name="Random Contractor",
        email="contractor@random.com"
    )
    assert mm_id is None
    assert method == "UNRESOLVED"


@pytest.mark.asyncio
async def test_action_engine_blocks_unmapped_user(temp_db):
    """Test that ActionEngine stops and flags USER_MAPPING_REQUIRED when mapping is missing."""
    engine = ActionEngine(manager=temp_db)
    engine.register_connector(MattermostConnector())
    engine.register_connector(DiscordWebhookConnector())

    # Create SendMessage action for an unmapped Jira user
    action = create_send_message_action(
        target_system="mattermost",
        target_id="jira-unmapped-user-777",
        text="Stale task reminder",
        recipient_name="Unmapped Contractor"
    )

    result = await engine.execute(action)
    assert result.success is False
    assert result.status == ActionStatus.USER_MAPPING_REQUIRED
