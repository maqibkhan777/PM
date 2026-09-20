"""Comprehensive tests for Discord Channel Separation.

Verifies strict isolation between:
- #notifications: Real-Time Jira Activity (Assignments, Unassignments, Mentions, Comments)
- #pm-alerts: PM Operations, Workflow Violations, Scheduled Reports, /pm Slash Commands
"""

from unittest.mock import AsyncMock, patch, MagicMock
import pytest
import httpx
from app.config.settings import Settings, settings
from app.connectors.discord.webhook_connector import DiscordWebhookConnector
from app.connectors.discord.slash_commands import DiscordSlashCommandHandler
from app.core.actions.engine import ActionEngine
from app.core.actions.types import create_send_notification_action
from app.core.events.types import (
    TaskAssigned,
    TaskCommentAdded,
    TaskCreated,
    TaskBlocked,
    TaskReopened,
    StaleTask,
    OverdueTask,
)
from app.core.models.enums import ActionType
from app.core.rules.builtin import (
    ActiveWorkRule,
    AssignmentRule,
    CommentNotificationRule,
    StaleTaskRule,
    OverdueRule,
    BlockedRule,
    ReopenedRule,
    TicketCreationRule,
)
from app.core.rules.mubashir_support_rule import MubashirSupportRule
from app.core.reports.worklog_report import DailyWorklogReportGenerator
from app.core.reports.overdue_report import DailyOverdueReportGenerator
from app.core.reports.attention_report import DailyPMAttentionReportGenerator
from app.core.reports.daily_report import DailyActivityReportGenerator
from app.core.reports.mubashir_report import MubashirAutomationReportGenerator
from app.services.notification_deduplication import notification_dedup_service
from app.services.user_identity_service import user_identity_service


@pytest.fixture(autouse=True)
def reset_state():
    """Ensure clean notification deduplication, allowed users, and identity state."""
    notification_dedup_service.clear_all()
    user_identity_service.set_identity(
        account_id="jira-user-me-123",
        email="me@example.com",
        display_name="Aqib Khan",
    )
    prev_users = settings.DISCORD_PM_ALLOWED_USERS
    prev_channel_id = settings.DISCORD_PM_CHANNEL_ID
    settings.DISCORD_PM_ALLOWED_USERS = "*"
    settings.DISCORD_PM_CHANNEL_ID = "1547090800771604482"
    yield
    settings.DISCORD_PM_ALLOWED_USERS = prev_users
    settings.DISCORD_PM_CHANNEL_ID = prev_channel_id
    notification_dedup_service.clear_all()


# ==============================================================================
# A. Real-Time Jira Activity Notifications -> #notifications
# ==============================================================================

def test_task_assigned_routes_to_notifications_channel():
    """1. Task Assigned to team member must route to JIRA_NOTIFICATION_DISCORD_CHANNEL."""
    rule = AssignmentRule()
    event = TaskAssigned(
        source="jira",
        external_event_id="jira:HCF7-634:assign:1",
        timestamp="2026-09-19T10:00:00Z",
        actor_name="Mursaleen",
        task_key="HCF7-634",
        new_assignee_name="Daniyal Raza",
        new_assignee_id="user-daniyal",
        payload={"issue": {"fields": {"summary": "Implement Feature"}}},
    )
    actions = rule.evaluate(event)
    assert len(actions) == 1
    assert actions[0].parameters["channel"] == settings.JIRA_NOTIFICATION_DISCORD_CHANNEL
    assert "Task Assigned — HCF7-634" in actions[0].parameters["title"]


def test_task_unassigned_routes_to_notifications_channel():
    """2. Task Unassigned must route to JIRA_NOTIFICATION_DISCORD_CHANNEL."""
    rule = AssignmentRule()
    event = TaskAssigned(
        source="jira",
        external_event_id="jira:HCF7-634:assign:2",
        timestamp="2026-09-19T10:05:00Z",
        actor_name="Mursaleen",
        task_key="HCF7-634",
        new_assignee_name="Unassigned",
        new_assignee_id=None,
        payload={"issue": {"fields": {"summary": "Implement Feature"}}},
    )
    actions = rule.evaluate(event)
    assert len(actions) == 1
    assert actions[0].parameters["channel"] == settings.JIRA_NOTIFICATION_DISCORD_CHANNEL
    assert "Task Assigned — HCF7-634" in actions[0].parameters["title"]


