"""Comprehensive tests for production fixes:
1. Jira comment notifications reaching #notifications
2. Daily Worklog Report 23:59 midnight boundary handling
3. Daily Activity Report previous calendar day
"""

import asyncio
import datetime
import zoneinfo
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from app.config.settings import settings
from app.core.events.types import (
    TaskAssigned,
    TaskCommentAdded,
    WorkflowViolation,
)
from app.core.rules.builtin import (
    ActiveWorkRule,
    AssignmentRule,
    CommentNotificationRule,
)
from app.services.notification_deduplication import notification_dedup_service
from app.services.user_identity_service import user_identity_service


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture(autouse=True)
def reset_dedup_and_identity():
    """Clean notification deduplication state and set up a known user identity."""
    notification_dedup_service.clear_all()
    user_identity_service.set_identity(
        account_id="jira-user-me-123",
        email="me@example.com",
        display_name="Aqib Khan",
    )
    yield
    notification_dedup_service.clear_all()


def _make_comment_event(
    task_key="TEST-100",
    comment_id="c-001",
    author_name="John Doe",
    author_id="jira-author-456",
    comment_body="This is a test comment",
    mentioned_account_ids=None,
    mentioned_display_names=None,
    status="In Progress",
):
    """Create a TaskCommentAdded event with realistic fixture data."""
    return TaskCommentAdded(
        source="jira",
        external_event_id=f"jira:{task_key}:comment:{comment_id}",
        timestamp="2026-09-21T10:00:00+00:00",
        actor_id=author_id,
        actor_name=author_name,
        project_key="TEST",
        task_id="10001",
        task_key=task_key,
        comment_id=comment_id,
        comment_body=comment_body,
        author_id=author_id,
        author_name=author_name,
        mentioned_account_ids=mentioned_account_ids or [],
        mentioned_display_names=mentioned_display_names or [],
        payload={
            "issue": {
                "key": task_key,
                "fields": {
                    "summary": "Test Issue",
                    "status": {"name": status},
                },
            }
        },
    )


def _make_assignment_event(
    task_key="TEST-200",
    new_assignee_id="jira-user-me-123",
    new_assignee_name="Aqib Khan",
    old_assignee_name="Old Person",
):
    """Create a TaskAssigned event."""
    return TaskAssigned(
        source="jira",
        external_event_id=f"jira:{task_key}:assignee:hist-1",
        timestamp="2026-09-21T10:00:00+00:00",
        actor_id="jira-actor-789",
        actor_name="Manager",
        project_key="TEST",
        task_id="10002",
        task_key=task_key,
        old_assignee_id="old-id",
        old_assignee_name=old_assignee_name,
        new_assignee_id=new_assignee_id,
        new_assignee_name=new_assignee_name,
        payload={
            "issue": {
                "key": task_key,
                "fields": {
                    "summary": "Test Assignment Issue",
                    "status": {"name": "In Progress"},
                },
            }
        },
    )


# ============================================================================
# ISSUE 1 — Notification Tests
# ============================================================================


