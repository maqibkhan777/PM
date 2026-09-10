"""Tests for notification relevance, mention detection, assignment alerts, and clickable Jira links."""

import pytest
from app.config.settings import settings
from app.services.user_identity_service import user_identity_service
from app.connectors.jira.normalizer import JiraEventNormalizer
from app.connectors.discord.formatter import DiscordFormatter
from app.core.events.types import TaskCommentAdded, TaskAssigned, StaleTask, OverdueTask, TaskReopened
from app.core.rules.builtin import (
    CommentNotificationRule,
    AssignmentRule,
    StaleTaskRule,
    OverdueRule,
    ReopenedRule,
)
from app.services.notification_deduplication import notification_dedup_service
from app.core.models.enums import ActionType


@pytest.fixture(autouse=True)
def setup_user_identity():
    """Configure predictable user identity for tests."""
    user_identity_service.set_identity(
        account_id="jira-user-me-123",
        email="me@example.com",
        display_name="Aqib Khan"
    )
    prev_stale = settings.STALE_TASK_NOTIFY_PM
    prev_overdue = settings.OVERDUE_NOTIFY_PM
    prev_all_comments = settings.COMMENT_NOTIFY_ALL
    prev_team_assign = settings.ASSIGNMENT_NOTIFY_TEAM
    prev_base_url = settings.JIRA_BASE_URL

    settings.STALE_TASK_NOTIFY_PM = False
    settings.OVERDUE_NOTIFY_PM = False
    settings.COMMENT_NOTIFY_ALL = False
    settings.ASSIGNMENT_NOTIFY_TEAM = True
    settings.JIRA_BASE_URL = "https://custom-jira.example.com"

    notification_dedup_service.clear_all()
    yield

    settings.STALE_TASK_NOTIFY_PM = prev_stale
    settings.OVERDUE_NOTIFY_PM = prev_overdue
    settings.COMMENT_NOTIFY_ALL = prev_all_comments
    settings.ASSIGNMENT_NOTIFY_TEAM = prev_team_assign
    settings.JIRA_BASE_URL = prev_base_url
    notification_dedup_service.clear_all()


def test_clickable_jira_link_generation():
    """Verify that Jira issue keys are formatted into clickable Markdown URLs using JIRA_BASE_URL."""
    link_1 = DiscordFormatter.format_jira_link("WSSS-326")
    assert link_1 == "[WSSS-326](https://custom-jira.example.com/browse/WSSS-326)"

    link_2 = DiscordFormatter.format_jira_link("PROJ-999")
    assert link_2 == "[PROJ-999](https://custom-jira.example.com/browse/PROJ-999)"

    embed = DiscordFormatter.format_reopened_task(
        task_key="WSSS-326",
        task_title="Fix bug",
        reopened_by="Alice",
        prev_status="Done",
        new_status="In Progress"
    )
    assert embed["embeds"][0]["url"] == "https://custom-jira.example.com/browse/WSSS-326"
    assert "[WSSS-326](https://custom-jira.example.com/browse/WSSS-326)" in embed["embeds"][0]["description"]


def test_mention_extraction_from_adf_and_text():
    """Verify extraction of mentioned account IDs and display names from ADF and plain text."""
    # 1. ADF mention node
    adf_doc = {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {
                        "type": "mention",
                        "attrs": {
                            "id": "jira-user-me-123",
                            "text": "@Aqib Khan",
                            "userType": "DEFAULT"
                        }
                    },
                    {"type": "text", "text": " please review this fix."}
                ]
            }
        ]
    }
    text, acc_ids, names = JiraEventNormalizer.extract_adf_text_and_mentions(adf_doc)
    assert "jira-user-me-123" in acc_ids
    assert "Aqib Khan" in names
    assert "@Aqib Khan please review this fix." in text

    # 2. Jira wiki markup string
    wiki_text = "Hey [~accountid:jira-user-me-123] check this out!"
    text2, acc_ids2, _ = JiraEventNormalizer.extract_adf_text_and_mentions(wiki_text)
    assert "jira-user-me-123" in acc_ids2


def test_customer_support_reply_without_mention_produces_no_notification():
    """Customer or support reply on team ticket without mention must NOT notify PM."""
    rule = CommentNotificationRule()

    event = TaskCommentAdded(
        source="jira",
        external_event_id="jira:WSSS-100:comment:1",
        timestamp="2026-09-10T10:00:00Z",
        actor_id="support-agent-mubashir",
        actor_name="Mubashir Butt",
        task_key="WSSS-100",
        comment_id="1",
        comment_body="We have received your ticket and are investigating.",
        mentioned_account_ids=["customer-account-456"],
        mentioned_display_names=["Customer John"],
        payload={"issue": {"fields": {"summary": "Support request"}}}
    )

    actions = rule.evaluate(event)
    assert len(actions) == 0, "Support reply without PM mention should be suppressed from Discord"