def test_task_assigned_to_me_routes_to_notifications_channel():
    """3. Task Assigned to You must route to JIRA_NOTIFICATION_DISCORD_CHANNEL."""
    rule = AssignmentRule()
    event = TaskAssigned(
        source="jira",
        external_event_id="jira:HCF7-634:assign:3",
        timestamp="2026-09-19T10:10:00Z",
        actor_name="Mursaleen",
        task_key="HCF7-634",
        new_assignee_name="Aqib Khan",
        new_assignee_id="jira-user-me-123",
        payload={"issue": {"fields": {"summary": "Implement Feature"}}},
    )
    actions = rule.evaluate(event)
    assert len(actions) == 1
    assert actions[0].parameters["channel"] == settings.JIRA_NOTIFICATION_DISCORD_CHANNEL
    assert "Task Assigned to You — HCF7-634" in actions[0].parameters["title"]


def test_comment_mention_routes_to_notifications_channel():
    """4. You were mentioned on a Jira ticket must route to JIRA_NOTIFICATION_DISCORD_CHANNEL."""
    rule = CommentNotificationRule()
    event = TaskCommentAdded(
        source="jira",
        external_event_id="jira:HCF7-634:comment:1",
        timestamp="2026-09-19T10:15:00Z",
        actor_name="Ahmed Raza",
        task_key="HCF7-634",
        comment_id="c-1",
        comment_body="@Aqib Khan please review this PR.",
        mentioned_account_ids=["jira-user-me-123"],
        mentioned_display_names=["Aqib Khan"],
        payload={"issue": {"fields": {"summary": "Bugfix", "status": {"name": "In Progress"}}}},
    )
    actions = rule.evaluate(event)
    assert len(actions) == 1
    assert actions[0].parameters["channel"] == settings.JIRA_NOTIFICATION_DISCORD_CHANNEL
    assert "You were mentioned on HCF7-634" in actions[0].parameters["title"]


def test_comment_added_all_routes_to_notifications_channel():
    """5. Jira Comment Added (when COMMENT_NOTIFY_ALL=True) must route to JIRA_NOTIFICATION_DISCORD_CHANNEL."""
    prev = settings.COMMENT_NOTIFY_ALL
    settings.COMMENT_NOTIFY_ALL = True
    try:
        rule = CommentNotificationRule()
        event = TaskCommentAdded(
            source="jira",
            external_event_id="jira:HCF7-634:comment:2",
            timestamp="2026-09-19T10:20:00Z",
            actor_name="Support Agent",
            task_key="HCF7-634",
            comment_id="c-2",
            comment_body="General progress update without mention.",
            payload={"issue": {"fields": {"summary": "Bugfix", "status": {"name": "In Progress"}}}},
        )
        actions = rule.evaluate(event)
        assert len(actions) == 1
        assert actions[0].parameters["channel"] == settings.JIRA_NOTIFICATION_DISCORD_CHANNEL
        assert "Jira Comment Added — HCF7-634" in actions[0].parameters["title"]
    finally:
        settings.COMMENT_NOTIFY_ALL = prev


# ==============================================================================
# B. PM Operations & Workflow Alerts -> #pm-alerts
# ==============================================================================

def test_jira_workflow_alert_remains_in_pm_alerts():
    """6. Jira Workflow Alert (ActiveWorkRule) MUST remain in PM_DISCORD_CHANNEL (pm-alerts)."""
    rule = ActiveWorkRule()
    event = TaskCommentAdded(
        source="jira",
        task_key="HCF7-634",
        comment_body="Starting development now.",
        actor_name="Ahsan Amin",
        payload={
            "issue": {
                "fields": {
                    "status": {"name": "To Do"},
                    "summary": "Implement Feature",
                    "project": {"key": "HCF7", "name": "HCF7 Apps"},
                }
            }
        },
    )
    actions = rule.evaluate(event)
    assert len(actions) == 1
    assert actions[0].parameters["channel"] == settings.PM_DISCORD_CHANNEL
    assert "Jira Workflow Alert" in actions[0].parameters["title"]


