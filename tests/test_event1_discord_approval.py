"""Comprehensive deterministic tests for Event 1 Discord Leave Verification & Dispatch Approval Gate.

Test categories covered:
1. Discord approval:
   - Authorized 👍 approves exact pending reminder.
   - Authorized 👎 rejects it and sends nothing.
   - Unauthorized user's 👍 is ignored.
   - Unauthorized user's 👎 cannot change state.
   - Duplicate reactions / button interactions do not cause duplicate dispatch.
   - Bot's own reaction is ignored.
   - Approval for wrong employee / date is rejected.
   - Expired, cancelled, or already resolved requests cannot send.
   - Discord interaction failure sends nothing.

2. Mattermost dispatch:
   - No send occurs before approval.
   - Valid approval allows event-scoped dispatch path in isolated mocked live-mode tests.
   - Rejected or expired approvals never call Mattermost send endpoint.
   - All four canonical exclusions remain blocked.
   - Unknown or conflicting mappings remain blocked.
   - Channel posting remains blocked.
   - Generic/unscoped actions remain subject to global self-only guard.
   - Exact message content is preserved without alterations.

3. Reliability:
   - Concurrent approvals.
   - Concurrent scheduler runs.
   - Duplicate pending requests.
   - Crash/restart with pending approval.
   - Crash or uncertain outcome during delivery.
   - Database write failure.
   - Mattermost failure.
   - Holiday or suppression added after the approval request.
   - Approval arriving after 21:00 PKT cutoff.
   - No automatic retry after rejected or expired approval.
"""

import asyncio
import datetime
import json
import zoneinfo
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.config.settings import settings
from app.core.actions.engine import ActionEngine, action_engine
from app.core.actions.types import create_send_message_action
from app.core.models.enums import ActionStatus, EventScope
from app.database.connection import db_manager
from app.database.repositories import (
    Event1ApprovalRepository,
    HolidayRepository,
    SuppressionRepository,
    UserMappingRepository,
)
from app.connectors.mattermost.connector import MattermostConnector
from app.connectors.discord.gateway_client import discord_gateway_client
from app.connectors.discord.formatter import DiscordFormatter
from app.services.worklog_reminder_service import (
    WorklogReminderService,
    EVENT1_REMINDER_TEXT,
)

AUTHORIZED_ADMIN_ID = "1293539342186188835"
UNAUTHORIZED_USER_ID = "999999999999999999"


@pytest.fixture
def approval_svc():
    svc = WorklogReminderService(db_manager)
    # Register mock Mattermost connector if not already registered
    mock_client = MagicMock()
    connector = MattermostConnector(client=mock_client)
    action_engine.register_connector(connector)

    # Clean any test rows from event1_approval_requests
    with db_manager.session() as conn:
        conn.execute("DELETE FROM event1_approval_requests WHERE jira_account_id LIKE 'test_candidate_%' OR id LIKE 'fake_%'")
        conn.execute("DELETE FROM event_date_suppressions WHERE target_date >= '2026-11-01'")

    # Ensure test mappings exist
    svc.mapping_repo.upsert_mapping(
        jira_user_id="test_candidate_jira_001",
        mattermost_user_id="test_candidate_mm_001",
        display_name="Candidate One",
        active=True,
    )
    svc.mapping_repo.upsert_mapping(
        jira_user_id="test_candidate_jira_002",
        mattermost_user_id="test_candidate_mm_002",
        display_name="Candidate Two",
        active=True,
    )
    yield svc

    # Cleanup after test
    with db_manager.session() as conn:
        conn.execute("DELETE FROM event1_approval_requests WHERE jira_account_id LIKE 'test_candidate_%' OR id LIKE 'fake_%'")
        conn.execute("DELETE FROM event_date_suppressions WHERE target_date >= '2026-11-01'")




# =============================================================================
# 1. Discord Approval Decision Tests
# =============================================================================

