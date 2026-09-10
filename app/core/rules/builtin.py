"""Built-in workflow rules for PM Operations Agent V0.1."""

from typing import Any, Dict, List, Optional
from app.core.rules.base import BaseRule
from app.core.events.base import BaseEvent
from app.core.events.types import (
    TaskCommentAdded,
    TaskWorklogged,
    TaskStatusChanged,
    TaskReopened,
    TaskAssigned,
    StaleTask,
    OverdueTask,
    TaskBlocked,
    WorkflowViolation,
)
from app.core.actions.base import BaseAction
from app.core.actions.types import (
    create_send_notification_action,
    create_send_message_action,
)
from app.connectors.discord.formatter import DiscordFormatter
from app.services.notification_deduplication import notification_dedup_service
from app.services.user_identity_service import user_identity_service
from app.config.settings import settings
from app.utils.time import parse_iso_datetime, utc_now, hours_between
from app.utils.logger import logger


class ActiveWorkRule(BaseRule):
    """Rule 1: Active Work Detection.

    If TaskStatus = 'To Do' and an activity occurs indicating work has started
    (e.g. comment added, work logged), generate WorkflowViolation alert to PM via Discord.
    """

    def __init__(self, enabled: bool = True, configuration: Optional[Dict[str, Any]] = None):
        super().__init__(
            name="ActiveWorkDetection",
            description="Detects active work performed on a task while remaining in 'To Do'.",
            enabled=enabled,
            configuration=configuration or {}
        )

    def evaluate(self, event: BaseEvent, context: Optional[Dict[str, Any]] = None) -> List[BaseAction]:
        if not self.enabled:
            return []

        task_status = "Unknown"
        # Determine current status from event payload or context
        if isinstance(event, (TaskCommentAdded, TaskWorklogged)):
            issue_data = event.payload.get("issue") if isinstance(event.payload, dict) and "issue" in event.payload else event.payload
            fields = issue_data.get("fields", {}) if isinstance(issue_data, dict) else {}
            task_status = fields.get("status", {}).get("name", "")
            if not task_status and context:
                task_status = context.get("current_status", "")

        elif isinstance(event, WorkflowViolation):
            task_status = event.current_status

        # Check if status is "To Do"
        if task_status.lower() in ("to do", "todo", "open", "backlog"):
            task_key = event.task_key or event.task_id or "Unknown"
            resource_name = event.actor_name or "Unassigned"
            project_name = event.project_key or "N/A"
            issue_obj = event.payload.get("issue") if isinstance(event.payload, dict) and "issue" in event.payload else event.payload
            task_title = issue_obj.get("fields", {}).get("summary", "") if isinstance(issue_obj, dict) else ""

            # Deduplication check
            if not notification_dedup_service.should_notify(
                rule_id=self.name,
                target_id=task_key,
                condition="ActiveWorkOnToDo"
            ):
                return []

            details = f"Activity detected ({event.event_type}) while task remains in '{task_status}' status."
            logger.info(f"Rule [ActiveWorkDetection] triggered for {task_key} by {resource_name}")

            embed_payload = DiscordFormatter.format_workflow_violation(
                task_key=task_key,
                task_title=task_title,
                resource_name=resource_name,
                project_name=project_name,
                details=details,
                timestamp=event.timestamp
            )

            notification_action = create_send_notification_action(
                target_system="discord",
                channel=settings.PM_DISCORD_CHANNEL,
                title="🚨 Jira Workflow Alert",
                message=details,
                level="VIOLATION",
                fields=embed_payload.get("embeds"),
                requested_by=self.name
            )
            # Embed the full Discord payload directly into parameters
            notification_action.parameters["embeds"] = embed_payload.get("embeds")

            notification_dedup_service.record_notification_sent(
                rule_id=self.name,
                target_id=task_key,
                condition="ActiveWorkOnToDo"
            )

            return [notification_action]

        return []