def test_stale_task_alert_remains_in_pm_alerts():
    """7. Stale Task Alert MUST remain in PM_DISCORD_CHANNEL (pm-alerts)."""
    prev_pm = settings.STALE_TASK_NOTIFY_PM
    settings.STALE_TASK_NOTIFY_PM = True
    try:
        rule = StaleTaskRule(configuration={"threshold_hours": 24})
        event = StaleTask(
            source="scheduler",
            task_key="HCF7-634",
            task_title="Stale work item",
            assignee_name="Daniyal Raza",
            hours_inactive=30.0,
            threshold_hours=24,
        )
        actions = rule.evaluate(event)
        discord_actions = [a for a in actions if a.target_system == "discord"]
        assert len(discord_actions) == 1
        assert discord_actions[0].parameters["channel"] == settings.PM_DISCORD_CHANNEL
        assert "Stale Task Alert" in discord_actions[0].parameters["title"]
    finally:
        settings.STALE_TASK_NOTIFY_PM = prev_pm


def test_overdue_task_alert_remains_in_pm_alerts():
    """8. Overdue Task Alert MUST remain in PM_DISCORD_CHANNEL (pm-alerts)."""
    prev = settings.OVERDUE_NOTIFY_PM
    settings.OVERDUE_NOTIFY_PM = True
    try:
        rule = OverdueRule()
        event = OverdueTask(
            source="scheduler",
            task_key="HCF7-634",
            task_title="Overdue task",
            assignee_name="Daniyal Raza",
            due_date="2026-09-01T00:00:00Z",
            current_status="In Progress",
        )
        actions = rule.evaluate(event)
        assert len(actions) == 1
        assert actions[0].parameters["channel"] == settings.PM_DISCORD_CHANNEL
        assert "Overdue Task Alert" in actions[0].parameters["title"]
    finally:
        settings.OVERDUE_NOTIFY_PM = prev


def test_blocked_task_alert_remains_in_pm_alerts():
    """9. Blocked Task Alert MUST remain in PM_DISCORD_CHANNEL (pm-alerts)."""
    rule = BlockedRule()
    event = TaskBlocked(
        source="jira",
        task_key="HCF7-634",
        actor_name="Daniyal Raza",
        payload={"issue": {"fields": {"summary": "Blocked by API"}}},
    )
    actions = rule.evaluate(event)
    assert len(actions) == 1
    assert actions[0].parameters["channel"] == settings.PM_DISCORD_CHANNEL
    assert "Task Blocked Alert" in actions[0].parameters["title"]


def test_reopened_task_alert_remains_in_pm_alerts():
    """10. Reopened Task Alert MUST remain in PM_DISCORD_CHANNEL (pm-alerts)."""
    rule = ReopenedRule()
    event = TaskReopened(
        source="jira",
        task_key="HCF7-634",
        previous_status="Done",
        new_status="In Progress",
        actor_name="QA Lead",
        payload={"issue": {"fields": {"summary": "Reopened fix"}}},
    )
    actions = rule.evaluate(event)
    assert len(actions) == 1
    assert actions[0].parameters["channel"] == settings.PM_DISCORD_CHANNEL
    assert "Task Reopened Alert" in actions[0].parameters["title"]


def test_ticket_creation_alert_remains_in_pm_alerts():
    """11. Ticket Creation Policy Alert MUST remain in PM_DISCORD_CHANNEL (pm-alerts)."""
    prev = settings.TICKET_CREATION_NOTIFY_PM
    settings.TICKET_CREATION_NOTIFY_PM = True
    try:
        rule = TicketCreationRule()
        event = TaskCreated(
            source="jira",
            task_key="HCF7-999",
            task_id="9999",
            actor_name="Azain Hassan",
            actor_id="712020:1ddac8e3-e006-48e7-b4c9-ee941efc8e6e",
            title="Create new API gateway",
            issue_type="Task",
            project_key="HCF7",
            payload={"issue": {"fields": {"summary": "Create new API gateway", "creator": {"displayName": "Azain Hassan"}}}},
        )
        actions = rule.evaluate(event)
        assert len(actions) == 1
        assert actions[0].parameters["channel"] == settings.PM_DISCORD_CHANNEL
        assert "Ticket Created" in actions[0].parameters["title"]
    finally:
        settings.TICKET_CREATION_NOTIFY_PM = prev


