"""Daily activity report generation from normalized SQLite events."""

import datetime
from typing import Any, Dict, List, Optional, Tuple
from collections import defaultdict
from app.database.repositories import EventRepository, EmployeeRoleRepository, DailyReportHistoryRepository
from app.database.connection import db_manager, DatabaseManager
from app.connectors.discord.formatter import DiscordFormatter
from app.core.actions.types import create_send_notification_action
from app.core.actions.engine import action_engine
from app.config.settings import settings
from app.core.performance.roles import resolve_canonical_account_id
from app.utils.time import utc_now_iso, utc_now
from app.utils.logger import logger


def format_date_human(date_str: str) -> str:
    """Format YYYY-MM-DD into human-readable date."""
    try:
        dt = datetime.datetime.strptime(date_str[:10], "%Y-%m-%d")
        return dt.strftime("%B %d, %Y")
    except Exception:
        return date_str


class DailyActivityReportGenerator:
    """Generates Daily PM Activity Reports from persisted normalized events."""

    def __init__(self, manager: Optional[DatabaseManager] = None):
        self.mgr = manager or db_manager
        self.event_repo = EventRepository(self.mgr)
        self.role_repo = EmployeeRoleRepository(self.mgr)
        self.history_repo = DailyReportHistoryRepository(self.mgr)

    def resolve_actor_membership(
        self,
        actor_id: Optional[str],
        actor_name: Optional[str] = None
    ) -> Optional[Tuple[str, str]]:
        """Resolve an event actor against authoritative EmployeeRoleRepository.

        Returns (canonical_account_id, canonical_display_name) if the actor is an
        active, non-excluded member of the Mursaleen Cluster.
        Returns None if the actor cannot be resolved confidently or is not a member.
        """
        if not actor_id and not actor_name:
            return None

        # 1. Resolve canonical account ID via deterministic role resolver
        canonical_id = resolve_canonical_account_id(
            identifier=actor_id,
            display_name=actor_name,
            role_repo=self.role_repo
        )

        assignment = None
        if canonical_id:
            assignment = self.role_repo.get_by_account_id(canonical_id)

        if not assignment and actor_name:
            assignment = self.role_repo.get_by_display_name(actor_name)

        if not assignment:
            return None

        account_id = assignment["account_id"]
        display_name = assignment.get("display_name") or actor_name or account_id

        # 2. Check canonical exclusion list (e.g. Jira admins, MORITZ, QA runner, external users)
        if settings.is_canonical_excluded(account_id, display_name):
            return None

        return account_id, display_name

    def generate_report(self, target_date: Optional[str] = None) -> Dict[str, Any]:
        """Generate daily activity metrics for a date string 'YYYY-MM-DD' (defaults to today UTC)."""
        date_str = target_date or utc_now().strftime("%Y-%m-%d")
        formatted_date = format_date_human(date_str)
        team_name = settings.JIRA_TEAM_GROUP.strip() if settings.is_jira_team_group_configured() else "Mursaleen Cluster"

        events = self.event_repo.get_events_for_date(date_str)

        total_activities = 0
        by_resource: Dict[str, int] = defaultdict(int)
        member_breakdowns: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
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

        transitions_list: List[Dict[str, Any]] = []
        activities_list: List[Dict[str, Any]] = []

        for e in events:
            etype = e.get("event_type", "")
            actor_id = e.get("actor_id")
            actor_name = e.get("actor_name")

            # Extract actor info from payload if top-level fields are empty
            payload = e.get("payload", {})
            if not actor_id and isinstance(payload, dict):
                user_obj = payload.get("user") or payload.get("author") or payload.get("creator") or {}
                if isinstance(user_obj, dict):
                    actor_id = user_obj.get("accountId") or user_obj.get("name")
                    if not actor_name:
                        actor_name = user_obj.get("displayName") or user_obj.get("name")

            resolved = self.resolve_actor_membership(actor_id, actor_name)
            if not resolved:
                # Exclude activities performed by non-members, admins, service accounts, external users
                continue

            canonical_account_id, canonical_name = resolved

            total_activities += 1
            by_resource[canonical_name] += 1
            member_breakdowns[canonical_name][etype] += 1

            task_key = e.get("task_key") or e.get("task_id") or "N/A"
            if task_key and task_key != "N/A":
                jira_url = settings.get_jira_browse_url(task_key)
                jira_link = DiscordFormatter.format_jira_link(task_key)
            else:
                jira_url = None
                jira_link = "N/A"

            if etype == "TaskCreated":
                tasks_created += 1
            elif etype == "TaskUpdated":
                tasks_updated += 1
            elif etype == "TaskCompleted":
                tasks_completed += 1
                status_transitions += 1
                old_st = payload.get("old_status", "In Progress") if isinstance(payload, dict) else "In Progress"
                transitions_list.append({
                    "task": task_key,
                    "task_url": jira_url,
                    "jira_link": jira_link,
                    "actor": canonical_name,
                    "old_status": old_st,
                    "new_status": "Done",
                    "timestamp": e.get("timestamp")
                })
            elif etype == "TaskStatusChanged":
                status_transitions += 1
                old_st = payload.get("old_status", "Unknown") if isinstance(payload, dict) else "Unknown"
                new_st = payload.get("new_status", "Unknown") if isinstance(payload, dict) else "Unknown"
                transitions_list.append({
                    "task": task_key,
                    "task_url": jira_url,
                    "jira_link": jira_link,
                    "actor": canonical_name,
                    "old_status": old_st,
                    "new_status": new_st,
                    "timestamp": e.get("timestamp")
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

            activities_list.append({
                "event_type": etype,
                "task": task_key,
                "task_url": jira_url,
                "jira_link": jira_link,
                "actor": canonical_name,
                "timestamp": e.get("timestamp")
            })

        # Serialized member breakdown
        member_stats = []
        for name, count in sorted(by_resource.items(), key=lambda x: (-x[1], x[0])):
            counts_by_type = dict(member_breakdowns[name])
            member_stats.append({
                "display_name": name,
                "total_activities": count,
                "breakdown": counts_by_type
            })

        report = {
            "date": date_str,
            "formatted_date": formatted_date,
            "team_name": team_name,
            "generated_at": utc_now_iso(),
            "total_activities": total_activities,
            "active_members_count": len(by_resource),
            "activities_by_resource": dict(by_resource),
            "members": member_stats,
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
            "recent_transitions": transitions_list,
            "activities": activities_list,
        }
        return report

    async def send_report_to_discord(
        self,
        target_date: Optional[str] = None,
        force: bool = False,
        record_history: bool = True
    ) -> Dict[str, Any]:
        """Generate and dispatch daily activity report embed to Discord with persistent idempotency tracking."""
        report_data = self.generate_report(target_date)
        date_str = report_data["date"]
        team_group = report_data.get("team_name") or (
            settings.JIRA_TEAM_GROUP.strip() if settings.is_jira_team_group_configured() else "Mursaleen Cluster"
        )

        # Idempotency check: skip if already sent today unless force is True
        if not force and self.history_repo.has_report_been_sent(
            team_group=team_group,
            report_date=date_str,
            report_type="daily_activity_report"
        ):
            logger.info(f"Daily PM Activity Report for team '{team_group}' on {date_str} already dispatched. Skipping.")
            return {
                "status": "skipped",
                "reason": "already_sent_today",
                "date": date_str,
                "team_name": team_group,
            }

        embed_payload = DiscordFormatter.format_daily_report(report_data)
        channel = settings.DAILY_ACTIVITY_REPORT_CHANNEL or settings.PM_DISCORD_CHANNEL

        embeds = embed_payload.get("embeds", [])
        embed_title = embeds[0].get("title") if embeds else f"📋 Daily PM Activity Report — {date_str}"
        embed_desc = embeds[0].get("description") if embeds else f"Daily activity report generated for {date_str}"

        action = create_send_notification_action(
            target_system="discord",
            channel=channel,
            title=embed_title,
            message=embed_desc,
            level="INFO",
            fields=embeds,
            requested_by="DailyActivityReportScheduler"
        )
        action.parameters["embeds"] = embeds

        # Ensure Discord connector is registered
        if not action_engine.get_connector("discord"):
            from app.connectors.discord import DiscordWebhookConnector
            action_engine.register_connector(DiscordWebhookConnector())

        res = await action_engine.execute(action)
        status_val = res.status.value if hasattr(res, "status") and hasattr(res.status, "value") else str(getattr(res, "status", "unknown"))
        is_success = res.success if hasattr(res, "success") else (status_val.upper() in ("COMPLETED", "SUCCESS", "SIMULATED", "DRY_RUN_SIMULATED"))

        if is_success and record_history:
            self.history_repo.record_report_sent(
                team_group=team_group,
                report_date=date_str,
                payload=report_data,
                report_type="daily_activity_report"
            )
            logger.info(f"Recorded daily activity report sent for team '{team_group}' on {date_str}.")

        return {
            "status": status_val,
            "date": date_str,
            "team_name": team_group,
            "total_activities": report_data.get("total_activities", 0),
            "recorded_history": record_history and is_success,
            "report": report_data,
            "action_result": res.model_dump() if hasattr(res, "model_dump") else res
        }


# Global daily report generator instance
daily_report_generator = DailyActivityReportGenerator()
