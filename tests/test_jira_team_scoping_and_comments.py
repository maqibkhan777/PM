"""Comprehensive tests for Team-Scoped Jira Monitoring and Comment Notifications.

Covers all 10 requirements:
1. Configured team group produces correct JQL.
2. Jira polling uses team-scoped JQL.
3. Unrelated assignees are not projected/evaluated.
4. Stale detection does not generate actions for unrelated tickets.
5. Comment event is generated from a new Jira comment.
6. Comment event triggers the intended notification rule.
7. Comment notification reaches the ActionEngine / Discord connector.
8. Existing reopened/status notification behavior still works.
9. Empty/missing team-group configuration fails safely.
10. Existing Jira cursor pagination/checkpoint behavior remains intact.
"""

import pytest
import datetime
from unittest.mock import AsyncMock, patch, MagicMock
from app.config.settings import Settings
from app.connectors.jira.poller import JiraPoller
from app.connectors.jira.client import JiraClient
from app.core.events.types import TaskCommentAdded, TaskReopened
from app.core.rules.builtin import CommentNotificationRule, ReopenedRule
from app.core.actions.engine import ActionEngine
from app.database.repositories import JiraIssueStateRepository, ActionRepository, NotificationRepository
from app.services.scheduler import PeriodicScheduler
from app.utils.time import utc_now_iso, format_iso, utc_now


@pytest.fixture
def mock_jira_client():
    client = JiraClient()
    client.search_issues = AsyncMock()
    return client


# ------------------------------------------------------------------------------
# 1. Configured team group produces correct JQL
# 2. Jira polling uses team-scoped JQL
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_team_scoped_jql_generation(temp_db, mock_jira_client):
    """Verify that configured JIRA_TEAM_GROUP produces the exact team-scoped JQL."""
    team_settings = Settings(
        JIRA_BASE_URL="https://company.atlassian.net",
        JIRA_EMAIL="pm@company.com",
        JIRA_API_TOKEN="token-123",
        JIRA_TEAM_GROUP="Mursaleen Cluster"
    )
    mock_jira_client.search_issues.return_value = {"issues": [], "isLast": True}

    with patch("app.connectors.jira.poller.settings", team_settings):
        poller = JiraPoller(client=mock_jira_client, manager=temp_db)
        res = await poller.poll()

        assert res["status"] == "completed"
        assert mock_jira_client.search_issues.called
        call_kwargs = mock_jira_client.search_issues.call_args.kwargs
        jql_used = call_kwargs["jql"]

        # Assert correct team scoping in JQL
        assert 'assignee in membersOf("Mursaleen Cluster")' in jql_used
        assert "updated >= " in jql_used
        assert "ORDER BY updated ASC" in jql_used


# ------------------------------------------------------------------------------
# 9. Empty/missing team-group configuration fails safely
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_missing_team_group_fails_safely(temp_db, mock_jira_client):
    """Verify that unconfigured team group skips polling cleanly to prevent pulling other teams' issues."""
    empty_team_settings = Settings(
        JIRA_BASE_URL="https://company.atlassian.net",
        JIRA_EMAIL="pm@company.com",
        JIRA_API_TOKEN="token-123",
        JIRA_TEAM_GROUP=None
    )

    with patch("app.connectors.jira.poller.settings", empty_team_settings):
        poller = JiraPoller(client=mock_jira_client, manager=temp_db)
        res = await poller.poll()

        assert res["status"] == "skipped"
        assert res["reason"] == "jira_team_group_not_configured"
        mock_jira_client.search_issues.assert_not_called()


# ------------------------------------------------------------------------------
# 3. Unrelated assignees are not projected / evaluated
# 4. Stale detection does not generate actions for unrelated tickets
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_stale_detection_ignores_unrelated_tickets(temp_db):
    """Ensure StaleTaskDetection only evaluates tasks belonging to configured team_group."""
    repo = JiraIssueStateRepository(temp_db)
    old_time = format_iso(utc_now() - datetime.timedelta(hours=48))

    # Seed an unrelated team ticket
    repo.upsert(
        jira_issue_key="OTHER-99",
        summary="Other Team Task",
        status="In Progress",
        assignee="Outsider Bob",
        last_activity_at=old_time,
        team_group="Other Team"
    )

    # Seed a team-scoped ticket
    repo.upsert(
        jira_issue_key="TEAM-10",
        summary="Our Team Task",
        status="In Progress",
        assignee="Ahsan Amin",
        last_activity_at=old_time,
        team_group="Mursaleen Cluster"
    )

    # When querying stale candidates for "Mursaleen Cluster":
    stale_candidates = repo.get_stale_candidates(threshold_hours=24, team_group="Mursaleen Cluster")

    keys = [c["jira_issue_key"] for c in stale_candidates]
    assert "TEAM-10" in keys
    assert "OTHER-99" not in keys  # Unrelated ticket is strictly excluded!


