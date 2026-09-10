import re
from typing import Any, Dict, List, Optional, Tuple
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
            body = comment.get("body", "")
            body_text, mentioned_acc_ids, mentioned_names = JiraEventNormalizer.extract_adf_text_and_mentions(body)

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
                mentioned_account_ids=mentioned_acc_ids,
                mentioned_display_names=mentioned_names,
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
    def extract_adf_text_and_mentions(doc_or_text: Any) -> Tuple[str, List[str], List[str]]:
        """Extract plain text, mentioned account IDs, and mentioned display names.
        
        Supports both ADF JSON dicts and plain text / Jira wiki markup strings.
        """
        texts: List[str] = []
        account_ids: List[str] = []
        display_names: List[str] = []

        if isinstance(doc_or_text, str):
            # Parse Jira wiki markup mentions: [~accountid:712020:...] or [~712020:...]
            for match in re.finditer(r"\[~accountid:([^\]]+)\]", doc_or_text, re.IGNORECASE):
                acc = match.group(1).strip()
                if acc and acc not in account_ids:
                    account_ids.append(acc)
            for match in re.finditer(r"\[~([a-zA-Z0-9_:\-]+)\]", doc_or_text):
                val = match.group(1).strip()
                if val.lower().startswith("accountid:"):
                    val = val[10:].strip()
                if val and val not in account_ids:
                    account_ids.append(val)
            return doc_or_text, account_ids, display_names

        if not isinstance(doc_or_text, dict):
            return str(doc_or_text or ""), account_ids, display_names

        def _traverse(node: Any):
            if isinstance(node, dict):
                node_type = node.get("type")
                if node_type == "text":
                    texts.append(node.get("text", ""))
                elif node_type == "mention":
                    attrs = node.get("attrs", {})
                    acc_id = attrs.get("id")
                    disp_name = attrs.get("text", "")
                    if acc_id and acc_id not in account_ids:
                        account_ids.append(acc_id)
                    if disp_name and disp_name not in display_names:
                        display_names.append(disp_name.lstrip("@"))
                    # Render mention text in readable string
                    texts.append(disp_name or f"@{acc_id}")
                for val in node.values():
                    _traverse(val)
            elif isinstance(node, list):
                for elem in node:
                    _traverse(elem)

        _traverse(doc_or_text)
        rendered_text = re.sub(r"\s+", " ", " ".join(texts)).strip()

        # Check for any embedded wiki markup inside extracted text
        for match in re.finditer(r"\[~accountid:([^\]]+)\]", rendered_text, re.IGNORECASE):
            acc = match.group(1).strip()
            if acc and acc not in account_ids:
                account_ids.append(acc)

        return rendered_text, account_ids, display_names

    @staticmethod
    def _extract_adf_text(doc: Dict[str, Any]) -> str:
        """Recursively extract plain text from an Atlassian Document Format JSON object."""
        text, _, _ = JiraEventNormalizer.extract_adf_text_and_mentions(doc)
        return text

