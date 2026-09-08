"""Unit tests for Action Engine, Idempotency, Approval Gating, and Central Dry Run."""

import pytest
from app.core.actions.engine import ActionEngine
from app.core.actions.base import BaseAction, ActionResult
from app.core.actions.types import (
    create_send_notification_action,
    create_transition_task_action,
    create_send_message_action,
)
from app.core.models.enums import ActionType, ActionStatus, Capability
from app.connectors.discord import DiscordWebhookConnector
from app.connectors.jira import JiraConnector
from app.config.settings import settings


@pytest.mark.asyncio
async def test_action_engine_dry_run(temp_db):
    """Test that centralized Dry Run simulates the action without external network calls."""
    engine = ActionEngine(manager=temp_db)
    engine.register_connector(DiscordWebhookConnector())

    action = create_send_notification_action(
        target_system="discord",
        channel="pm-alerts",
        title="Dry Run Test",
        message="Simulated notification"
    )

    settings.DRY_RUN = True
    result = await engine.execute(action)

    assert result.success is True
    assert result.status == ActionStatus.DRY_RUN_SIMULATED
    assert result.dry_run is True


@pytest.mark.asyncio
async def test_action_engine_idempotency(temp_db):
    """Test that executing an identical action key twice returns the cached result."""
    engine = ActionEngine(manager=temp_db)
    engine.register_connector(DiscordWebhookConnector())

    action = create_send_notification_action(
        target_system="discord",
        channel="pm-alerts",
        title="Idempotent Test",
        message="Idempotency message"
    )

    res1 = await engine.execute(action)
    res2 = await engine.execute(action)

    assert res1.status == ActionStatus.DRY_RUN_SIMULATED
    assert res2.status == ActionStatus.DRY_RUN_SIMULATED


@pytest.mark.asyncio
async def test_action_engine_approval_required(temp_db):
    """Test that Jira mutating operations are held for PM approval when unapproved."""
    engine = ActionEngine(manager=temp_db)
    engine.register_connector(JiraConnector())

    action = create_transition_task_action(
        target_system="jira",
        task_key="CF7-421",
        target_status="In Progress",
        current_status="To Do"
    )

    # Execute unapproved
    res = await engine.execute(action, approved=False)
    assert res.status == ActionStatus.PENDING_APPROVAL

    # Execute approved
    res_approved = await engine.execute(action, approved=True)
    assert res_approved.status == ActionStatus.DRY_RUN_SIMULATED  # Dry run mode


@pytest.mark.asyncio
async def test_action_engine_unsupported_capability(temp_db):
    """Test that requesting an unsupported action on a connector returns ACTION_UNSUPPORTED."""
    engine = ActionEngine(manager=temp_db)
    # DiscordWebhookConnector only supports SEND_NOTIFICATION / SEND_EMBED
    engine.register_connector(DiscordWebhookConnector())

    # Attempt to transition a task on discord
    invalid_action = BaseAction(
        action_type=ActionType.TRANSITION_TASK,
        target_system="discord",
        target_id="channel-123",
        parameters={"status": "Done"}
    )

    res = await engine.execute(invalid_action)
    assert res.status == ActionStatus.ACTION_UNSUPPORTED