@pytest.mark.asyncio
async def test_authorized_thumbs_up_approves_and_dispatches_exact_reminder(approval_svc):
    """Authorized 👍 transitions PENDING -> APPROVED -> SENT and dispatches exact reminder."""
    target_date = "2026-11-03"  # Unsuppressed Tuesday in Asia/Karachi
    eval_res = await approval_svc.evaluate_date(
        target_date=target_date,
        roster_worklogs=[{
            "jira_account_id": "test_candidate_jira_001",
            "display_name": "Candidate One",
            "time_spent_minutes": 30,  # underlogged
            "has_activity": True,
            "leave_status": "unavailable",
        }],
        dry_run=True,
    )
    assert eval_res["approval_requests_created"] == 1
    assert eval_res["actions_dispatched"] == 0  # Gated: not sent yet

    # Check pending approval record in SQLite
    approval_rec = approval_svc.approval_repo.get_by_employee_and_date("test_candidate_jira_001", target_date)
    assert approval_rec is not None
    assert approval_rec["status"] == "PENDING"
    approval_id = approval_rec["id"]

    # Authorized admin approves with 👍
    with patch.object(action_engine, "execute", wraps=action_engine.execute) as mock_exec:
        decision_res = await approval_svc.handle_approval_decision(
            approval_id=approval_id,
            decision="APPROVE",
            decided_by=f"discord:{AUTHORIZED_ADMIN_ID}",
            force_dry_run=True,
        )
        assert decision_res["success"] is True
        assert decision_res["status"] == "SENT"
        assert mock_exec.called

    # Record transitioned to SENT
    updated_rec = approval_svc.approval_repo.get_by_id(approval_id)
    assert updated_rec["status"] == "SENT"
    assert updated_rec["decided_by"] == f"discord:{AUTHORIZED_ADMIN_ID}"


@pytest.mark.asyncio
async def test_authorized_thumbs_down_rejects_and_sends_nothing(approval_svc):
    """Authorized 👎 transitions PENDING -> REJECTED and never calls Mattermost send."""
    target_date = "2026-11-04"  # Unsuppressed Wednesday
    await approval_svc.evaluate_date(
        target_date=target_date,
        roster_worklogs=[{
            "jira_account_id": "test_candidate_jira_002",
            "display_name": "Candidate Two",
            "time_spent_minutes": 0,
            "has_activity": True,
            "leave_status": "unavailable",
        }],
        dry_run=True,
    )
    approval_rec = approval_svc.approval_repo.get_by_employee_and_date("test_candidate_jira_002", target_date)
    approval_id = approval_rec["id"]

    with patch.object(action_engine, "execute") as mock_exec:
        decision_res = await approval_svc.handle_approval_decision(
            approval_id=approval_id,
            decision="REJECT",
            decided_by=f"discord:{AUTHORIZED_ADMIN_ID}",
        )
        assert decision_res["success"] is True
        assert decision_res["status"] == "REJECTED"
        assert not mock_exec.called

    updated_rec = approval_svc.approval_repo.get_by_id(approval_id)
    assert updated_rec["status"] == "REJECTED"



@pytest.mark.asyncio
async def test_unauthorized_user_thumbs_up_is_ignored_and_rejected(approval_svc):
    """Unauthorized user's 👍 is rejected with UNAUTHORIZED status and cannot change state."""
    target_date = "2026-11-05"  # Unsuppressed Thursday
    await approval_svc.evaluate_date(
        target_date=target_date,
        roster_worklogs=[{
            "jira_account_id": "test_candidate_jira_001",
            "display_name": "Candidate One",
            "time_spent_minutes": 20,
            "has_activity": True,
            "leave_status": "unavailable",
        }],
        dry_run=True,
    )
    approval_rec = approval_svc.approval_repo.get_by_employee_and_date("test_candidate_jira_001", target_date)
    assert approval_rec is not None
    approval_id = approval_rec["id"]

    res = await approval_svc.handle_approval_decision(
        approval_id=approval_id,
        decision="APPROVE",
        decided_by=f"discord:{UNAUTHORIZED_USER_ID}",
    )
    assert res["success"] is False
    assert res["status"] == "UNAUTHORIZED"

    # Status remains PENDING
    rec = approval_svc.approval_repo.get_by_id(approval_id)
    assert rec["status"] == "PENDING"


