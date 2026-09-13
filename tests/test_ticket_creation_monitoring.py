"""Unit and regression tests for instant Jira ticket creation monitoring and policy rules."""

import pytest
import datetime
from app.core.models.enums import ActionType
from app.core.events.types import TaskCreated
from app.core.rules.ticket_creation_policy import (
    TicketCreationPolicy,
    TicketCreationDecision,
    TAHIR_ALI_ACCOUNT_ID,
    MUBASHIR_BUTT_ACCOUNT_ID,
    QA_CANONICAL_ACCOUNT_IDS,
)
from app.core.rules.builtin import TicketCreationRule
from app.core.rules.engine import RulesEngine
from app.connectors.jira.poller import JiraPoller
from app.connectors.jira.normalizer import JiraEventNormalizer
from app.connectors.discord.formatter import DiscordFormatter, COLOR_DARK_PINK
from app.database.repositories import JiraIssueStateRepository, EventRepository, JiraPollingStateRepository
from app.services.notification_deduplication import notification_dedup_service
from app.config.settings import settings
from app.utils.time import utc_now_iso, format_iso


@pytest.fixture(autouse=True)
def clean_dedup():
    """Ensure clean notification deduplication state for each test."""
    notification_dedup_service.clear_all()
    yield
    notification_dedup_service.clear_all()


# ==============================================================================
# 1. Policy Evaluation Unit Tests
# ==============================================================================

def test_policy_tahir_ali_all_types():
    """Tahir Ali is authorized to create tickets (all types -> EXPECTED)."""
    for itype in ["Task", "Story", "Bug", "Content"]:
        dec, reason, can_id, name = TicketCreationPolicy.evaluate(
            creator_account_id=TAHIR_ALI_ACCOUNT_ID,
            creator_display_name="Tahir Ali",
            issue_type=itype,
        )
        assert dec == TicketCreationDecision.EXPECTED
        assert "Tahir Ali is authorized" in reason


def test_policy_mubashir_support_type():
    """Mubashir is authorized to create Support tickets -> EXPECTED."""
    dec, reason, can_id, name = TicketCreationPolicy.evaluate(
        creator_account_id=MUBASHIR_BUTT_ACCOUNT_ID,
        creator_display_name="Mubashir Butt",
        issue_type="Support",
    )
    assert dec == TicketCreationDecision.EXPECTED
    assert "Customer Support is authorized" in reason


def test_policy_mubashir_non_support_type():
    """Mubashir creating non-Support ticket -> REVIEW."""
    for itype in ["Task", "Bug", "Story"]:
        dec, reason, can_id, name = TicketCreationPolicy.evaluate(
            creator_account_id=MUBASHIR_BUTT_ACCOUNT_ID,
            creator_display_name="Mubashir Butt",
            issue_type=itype,
        )
        assert dec == TicketCreationDecision.REVIEW
        assert "Support team member created" in reason


def test_policy_qa_bug_and_subtask():
    """QA team members are authorized to create Bug and Sub-task tickets -> EXPECTED."""
    qa_id = next(iter(QA_CANONICAL_ACCOUNT_IDS))
    for itype in ["Bug", "bug", "Sub-task", "sub-task", "Subtask", "sub task"]:
        dec, reason, can_id, name = TicketCreationPolicy.evaluate(
            creator_account_id=qa_id,
            creator_display_name="QA Engineer",
            issue_type=itype,
        )
        assert dec == TicketCreationDecision.EXPECTED
        assert "QA may create" in reason


def test_policy_qa_story_and_task():
    """QA team members creating Story/Task are review-visible exceptions -> REVIEW."""
    qa_id = next(iter(QA_CANONICAL_ACCOUNT_IDS))
    for itype in ["Story", "story", "Task", "task"]:
        dec, reason, can_id, name = TicketCreationPolicy.evaluate(
            creator_account_id=qa_id,
            creator_display_name="QA Engineer",
            issue_type=itype,
        )
        assert dec == TicketCreationDecision.REVIEW
        assert "exception review" in reason


def test_policy_general_team_member():
    """General team members creating tickets -> REVIEW."""
    dec, reason, can_id, name = TicketCreationPolicy.evaluate(
        creator_account_id="5fb3d908facfd6007697c25a",  # Muhammad Hamza (WP Dev)
        creator_display_name="Muhammad Hamza",
        issue_type="Task",
    )
    assert dec == TicketCreationDecision.REVIEW
    assert "General team member" in reason