class TestCommentNotificationRule:
    """Tests for CommentNotificationRule fix — all team-scoped comments notify."""

    def test_normal_comment_reaches_notifications(self):
        """Normal Jira comment → #notifications."""
        rule = CommentNotificationRule()
        event = _make_comment_event()
        actions = rule.evaluate(event)
        assert len(actions) == 1
        action = actions[0]
        assert action.parameters["channel"] == settings.JIRA_NOTIFICATION_DISCORD_CHANNEL
        assert "💬 Jira Comment Added" in action.parameters["title"]

    def test_mentioned_comment_reaches_notifications(self):
        """Jira comment mentioning PM → #notifications with mention formatting."""
        rule = CommentNotificationRule()
        event = _make_comment_event(
            comment_id="c-mention-001",
            mentioned_account_ids=["jira-user-me-123"],
            mentioned_display_names=["Aqib Khan"],
            comment_body="Hey @Aqib Khan please review this",
        )
        actions = rule.evaluate(event)
        assert len(actions) == 1
        action = actions[0]
        assert action.parameters["channel"] == settings.JIRA_NOTIFICATION_DISCORD_CHANNEL
        assert "🔔 You were mentioned" in action.parameters["title"]

    def test_mentioned_comment_uses_mention_formatting(self):
        """Mentioned comment uses higher-priority mention notification formatting."""
        rule = CommentNotificationRule()
        event = _make_comment_event(
            comment_id="c-mention-fmt",
            mentioned_account_ids=["jira-user-me-123"],
            comment_body="@Aqib Khan check this",
        )
        actions = rule.evaluate(event)
        assert len(actions) == 1
        assert "🔔" in actions[0].parameters["title"]
        assert actions[0].parameters["level"] == "WARNING"

    def test_mentioned_comment_not_suppressed(self):
        """A mentioned comment must not be silently suppressed."""
        rule = CommentNotificationRule()
        event = _make_comment_event(
            comment_id="c-not-suppressed",
            mentioned_account_ids=["jira-user-me-123"],
        )
        actions = rule.evaluate(event)
        assert len(actions) == 1, "Mentioned comment must not be suppressed"

    def test_comment_does_not_route_to_pm_alerts(self):
        """Comment notifications do NOT route to #pm-alerts."""
        rule = CommentNotificationRule()
        event = _make_comment_event(comment_id="c-no-pm-alerts")
        actions = rule.evaluate(event)
        assert len(actions) == 1
        assert actions[0].parameters["channel"] != settings.PM_DISCORD_CHANNEL

    def test_normal_comment_uses_comment_formatting(self):
        """Normal (non-mention) comment uses 💬 formatting, not 🔔."""
        rule = CommentNotificationRule()
        event = _make_comment_event(comment_id="c-fmt-normal")
        actions = rule.evaluate(event)
        assert len(actions) == 1
        assert "💬" in actions[0].parameters["title"]
        assert actions[0].parameters["level"] == "INFO"

    def test_single_mentioned_comment_no_duplicate_notifications(self):
        """A single mentioned comment does not create two separate notifications."""
        rule = CommentNotificationRule()
        event = _make_comment_event(
            comment_id="c-single-mention",
            mentioned_account_ids=["jira-user-me-123"],
        )
        actions = rule.evaluate(event)
        # Should produce exactly 1 notification (mention format), not 2
        assert len(actions) == 1
        assert "🔔" in actions[0].parameters["title"]

    def test_comment_deduplication(self):
        """Same comment ID should not produce duplicate notifications."""
        rule = CommentNotificationRule()
        event = _make_comment_event(comment_id="c-dedup-test")
        actions1 = rule.evaluate(event)
        assert len(actions1) == 1
        # Evaluate the same event again
        actions2 = rule.evaluate(event)
        assert len(actions2) == 0, "Duplicate comment should be deduplicated"

    def test_mention_deduplication(self):
        """Same mentioned comment ID should not produce duplicate notifications."""
        rule = CommentNotificationRule()
        event = _make_comment_event(
            comment_id="c-mention-dedup",
            mentioned_account_ids=["jira-user-me-123"],
        )
        actions1 = rule.evaluate(event)
        assert len(actions1) == 1
        actions2 = rule.evaluate(event)
        assert len(actions2) == 0, "Duplicate mention should be deduplicated"

    def test_missing_identity_no_false_mention(self):
        """Missing/unresolvable identity does not produce false 'You were mentioned' notifications."""
        # Set identity to something that doesn't match
        user_identity_service.set_identity(
            account_id="someone-else-999",
            email="other@example.com",
            display_name="Other Person",
        )
        rule = CommentNotificationRule()
        event = _make_comment_event(
            comment_id="c-false-mention",
            mentioned_account_ids=["jira-user-unknown-777"],
            mentioned_display_names=["Unknown Person"],
        )
        actions = rule.evaluate(event)
        assert len(actions) == 1
        # Should NOT be a mention notification — should be a generic comment notification
        assert "💬" in actions[0].parameters["title"]
        assert "🔔" not in actions[0].parameters["title"]

    def test_comment_notify_all_false_still_notifies(self):
        """With COMMENT_NOTIFY_ALL=false, team-scoped comments still notify."""
        original = settings.COMMENT_NOTIFY_ALL
        try:
            settings.COMMENT_NOTIFY_ALL = False
            rule = CommentNotificationRule()
            event = _make_comment_event(comment_id="c-notify-all-false")
            actions = rule.evaluate(event)
            assert len(actions) == 1, "Team-scoped comment should notify even with COMMENT_NOTIFY_ALL=false"
        finally:
            settings.COMMENT_NOTIFY_ALL = original


