"""Normalizer converting raw Jira Cloud webhook JSON payloads into internal BaseEvent models."""

from typing import Any, Dict, List, Optional
from app.core.events.base import BaseEvent
from app.core.events.types import (
    TaskCreated,
    TaskUpdated,
    TaskStatusChanged,
    TaskAssigned,
    TaskCommentAdded,
    TaskPriorityChanged,
    TaskCompleted,
    TaskReopened,
    TaskWorklogged,
)
from app.utils.time import utc_now_iso, parse_iso_datetime, format_iso
from app.utils.logger import logger


class JiraEventNormalizer:
    """Parses and normalizes Jira Cloud webhook events into generic internal events."""

    @staticmethod
    def normalize(payload: Dict[str, Any]) -> Optional[BaseEvent]:
        """Convert a Jira webhook payload dictionary into a typed BaseEvent."""
        if not payload or not isinstance(payload, dict):
            return None

        webhook_event = payload.get("webhookEvent", "")
        issue = payload.get("issue", {})
        user = payload.get("user", {})
        changelog = payload.get("changelog", {})
        comment = payload.get("comment", {})
        worklog = payload.get("worklog", {})

        # Extract common metadata
        task_id = issue.get("id") if issue else None
        task_key = issue.get("key") if issue else None
        fields = issue.get("fields", {}) if issue else {}
        project = fields.get("project", {}) if fields else {}
        project_id = project.get("id")
        project_key = project.get("key")

        actor_id = user.get("accountId") or user.get("name")
        actor_name = user.get("displayName") or user.get("name")
        actor_email = user.get("emailAddress")

        timestamp = payload.get("timestamp")
        if isinstance(timestamp, (int, float)):
            # Jira epoch timestamp in milliseconds
            import datetime
            dt = datetime.datetime.fromtimestamp(timestamp / 1000.0, tz=datetime.timezone.utc)
            event_time = format_iso(dt) or utc_now_iso()
        elif isinstance(timestamp, str):
            dt = parse_iso_datetime(timestamp)
            event_time = format_iso(dt) or utc_now_iso()
        else:
            event_time = utc_now_iso()

        # External Event ID for deduplication
        external_event_id = None
        if changelog and changelog.get("id"):
            external_event_id = f"jira:changelog:{changelog.get('id')}"
        elif comment and comment.get("id"):
            external_event_id = f"jira:comment:{comment.get('id')}"
        elif worklog and worklog.get("id"):
            external_event_id = f"jira:worklog:{worklog.get('id')}"
        elif task_key and webhook_event:
            external_event_id = f"jira:{webhook_event}:{task_key}:{timestamp}"

        # Assignee & Reporter
        assignee = fields.get("assignee") or {}
        reporter = fields.get("reporter") or {}
        status_obj = fields.get("status") or {}
        priority_obj = fields.get("priority") or {}

        # ----------------------------------------------------------------------
        # 1. Issue Created
        # ----------------------------------------------------------------------
        if webhook_event == "jira:issue_created":
            return TaskCreated(
                source="jira",
                external_event_id=external_event_id,
                timestamp=event_time,
                actor_id=actor_id,
                actor_name=actor_name,
                actor_email=actor_email,
                project_id=project_id,
                project_key=project_key,
                task_id=task_id,
                task_key=task_key,
                title=fields.get("summary", "Untitled"),
                description=str(fields.get("description", "")),
                status=status_obj.get("name", "To Do"),
                priority=priority_obj.get("name", "Medium"),
                assignee_id=assignee.get("accountId"),
                assignee_name=assignee.get("displayName"),
                reporter_id=reporter.get("accountId"),
                reporter_name=reporter.get("displayName"),
                due_date=fields.get("duedate"),
                payload=payload
            )

        # ----------------------------------------------------------------------
        # 2. Comment Added
        # ----------------------------------------------------------------------
        if webhook_event in ("comment_created", "jira:comment_created"):
            body_text = ""
            body = comment.get("body", "")
            if isinstance(body, dict):
                # Extract text from ADF
                body_text = JiraEventNormalizer._extract_adf_text(body)
            elif isinstance(body, str):
                body_text = body

            comment_author = comment.get("author", {}) or user
            return TaskCommentAdded(
                source="jira",
                external_event_id=external_event_id,
                timestamp=event_time,
                actor_id=comment_author.get("accountId") or actor_id,
                actor_name=comment_author.get("displayName") or actor_name,
                actor_email=comment_author.get("emailAddress") or actor_email,
                project_id=project_id,
                project_key=project_key,
                task_id=task_id,
                task_key=task_key,
                comment_id=comment.get("id"),
                comment_body=body_text,
                author_id=comment_author.get("accountId"),
                author_name=comment_author.get("displayName"),
                payload=payload
            )

        # ----------------------------------------------------------------------
        # 3. Worklog Created
        # ----------------------------------------------------------------------
        if webhook_event in ("worklog_created", "jira:worklog_created"):
            worklog_author = worklog.get("author", {}) or user
            time_spent_secs = int(worklog.get("timeSpentSeconds", 0))
            return TaskWorklogged(
                source="jira",
                external_event_id=external_event_id,
                timestamp=event_time,
                actor_id=worklog_author.get("accountId") or actor_id,
                actor_name=worklog_author.get("displayName") or actor_name,
                actor_email=worklog_author.get("emailAddress") or actor_email,
                project_id=project_id,
                project_key=project_key,
                task_id=task_id,
                task_key=task_key,
                worklog_id=worklog.get("id"),
                time_spent_seconds=time_spent_secs,
                time_spent_human=worklog.get("timeSpent"),
                comment=str(worklog.get("comment", "")),
                payload=payload
            )

        # ----------------------------------------------------------------------
        # 4. Issue Updated (Inspect Changelog items)
        # ----------------------------------------------------------------------
        if webhook_event == "jira:issue_updated" or changelog:
            items = changelog.get("items", []) if changelog else []

            # Check for Status Change
            for item in items:
                if item.get("field") == "status":
                    old_status = item.get("fromString", "")
                    new_status = item.get("toString", "")

                    # Check if reopened (Done -> In Progress/To Do)
                    if old_status.lower() in ("done", "completed", "resolved", "closed") and new_status.lower() not in ("done", "completed", "resolved", "closed"):
                        return TaskReopened(
                            source="jira",
                            external_event_id=external_event_id,
                            timestamp=event_time,
                            actor_id=actor_id,
                            actor_name=actor_name,
                            actor_email=actor_email,
                            project_id=project_id,
                            project_key=project_key,
                            task_id=task_id,
                            task_key=task_key,
                            previous_status=old_status,
                            new_status=new_status,
                            reopened_by=actor_name,
                            payload=payload
                        )
                    # Check if completed
                    elif new_status.lower() in ("done", "completed", "resolved", "closed") and old_status.lower() not in ("done", "completed", "resolved", "closed"):
                        return TaskCompleted(
                            source="jira",
                            external_event_id=external_event_id,
                            timestamp=event_time,
                            actor_id=actor_id,
                            actor_name=actor_name,
                            actor_email=actor_email,
                            project_id=project_id,
                            project_key=project_key,
                            task_id=task_id,
                            task_key=task_key,
                            completion_time=event_time,
                            resolved_by=actor_name,
                            payload=payload
                        )
                    else:
                        return TaskStatusChanged(
                            source="jira",
                            external_event_id=external_event_id,
                            timestamp=event_time,
                            actor_id=actor_id,
                            actor_name=actor_name,
                            actor_email=actor_email,
                            project_id=project_id,
                            project_key=project_key,
                            task_id=task_id,
                            task_key=task_key,
                            old_status=old_status,
                            new_status=new_status,
                            payload=payload
                        )

            # Check for Assignee Change
            for item in items:
                if item.get("field") == "assignee":
                    return TaskAssigned(
                        source="jira",
                        external_event_id=external_event_id,
                        timestamp=event_time,
                        actor_id=actor_id,
                        actor_name=actor_name,
                        actor_email=actor_email,
                        project_id=project_id,
                        project_key=project_key,
                        task_id=task_id,
                        task_key=task_key,
                        old_assignee_id=item.get("from"),
                        old_assignee_name=item.get("fromString"),
                        new_assignee_id=item.get("to"),
                        new_assignee_name=item.get("toString"),
                        payload=payload
                    )

            # Check for Priority Change
            for item in items:
                if item.get("field") == "priority":
                    return TaskPriorityChanged(
                        source="jira",
                        external_event_id=external_event_id,
                        timestamp=event_time,
                        actor_id=actor_id,
                        actor_name=actor_name,
                        actor_email=actor_email,
                        project_id=project_id,
                        project_key=project_key,
                        task_id=task_id,
                        task_key=task_key,
                        old_priority=item.get("fromString", "Unknown"),
                        new_priority=item.get("toString", "Unknown"),
                        payload=payload
                    )

            # Generic update fallback
            changed_fields = [it.get("field", "") for it in items]
            changes = {it.get("field", ""): {"from": it.get("fromString"), "to": it.get("toString")} for it in items}
            return TaskUpdated(
                source="jira",
                external_event_id=external_event_id,
                timestamp=event_time,
                actor_id=actor_id,
                actor_name=actor_name,
                actor_email=actor_email,
                project_id=project_id,
                project_key=project_key,
                task_id=task_id,
                task_key=task_key,
                changed_fields=changed_fields,
                changes=changes,
                payload=payload
            )

        logger.warning(f"Unhandled Jira webhook event type: '{webhook_event}'")
        return None

    @staticmethod
    def _extract_adf_text(doc: Dict[str, Any]) -> str:
        """Recursively extract plain text from an Atlassian Document Format JSON object."""
        texts: List[str] = []

        def _traverse(node: Any):
            if isinstance(node, dict):
                if node.get("type") == "text":
                    texts.append(node.get("text", ""))
                for val in node.values():
                    _traverse(val)
            elif isinstance(node, list):
                for elem in node:
                    _traverse(elem)

        _traverse(doc)
        return " ".join(texts).strip()
