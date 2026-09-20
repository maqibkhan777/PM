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

    def build_attention_context(
        self,
        team_group: Optional[str] = None,
        stale_threshold_hours: Optional[int] = None,
        max_items_per_category: int = 10,
        objective: str = "Analyze PM attention candidates and synthesize recommendations for human review",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AIContext:
        """Construct bounded, sanitized context from existing PM attention signals.
        
        Extracts facts strictly from local JiraIssueStateRepository projections:
        - Inactive/stale candidates exceeding inactivity threshold
        - Overdue candidates whose due date has passed
        - Reopened candidates
        - Unassigned active candidates
        """
        from app.config.settings import settings
        from app.core.reports.attention_report import calculate_inactive_for, format_display_date

        team = team_group or (settings.JIRA_TEAM_GROUP.strip() if settings.is_jira_team_group_configured() else "Mursaleen Cluster")
        threshold = stale_threshold_hours or settings.STALE_TASK_HOURS

        stale_items = self.issue_repo.get_stale_candidates(threshold_hours=threshold, team_group=team)[:max_items_per_category]
        overdue_items = self.issue_repo.get_overdue_candidates(team_group=team)[:max_items_per_category]
        reopened_items = self.issue_repo.get_reopened_candidates(team_group=team)[:max_items_per_category]
        unassigned_items = self.issue_repo.get_unassigned_candidates(team_group=team)[:max_items_per_category]

        activity_summaries: List[str] = []
        for item in stale_items:
            k = item.get("jira_issue_key", "UNKNOWN")
            inact = calculate_inactive_for(item.get("updated_at") or item.get("last_activity_at"))
            activity_summaries.append(f"STALE: {k} | Status: {item.get('status')} | Inactive: {inact} | Assignee: {item.get('assignee') or 'None'}")

        for item in overdue_items:
            k = item.get("jira_issue_key", "UNKNOWN")
            activity_summaries.append(f"OVERDUE: {k} | Due: {item.get('due_date')} | Status: {item.get('status')} | Assignee: {item.get('assignee') or 'None'}")

        for item in reopened_items:
            k = item.get("jira_issue_key", "UNKNOWN")
            activity_summaries.append(f"REOPENED: {k} | Status: {item.get('status')} | Assignee: {item.get('assignee') or 'None'}")

        for item in unassigned_items:
            k = item.get("jira_issue_key", "UNKNOWN")
            activity_summaries.append(f"UNASSIGNED: {k} | Status: {item.get('status')} | Priority: {item.get('priority') or 'None'}")

        metrics = [
            MetricSummaryContext(metric_name="stale_count", value=len(stale_items), description=f"Tasks inactive > {threshold}h"),
            MetricSummaryContext(metric_name="overdue_count", value=len(overdue_items), description="Tasks past due date"),
            MetricSummaryContext(metric_name="reopened_count", value=len(reopened_items), description="Tasks reopened and active"),
            MetricSummaryContext(metric_name="unassigned_count", value=len(unassigned_items), description="Active tasks without assignee"),
            MetricSummaryContext(metric_name="total_attention_count", value=len(stale_items) + len(overdue_items) + len(reopened_items) + len(unassigned_items)),
        ]

        clean_metadata = sanitize_dict(metadata) if metadata else {}
        repo_stale = [
            {
                "key": it.get("jira_issue_key"),
                "summary": it.get("summary"),
                "status": it.get("status"),
                "assignee": it.get("assignee"),
                "priority": it.get("priority"),
                "due_date": it.get("due_date"),
                "updated_at": format_display_date(it.get("updated_at")),
                "inactivity_duration": calculate_inactive_for(it.get("updated_at") or it.get("last_activity_at")),
            }
            for it in stale_items
        ]
        repo_overdue = [
            {
                "key": it.get("jira_issue_key"),
                "summary": it.get("summary"),
                "status": it.get("status"),
                "assignee": it.get("assignee"),
                "priority": it.get("priority"),
                "due_date": it.get("due_date"),
                "updated_at": format_display_date(it.get("updated_at")),
            }
            for it in overdue_items
        ]
        repo_reopened = [
            {
                "key": it.get("jira_issue_key"),
                "summary": it.get("summary"),
                "status": it.get("status"),
                "assignee": it.get("assignee"),
                "priority": it.get("priority"),
                "updated_at": format_display_date(it.get("updated_at")),
            }
            for it in reopened_items
        ]
        repo_unassigned = [
            {
                "key": it.get("jira_issue_key"),
                "summary": it.get("summary"),
                "status": it.get("status"),
                "assignee": it.get("assignee"),
                "priority": it.get("priority"),
                "updated_at": format_display_date(it.get("updated_at")),
            }
            for it in unassigned_items
        ]

        # Combine repo items with any caller-specified metadata items
        combined_stale = (clean_metadata.get("stale_items") or []) + repo_stale
        combined_overdue = (clean_metadata.get("overdue_items") or []) + repo_overdue
        combined_reopened = (clean_metadata.get("reopened_items") or []) + repo_reopened
        combined_unassigned = (clean_metadata.get("unassigned_items") or []) + repo_unassigned

        clean_metadata.update({
            "stale_items": combined_stale,
            "overdue_items": combined_overdue,
            "reopened_items": combined_reopened,
            "unassigned_items": combined_unassigned,
        })

        return AIContext(
            context_id=f"ctx-{uuid.uuid4().hex[:12]}",
            timestamp=utc_now_iso(),
            objective=objective,
            team_name=team,
            recent_activity_summary=activity_summaries,
            metrics=metrics,
            applicable_policies=["OverdueTaskPolicy", "StaleTaskPolicy", "ReopenedTaskPolicy", "UnassignedTaskPolicy"],
            metadata=clean_metadata,
        )

