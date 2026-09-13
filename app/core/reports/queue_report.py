"""Resource Active Queue Report generator using canonical Jira Active Queue filter source of truth."""

import datetime
import zoneinfo
from typing import Any, Dict, List, Optional
from app.config.settings import settings
from app.database.connection import db_manager, DatabaseManager
from app.database.repositories import JiraIssueStateRepository
from app.core.reports.overdue_report import format_jira_due_date, format_date_human, resolve_digest_date
from app.utils.logger import logger


class ResourceQueueReportGenerator:
    """Generates active queue reports for individual resources using canonical Jira filter semantics."""

    def __init__(self, manager: Optional[DatabaseManager] = None):
        self.mgr = manager or db_manager
        self.issue_state_repo = JiraIssueStateRepository(self.mgr)

    def generate_user_queue_report(
        self,
        account_id: str,
        display_name: Optional[str] = None,
        target_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate active queue report for a resource using canonical active queue criteria."""
        date_str = resolve_digest_date(target_date)
        formatted_date = format_date_human(date_str)
        team_group = settings.JIRA_TEAM_GROUP.strip() if settings.is_jira_team_group_configured() else "Mursaleen Cluster"

        if settings.is_canonical_excluded(account_id, display_name):
            return {
                "account_id": account_id,
                "display_name": display_name or account_id,
                "team_name": team_group,
                "date": date_str,
                "formatted_date": formatted_date,
                "active_count": 0,
                "tickets": [],
                "is_excluded": True,
                "jql": settings.get_active_queue_jql(assignee_account_id=account_id, assignee_display_name=display_name),
            }

        # Query active issues from local SQLite repository projection (derived from canonical Jira active queue criteria)
        raw_issues = self.issue_state_repo.get_active_issues_for_resource(
            account_id=account_id,
            display_name=display_name,
            team_group=team_group,
        )

        tickets: List[Dict[str, Any]] = []
        for item in raw_issues:
            tkey = item.get("jira_issue_key")
            if not tkey:
                continue

            summary = item.get("summary") or "Untitled"
            status = item.get("status") or "To Do"
            priority = item.get("priority") or "Medium"
            due_date_raw = item.get("due_date")

            tickets.append({
                "key": tkey,
                "summary": summary,
                "status": status,
                "priority": priority,
                "due_date": format_jira_due_date(due_date_raw),
                "due_date_raw": due_date_raw,
                "url": settings.get_jira_browse_url(tkey),
            })

        return {
            "account_id": account_id,
            "display_name": display_name or account_id,
            "team_name": team_group,
            "date": date_str,
            "formatted_date": formatted_date,
            "active_count": len(tickets),
            "tickets": tickets,
            "is_excluded": False,
            "jql": settings.get_active_queue_jql(assignee_account_id=account_id, assignee_display_name=display_name),
        }


# Global singleton instance
resource_queue_report_generator = ResourceQueueReportGenerator()