class StaleTaskRule(BaseRule):
    """Rule 2: Stale Work Detection.

    If a task is 'In Progress' and there has been no activity for > X hours (default 24h),
    generate StaleTask alert to Discord and send Mattermost DM reminder to assignee.
    """

    def __init__(self, enabled: bool = True, configuration: Optional[Dict[str, Any]] = None):
        super().__init__(
            name="StaleTaskDetection",
            description="Alerts PM and reminds assignee when an In Progress task is stale.",
            enabled=enabled,
            configuration=configuration or {"threshold_hours": settings.STALE_TASK_HOURS}
        )

    def evaluate(self, event: BaseEvent, context: Optional[Dict[str, Any]] = None) -> List[BaseAction]:
        if not self.enabled:
            return []

        actions: List[BaseAction] = []
        task_key = event.task_key or event.task_id or "Unknown"
        threshold = self.configuration.get("threshold_hours", settings.STALE_TASK_HOURS)

        hours_inactive = 0.0
        assignee_id = None
        assignee_name = None
        task_title = None

        if isinstance(event, StaleTask):
            hours_inactive = event.hours_inactive
            assignee_id = event.assignee_id
            assignee_name = event.assignee_name
            task_title = event.task_title
        elif context and context.get("status", "").lower() in ("in progress", "doing", "active"):
            last_activity = context.get("last_activity_time")
            if last_activity:
                dt = parse_iso_datetime(last_activity)
                if dt:
                    hours_inactive = hours_between(dt)
            assignee_id = context.get("assignee_id")
            assignee_name = context.get("assignee_name")
            task_title = context.get("title")

        if hours_inactive >= threshold:
            if not notification_dedup_service.should_notify(
                rule_id=self.name,
                target_id=task_key,
                condition=f"StaleTask_{int(hours_inactive)}h"
            ):
                return []

            logger.info(f"Rule [StaleTaskDetection] triggered for {task_key} (inactivity={hours_inactive:.1f}h)")

            # 1. Discord Notification for PM
            if settings.STALE_TASK_NOTIFY_PM:
                embed_payload = DiscordFormatter.format_stale_task(
                    task_key=task_key,
                    task_title=task_title or "",
                    assignee_name=assignee_name or "Unassigned",
                    hours_inactive=hours_inactive,
                    timestamp=event.timestamp
                )
                notif_action = create_send_notification_action(
                    target_system="discord",
                    channel=settings.PM_DISCORD_CHANNEL,
                    title="⚠️ Stale Task Alert",
                    message=f"Task {task_key} has been inactive for {hours_inactive:.1f}h",
                    level="WARNING",
                    fields=embed_payload.get("embeds"),
                    requested_by=self.name
                )
                notif_action.parameters["embeds"] = embed_payload.get("embeds")
                actions.append(notif_action)

            # 2. Mattermost DM Reminder for Assignee
            if settings.STALE_TASK_NOTIFY_ASSIGNEE and assignee_id:
                dm_text = (
                    f"Hey {assignee_name or 'there'},\n\n"
                    f"**{task_key}** ({task_title or 'Untitled'}) has not been updated for "
                    f"{int(hours_inactive)} hours.\n"
                    "Please share the current status, update the ticket, or let us know if there are any blockers."
                )
                dm_action = create_send_message_action(
                    target_system="mattermost",
                    target_id=assignee_id,  # Jira assignee ID (Action Engine will resolve via user_mapping_service)
                    text=dm_text,
                    recipient_name=assignee_name,
                    requested_by=self.name
                )
                # Attach metadata for mapping service
                dm_action.parameters["jira_user_id"] = assignee_id
                dm_action.parameters["display_name"] = assignee_name
                actions.append(dm_action)

            notification_dedup_service.record_notification_sent(
                rule_id=self.name,
                target_id=task_key,
                condition=f"StaleTask_{int(hours_inactive)}h"
            )

        return actions


class OverdueRule(BaseRule):
    """Rule 3: Overdue Task Detection.

    If due_date < current_time AND status != Done, generate OverdueTask alert to Discord.
    """

    def __init__(self, enabled: bool = True, configuration: Optional[Dict[str, Any]] = None):
        super().__init__(
            name="OverdueTaskDetection",
            description="Alerts PM when an uncompleted task exceeds its due date.",
            enabled=enabled,
            configuration=configuration or {}
        )

    def evaluate(self, event: BaseEvent, context: Optional[Dict[str, Any]] = None) -> List[BaseAction]:
        if not self.enabled:
            return []

        task_key = event.task_key or event.task_id or "Unknown"
        due_date_str = None
        status = "Unknown"
        assignee_name = None
        task_title = None

        if isinstance(event, OverdueTask):
            due_date_str = event.due_date
            status = event.current_status
            assignee_name = event.assignee_name
            task_title = event.task_title
        elif context:
            due_date_str = context.get("due_date")
            status = context.get("status", "Unknown")
            assignee_name = context.get("assignee_name")
            task_title = context.get("title")

        if due_date_str and status.lower() not in ("done", "completed", "resolved", "closed"):
            due_dt = parse_iso_datetime(due_date_str)
            if due_dt and due_dt < utc_now():
                if not settings.OVERDUE_NOTIFY_PM:
                    logger.debug(f"Overdue notification for {task_key} suppressed (OVERDUE_NOTIFY_PM=False).")
                    return []

                if not notification_dedup_service.should_notify(
                    rule_id=self.name,
                    target_id=task_key,
                    condition="OverdueTask"
                ):
                    return []

                logger.info(f"Rule [OverdueTaskDetection] triggered for {task_key} (due={due_date_str})")

                embed_payload = DiscordFormatter.format_overdue_task(
                    task_key=task_key,
                    task_title=task_title or "",
                    assignee_name=assignee_name or "Unassigned",
                    due_date=due_date_str,
                    current_status=status,
                    timestamp=event.timestamp
                )

                notif_action = create_send_notification_action(
                    target_system="discord",
                    channel=settings.PM_DISCORD_CHANNEL,
                    title="⏰ Overdue Task Alert",
                    message=f"Task {task_key} is overdue since {due_date_str}",
                    level="WARNING",
                    fields=embed_payload.get("embeds"),
                    requested_by=self.name
                )
                notif_action.parameters["embeds"] = embed_payload.get("embeds")

                notification_dedup_service.record_notification_sent(
                    rule_id=self.name,
                    target_id=task_key,
                    condition="OverdueTask"
                )

                return [notif_action]

        return []