# ==============================================================================
# C. Mubashir Support & Reports -> #pm-alerts
# ==============================================================================

def test_mubashir_support_workflow_check_remains_in_pm_alerts(temp_db):
    """12. Mubashir Support Ticket Check MUST remain in PM_DISCORD_CHANNEL (pm-alerts)."""
    prev = settings.MUBASHIR_SUPPORT_RULE_ENABLED
    settings.MUBASHIR_SUPPORT_RULE_ENABLED = True
    try:
        rule = MubashirSupportRule(manager=temp_db)
        event = TaskCreated(
            source="jira",
            task_key="HCF7-500",
            task_id="5000",
            actor_name="Mubashir Butt",
            actor_id="712020:e268bcd8-d981-4b4d-992d-d5694745df8b",
            title="Customer Support Inquiry",
            issue_type="Support",
            project_key="HCF7",
            payload={
                "issue": {
                    "fields": {
                        "summary": "Customer Support Inquiry",
                        "creator": {"displayName": "Mubashir Butt", "accountId": "712020:e268bcd8-d981-4b4d-992d-d5694745df8b"},
                        "description": "Short description without required fields",
                        "status": {"name": "To Do"},
                    }
                }
            },
        )
        actions = rule.evaluate(event)
        discord_actions = [a for a in actions if a.target_system == "discord"]
        assert len(discord_actions) == 1
        assert discord_actions[0].parameters["channel"] == settings.PM_DISCORD_CHANNEL
        assert "Support Ticket Workflow Check" in discord_actions[0].parameters["title"]
    finally:
        settings.MUBASHIR_SUPPORT_RULE_ENABLED = prev