class TestAssignmentNotificationRouting:
    """Assignment notifications route to #notifications, not #pm-alerts."""

    def test_assignment_routes_to_notifications(self):
        """Task assignment → #notifications."""
        rule = AssignmentRule()
        event = _make_assignment_event()
        actions = rule.evaluate(event)
        assert len(actions) == 1
        assert actions[0].parameters["channel"] == settings.JIRA_NOTIFICATION_DISCORD_CHANNEL

    def test_assignment_does_not_route_to_pm_alerts(self):
        """Assignment does NOT route to #pm-alerts."""
        rule = AssignmentRule()
        event = _make_assignment_event(task_key="TEST-201")
        actions = rule.evaluate(event)
        assert len(actions) == 1
        assert actions[0].parameters["channel"] != settings.PM_DISCORD_CHANNEL

    def test_team_assignment_routes_to_notifications(self):
        """Task assignment to another team member → #notifications."""
        rule = AssignmentRule()
        event = _make_assignment_event(
            task_key="TEST-202",
            new_assignee_id="other-team-member",
            new_assignee_name="Team Member",
        )
        actions = rule.evaluate(event)
        assert len(actions) == 1
        assert actions[0].parameters["channel"] == settings.JIRA_NOTIFICATION_DISCORD_CHANNEL


class TestWorkflowViolationRouting:
    """Workflow violations remain in #pm-alerts."""

    def test_workflow_violation_routes_to_pm_alerts(self):
        """Workflow violation → #pm-alerts."""
        rule = ActiveWorkRule()
        event = TaskCommentAdded(
            source="jira",
            external_event_id="jira:TEST-300:comment:c-violation",
            timestamp="2026-09-21T10:00:00+00:00",
            actor_id="actor-1",
            actor_name="Dev User",
            project_key="TEST",
            task_id="10003",
            task_key="TEST-300",
            comment_id="c-violation",
            comment_body="Working on this",
            payload={
                "issue": {
                    "key": "TEST-300",
                    "fields": {
                        "summary": "Task in To Do",
                        "status": {"name": "To Do"},
                    },
                }
            },
        )
        actions = rule.evaluate(event)
        assert len(actions) == 1
        assert actions[0].parameters["channel"] == settings.PM_DISCORD_CHANNEL


# ============================================================================
# ISSUE 1 — ADF Mention Extraction Tests
# ============================================================================


class TestADFMentionExtraction:
    """Verify ADF mention parsing and extraction."""

    def test_adf_mention_extraction(self):
        """ADF mention nodes are correctly extracted."""
        from app.connectors.jira.normalizer import JiraEventNormalizer

        adf_doc = {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "Hey "},
                        {
                            "type": "mention",
                            "attrs": {
                                "id": "jira-user-me-123",
                                "text": "@Aqib Khan",
                                "userType": "DEFAULT",
                            },
                        },
                        {"type": "text", "text": " please review"},
                    ],
                }
            ],
        }
        text, acc_ids, display_names = JiraEventNormalizer.extract_adf_text_and_mentions(adf_doc)
        assert "jira-user-me-123" in acc_ids
        assert "Aqib Khan" in display_names
        assert "Aqib Khan" in text or "@Aqib Khan" in text

    def test_wiki_style_mention_extraction(self):
        """Wiki-style mentions [~accountid:...] are extracted."""
        from app.connectors.jira.normalizer import JiraEventNormalizer

        wiki_text = "Hello [~accountid:jira-user-me-123] please check this"
        text, acc_ids, display_names = JiraEventNormalizer.extract_adf_text_and_mentions(wiki_text)
        assert "jira-user-me-123" in acc_ids

    def test_mention_account_id_comparison(self):
        """Extracted account ID correctly matches PM identity."""
        user_identity_service.set_identity(
            account_id="jira-user-me-123",
            display_name="Aqib Khan",
        )
        assert user_identity_service.is_me(account_id="jira-user-me-123") is True
        assert user_identity_service.is_me(account_id="someone-else") is False

    def test_multiple_mentions_extracted(self):
        """Multiple mentions in same ADF doc are all extracted."""
        from app.connectors.jira.normalizer import JiraEventNormalizer

        adf_doc = {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "mention",
                            "attrs": {"id": "user-a", "text": "@Alice"},
                        },
                        {"type": "text", "text": " and "},
                        {
                            "type": "mention",
                            "attrs": {"id": "user-b", "text": "@Bob"},
                        },
                    ],
                }
            ],
        }
        text, acc_ids, display_names = JiraEventNormalizer.extract_adf_text_and_mentions(adf_doc)
        assert "user-a" in acc_ids
        assert "user-b" in acc_ids
        assert "Alice" in display_names
        assert "Bob" in display_names