class BlockedRule(BaseRule):
    """Rule 4: Blocked Task Detection.

    If a task becomes blocked, generate TaskBlocked notification to PM.
    """

    def __init__(self, enabled: bool = True, configuration: Optional[Dict[str, Any]] = None):
        super().__init__(
            name="BlockedTaskDetection",
            description="Alerts PM when a task is moved to Blocked status.",
            enabled=enabled,
            configuration=configuration or {}
        )

    def evaluate(self, event: BaseEvent, context: Optional[Dict[str, Any]] = None) -> List[BaseAction]:
        if not self.enabled:
            return []

        task_key = event.task_key or event.task_id or "Unknown"
        is_blocked = False

        if isinstance(event, TaskBlocked):
            is_blocked = True
        elif isinstance(event, TaskStatusChanged) and event.new_status.lower() in ("blocked", "impediment"):
            is_blocked = True

        if is_blocked:
            if not notification_dedup_service.should_notify(
                rule_id=self.name,
                target_id=task_key,
                condition="TaskBlocked"
            ):
                return []

            logger.info(f"Rule [BlockedTaskDetection] triggered for {task_key}")
            task_title = event.payload.get("issue", {}).get("fields", {}).get("summary", "")

            fields = [
                {"name": "Issue", "value": task_key, "inline": True},
                {"name": "Actor", "value": event.actor_name or "Unknown", "inline": True},
                {"name": "Status", "value": "Blocked", "inline": True},
                {"name": "Title", "value": task_title or "Untitled", "inline": False},
            ]
            embed_payload = DiscordFormatter.format_embed(
                title="🚫 Task Blocked Alert",
                description=f"Task **{task_key}** has been marked as **Blocked**.",
                color=15158332,  # Red
                fields=fields,
                timestamp=event.timestamp
            )

            notif_action = create_send_notification_action(
                target_system="discord",
                channel=settings.PM_DISCORD_CHANNEL,
                title="🚫 Task Blocked Alert",
                message=f"Task {task_key} is blocked.",
                level="ERROR",
                fields=embed_payload.get("embeds"),
                requested_by=self.name
            )
            notif_action.parameters["embeds"] = embed_payload.get("embeds")

            notification_dedup_service.record_notification_sent(
                rule_id=self.name,
                target_id=task_key,
                condition="TaskBlocked"
            )

            return [notif_action]

        return []


