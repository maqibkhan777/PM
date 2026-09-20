"""Context builder transforming PM domain entities into bounded, sanitized AIContext payloads."""

import uuid
from typing import Any, Dict, List, Optional
from app.database.connection import DatabaseManager, db_manager
from app.database.repositories import (
    EmployeeRoleRepository,
    EventRepository,
    JiraIssueStateRepository,
)
from app.services.ai.models import (
    AIContext,
    MetricSummaryContext,
    ResourceSummaryContext,
    TaskSummaryContext,
)
from app.utils.logger import sanitize_dict
from app.utils.time import utc_now_iso


class ContextBuilder:
    """Safely extracts and shapes domain data for AI ingestion without exposing secrets."""

    def __init__(self, manager: Optional[DatabaseManager] = None):
        self.mgr = manager or db_manager
        self.issue_repo = JiraIssueStateRepository(self.mgr)
        self.role_repo = EmployeeRoleRepository(self.mgr)
        self.event_repo = EventRepository(self.mgr)

    def build_task_context(
        self,
        task_key: str,
        objective: str = "Assess task status and PM intervention requirements",
        include_recent_events: bool = True,
        max_events: int = 5,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AIContext:
        """Construct a bounded, typed AIContext for a specific Jira issue."""
        issue_data = self.issue_repo.get(task_key)
        task_summary = None
        assignee_summary = None
        recent_summaries = []

        if issue_data:
            assignee_name = issue_data.get("assignee")
            task_summary = TaskSummaryContext(
                task_id=str(issue_data.get("id") or issue_data.get("jira_issue_id") or task_key),
                key=task_key,
                title=str(issue_data.get("summary") or "Untitled"),
                status=str(issue_data.get("status") or "Unknown"),
                priority=str(issue_data.get("priority") or "Unknown"),
                assignee_name=assignee_name,
                reporter_name=issue_data.get("reporter"),
                due_date=issue_data.get("due_date"),
                updated_at=issue_data.get("updated_at"),
                labels=issue_data.get("labels") or [],
            )

            if assignee_name:
                role_record = self.role_repo.get_by_display_name(assignee_name)
                assignee_summary = ResourceSummaryContext(
                    account_id=role_record.get("account_id") if role_record else None,
                    display_name=assignee_name,
                    role=role_record.get("role_title") if role_record else None,
                    is_team_member=bool(role_record),
                )

        if include_recent_events:
            events = self.event_repo.list_events(task_id=task_key, limit=max_events)
            for ev in events:
                etype = ev.get("event_type", "Event")
                actor = ev.get("actor_name") or "System"
                ts = ev.get("timestamp", "")[:19]
                recent_summaries.append(f"[{ts}] {etype} by {actor}")

        # Sanitize any caller-provided metadata to ensure zero credentials leak
        clean_metadata = sanitize_dict(metadata) if metadata else {}

        return AIContext(
            context_id=f"ctx-{uuid.uuid4().hex[:12]}",
            timestamp=utc_now_iso(),
            objective=objective,
            task=task_summary,
            resource=assignee_summary,
            team_name=issue_data.get("team_group") if issue_data else None,
            recent_activity_summary=recent_summaries,
            metrics=[],
            applicable_policies=["StaleTaskPolicy", "OverdueTaskPolicy", "WorkflowViolationPolicy"],
            metadata=clean_metadata,
        )

    def build_custom_context(
        self,
        objective: str,
        task: Optional[TaskSummaryContext] = None,
        resource: Optional[ResourceSummaryContext] = None,
        team_name: Optional[str] = None,
        recent_activity_summary: Optional[List[str]] = None,
        metrics: Optional[List[MetricSummaryContext]] = None,
        applicable_policies: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AIContext:
        """Construct an AIContext with custom bounded inputs."""
        clean_metadata = sanitize_dict(metadata) if metadata else {}
        return AIContext(
            context_id=f"ctx-{uuid.uuid4().hex[:12]}",
            timestamp=utc_now_iso(),
            objective=objective,
            task=task,
            resource=resource,
            team_name=team_name,
            recent_activity_summary=recent_activity_summary or [],
            metrics=metrics or [],
            applicable_policies=applicable_policies or [],
            metadata=clean_metadata,
        )