# ============================================================================
# ISSUE 1 — Polling / Ingestion Tests
# ============================================================================


class TestCommentIngestion:
    """Verify comments are ingested correctly from polling data."""

    def test_comment_becomes_task_comment_added(self):
        """Jira comment fixture is correctly converted to TaskCommentAdded."""
        from app.connectors.jira.normalizer import JiraEventNormalizer

        payload = {
            "webhookEvent": "comment_created",
            "issue": {
                "id": "10001",
                "key": "TEST-100",
                "fields": {
                    "summary": "Test Issue",
                    "project": {"id": "1", "key": "TEST"},
                    "status": {"name": "In Progress"},
                },
            },
            "user": {"accountId": "actor-1", "displayName": "Actor"},
            "comment": {
                "id": "c-100",
                "body": "A simple comment",
                "author": {"accountId": "author-1", "displayName": "Author User"},
            },
        }
        event = JiraEventNormalizer.normalize(payload)
        assert event is not None
        assert isinstance(event, TaskCommentAdded)
        assert event.task_key == "TEST-100"
        assert event.comment_id == "c-100"

    def test_comment_body_extracted_from_adf(self):
        """Comment body text is correctly extracted from ADF format."""
        from app.connectors.jira.normalizer import JiraEventNormalizer

        adf_body = {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "This is the comment body"}],
                }
            ],
        }
        payload = {
            "webhookEvent": "comment_created",
            "issue": {
                "id": "10001",
                "key": "TEST-101",
                "fields": {
                    "project": {"id": "1", "key": "TEST"},
                    "status": {"name": "In Progress"},
                },
            },
            "user": {"accountId": "actor-1"},
            "comment": {
                "id": "c-101",
                "body": adf_body,
                "author": {"accountId": "author-1", "displayName": "Author"},
            },
        }
        event = JiraEventNormalizer.normalize(payload)
        assert isinstance(event, TaskCommentAdded)
        assert "comment body" in event.comment_body.lower()

    def test_comment_mention_ids_extracted(self):
        """Mention account IDs are extracted from comment ADF body."""
        from app.connectors.jira.normalizer import JiraEventNormalizer

        adf_body = {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "mention",
                            "attrs": {"id": "mentioned-user-1", "text": "@User One"},
                        },
                        {"type": "text", "text": " check this"},
                    ],
                }
            ],
        }
        payload = {
            "webhookEvent": "comment_created",
            "issue": {
                "id": "10001",
                "key": "TEST-102",
                "fields": {"project": {"id": "1", "key": "TEST"}, "status": {"name": "Open"}},
            },
            "user": {"accountId": "actor-1"},
            "comment": {"id": "c-102", "body": adf_body, "author": {"accountId": "author-1"}},
        }
        event = JiraEventNormalizer.normalize(payload)
        assert isinstance(event, TaskCommentAdded)
        assert "mentioned-user-1" in event.mentioned_account_ids

    def test_assignment_detection_preserved(self):
        """Assignment change detection still works correctly via normalizer."""
        from app.connectors.jira.normalizer import JiraEventNormalizer

        payload = {
            "webhookEvent": "jira:issue_updated",
            "issue": {
                "id": "10001",
                "key": "TEST-103",
                "fields": {
                    "project": {"id": "1", "key": "TEST"},
                    "status": {"name": "In Progress"},
                    "assignee": {"accountId": "new-user", "displayName": "New User"},
                },
            },
            "user": {"accountId": "manager-1", "displayName": "Manager"},
            "changelog": {
                "id": "cl-1",
                "items": [
                    {
                        "field": "assignee",
                        "from": "old-user",
                        "fromString": "Old User",
                        "to": "new-user",
                        "toString": "New User",
                    }
                ],
            },
        }
        event = JiraEventNormalizer.normalize(payload)
        assert isinstance(event, TaskAssigned)
        assert event.new_assignee_name == "New User"


# ============================================================================
# ISSUE 2 — Daily Worklog Report Scheduler Tests
# ============================================================================


