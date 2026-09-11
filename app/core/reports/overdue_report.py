"""Daily consolidated overdue task digest report generation."""

import datetime
import zoneinfo
from typing import Any, Dict, List, Optional
from app.database.connection import db_manager, DatabaseManager
from app.database.repositories import (
    JiraIssueStateRepository,
    DailyReportHistoryRepository,
)
from app.connectors.discord.formatter import DiscordFormatter
from app.core.actions.types import create_send_notification_action
from app.core.actions.engine import action_engine
from app.config.settings import settings
from app.utils.time import utc_now, parse_iso_datetime
from app.utils.logger import logger


def format_date_human(date_str: str) -> str:
    """Format YYYY-MM-DD into readable date (e.g. September 10, 2026)."""
    try:
        dt = datetime.datetime.strptime(date_str[:10], "%Y-%m-%d")
        return dt.strftime("%B %d, %Y")
    except Exception:
        return date_str


def format_display_date(date_str: Optional[str]) -> str:
    """Format date string into readable short date (e.g. Sep 08, 2026)."""
    if not date_str or not str(date_str).strip():
        return "N/A"
    s = str(date_str).strip()
    dt = parse_iso_datetime(s)
    if not dt:
        try:
            dt = datetime.datetime.strptime(s[:10], "%Y-%m-%d")
        except Exception:
            pass
    if dt:
        return dt.strftime("%b %d, %Y")
    return s


def resolve_digest_date(target_date: Optional[str] = None) -> str:
    """Resolve report date in configured timezone (defaults to Asia/Karachi)."""
    if target_date and target_date.strip():
        return target_date.strip()[:10]
    try:
        tz = zoneinfo.ZoneInfo(settings.OVERDUE_DIGEST_TIMEZONE)
        return datetime.datetime.now(tz).strftime("%Y-%m-%d")
    except Exception:
        return utc_now().strftime("%Y-%m-%d")


class DailyOverdueReportGenerator:
    """Generates and dispatches daily consolidated overdue task digests."""

    def __init__(self, manager: Optional[DatabaseManager] = None):
        self.mgr = manager or db_manager
        self.issue_state_repo = JiraIssueStateRepository(self.mgr)
        self.history_repo = DailyReportHistoryRepository(self.mgr)

    def generate_digest(self, target_date: Optional[str] = None) -> Dict[str, Any]:
        """Generate overdue task digest data by consuming existing overdue state candidates."""
        date_str = resolve_digest_date(target_date)
        formatted_date = format_date_human(date_str)
        team_group = settings.JIRA_TEAM_GROUP.strip() if settings.is_jira_team_group_configured() else "Mursaleen Cluster"

        # Consume existing overdue candidates from single source of truth in repository
        candidates = self.issue_state_repo.get_overdue_candidates(team_group=team_group)

        tickets: List[Dict[str, Any]] = []
        for item in candidates:
            tkey = item.get("jira_issue_key")
            if not tkey:
                continue

            due_date_raw = item.get("due_date")
            updated_at_raw = item.get("updated_at")
            summary = item.get("summary") or "No summary"
            assignee = item.get("assignee") or "Unassigned"

            tickets.append({
                "key": tkey,
                "summary": summary,
                "assignee": assignee,
                "url": settings.get_jira_browse_url(tkey),
                "due_date": format_display_date(due_date_raw),
                "due_date_raw": due_date_raw,
                "updated_at": format_display_date(updated_at_raw),
                "updated_at_raw": updated_at_raw,
            })


        return {
            "team_name": team_group,
            "date": date_str,
            "formatted_date": formatted_date,
            "overdue_count": len(tickets),
            "tickets": tickets,
        }

    async def send_digest_to_discord(
        self,
        target_date: Optional[str] = None,
        force: bool = False,
        record_history: bool = True
    ) -> Dict[str, Any]:
        """Generate and dispatch daily overdue digest to Discord with idempotency protection."""
        date_str = resolve_digest_date(target_date)
        team_group = settings.JIRA_TEAM_GROUP.strip() if settings.is_jira_team_group_configured() else "Mursaleen Cluster"

        # Idempotency check: skip if already sent today and force is not requested
        if not force and self.history_repo.has_report_been_sent(team_group, date_str, report_type="overdue_digest"):
            logger.info(f"Daily overdue digest for team '{team_group}' on {date_str} already dispatched. Skipping.")
            return {
                "status": "skipped",
                "reason": "already_sent_today",
                "date": date_str,
                "team_name": team_group,
            }

        # Generate report data
        report_data = self.generate_digest(target_date=date_str)

        # Format embed
        embed_payload = DiscordFormatter.format_overdue_digest(report_data)

        # Target Discord channel
        channel = settings.OVERDUE_DIGEST_CHANNEL or settings.PM_DISCORD_CHANNEL

        # Construct notification action
        embeds = embed_payload.get("embeds", [])
        embed_title = embeds[0].get("title") if embeds else f"🚨 {team_group} — Overdue Tasks"
        embed_desc = embeds[0].get("description") if embeds else ""

        notif_action = create_send_notification_action(
            target_system="discord",
            channel=channel,
            title=embed_title,
            message=embed_desc,
            level="WARNING" if report_data["overdue_count"] > 0 else "INFO",
            fields=embeds,
            requested_by="DailyOverdueDigestScheduler"
        )
        notif_action.parameters["embeds"] = embeds

        # Ensure Discord connector is registered
        if not action_engine.get_connector("discord"):
            from app.connectors.discord import DiscordWebhookConnector
            action_engine.register_connector(DiscordWebhookConnector())

        # Dispatch via action engine
        action_res = await action_engine.execute(notif_action)
        status_val = action_res.status.value if hasattr(action_res, "status") else str(action_res.get("status", "unknown"))
        is_success = action_res.success if hasattr(action_res, "success") else (status_val in ("success", "simulated", "dry_run_simulated"))

        # Record dispatch history for persistent idempotency if successful and enabled
        if is_success and record_history:
            self.history_repo.record_report_sent(
                team_group=team_group,
                report_date=date_str,
                payload=report_data,
                report_type="overdue_digest"
            )
            logger.info(f"Recorded daily overdue digest sent for team '{team_group}' on {date_str}.")

        return {
            "status": status_val,
            "date": date_str,
            "team_name": team_group,
            "overdue_count": report_data["overdue_count"],
            "recorded_history": record_history and is_success,
            "action_result": {
                "status": status_val,
                "action_id": getattr(action_res, "action_id", None),
            },
        }


# Global singleton generator instance
daily_overdue_report_generator = DailyOverdueReportGenerator()