def test_policy_canonical_excluded_account():
    """Canonical excluded accounts (PM, bots, management) are ignored -> IGNORE."""
    pm_id = "712020:566cad70-4a54-42bc-bf36-0c6132fe3cf0"
    dec, reason, can_id, name = TicketCreationPolicy.evaluate(
        creator_account_id=pm_id,
        creator_display_name="Aqib Khan",
        issue_type="Task",
    )
    assert dec == TicketCreationDecision.IGNORE


def test_policy_unknown_creator():
    """Unknown Jira creators default safely to REVIEW."""
    dec, reason, can_id, name = TicketCreationPolicy.evaluate(
        creator_account_id=None,
        creator_display_name=None,
        issue_type="Task",
    )
    assert dec == TicketCreationDecision.REVIEW
    assert "Unknown Jira User" in reason


# ==============================================================================
# 2. Rule Execution & Deduplication Tests
# ==============================================================================

def test_ticket_creation_rule_generates_dark_pink_embed(temp_db):
    """TicketCreationRule generates Discord action with dark pink/red embed."""
    rule = TicketCreationRule()
    event = TaskCreated(
        source="jira",
        external_event_id="jira:TREN-999:created",
        timestamp=utc_now_iso(),
        task_id="999",
        task_key="TREN-999",
        title="Payment gateway bug",
        creator_id=next(iter(QA_CANONICAL_ACCOUNT_IDS)),
        creator_name="Muhammad Bilal Khan",
        issue_type="Bug",
        project_key="TREN",
        created_at=utc_now_iso(),
    )

    actions = rule.evaluate(event)
    assert len(actions) == 1
    action = actions[0]
    assert action.action_type == ActionType.SEND_NOTIFICATION
    embeds = action.parameters.get("embeds", [])
    assert len(embeds) == 1
    assert embeds[0]["color"] == COLOR_DARK_PINK
    assert "TREN-999" in embeds[0]["title"] or any("TREN-999" in f["value"] for f in embeds[0].get("fields", []))


def test_ticket_creation_rule_idempotency_duplicate_event(temp_db):
    """Duplicate event for same issue key generates exactly ONE notification."""
    rule = TicketCreationRule()
    event = TaskCreated(
        source="jira",
        external_event_id="jira:TREN-888:created",
        timestamp=utc_now_iso(),
        task_id="888",
        task_key="TREN-888",
        title="Urgent task",
        creator_id="638855b85fce844d606bb422",
        creator_name="Tahir Ali",
        issue_type="Task",
        project_key="TREN",
        created_at=utc_now_iso(),
    )

    actions1 = rule.evaluate(event)
    assert len(actions1) == 1

    actions2 = rule.evaluate(event)
    assert len(actions2) == 0  # Deduplicated!


def test_ticket_creation_rule_ignores_initial_sync(temp_db):
    """Event marked is_initial_sync=True generates 0 notifications."""
    rule = TicketCreationRule()
    event = TaskCreated(
        source="jira",
        external_event_id="jira:TREN-777:created",
        timestamp=utc_now_iso(),
        task_id="777",
        task_key="TREN-777",
        title="Pre-existing issue",
        creator_id=next(iter(QA_CANONICAL_ACCOUNT_IDS)),
        creator_name="Muhammad Bilal Khan",
        issue_type="Bug",
        project_key="TREN",
        created_at=utc_now_iso(),
        is_initial_sync=True,
    )

    actions = rule.evaluate(event)
    assert len(actions) == 0