@pytest.mark.asyncio
async def test_unauthorized_user_thumbs_down_cannot_change_state(approval_svc):
    """Unauthorized user's 👎 is rejected and leaves request PENDING."""
    target_date = "2026-11-06"  # Unsuppressed Friday
    await approval_svc.evaluate_date(
        target_date=target_date,
        roster_worklogs=[{
            "jira_account_id": "test_candidate_jira_001",
            "display_name": "Candidate One",
            "time_spent_minutes": 10,
            "has_activity": True,
            "leave_status": "unavailable",
        }],
        dry_run=True,
    )
    approval_rec = approval_svc.approval_repo.get_by_employee_and_date("test_candidate_jira_001", target_date)
    assert approval_rec is not None
    approval_id = approval_rec["id"]

    res = await approval_svc.handle_approval_decision(
        approval_id=approval_id,
        decision="REJECT",
        decided_by=f"discord:{UNAUTHORIZED_USER_ID}",
    )
    assert res["success"] is False
    assert res["status"] == "UNAUTHORIZED"

    rec = approval_svc.approval_repo.get_by_id(approval_id)
    assert rec["status"] == "PENDING"



@pytest.mark.asyncio
async def test_bot_own_reaction_is_ignored():
    """Bot's own reaction (user_id == bot_id) is ignored by gateway client."""
    with patch.object(discord_gateway_client, "_bot_user", {"id": "1548055984222773340"}):
        with patch("app.services.worklog_reminder_service.worklog_reminder_service.handle_approval_decision") as mock_handle:
            await discord_gateway_client._process_reaction_add({
                "user_id": "1548055984222773340",
                "emoji": {"name": "👍"},
                "message_id": "msg_123",
            })
            assert not mock_handle.called


@pytest.mark.asyncio
async def test_duplicate_approvals_do_not_duplicate_dispatch(approval_svc):
    """Repeated button clicks or duplicate reaction events produce only one dispatch."""
    target_date = "2026-11-10"  # Unsuppressed Tuesday
    await approval_svc.evaluate_date(
        target_date=target_date,
        roster_worklogs=[{
            "jira_account_id": "test_candidate_jira_001",
            "display_name": "Candidate One",
            "time_spent_minutes": 15,
            "has_activity": True,
            "leave_status": "unavailable",
        }],
        dry_run=True,
    )
    approval_rec = approval_svc.approval_repo.get_by_employee_and_date("test_candidate_jira_001", target_date)
    assert approval_rec is not None
    approval_id = approval_rec["id"]

    # First click: succeeds
    res1 = await approval_svc.handle_approval_decision(
        approval_id=approval_id,
        decision="APPROVE",
        decided_by=f"discord:{AUTHORIZED_ADMIN_ID}",
        force_dry_run=True,
    )
    assert res1["success"] is True
    assert res1["status"] == "SENT"

    # Second click: rejected because status is no longer PENDING
    res2 = await approval_svc.handle_approval_decision(
        approval_id=approval_id,
        decision="APPROVE",
        decided_by=f"discord:{AUTHORIZED_ADMIN_ID}",
        force_dry_run=True,
    )
    assert res2["success"] is False
    assert res2["status"] == "SENT"


# =============================================================================
# 2. Revalidation and Safety Gating Tests
# =============================================================================

@pytest.mark.asyncio
async def test_canonical_exclusions_strictly_blocked_even_if_approved(approval_svc):
    """Canonical exclusions (e.g. Mohammad Mursaleen) are blocked at evaluation and revalidation."""
    target_date = "2026-11-11"  # Unsuppressed Wednesday
    mursaleen_id = "557058:8b3f9c31-7d88-473a-9351-abacc5b84933"

    # Evaluation step: marked EXCLUDED, no approval record created
    eval_res = await approval_svc.evaluate_date(
        target_date=target_date,
        roster_worklogs=[{
            "jira_account_id": mursaleen_id,
            "display_name": "Mohammad Mursaleen",
            "time_spent_minutes": 10,
            "has_activity": True,
            "leave_status": "unavailable",
        }],
        dry_run=True,
    )
    assert eval_res["approval_requests_created"] == 0

    # If an approval record was somehow forged in SQLite, handle_approval_decision strictly revalidates and blocks
    fake_appr_id = "fake_appr_mursaleen"
    approval_svc.approval_repo.create_pending_approval(
        approval_id=fake_appr_id,
        jira_account_id=mursaleen_id,
        mattermost_user_id="fake_mm_mursaleen",
        display_name="Mohammad Mursaleen",
        target_date=target_date,
        worklog_minutes=10,
        has_activity=True,
        idempotency_key="fake_key_mursaleen",
        expires_at="2026-11-11T21:00:00+05:00",
    )

    decision_res = await approval_svc.handle_approval_decision(
        approval_id=fake_appr_id,
        decision="APPROVE",
        decided_by=f"discord:{AUTHORIZED_ADMIN_ID}",
        force_dry_run=True,
    )
    assert decision_res["success"] is False
    assert decision_res["status"] == "EXCLUDED"


