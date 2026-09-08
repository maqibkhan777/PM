"""Built-in workflow rules for PM Operations Agent V0.1."""

from typing import Any, Dict, List, Optional
from app.core.rules.base import BaseRule
from app.core.events.base import BaseEvent
from app.core.events.types import (
    TaskCommentAdded,
    TaskWorklogged,
    TaskStatusChanged,
    TaskReopened,
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
            issue_data = event.payload.get("issue", {})
            fields = issue_data.get("fields", {})
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
            task_title = event.payload.get("issue", {}).get("fields", {}).get("summary", "")

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

            fields = [
                {"name": "Issue", "value": task_key, "inline": True},
                {"name": "Reopened by", "value": event.actor_name or "Unknown", "inline": True},
                {"name": "Transition", "value": f"`{prev_status}` ➔ `{new_status}`", "inline": True},
                {"name": "Title", "value": task_title or "Untitled", "inline": False},
            ]
            embed_payload = DiscordFormatter.format_embed(
                title="🔄 Task Reopened Alert",
                description=f"Task **{task_key}** was reopened from **{prev_status}** to **{new_status}**.",
                color=3447003,  # Blue
                fields=fields,
                timestamp=event.timestamp
            )

            notif_action = create_send_notification_action(
                target_system="discord",
                channel=settings.PM_DISCORD_CHANNEL,
                title="🔄 Task Reopened Alert",
                message=f"Task {task_key} was reopened.",
                level="INFO",
                fields=embed_payload.get("embeds"),
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