def test_comment_mentioning_me_produces_personal_discord_notification():
    """Comment mentioning my account ID must trigger high-priority personal Discord alert."""
    rule = CommentNotificationRule()

    event = TaskCommentAdded(
        source="jira",
        external_event_id="jira:WSSS-101:comment:2",
        timestamp="2026-09-10T10:05:00Z",
        actor_id="colleague-ahmed",
        actor_name="Ahmed Raza",
        task_key="WSSS-101",
        comment_id="2",
        comment_body="@Aqib Khan can you check this blocker?",
        mentioned_account_ids=["jira-user-me-123"],
        mentioned_display_names=["Aqib Khan"],
        payload={"issue": {"fields": {"summary": "Critical API bug", "status": {"name": "In Progress"}}}}
    )

    actions = rule.evaluate(event)
    assert len(actions) == 1
    action = actions[0]
    assert action.action_type == ActionType.SEND_NOTIFICATION
    assert "You were mentioned on WSSS-101" in action.parameters.get("title", "")
    assert "[WSSS-101](https://custom-jira.example.com/browse/WSSS-101)" in action.parameters["embeds"][0]["description"]


def test_ticket_assigned_to_me_produces_personal_notification():
    """Assignment to my account ID triggers 'Task Assigned to You' notification."""
    rule = AssignmentRule()

    event = TaskAssigned(
        source="jira",
        external_event_id="jira:WSSS-102:assign:1",
        timestamp="2026-09-10T10:10:00Z",
        actor_id="manager-mursaleen",
        actor_name="Mursaleen",
        task_key="WSSS-102",
        old_assignee_id="someone-else",
        old_assignee_name="Daniyal",
        new_assignee_id="jira-user-me-123",
        new_assignee_name="Aqib Khan",
        payload={"issue": {"fields": {"summary": "Lead sprint planning"}}}
    )

    actions = rule.evaluate(event)
    assert len(actions) == 1
    action = actions[0]
    assert "Task Assigned to You — WSSS-102" in action.parameters.get("title", "")
    assert action.parameters.get("level") == "SUCCESS"
    assert "[WSSS-102](https://custom-jira.example.com/browse/WSSS-102)" in action.parameters["embeds"][0]["description"]


def test_ticket_assigned_to_another_team_member_produces_team_awareness_notification():
    """Assignment to another team member triggers team awareness notification when configured."""
    rule = AssignmentRule()

    event = TaskAssigned(
        source="jira",
        external_event_id="jira:WSSS-103:assign:2",
        timestamp="2026-09-10T10:15:00Z",
        actor_id="manager-mursaleen",
        actor_name="Mursaleen",
        task_key="WSSS-103",
        old_assignee_id=None,
        old_assignee_name="Unassigned",
        new_assignee_id="teammate-daniyal",
        new_assignee_name="Daniyal Raza",
        payload={"issue": {"fields": {"summary": "Frontend polishing"}}}
    )

    actions = rule.evaluate(event)
    assert len(actions) == 1
    action = actions[0]
    assert "Task Assigned — WSSS-103" in action.parameters.get("title", "")
    assert action.parameters.get("level") == "INFO"

    # If ASSIGNMENT_NOTIFY_TEAM is disabled, no action should be emitted
    settings.ASSIGNMENT_NOTIFY_TEAM = False
    notification_dedup_service.clear_all()
    actions_disabled = rule.evaluate(event)
    assert len(actions_disabled) == 0


def test_stale_and_overdue_candidates_do_not_dispatch_discord_notifications():
    """Stale and overdue rules must NOT dispatch Discord notifications when STALE/OVERDUE_NOTIFY_PM=False."""
    prev_stale = settings.STALE_TASK_NOTIFY_PM
    prev_overdue = settings.OVERDUE_NOTIFY_PM
    settings.STALE_TASK_NOTIFY_PM = False
    settings.OVERDUE_NOTIFY_PM = False
    try:
        stale_rule = StaleTaskRule()
        stale_event = StaleTask(
            source="scheduler",
            task_key="WSSS-104",
            task_title="Stale work item",
            assignee_name="Daniyal",
            hours_inactive=48.0,
            threshold_hours=24
        )
        stale_actions = stale_rule.evaluate(stale_event)
        discord_stale = [a for a in stale_actions if a.target_system == "discord"]
        assert len(discord_stale) == 0, "Stale PM Discord notification must be muted"

        overdue_rule = OverdueRule()
        overdue_event = OverdueTask(
            source="scheduler",
            task_key="WSSS-105",
            task_title="Overdue work item",
            assignee_name="Ahmed",
            due_date="2026-09-01",
            current_status="In Progress"
        )
        overdue_actions = overdue_rule.evaluate(overdue_event)
        discord_overdue = [a for a in overdue_actions if a.target_system == "discord"]
        assert len(discord_overdue) == 0, "Overdue PM Discord notification must be muted"
    finally:
        settings.STALE_TASK_NOTIFY_PM = prev_stale
        settings.OVERDUE_NOTIFY_PM = prev_overdue