# ==============================================================================
# 3. Poller Novelty & Historical Flood Prevention Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_poller_initial_sync_generates_zero_creation_notifications(temp_db):
    """Initial poller run (bootstrap without checkpoint) updates projection but emits ZERO TaskCreated events."""
    poller = JiraPoller(manager=temp_db)
    past_time = format_iso(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=5))

    issues = [
        {
            "id": f"100{i}",
            "key": f"TREN-100{i}",
            "fields": {
                "summary": f"Historical issue {i}",
                "status": {"name": "In Progress"},
                "assignee": {"accountId": "712020:12e1da4b-147f-4f91-9d2d-965b66e19b61", "displayName": "Muhammad Bilal Khan"},
                "creator": {"accountId": "712020:12e1da4b-147f-4f91-9d2d-965b66e19b61", "displayName": "Muhammad Bilal Khan"},
                "issuetype": {"name": "Bug", "id": "10004"},
                "created": past_time,
                "updated": past_time,
                "project": {"key": "TREN", "id": "10000"},
            },
            "changelog": {"histories": []}
        }
        for i in range(10)
    ]

    query_start = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=60)
    total_events = 0

    for issue in issues:
        gen, dups = await poller._process_issue(
            issue=issue,
            query_start_dt=query_start,
            is_initial_sync=True,
            checkpoint_dt=None
        )
        total_events += gen

    # Zero creation events generated during bootstrap
    assert total_events == 0

    # But issue state projection was successfully populated
    state_repo = JiraIssueStateRepository(temp_db)
    cached = state_repo.get("TREN-1000")
    assert cached is not None
    assert cached["summary"] == "Historical issue 0"


@pytest.mark.asyncio
async def test_poller_live_creation_emits_task_created(temp_db):
    """During live polling, a newly created issue after the checkpoint emits TaskCreated."""
    poller = JiraPoller(manager=temp_db)
    now_dt = datetime.datetime.now(datetime.timezone.utc)
    checkpoint_dt = now_dt - datetime.timedelta(minutes=2)
    created_time = format_iso(now_dt - datetime.timedelta(seconds=30))

    new_issue = {
        "id": "2001",
        "key": "TREN-2001",
        "fields": {
            "summary": "Live payment failure",
            "status": {"name": "To Do"},
            "assignee": {"accountId": "712020:12e1da4b-147f-4f91-9d2d-965b66e19b61", "displayName": "Muhammad Bilal Khan"},
            "creator": {"accountId": "712020:12e1da4b-147f-4f91-9d2d-965b66e19b61", "displayName": "Muhammad Bilal Khan"},
            "issuetype": {"name": "Bug", "id": "10004"},
            "created": created_time,
            "updated": created_time,
            "project": {"key": "TREN", "id": "10000"},
        },
        "changelog": {"histories": []}
    }

    captured_events = []
    from unittest.mock import patch, AsyncMock
    async def mock_ingest(ev):
        captured_events.append(ev)
        return "event-123"

    with patch("app.services.orchestrator.orchestrator.ingest_polled_event", side_effect=mock_ingest):
        gen, dups = await poller._process_issue(
            issue=new_issue,
            query_start_dt=checkpoint_dt,
            is_initial_sync=False,
            checkpoint_dt=checkpoint_dt
        )

    assert gen == 1
    assert len(captured_events) == 1
    created_event = captured_events[0]
    assert created_event.event_type == "TaskCreated"
    assert created_event.task_key == "TREN-2001"
    assert created_event.creator_id == "712020:12e1da4b-147f-4f91-9d2d-965b66e19b61"
    assert created_event.issue_type == "Bug"


# ==============================================================================
# 4. Webhook Normalization Tests
# ==============================================================================

def test_webhook_normalizer_extracts_authoritative_creator():
    """JiraEventNormalizer extracts authoritative fields.creator and issuetype."""
    payload = {
        "webhookEvent": "jira:issue_created",
        "timestamp": 1726142400000,
        "issue": {
            "id": "3001",
            "key": "TREN-3001",
            "fields": {
                "summary": "Webhook created issue",
                "description": "Details here",
                "status": {"name": "To Do"},
                "priority": {"name": "High"},
                "creator": {"accountId": "638855b85fce844d606bb422", "displayName": "Tahir Ali", "emailAddress": "tahir@objects.ws"},
                "reporter": {"accountId": "other-reporter", "displayName": "Other Reporter"},
                "assignee": {"accountId": "assignee-acc", "displayName": "Assignee User"},
                "issuetype": {"name": "Task", "id": "10002"},
                "project": {"key": "TREN", "id": "10000"},
                "created": "2026-09-12T16:00:00.000Z",
                "duedate": "2026-09-20"
            }
        },
        "user": {"accountId": "actor-acc", "displayName": "Actor User"}
    }

    event = JiraEventNormalizer.normalize(payload)
    assert isinstance(event, TaskCreated)
    assert event.task_key == "TREN-3001"
    assert event.creator_id == "638855b85fce844d606bb422"
    assert event.creator_name == "Tahir Ali"
    assert event.issue_type == "Task"
    assert event.issue_type_id == "10002"