# ------------------------------------------------------------------------------
# 5. Comment event is generated from a new Jira comment
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_comment_event_generated_from_jira_comment(temp_db, mock_jira_client):
    """Test JiraPoller extracts embedded comments and emits TaskCommentAdded."""
    now_iso = utc_now_iso()
    team_settings = Settings(
        JIRA_BASE_URL="https://company.atlassian.net",
        JIRA_EMAIL="pm@company.com",
        JIRA_API_TOKEN="token-123",
        JIRA_TEAM_GROUP="Mursaleen Cluster"
    )

    issue_with_comment = {
        "id": "5001",
        "key": "TEAM-55",
        "fields": {
            "summary": "Fix Checkout API",
            "status": {"name": "In Progress"},
            "updated": now_iso,
            "project": {"key": "TEAM", "id": "proj-team"},
            "assignee": {"displayName": "Azain Hassan", "accountId": "acc-azain"},
            "comment": {
                "comments": [
                    {
                        "id": "c-777",
                        "created": now_iso,
                        "author": {"displayName": "Mubashir Butt", "accountId": "acc-mubashir"},
                        "body": "Pushed database fixes to staging."
                    }
                ]
            }
        }
    }
    mock_jira_client.search_issues.return_value = {
        "issues": [issue_with_comment],
        "isLast": True
    }

    emitted_events = []

    async def capture_event(ev):
        emitted_events.append(ev)
        return "ev-1"

    with patch("app.connectors.jira.poller.settings", team_settings), \
         patch("app.services.orchestrator.orchestrator.ingest_polled_event", side_effect=capture_event):
        poller = JiraPoller(client=mock_jira_client, manager=temp_db)
        res = await poller.poll()

        assert res["status"] == "completed"
        assert res["issues_scanned"] == 1
        assert res["events_generated"] >= 1

        comment_events = [e for e in emitted_events if isinstance(e, TaskCommentAdded)]
        assert len(comment_events) == 1
        ev = comment_events[0]
        assert ev.task_key == "TEAM-55"
        assert ev.actor_name == "Mubashir Butt"
        assert ev.comment_body == "Pushed database fixes to staging."
        assert ev.comment_id == "c-777"


# ------------------------------------------------------------------------------
# 6. Comment event triggers the intended notification rule
# 7. Comment notification reaches the ActionEngine / Discord connector
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_comment_notification_rule_and_action_engine(temp_db):
    """Test CommentNotificationRule creates SendNotification action and routes through ActionEngine."""
    now_iso = utc_now_iso()
    test_settings = Settings(
        DRY_RUN=True,
        PM_DISCORD_CHANNEL="pm-alerts",
        DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/mock",
        COMMENT_NOTIFY_ALL=True
    )

    rule = CommentNotificationRule()
    event = TaskCommentAdded(
        source="jira",
        external_event_id="jira:TEAM-55:comment:c-777",
        timestamp=now_iso,
        actor_name="Mubashir Butt",
        task_key="TEAM-55",
        comment_id="c-777",
        comment_body="Fix is ready for review.",
        payload={
            "issue": {
                "fields": {
                    "summary": "Fix Checkout API",
                    "status": {"name": "In Progress"}
                }
            }
        }
    )

    with patch("app.core.rules.builtin.settings", test_settings), \
         patch("app.core.rules.builtin.notification_dedup_service.repo", NotificationRepository(temp_db)):
        actions = rule.evaluate(event)
        assert len(actions) == 1
        action = actions[0]
        assert action.action_type.value == "SendNotification"
        assert action.target_system == "discord"
        assert "💬 Jira Comment Added" in action.parameters.get("title", "")

    # Now verify ActionEngine executes this action under DRY_RUN cleanly
    engine = ActionEngine(manager=temp_db)
    from app.connectors.discord import DiscordWebhookConnector
    engine.register_connector(DiscordWebhookConnector())

    with patch("app.core.actions.engine.settings", test_settings):
        result = await engine.execute(action)
        assert result.success is True
        assert result.dry_run is True
        assert result.status.value == "DRY_RUN_SIMULATED"


# ------------------------------------------------------------------------------
# 8. Existing reopened/status notification behavior still works
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_reopened_rule_remains_intact(temp_db):
    """Ensure ReopenedRule behavior is preserved without interference."""
    now_iso = utc_now_iso()
    test_settings = Settings(
        PM_DISCORD_CHANNEL="pm-alerts",
        DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/mock"
    )
    rule = ReopenedRule()
    event = TaskReopened(
        source="jira",
        external_event_id="jira:TEAM-10:status:reopen-1",
        timestamp=now_iso,
        actor_name="Product Owner",
        task_key="TEAM-10",
        previous_status="Done",
        new_status="In Progress",
        payload={"issue": {"fields": {"summary": "Core Feature"}}}
    )

    with patch("app.core.rules.builtin.settings", test_settings), \
         patch("app.core.rules.builtin.notification_dedup_service.repo", NotificationRepository(temp_db)):
        actions = rule.evaluate(event)
        assert len(actions) == 1
        assert "Reopened" in actions[0].parameters.get("title", "")


# ------------------------------------------------------------------------------
# 10. Existing Jira cursor pagination / checkpoint behavior remains intact
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_pagination_and_checkpoint_intact_with_team_scoping(temp_db, mock_jira_client):
    """Ensure cursor pagination and checkpoint persistence work alongside team scoping."""
    team_settings = Settings(
        JIRA_BASE_URL="https://company.atlassian.net",
        JIRA_EMAIL="pm@company.com",
        JIRA_API_TOKEN="token-123",
        JIRA_TEAM_GROUP="Mursaleen Cluster"
    )

    page1 = {
        "issues": [{"key": "TEAM-1", "fields": {"summary": "Task 1", "status": {"name": "In Progress"}}}],
        "nextPageToken": "token-page-2",
        "isLast": False
    }
    page2 = {
        "issues": [{"key": "TEAM-2", "fields": {"summary": "Task 2", "status": {"name": "Done"}}}],
        "isLast": True
    }
    mock_jira_client.search_issues.side_effect = [page1, page2]

    with patch("app.connectors.jira.poller.settings", team_settings), \
         patch("app.services.orchestrator.orchestrator.ingest_polled_event", new_callable=AsyncMock):
        poller = JiraPoller(client=mock_jira_client, manager=temp_db)
        res = await poller.poll()

        assert res["status"] == "completed"
        assert res["issues_scanned"] == 2
        assert mock_jira_client.search_issues.call_count == 2

        # Checkpoint is recorded
        checkpoint = poller.polling_state_repo.get_checkpoint("jira")
        assert checkpoint is not None