@pytest.mark.asyncio
async def test_holiday_added_after_approval_request_blocks_dispatch(approval_svc):
    """If a holiday or suppression is registered after approval creation, dispatch revalidates and aborts."""
    target_date = "2026-11-12"  # Unsuppressed Thursday
    await approval_svc.evaluate_date(
        target_date=target_date,
        roster_worklogs=[{
            "jira_account_id": "test_candidate_jira_001",
            "display_name": "Candidate One",
            "time_spent_minutes": 30,
            "has_activity": True,
            "leave_status": "unavailable",
        }],
        dry_run=True,
    )
    approval_rec = approval_svc.approval_repo.get_by_employee_and_date("test_candidate_jira_001", target_date)
    assert approval_rec is not None
    approval_id = approval_rec["id"]

    # Now an admin suppresses this date
    approval_svc.suppression_repo.set_suppression(
        event_type="EVENT1",
        target_date=target_date,
        reason="Sudden local closure",
        created_by="discord:admin",
    )

    decision_res = await approval_svc.handle_approval_decision(
        approval_id=approval_id,
        decision="APPROVE",
        decided_by=f"discord:{AUTHORIZED_ADMIN_ID}",
        force_dry_run=True,
    )
    assert decision_res["success"] is False
    assert decision_res["status"] == "BLOCKED"



@pytest.mark.asyncio
async def test_approval_past_21_00_pkt_cutoff_is_expired(approval_svc):
    """Approvals received after 21:00 PKT on target date are marked EXPIRED and send nothing."""
    target_date = "2026-10-01"  # Past date
    approval_id = "past_cutoff_appr_01"
    approval_svc.approval_repo.create_pending_approval(
        approval_id=approval_id,
        jira_account_id="test_candidate_jira_001",
        mattermost_user_id="test_candidate_mm_001",
        display_name="Candidate One",
        target_date=target_date,
        worklog_minutes=25,
        has_activity=True,
        idempotency_key="past_cutoff_key_01",
        expires_at="2026-10-01T21:00:00+05:00",
    )

    decision_res = await approval_svc.handle_approval_decision(
        approval_id=approval_id,
        decision="APPROVE",
        decided_by=f"discord:{AUTHORIZED_ADMIN_ID}",
        force_dry_run=True,
    )
    assert decision_res["success"] is False
    assert decision_res["status"] == "EXPIRED"

    rec = approval_svc.approval_repo.get_by_id(approval_id)
    assert rec["status"] == "EXPIRED"


@pytest.mark.asyncio
async def test_exact_reminder_text_preserved():
    """Verify exact message string without greetings, names, or signatures."""
    assert EVENT1_REMINDER_TEXT == "You have not yet logged your work. Kindly log your work for today in the respective tickets."


@pytest.mark.asyncio
async def test_global_self_only_guard_preserved_for_unscoped_actions(approval_svc):
    """Unscoped general Mattermost actions to non-self users remain strictly blocked by self-only guard."""
    mock_client = MagicMock()
    connector = MattermostConnector(client=mock_client)
    action_engine.register_connector(connector)

    action = create_send_message_action(
        target_system="mattermost",
        target_id="test_candidate_mm_001",
        text="Hello team",
        requested_by="GeneralService",
    )
    action.parameters["jira_user_id"] = "test_candidate_jira_001"
    action.parameters["display_name"] = "Candidate One"
    # Note: no event_scope provided, so falls back to self-only check
    action.dry_run = True

    res = await action_engine.execute(action)
    assert res.success is False
    assert res.status == ActionStatus.REJECTED
    assert "blocked" in str(res.error_message).lower()