@pytest.mark.asyncio
async def test_mubashir_automation_report_remains_in_pm_alerts(temp_db):
    """13. Mubashir Automation Report MUST use MUBASHIR_AUTOMATION_REPORT_CHANNEL / pm-alerts."""
    gen = MubashirAutomationReportGenerator(manager=temp_db)
    mock_report = {
        "date_str": "2026-09-19",
        "total_actions": 5,
        "by_automation": {"MubashirSupportRule": [], "MubashirStaleSupportRule": []},
        "all_items": [],
        "summary": "All actions logged",
    }
    with patch.object(gen, "generate_report", return_value=mock_report), \
         patch("app.core.reports.mubashir_report.action_engine.execute", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = MagicMock(status=MagicMock(value="success"), dry_run=True, success=True, model_dump=lambda: {})
        await gen.send_report_to_discord("2026-09-19")
        assert mock_exec.called
        action = mock_exec.call_args[0][0]
        assert action.parameters["channel"] == (settings.MUBASHIR_AUTOMATION_REPORT_CHANNEL or settings.PM_DISCORD_CHANNEL)
        assert action.target_id == "pm-alerts"


# ==============================================================================
# D. Automated Reports -> #pm-alerts (preserving dedicated report configs)
# ==============================================================================

@pytest.mark.asyncio
async def test_daily_worklog_report_remains_in_pm_alerts(temp_db):
    """14. Daily Worklog Report MUST route to DAILY_WORKLOG_REPORT_CHANNEL (pm-alerts)."""
    gen = DailyWorklogReportGenerator(manager=temp_db)
    mock_report = {
        "date": "2026-09-19",
        "formatted_date": "Saturday, Sep 19, 2026",
        "total_seconds": 28800,
        "total_time_human": "8h 0m",
        "total_worklogs": 4,
        "contributors_count": 2,
        "users": {},
        "issues": {},
    }
    with patch.object(gen, "generate_report", new_callable=AsyncMock, return_value=mock_report), \
         patch("app.core.reports.worklog_report.action_engine.execute", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = MagicMock(status=MagicMock(value="success"), dry_run=True, model_dump=lambda: {})
        await gen.send_report_to_discord("2026-09-19", sync_jira=False)
        assert mock_exec.called
        action = mock_exec.call_args[0][0]
        assert action.parameters["channel"] == settings.DAILY_WORKLOG_REPORT_CHANNEL


@pytest.mark.asyncio
async def test_daily_overdue_digest_remains_in_pm_alerts(temp_db):
    """15. Daily Overdue Digest MUST route to OVERDUE_DIGEST_CHANNEL / pm-alerts."""
    gen = DailyOverdueReportGenerator(manager=temp_db)
    mock_data = {
        "date": "2026-09-19",
        "overdue_count": 0,
        "overdue_items": [],
        "team_group": "Mursaleen Cluster",
    }
    with patch.object(gen, "generate_digest", return_value=mock_data), \
         patch("app.core.reports.overdue_report.action_engine.execute", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = MagicMock(status=MagicMock(value="success"), dry_run=True, model_dump=lambda: {})
        await gen.send_digest_to_discord("2026-09-19")
        assert mock_exec.called
        action = mock_exec.call_args[0][0]
        assert action.parameters["channel"] == (settings.OVERDUE_DIGEST_CHANNEL or settings.PM_DISCORD_CHANNEL)


@pytest.mark.asyncio
async def test_pm_attention_digest_remains_in_pm_alerts(temp_db):
    """16. PM Attention Digest MUST route to PM_ATTENTION_DIGEST_CHANNEL / pm-alerts."""
    gen = DailyPMAttentionReportGenerator(manager=temp_db)
    mock_data = {
        "date": "2026-09-19",
        "total_count": 0,
        "categories": {},
        "team_group": "Mursaleen Cluster",
    }
    with patch.object(gen, "generate_digest", return_value=mock_data), \
         patch("app.core.reports.attention_report.action_engine.execute", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = MagicMock(status=MagicMock(value="success"), dry_run=True, model_dump=lambda: {})
        await gen.send_digest_to_discord("2026-09-19")
        assert mock_exec.called
        action = mock_exec.call_args[0][0]
        assert action.parameters["channel"] == (settings.PM_ATTENTION_DIGEST_CHANNEL or settings.PM_DISCORD_CHANNEL)


@pytest.mark.asyncio
async def test_daily_activity_report_remains_in_pm_alerts(temp_db):
    """17. Daily Activity Report MUST route to DAILY_ACTIVITY_REPORT_CHANNEL / pm-alerts."""
    gen = DailyActivityReportGenerator(manager=temp_db)
    mock_data = {
        "date": "2026-09-19",
        "formatted_date": "Saturday, Sep 19, 2026",
        "team_name": "Mursaleen Cluster",
        "total_activities": 0,
        "active_members_count": 0,
        "activities_by_resource": {},
        "members": [],
        "tasks_created": 0,
        "tasks_updated": 0,
        "tasks_completed": 0,
        "status_transitions": 0,
        "comments_added": 0,
        "worklogs_logged": 0,
        "workflow_violations": 0,
        "stale_tasks": 0,
        "blocked_tasks": 0,
        "reopened_tasks": 0,
        "recent_transitions": [],
        "activities": [],
    }
    with patch.object(gen, "generate_report", return_value=mock_data), \
         patch("app.core.reports.daily_report.action_engine.execute", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = MagicMock(status=MagicMock(value="success"), dry_run=True, model_dump=lambda: {})
        await gen.send_report_to_discord("2026-09-19")
        assert mock_exec.called
        action = mock_exec.call_args[0][0]
        assert action.parameters["channel"] == settings.PM_DISCORD_CHANNEL


# ==============================================================================
# E. Webhook Routing & Dual-Destination Selection
# ==============================================================================

@pytest.mark.asyncio
async def test_webhook_routing_to_notifications_webhook():
    """18. Action targeting notifications must post to DISCORD_NOTIFICATIONS_WEBHOOK_URL."""
    notif_webhook = "https://discord.com/api/webhooks/999999/notifications-secret-token"
    pm_webhook = "https://discord.com/api/webhooks/111111/pm-alerts-secret-token"

    connector = DiscordWebhookConnector(
        webhook_url=pm_webhook,
        notifications_webhook_url=notif_webhook,
    )

    action = create_send_notification_action(
        target_system="discord",
        channel="notifications",
        title="Task Assigned — HCF7-634",
        message="Task assigned to Daniyal Raza",
    )

    mock_resp = httpx.Response(status_code=204)
    with patch.object(connector._get_client(), "post", new_callable=AsyncMock, return_value=mock_resp) as mock_post:
        res = await connector.execute_action(action)
        assert res["status"] == "success"
        assert mock_post.called
        called_url = mock_post.call_args[0][0]
        assert called_url == notif_webhook


@pytest.mark.asyncio
async def test_webhook_routing_to_pm_alerts_webhook():
    """19. Action targeting pm-alerts must post to DISCORD_WEBHOOK_URL."""
    notif_webhook = "https://discord.com/api/webhooks/999999/notifications-secret-token"
    pm_webhook = "https://discord.com/api/webhooks/111111/pm-alerts-secret-token"

    connector = DiscordWebhookConnector(
        webhook_url=pm_webhook,
        notifications_webhook_url=notif_webhook,
    )

    action = create_send_notification_action(
        target_system="discord",
        channel="pm-alerts",
        title="🚨 Jira Workflow Alert — HCF7-634",
        message="Activity detected on To Do ticket",
    )

    mock_resp = httpx.Response(status_code=204)
    with patch.object(connector._get_client(), "post", new_callable=AsyncMock, return_value=mock_resp) as mock_post:
        res = await connector.execute_action(action)
        assert res["status"] == "success"
        assert mock_post.called
        called_url = mock_post.call_args[0][0]
        assert called_url == pm_webhook


@pytest.mark.asyncio
async def test_webhook_routing_notifications_missing_fallback_safe():
    """20. When notifications webhook is missing/placeholder, do NOT post to PM webhook."""
    pm_webhook = "https://discord.com/api/webhooks/111111/pm-alerts-secret-token"

    action = create_send_notification_action(
        target_system="discord",
        channel="notifications",
        title="Task Assigned — HCF7-634",
        message="Task assigned to Daniyal Raza",
    )

    with patch.object(settings, "DISCORD_NOTIFICATIONS_WEBHOOK_URL", None):
        connector = DiscordWebhookConnector(
            webhook_url=pm_webhook,
            notifications_webhook_url=None,  # Not configured
        )
        with patch.object(connector._get_client(), "post", new_callable=AsyncMock) as mock_post:
            res = await connector.execute_action(action)
            assert res["status"] == "simulated"
            assert res["reason"] == "no_notifications_webhook_url"
            # Crucial check: must NOT have called post on the PM webhook URL
            assert not mock_post.called


# ==============================================================================
# F. /pm Slash Command Channel Restrictions (Strict Allowlist)
# ==============================================================================

@pytest.mark.asyncio
async def test_pm_slash_command_in_pm_alerts_succeeds():
    """21. /pm command invoked in #pm-alerts succeeds normally."""
    handler = DiscordSlashCommandHandler()
    res = await handler.execute_subcommand(
        subcommand="help",
        options={},
        discord_user_id="123456789",
        channel_id=settings.DISCORD_PM_CHANNEL_ID,
    )
    assert "PM Commands" in str(res)


@pytest.mark.asyncio
async def test_pm_slash_command_in_notifications_rejected():
    """22. /pm command invoked in #notifications is strictly rejected."""
    handler = DiscordSlashCommandHandler()
    res = await handler.execute_subcommand(
        subcommand="status",
        options={"ticket": "HCF7-634"},
        discord_user_id="123456789",
        channel_id="notifications",
    )
    assert str(res) == "❌ PM commands can only be used in #pm-alerts. Please use #pm-alerts for PM operations."


@pytest.mark.asyncio
async def test_pm_slash_command_in_general_rejected():
    """23. /pm command invoked in #general is strictly rejected."""
    handler = DiscordSlashCommandHandler()
    res = await handler.execute_subcommand(
        subcommand="status",
        options={"ticket": "HCF7-634"},
        discord_user_id="123456789",
        channel_id="general",
    )
    assert str(res) == "❌ PM commands can only be used in #pm-alerts. Please use #pm-alerts for PM operations."


@pytest.mark.asyncio
async def test_pm_slash_command_in_engineering_rejected():
    """24. /pm command invoked in #engineering is strictly rejected."""
    handler = DiscordSlashCommandHandler()
    res = await handler.execute_subcommand(
        subcommand="status",
        options={"ticket": "HCF7-634"},
        discord_user_id="123456789",
        channel_id="engineering",
    )
    assert str(res) == "❌ PM commands can only be used in #pm-alerts. Please use #pm-alerts for PM operations."


@pytest.mark.asyncio
async def test_pm_slash_command_in_arbitrary_channel_rejected():
    """25. /pm command invoked in arbitrary channel (#random, #dev-chat) is strictly rejected."""
    handler = DiscordSlashCommandHandler()
    res = await handler.execute_subcommand(
        subcommand="status",
        options={"ticket": "HCF7-634"},
        discord_user_id="123456789",
        channel_id="dev-chat",
    )
    assert str(res) == "❌ PM commands can only be used in #pm-alerts. Please use #pm-alerts for PM operations."


@pytest.mark.asyncio
async def test_pm_slash_command_with_none_channel_rejected():
    """26. /pm command invoked with channel_id=None is strictly rejected."""
    handler = DiscordSlashCommandHandler()
    res = await handler.execute_subcommand(
        subcommand="status",
        options={"ticket": "HCF7-634"},
        discord_user_id="123456789",
        channel_id=None,
    )
    assert str(res) == "❌ PM commands can only be used in #pm-alerts. Please use #pm-alerts for PM operations."


@pytest.mark.asyncio
async def test_pm_slash_command_with_empty_channel_rejected():
    """27. /pm command invoked with empty/whitespace channel_id is strictly rejected."""
    handler = DiscordSlashCommandHandler()
    res = await handler.execute_subcommand(
        subcommand="status",
        options={"ticket": "HCF7-634"},
        discord_user_id="123456789",
        channel_id="   ",
    )
    assert str(res) == "❌ PM commands can only be used in #pm-alerts. Please use #pm-alerts for PM operations."


@pytest.mark.asyncio
async def test_pm_slash_command_rejection_no_outbound_webhook(temp_db):
    """28. /pm command rejection does NOT trigger any outbound webhook action."""
    handler = DiscordSlashCommandHandler(manager=temp_db)
    with patch("app.core.actions.engine.action_engine.execute", new_callable=AsyncMock) as mock_exec:
        res = await handler.execute_subcommand(
            subcommand="worklog",
            options={},
            discord_user_id="123456789",
            channel_id="#notifications",
        )
        assert str(res) == "❌ PM commands can only be used in #pm-alerts. Please use #pm-alerts for PM operations."
        assert not mock_exec.called


@pytest.mark.asyncio
async def test_pm_slash_command_rbac_preserved():
    """29. Existing RBAC authorization checks remain intact with channel restriction."""
    prev = settings.DISCORD_PM_ALLOWED_USERS
    settings.DISCORD_PM_ALLOWED_USERS = "999888777"
    try:
        handler = DiscordSlashCommandHandler()
        # Unauthorized user in #pm-alerts
        unauth_res = await handler.execute_subcommand(
            subcommand="help",
            options={},
            discord_user_id="111222333",  # Not in allowlist
            channel_id=settings.DISCORD_PM_CHANNEL_ID,
        )
        assert "❌ You are not authorized to use PM commands." in str(unauth_res)

        # Authorized user in #pm-alerts
        auth_res = await handler.execute_subcommand(
            subcommand="help",
            options={},
            discord_user_id="999888777",  # In allowlist
            channel_id=settings.DISCORD_PM_CHANNEL_ID,
        )
        assert "PM Commands" in str(auth_res)
    finally:
        settings.DISCORD_PM_ALLOWED_USERS = prev
