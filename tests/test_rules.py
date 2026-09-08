"""Unit tests for the 5 built-in workflow rules."""

import pytest
from app.core.rules.builtin import (
    ActiveWorkRule,
    StaleTaskRule,
    OverdueRule,
    BlockedRule,
    ReopenedRule,
)
from app.core.events.types import (
    TaskCommentAdded,
    TaskStatusChanged,
    TaskReopened,
    StaleTask,
    OverdueTask,
    TaskBlocked,
)
from app.core.models.enums import ActionType
from app.services.notification_deduplication import notification_dedup_service


@pytest.fixture(autouse=True)
def clean_dedup():
    """Ensure clean notification history for each rule test."""
    notification_dedup_service.clear_all()
    yield
    notification_dedup_service.clear_all()


def test_active_work_rule_triggers_on_todo_comment():
    """Rule 1: If TaskStatus = 'To Do' and comment added -> WorkflowViolation."""
    rule = ActiveWorkRule()
    event = TaskCommentAdded(
        source="jira",
        task_key="CF7-ActiveWorkTest",
        comment_body="Starting development now.",
        actor_name="Ahsan Amin",
        payload={
            "issue": {
                "fields": {
                    "status": {"name": "To Do"},
                    "summary": "Payment Integration",
                    "project": {"key": "CF7", "name": "CF7 Apps"}
                }
            }
        }
    )
    actions = rule.evaluate(event)
    assert len(actions) == 1
    action = actions[0]
    assert action.action_type == ActionType.SEND_NOTIFICATION
    assert action.target_system == "discord"
    assert "Jira Workflow Alert" in action.parameters.get("title", "")


def test_active_work_rule_does_not_trigger_on_in_progress():
    """Rule 1 should not trigger when status is already 'In Progress'."""
    rule = ActiveWorkRule()
    event = TaskCommentAdded(
        source="jira",
        task_key="CF7-ActiveWorkTest-2",
        comment_body="Still working on this.",
        payload={"issue": {"fields": {"status": {"name": "In Progress"}}}}
    )
    actions = rule.evaluate(event)
    assert len(actions) == 0


def test_stale_task_rule_triggers_on_inactivity():
    """Rule 2: If task is 'In Progress' and inactive >24h -> Discord alert + Mattermost DM."""
    rule = StaleTaskRule(configuration={"threshold_hours": 24})
    event = StaleTask(
        source="scheduler",
        task_key="CF7-StaleTest-1",
        task_title="Payment Gateway Testing",
        assignee_id="jira-user-ahsan",
        assignee_name="Ahsan Amin",
        hours_inactive=28.5,
        threshold_hours=24
    )
    actions = rule.evaluate(event)
    # Should produce 2 actions: 1 Discord alert + 1 Mattermost DM reminder
    assert len(actions) == 2
    action_types = {a.target_system: a.action_type for a in actions}
    assert action_types["discord"] == ActionType.SEND_NOTIFICATION
    assert action_types["mattermost"] == ActionType.SEND_MESSAGE


def test_overdue_rule_triggers_on_past_due():
    """Rule 3: If due_date < now and status != Done -> Overdue alert."""
    rule = OverdueRule()
    event = OverdueTask(
        source="scheduler",
        task_key="CF7-OverdueTest-1",
        task_title="Deliverable Alpha",
        assignee_name="John Doe",
        due_date="2026-01-01T00:00:00Z",
        current_status="In Progress"
    )
    actions = rule.evaluate(event)
    assert len(actions) == 1
    assert actions[0].target_system == "discord"
    assert "Overdue" in actions[0].parameters.get("title", "")


def test_blocked_rule_triggers_on_blocked_status():
    """Rule 4: If task is moved to Blocked -> Blocked alert."""
    rule = BlockedRule()
    event = TaskStatusChanged(
        source="jira",
        task_key="CF7-BlockedTest-1",
        old_status="In Progress",
        new_status="Blocked",
        actor_name="Developer",
        payload={"issue": {"fields": {"summary": "Third party API outage"}}}
    )
    actions = rule.evaluate(event)
    assert len(actions) == 1
    assert "Blocked" in actions[0].parameters.get("title", "")


def test_reopened_rule_triggers_on_done_to_active():
    """Rule 5: If task moved from Done to In Progress -> Reopened alert."""
    rule = ReopenedRule()
    event = TaskReopened(
        source="jira",
        task_key="CF7-ReopenedTest-1",
        previous_status="Done",
        new_status="In Progress",
        actor_name="QA Engineer",
        payload={"issue": {"fields": {"summary": "Regression found in build"}}}
    )
    actions = rule.evaluate(event)
    assert len(actions) == 1
    assert "Reopened" in actions[0].parameters.get("title", "")