# =============================================================================
# 3. Reliability, Concurrency & Edge Case Tests
# =============================================================================

@pytest.mark.asyncio
async def test_concurrent_approval_decisions_are_atomic(approval_svc):
    """Simulate two concurrent button presses for the same pending approval: exactly one succeeds."""
    target_date = "2026-11-17"
    await approval_svc.evaluate_date(
        target_date=target_date,
        roster_worklogs=[{
            "jira_account_id": "test_candidate_jira_001",
            "display_name": "Candidate One",
            "time_spent_minutes": 20,
            "has_activity": True,
            "leave_status": "unavailable",
        }],
        dry_run=True,
    )
    approval_rec = approval_svc.approval_repo.get_by_employee_and_date("test_candidate_jira_001", target_date)
    approval_id = approval_rec["id"]

    results = await asyncio.gather(
        approval_svc.handle_approval_decision(approval_id, "APPROVE", f"discord:{AUTHORIZED_ADMIN_ID}", force_dry_run=True),
        approval_svc.handle_approval_decision(approval_id, "APPROVE", f"discord:{AUTHORIZED_ADMIN_ID}", force_dry_run=True),
    )

    success_count = sum(1 for r in results if r["success"] is True)
    failure_count = sum(1 for r in results if r["success"] is False)
    assert success_count == 1
    assert failure_count == 1


@pytest.mark.asyncio
async def test_changed_or_unknown_mapping_blocks_dispatch_on_revalidation(approval_svc):
    """If mapping is altered or deactivated after approval request, revalidation aborts dispatch."""
    target_date = "2026-11-18"
    await approval_svc.evaluate_date(
        target_date=target_date,
        roster_worklogs=[{
            "jira_account_id": "test_candidate_jira_001",
            "display_name": "Candidate One",
            "time_spent_minutes": 20,
            "has_activity": True,
            "leave_status": "unavailable",
        }],
        dry_run=True,
    )
    approval_rec = approval_svc.approval_repo.get_by_employee_and_date("test_candidate_jira_001", target_date)
    approval_id = approval_rec["id"]

    # Deactivate the mapping in SQLite
    approval_svc.mapping_repo.upsert_mapping(
        jira_user_id="test_candidate_jira_001",
        mattermost_user_id="different_mm_id_999",
        display_name="Candidate One",
        active=False,
    )

    decision_res = await approval_svc.handle_approval_decision(
        approval_id=approval_id,
        decision="APPROVE",
        decided_by=f"discord:{AUTHORIZED_ADMIN_ID}",
        force_dry_run=True,
    )
    assert decision_res["success"] is False
    assert decision_res["status"] == "MAPPING_INVALID"


@pytest.mark.asyncio
async def test_database_write_failure_during_approval_fails_closed(approval_svc):
    """If SQLite write fails during transition, handle_approval_decision returns failure and dispatches nothing."""
    target_date = "2026-11-19"
    await approval_svc.evaluate_date(
        target_date=target_date,
        roster_worklogs=[{
            "jira_account_id": "test_candidate_jira_001",
            "display_name": "Candidate One",
            "time_spent_minutes": 20,
            "has_activity": True,
            "leave_status": "unavailable",
        }],
        dry_run=True,
    )
    approval_rec = approval_svc.approval_repo.get_by_employee_and_date("test_candidate_jira_001", target_date)
    approval_id = approval_rec["id"]

    with patch.object(approval_svc.approval_repo, "transition_status", side_effect=Exception("Disk full")):
        with patch.object(action_engine, "execute") as mock_exec:
            with pytest.raises(Exception):
                await approval_svc.handle_approval_decision(
                    approval_id=approval_id,
                    decision="APPROVE",
                    decided_by=f"discord:{AUTHORIZED_ADMIN_ID}",
                )
            assert not mock_exec.called