class ReopenedRule(BaseRule):
    """Rule 5: Reopened Task Detection.

    If a task moves from Done to any active status, generate TaskReopened notification.
    """

    def __init__(self, enabled: bool = True, configuration: Optional[Dict[str, Any]] = None):
        super().__init__(
            name="ReopenedTaskDetection",
            description="Alerts PM when a completed task is reopened.",
            enabled=enabled,
            configuration=configuration or {}
        )

    def evaluate(self, event: BaseEvent, context: Optional[Dict[str, Any]] = None) -> List[BaseAction]:
        if not self.enabled:
            return []

        task_key = event.task_key or event.task_id or "Unknown"
        is_reopened = False
        prev_status = "Done"
        new_status = "Active"

        if isinstance(event, TaskReopened):
            is_reopened = True
            prev_status = event.previous_status
            new_status = event.new_status
        elif isinstance(event, TaskStatusChanged):
            if event.old_status.lower() in ("done", "completed", "resolved", "closed") and event.new_status.lower() not in ("done", "completed", "resolved", "closed"):
                is_reopened = True
                prev_status = event.old_status
                new_status = event.new_status

        if is_reopened:
            if not notification_dedup_service.should_notify(
                rule_id=self.name,
                target_id=task_key,
                condition=f"Reopened_{prev_status}_to_{new_status}"
            ):
                return []

            logger.info(f"Rule [ReopenedTaskDetection] triggered for {task_key} ({prev_status} -> {new_status})")
            task_title = event.payload.get("issue", {}).get("fields", {}).get("summary", "")

            embed_payload = DiscordFormatter.format_reopened_task(
                task_key=task_key,
                task_title=task_title or "",
                reopened_by=event.actor_name or "Unknown",
                prev_status=prev_status,
                new_status=new_status,
                timestamp=event.timestamp
            )

            notif_action = create_send_notification_action(
                target_system="discord",
                channel=settings.PM_DISCORD_CHANNEL,
                title=f"🔄 Task Reopened Alert — {task_key}",
                message=f"Task {task_key} was reopened.",
                level="INFO",
                fields=embed_payload.get("embeds", [{}])[0].get("fields"),
                requested_by=self.name
            )
            notif_action.parameters["embeds"] = embed_payload.get("embeds")

            notification_dedup_service.record_notification_sent(
                rule_id=self.name,
                target_id=task_key,
                condition=f"Reopened_{prev_status}_to_{new_status}"
            )

            return [notif_action]

        return []


class CommentNotificationRule(BaseRule):
    """Rule 6: Comment Notification Relevance Rule.

    Separates team data collection from personal notifications.
    Generates high-priority personal notification when 'I' am mentioned.
    Suppresses noisy customer/support replies unless COMMENT_NOTIFY_ALL is True.
    """

    def __init__(self, enabled: bool = True, configuration: Optional[Dict[str, Any]] = None):
        super().__init__(
            name="CommentNotificationRule",
            description="Alerts PM via Discord when mentioned in a Jira comment.",
            enabled=enabled,
            configuration=configuration or {}
        )

    def evaluate(self, event: BaseEvent, context: Optional[Dict[str, Any]] = None) -> List[BaseAction]:
        if not self.enabled:
            return []

        if not isinstance(event, TaskCommentAdded):
            return []

        task_key = event.task_key or event.task_id or "Unknown"
        comment_id = getattr(event, "comment_id", "") or getattr(event, "external_event_id", "default")
        author_name = event.actor_name or getattr(event, "author_name", "Unknown") or "Unknown"
        comment_body = getattr(event, "comment_body", "")

        # Check if "I" am mentioned
        is_mentioned = False
        mentioned_ids = getattr(event, "mentioned_account_ids", []) or []
        mentioned_names = getattr(event, "mentioned_display_names", []) or []

        for acc_id in mentioned_ids:
            if user_identity_service.is_me(account_id=acc_id):
                is_mentioned = True
                break

        if not is_mentioned:
            for disp_name in mentioned_names:
                if user_identity_service.is_me(display_name=disp_name):
                    is_mentioned = True
                    break

        # Fallback check on raw body text if structured mentions weren't present
        if not is_mentioned and comment_body:
            my_identity = user_identity_service._cached_display_name or settings.MY_JIRA_DISPLAY_NAME
            if my_identity and f"@{my_identity.lower()}" in comment_body.lower():
                is_mentioned = True

        # If not mentioned and not configured to notify all comments, do NOT notify PM
        if not is_mentioned and not settings.COMMENT_NOTIFY_ALL:
            logger.debug(f"Comment on {task_key} by {author_name} does not mention PM. Suppressing Discord alert.")
            return []

        # Deduplication check: notify once per comment
        condition_key = f"comment_{comment_id}"
        if not notification_dedup_service.should_notify(
            rule_id=self.name,
            target_id=task_key,
            condition=condition_key
        ):
            return []

        issue_obj = event.payload.get("issue") if isinstance(event.payload, dict) and "issue" in event.payload else event.payload
        fields = issue_obj.get("fields", {}) if isinstance(issue_obj, dict) else {}
        task_title = fields.get("summary", "")
        task_status = fields.get("status", {}).get("name", "")

        if is_mentioned:
            logger.info(f"Rule [CommentNotificationRule] triggered (MENTION) on {task_key} by {author_name}")
            embed_payload = DiscordFormatter.format_task_mention(
                task_key=task_key,
                task_title=task_title,
                author_name=author_name,
                comment_body=comment_body,
                task_status=task_status,
                timestamp=event.timestamp
            )
            title = f"🔔 You were mentioned on {task_key}"
            msg = f"You were mentioned in a comment on {task_key} by {author_name}."
            level = "WARNING"
        else:
            logger.info(f"Rule [CommentNotificationRule] triggered (ALL_COMMENTS) on {task_key} by {author_name}")
            embed_payload = DiscordFormatter.format_task_comment(
                task_key=task_key,
                task_title=task_title,
                author_name=author_name,
                comment_body=comment_body,
                task_status=task_status,
                timestamp=event.timestamp
            )
            title = f"💬 Jira Comment Added — {task_key}"
            msg = f"New comment posted on {task_key} by {author_name}."
            level = "INFO"

        notif_action = create_send_notification_action(
            target_system="discord",
            channel=settings.PM_DISCORD_CHANNEL,
            title=title,
            message=msg,
            level=level,
            fields=embed_payload.get("embeds", [{}])[0].get("fields"),
            requested_by=self.name
        )
        notif_action.parameters["embeds"] = embed_payload.get("embeds")

        notification_dedup_service.record_notification_sent(
            rule_id=self.name,
            target_id=task_key,
            condition=condition_key
        )

        return [notif_action]