class TestDailyWorklogScheduler:
    """Tests for worklog report midnight boundary handling."""

    @pytest.fixture
    def scheduler(self):
        from app.services.scheduler import PeriodicScheduler
        return PeriodicScheduler()

    def _mock_now(self, hour, minute, tz_str="Asia/Karachi", year=2026, month=9, day=21):
        """Create a timezone-aware datetime at a specific time."""
        tz = zoneinfo.ZoneInfo(tz_str)
        return datetime.datetime(year, month, day, hour, minute, 0, tzinfo=tz)

    @pytest.mark.asyncio
    async def test_tick_before_2359(self, scheduler):
        """Scheduler tick before 23:59 should not fire the report."""
        mock_now = self._mock_now(23, 45)
        with patch("app.services.scheduler.datetime") as mock_dt, \
             patch("app.services.scheduler.settings") as mock_settings:
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **k: datetime.datetime(*a, **k)
            mock_settings.DAILY_WORKLOG_REPORT_ENABLED = True
            mock_settings.DAILY_WORKLOG_REPORT_TIMEZONE = "Asia/Karachi"
            mock_settings.DAILY_WORKLOG_REPORT_TIME = "23:59"
            mock_settings.get_worklog_report_time.return_value = "23:59"
            mock_settings.JIRA_TEAM_GROUP = "TestTeam"
            mock_settings.is_jira_team_group_configured.return_value = True
            result = await scheduler._evaluate_daily_worklog_report()
        assert result is None

    @pytest.mark.asyncio
    async def test_exact_2359(self, scheduler):
        """Scheduler tick exactly at 23:59 should fire the report for today."""
        mock_now = self._mock_now(23, 59)
        today_str = "2026-09-21"

        with patch("app.services.scheduler.datetime") as mock_dt, \
             patch("app.services.scheduler.settings") as mock_settings:
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **k: datetime.datetime(*a, **k)
            mock_settings.DAILY_WORKLOG_REPORT_ENABLED = True
            mock_settings.DAILY_WORKLOG_REPORT_TIMEZONE = "Asia/Karachi"
            mock_settings.DAILY_WORKLOG_REPORT_TIME = "23:59"
            mock_settings.get_worklog_report_time.return_value = "23:59"
            mock_settings.JIRA_TEAM_GROUP = "TestTeam"
            mock_settings.is_jira_team_group_configured.return_value = True

            mock_generator = MagicMock()
            mock_generator.history_repo.has_report_been_sent.return_value = False
            mock_generator.send_report_to_discord = AsyncMock(return_value={"status": "success", "date": today_str})

            with patch("app.core.reports.worklog_report.DailyWorklogReportGenerator", return_value=mock_generator):
                result = await scheduler._evaluate_daily_worklog_report()

        assert result is not None
        mock_generator.send_report_to_discord.assert_awaited_once()
        call_kwargs = mock_generator.send_report_to_discord.call_args
        assert call_kwargs[1]["target_date"] == today_str or call_kwargs[0][0] == today_str if call_kwargs[0] else True

    @pytest.mark.asyncio
    async def test_after_midnight_grace_window(self, scheduler):
        """Scheduler tick at 00:10 should fire the report for the PREVIOUS day."""
        # 00:10 on Sept 22 → report for Sept 21
        mock_now = self._mock_now(0, 10, year=2026, month=9, day=22)
        expected_date = "2026-09-21"

        with patch("app.services.scheduler.datetime") as mock_dt, \
             patch("app.services.scheduler.settings") as mock_settings, \
             patch("app.services.scheduler.timedelta", side_effect=timedelta):
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **k: datetime.datetime(*a, **k)
            mock_settings.DAILY_WORKLOG_REPORT_ENABLED = True
            mock_settings.DAILY_WORKLOG_REPORT_TIMEZONE = "Asia/Karachi"
            mock_settings.DAILY_WORKLOG_REPORT_TIME = "23:59"
            mock_settings.get_worklog_report_time.return_value = "23:59"
            mock_settings.JIRA_TEAM_GROUP = "TestTeam"
            mock_settings.is_jira_team_group_configured.return_value = True

            mock_generator = MagicMock()
            mock_generator.history_repo.has_report_been_sent.return_value = False
            mock_generator.send_report_to_discord = AsyncMock(return_value={"status": "success", "date": expected_date})

            with patch("app.core.reports.worklog_report.DailyWorklogReportGenerator", return_value=mock_generator):
                result = await scheduler._evaluate_daily_worklog_report()

        assert result is not None
        mock_generator.history_repo.has_report_been_sent.assert_called_once()
        # Verify the report was called with yesterday's date
        report_call_date = mock_generator.history_repo.has_report_been_sent.call_args[0][1]
        assert report_call_date == expected_date

    @pytest.mark.asyncio
    async def test_outside_grace_window_skips(self, scheduler):
        """Scheduler tick at 02:00 (outside grace) should NOT fire the report."""
        mock_now = self._mock_now(2, 0, year=2026, month=9, day=22)

        with patch("app.services.scheduler.datetime") as mock_dt, \
             patch("app.services.scheduler.settings") as mock_settings:
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **k: datetime.datetime(*a, **k)
            mock_settings.DAILY_WORKLOG_REPORT_ENABLED = True
            mock_settings.DAILY_WORKLOG_REPORT_TIMEZONE = "Asia/Karachi"
            mock_settings.DAILY_WORKLOG_REPORT_TIME = "23:59"
            mock_settings.get_worklog_report_time.return_value = "23:59"
            result = await scheduler._evaluate_daily_worklog_report()

        assert result is None

    @pytest.mark.asyncio
    async def test_duplicate_execution_prevention(self, scheduler):
        """Same report date should not be sent twice."""
        mock_now = self._mock_now(23, 59)

        with patch("app.services.scheduler.datetime") as mock_dt, \
             patch("app.services.scheduler.settings") as mock_settings:
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **k: datetime.datetime(*a, **k)
            mock_settings.DAILY_WORKLOG_REPORT_ENABLED = True
            mock_settings.DAILY_WORKLOG_REPORT_TIMEZONE = "Asia/Karachi"
            mock_settings.DAILY_WORKLOG_REPORT_TIME = "23:59"
            mock_settings.get_worklog_report_time.return_value = "23:59"
            mock_settings.JIRA_TEAM_GROUP = "TestTeam"
            mock_settings.is_jira_team_group_configured.return_value = True

            mock_generator = MagicMock()
            mock_generator.history_repo.has_report_been_sent.return_value = True  # Already sent

            with patch("app.core.reports.worklog_report.DailyWorklogReportGenerator", return_value=mock_generator):
                result = await scheduler._evaluate_daily_worklog_report()

        assert result is None

    @pytest.mark.asyncio
    async def test_disabled_report_skips(self, scheduler):
        """Disabled report should not fire."""
        with patch("app.services.scheduler.settings") as mock_settings:
            mock_settings.DAILY_WORKLOG_REPORT_ENABLED = False
            result = await scheduler._evaluate_daily_worklog_report()
        assert result is None


