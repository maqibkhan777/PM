"""Daily activity report generation from normalized SQLite events."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from collections import defaultdict
from app.database.repositories import EventRepository
from app.database.connection import db_manager, DatabaseManager
from app.connectors.discord.formatter import DiscordFormatter
from app.core.actions.types import create_send_notification_action
from app.core.actions.engine import action_engine
from app.config.settings import settings
from app.utils.time import utc_now_iso, utc_now
from app.utils.logger import logger


class DailyActivityReportGenerator:
    """Generates Daily PM Activity Reports from persisted normalized events."""

    def __init__(self, manager: Optional[DatabaseManager] = None):
        self.mgr = manager or db_manager
        self.event_repo = EventRepository(self.mgr)

    def generate_report(self, target_date: Optional[str] = None) -> Dict[str, Any]:
        """Generate daily activity metrics for a date string 'YYYY-MM-DD' (defaults to today UTC)."""
        date_str = target_date or utc_now().strftime("%Y-%m-%d")
        events = self.event_repo.get_events_for_date(date_str)

        total_activities = len(events)
        by_resource: Dict[str, int] = defaultdict(int)
        tasks_created = 0
        tasks_updated = 0
        tasks_completed = 0
        status_transitions = 0
        comments_added = 0
        worklogs_logged = 0
        workflow_violations = 0
        stale_tasks = 0
        blocked_tasks = 0
        reopened_tasks = 0

        transitions_list: List[Dict[str, str]] = []

        for e in events:
            etype = e.get("event_type", "")
            actor = e.get("actor_name") or "System"
            if actor:
                by_resource[actor] += 1

            if etype == "TaskCreated":
                tasks_created += 1
            elif etype == "TaskUpdated":
                tasks_updated += 1
            elif etype == "TaskCompleted":
                tasks_completed += 1
                status_transitions += 1
            elif etype == "TaskStatusChanged":
                status_transitions += 1
                payload = e.get("payload", {})
                transitions_list.append({
                    "task": e.get("task_key") or e.get("task_id") or "N/A",
                    "actor": actor,
                    "old_status": payload.get("old_status", "Unknown"),
                    "new_status": payload.get("new_status", "Unknown")
                })
            elif etype == "TaskCommentAdded":
                comments_added += 1
            elif etype == "TaskWorklogged":
                worklogs_logged += 1
            elif etype == "WorkflowViolation":
                workflow_violations += 1
            elif etype == "StaleTask":
                stale_tasks += 1
            elif etype == "TaskBlocked":
                blocked_tasks += 1
            elif etype == "TaskReopened":
                reopened_tasks += 1

        report = {
            "date": date_str,
            "generated_at": utc_now_iso(),
            "total_activities": total_activities,
            "activities_by_resource": dict(by_resource),
            "tasks_created": tasks_created,
            "tasks_updated": tasks_updated,
            "tasks_completed": tasks_completed,
            "status_transitions": status_transitions,
            "comments_added": comments_added,
            "worklogs_logged": worklogs_logged,
            "workflow_violations": workflow_violations,
            "stale_tasks": stale_tasks,
            "blocked_tasks": blocked_tasks,
            "reopened_tasks": reopened_tasks,
            "recent_transitions": transitions_list[-10:],
        }
        return report

    async def send_report_to_discord(self, target_date: Optional[str] = None) -> Dict[str, Any]:
        """Generate and dispatch daily activity report embed to Discord."""
        report_data = self.generate_report(target_date)
        embed_payload = DiscordFormatter.format_daily_report(report_data)

        action = create_send_notification_action(
            target_system="discord",
            channel=settings.PM_DISCORD_CHANNEL,
            title=f"📋 Daily PM Activity Report — {report_data['date']}",
            message=f"Daily activity report generated for {report_data['date']}",
            level="INFO",
            fields=embed_payload.get("embeds"),
            requested_by="DailyReportGenerator"
        )
        action.parameters["embeds"] = embed_payload.get("embeds")

        res = await action_engine.execute(action)
        return {
            "report": report_data,
            "action_result": res.model_dump()
        }


# Global daily report generator instance
daily_report_generator = DailyActivityReportGenerator()