class AssignmentRule(BaseRule):
    """Rule 7: Assignment Notification Rule.

    Distinguishes:
    - Assigned to me -> High-priority personal notification.
    - Assigned to another team member -> Team awareness notification (if ASSIGNMENT_NOTIFY_TEAM=true).
    - Customer/support activity -> Not an assignment, no noise.
    """

    def __init__(self, enabled: bool = True, configuration: Optional[Dict[str, Any]] = None):
        super().__init__(
            name="AssignmentRule",
            description="Alerts when a ticket is assigned to me or a team member.",
            enabled=enabled,
            configuration=configuration or {}
        )

    def evaluate(self, event: BaseEvent, context: Optional[Dict[str, Any]] = None) -> List[BaseAction]:
        if not self.enabled:
            return []

        if not isinstance(event, TaskAssigned):
            return []

        task_key = event.task_key or event.task_id or "Unknown"
        new_assignee_id = getattr(event, "new_assignee_id", None)
        new_assignee_name = getattr(event, "new_assignee_name", None) or "Unassigned"
        old_assignee_name = getattr(event, "old_assignee_name", None)
        actor_name = event.actor_name or "System"

        # Check if assigned to "me"
        is_assigned_to_me = user_identity_service.is_me(
            account_id=new_assignee_id,
            display_name=new_assignee_name
        )

        # If assigned to someone else, check if team notifications are enabled
        if not is_assigned_to_me and not settings.ASSIGNMENT_NOTIFY_TEAM:
            return []

        condition_key = f"assignment_{new_assignee_id or new_assignee_name}"
        if not notification_dedup_service.should_notify(
            rule_id=self.name,
            target_id=task_key,
            condition=condition_key
        ):
            return []

        issue_obj = event.payload.get("issue") if isinstance(event.payload, dict) and "issue" in event.payload else event.payload
        fields = issue_obj.get("fields", {}) if isinstance(issue_obj, dict) else {}
        task_title = fields.get("summary", "")

        embed_payload = DiscordFormatter.format_task_assigned(
            task_key=task_key,
            task_title=task_title,
            new_assignee_name=new_assignee_name,
            old_assignee_name=old_assignee_name,
            assigned_by=actor_name,
            is_assigned_to_me=is_assigned_to_me,
            timestamp=event.timestamp
        )

        if is_assigned_to_me:
            logger.info(f"Rule [AssignmentRule] triggered (ASSIGNED_TO_ME) on {task_key}")
            title = f"🎯 Task Assigned to You — {task_key}"
            msg = f"Task {task_key} has been assigned to you."
            level = "SUCCESS"
        else:
            logger.info(f"Rule [AssignmentRule] triggered (TEAM_AWARENESS) on {task_key} to {new_assignee_name}")
            title = f"👤 Task Assigned — {task_key}"
            msg = f"Task {task_key} assigned to {new_assignee_name}."
            level = "INFO"

        notif_action = create_send_notification_action(
            target_system="discord",
            channel=settings.PM_DISCORD_CHANNEL,
            title=title,
            message=msg,
            level=level,
            fields=embed_payload.get("embeds", [{}])[0].get("fields"),
            requested_by=self.name
        )
        notif_action.parameters["embeds"] = embed_payload.get("embeds")

        notification_dedup_service.record_notification_sent(
            rule_id=self.name,
            target_id=task_key,
            condition=condition_key
        )

        return [notif_action]