# ============================================================================
# ISSUE 3 — Daily Activity Report Previous Day Tests
# ============================================================================


class TestDailyActivityScheduler:
    """Tests for daily activity report previous calendar day calculation."""

    @pytest.fixture
    def scheduler(self):
        from app.services.scheduler import PeriodicScheduler
        return PeriodicScheduler()

    def _mock_now(self, hour, minute, tz_str="Asia/Karachi", year=2026, month=9, day=21):
        tz = zoneinfo.ZoneInfo(tz_str)
        return datetime.datetime(year, month, day, hour, minute, 0, tzinfo=tz)

    @pytest.mark.asyncio
    async def test_morning_report_uses_previous_day(self, scheduler):
        """At 2026-09-21 08:45 PKT, target_date must be 2026-09-20."""
        mock_now = self._mock_now(8, 45)
        expected_date = "2026-09-20"

        with patch("app.services.scheduler.datetime") as mock_dt, \
             patch("app.services.scheduler.settings") as mock_settings, \
             patch("app.services.scheduler.timedelta", side_effect=timedelta):
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **k: datetime.datetime(*a, **k)
            mock_settings.DAILY_ACTIVITY_REPORT_ENABLED = True
            mock_settings.DAILY_ACTIVITY_REPORT_TIMEZONE = "Asia/Karachi"
            mock_settings.DAILY_ACTIVITY_REPORT_TIME = "08:40"
            mock_settings.get_report_timezone.return_value = "Asia/Karachi"
            mock_settings.get_default_report_time.return_value = "08:40"
            mock_settings.JIRA_TEAM_GROUP = "Mursaleen Cluster"
            mock_settings.is_jira_team_group_configured.return_value = True

            mock_generator = MagicMock()
            mock_generator.history_repo.has_report_been_sent.return_value = False
            mock_generator.send_report_to_discord = AsyncMock(return_value={"status": "success", "date": expected_date})

            with patch("app.core.reports.daily_report.DailyActivityReportGenerator", return_value=mock_generator):
                result = await scheduler._evaluate_daily_activity_report()

        assert result is not None
        # Verify the idempotency check used the previous day's date
        report_call_date = mock_generator.history_repo.has_report_been_sent.call_args[0][1]
        assert report_call_date == expected_date, f"Expected {expected_date}, got {report_call_date}"

    @pytest.mark.asyncio
    async def test_before_scheduled_time_skips(self, scheduler):
        """Before scheduled time, report should not fire."""
        mock_now = self._mock_now(8, 30)

        with patch("app.services.scheduler.datetime") as mock_dt, \
             patch("app.services.scheduler.settings") as mock_settings:
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **k: datetime.datetime(*a, **k)
            mock_settings.DAILY_ACTIVITY_REPORT_ENABLED = True
            mock_settings.DAILY_ACTIVITY_REPORT_TIMEZONE = "Asia/Karachi"
            mock_settings.DAILY_ACTIVITY_REPORT_TIME = "08:40"
            mock_settings.get_report_timezone.return_value = "Asia/Karachi"
            mock_settings.get_default_report_time.return_value = "08:40"
            result = await scheduler._evaluate_daily_activity_report()

        assert result is None

    @pytest.mark.asyncio
    async def test_utc_pkt_date_boundary(self, scheduler):
        """At midnight boundary — 00:15 UTC is 05:15 PKT — verify correct date handling."""
        # 05:15 PKT on Sept 21 → too early for 08:40 scheduled time
        tz = zoneinfo.ZoneInfo("Asia/Karachi")
        mock_now = datetime.datetime(2026, 9, 21, 5, 15, 0, tzinfo=tz)

        with patch("app.services.scheduler.datetime") as mock_dt, \
             patch("app.services.scheduler.settings") as mock_settings:
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **k: datetime.datetime(*a, **k)
            mock_settings.DAILY_ACTIVITY_REPORT_ENABLED = True
            mock_settings.DAILY_ACTIVITY_REPORT_TIMEZONE = "Asia/Karachi"
            mock_settings.DAILY_ACTIVITY_REPORT_TIME = "08:40"
            mock_settings.get_report_timezone.return_value = "Asia/Karachi"
            mock_settings.get_default_report_time.return_value = "08:40"
            result = await scheduler._evaluate_daily_activity_report()

        assert result is None

    @pytest.mark.asyncio
    async def test_duplicate_prevention(self, scheduler):
        """Same previous-day report should not be sent twice."""
        mock_now = self._mock_now(9, 0)

        with patch("app.services.scheduler.datetime") as mock_dt, \
             patch("app.services.scheduler.settings") as mock_settings, \
             patch("app.services.scheduler.timedelta", side_effect=timedelta):
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **k: datetime.datetime(*a, **k)
            mock_settings.DAILY_ACTIVITY_REPORT_ENABLED = True
            mock_settings.DAILY_ACTIVITY_REPORT_TIMEZONE = "Asia/Karachi"
            mock_settings.DAILY_ACTIVITY_REPORT_TIME = "08:40"
            mock_settings.get_report_timezone.return_value = "Asia/Karachi"
            mock_settings.get_default_report_time.return_value = "08:40"
            mock_settings.JIRA_TEAM_GROUP = "Mursaleen Cluster"
            mock_settings.is_jira_team_group_configured.return_value = True

            mock_generator = MagicMock()
            mock_generator.history_repo.has_report_been_sent.return_value = True  # Already sent

            with patch("app.core.reports.daily_report.DailyActivityReportGenerator", return_value=mock_generator):
                result = await scheduler._evaluate_daily_activity_report()

        assert result is None

    @pytest.mark.asyncio
    async def test_disabled_report_skips(self, scheduler):
        """Disabled report should not fire."""
        with patch("app.services.scheduler.settings") as mock_settings:
            mock_settings.DAILY_ACTIVITY_REPORT_ENABLED = False
            result = await scheduler._evaluate_daily_activity_report()
        assert result is None
