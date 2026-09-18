"""Mubashir Automation Report generator identifying successful Jira comments created by automations."""

import datetime
import zoneinfo
from typing import Any, Dict, List, Optional, Tuple

from app.config.settings import settings
from app.connectors.discord.formatter import DiscordFormatter
from app.core.actions.engine import action_engine
from app.core.actions.types import create_send_notification_action
from app.database.connection import DatabaseManager, db_manager
from app.database.repositories import ActionRepository, DailyReportHistoryRepository
from app.utils.logger import logger


def format_date_human(date_str: str) -> str:
    """Format YYYY-MM-DD into human-readable date e.g. '18 Sep 2026'."""
    try:
        dt = datetime.datetime.strptime(date_str[:10], "%Y-%m-%d")
        return dt.strftime("%d %b %Y")
    except Exception:
        return date_str


def resolve_calendar_day_window_utc(
    target_date: Optional[str] = None,
    tz_name: Optional[str] = None,
) -> Tuple[str, str, str, str]:
    """Resolve reporting calendar day and its half-open UTC ISO window [start_utc, end_utc).

    Returns:
        (date_str, formatted_date, start_utc_iso, end_utc_iso)
    """
    tz_str = tz_name or getattr(settings, "MUBASHIR_AUTOMATION_REPORT_TIMEZONE", None) or settings.get_report_timezone()
    tz = zoneinfo.ZoneInfo(tz_str)

    if target_date and str(target_date).strip():
        clean_date = str(target_date).strip()[:10]
        dt = datetime.datetime.strptime(clean_date, "%Y-%m-%d")
    else:
        # Scheduled morning report evaluates previous calendar day
        now_tz = datetime.datetime.now(tz)
        yesterday_tz = now_tz - datetime.timedelta(days=1)
        dt = datetime.datetime(yesterday_tz.year, yesterday_tz.month, yesterday_tz.day)

    date_str = dt.strftime("%Y-%m-%d")
    formatted_date = format_date_human(date_str)

    # Half-open interval [start_local, end_local)
    start_local = datetime.datetime(dt.year, dt.month, dt.day, 0, 0, 0, tzinfo=tz)
    end_local = start_local + datetime.timedelta(days=1)

    start_utc_iso = start_local.astimezone(datetime.timezone.utc).isoformat()
    end_utc_iso = end_local.astimezone(datetime.timezone.utc).isoformat()

    return date_str, formatted_date, start_utc_iso, end_utc_iso


class MubashirAutomationReportGenerator:
    """Generates read-only structured reports on successful Mubashir automation Jira comments."""

    def __init__(
        self,
        manager: Optional[DatabaseManager] = None,
        action_repo: Optional[ActionRepository] = None,
        history_repo: Optional[DailyReportHistoryRepository] = None,
    ):
        self.mgr = manager or db_manager
        self.action_repo = action_repo or ActionRepository(self.mgr)
        self.history_repo = history_repo or DailyReportHistoryRepository(self.mgr)

    def generate_report(
        self,
        target_date: Optional[str] = None,
        tz_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate structured report of successful real Jira comments created by Mubashir automations.

        Returns a structured dictionary without making any external network calls to Jira or Discord.
        """
        date_str, formatted_date, start_utc_iso, end_utc_iso = resolve_calendar_day_window_utc(
            target_date=target_date,
            tz_name=tz_name,
        )

        raw_actions = self.action_repo.get_successful_mubashir_comments(
            start_time_utc=start_utc_iso,
            end_time_utc=end_utc_iso,
        )

        team_name = (
            settings.JIRA_TEAM_GROUP.strip()
            if settings.is_jira_team_group_configured()
            else "Mursaleen Cluster"
        )

        by_automation: Dict[str, List[Dict[str, Any]]] = {
            "MubashirStaleSupport": [],
            "MubashirSupportRule": [],
        }
        comments: List[Dict[str, Any]] = []

        for act in raw_actions:
            automation_source = act.get("requested_by") or "Unknown"
            issue_key = act.get("target_id") or "Unknown"
            action_id = act.get("action_id") or act.get("id") or "Unknown"
            executed_at = act.get("execution_time") or act.get("executed_at") or act.get("created_at")

            params = act.get("parameters") or {}
            summary = params.get("title") or params.get("summary") or None
            comment_text = params.get("comment") or params.get("comment_body") or None

            jira_url = settings.get_jira_browse_url(issue_key) if issue_key and issue_key != "Unknown" else None

            entry: Dict[str, Any] = {
                "action_id": action_id,
                "automation_source": automation_source,
                "issue_key": issue_key,
                "summary": summary,
                "executed_at": executed_at,
                "comment": comment_text,
                "jira_url": jira_url,
            }

            comments.append(entry)
            if automation_source in by_automation:
                by_automation[automation_source].append(entry)
            else:
                by_automation.setdefault(automation_source, []).append(entry)

        return {
            "team_name": team_name,
            "date": date_str,
            "formatted_date": formatted_date,
            "total_comments": len(comments),
            "by_automation": by_automation,
            "comments": comments,
            "window_start_utc": start_utc_iso,
            "window_end_utc": end_utc_iso,
        }

    async def send_report_to_discord(
        self,
        target_date: Optional[str] = None,
        force: bool = False,
        record_history: bool = True,
    ) -> Dict[str, Any]:
        """Generate and dispatch Mubashir Automation Report to Discord with idempotency and DRY_RUN handling."""
        date_str, formatted_date, start_utc_iso, end_utc_iso = resolve_calendar_day_window_utc(
            target_date=target_date,
            tz_name=settings.MUBASHIR_AUTOMATION_REPORT_TIMEZONE,
        )
        team_group = "Mursaleen Cluster"

        # Check daily_report_history for idempotency unless force=True
        if not force and self.history_repo.has_report_been_sent(
            team_group=team_group,
            report_date=date_str,
            report_type="mubashir_automation_report",
        ):
            logger.info(
                f"Mubashir Automation Report for team '{team_group}' on {date_str} already dispatched. Skipping duplicate."
            )
            return {
                "status": "skipped_duplicate",
                "date": date_str,
                "team_name": team_group,
                "total_comments": 0,
                "recorded_history": False,
                "reason": "already_sent",
            }

        # Generate structured report using existing Phase 7B generator
        report_data = self.generate_report(
            target_date=date_str,
            tz_name=settings.MUBASHIR_AUTOMATION_REPORT_TIMEZONE,
        )

        # Format embed payload
        embed_payload = DiscordFormatter.format_mubashir_automation_report(report_data)

        # Target Discord channel (MUBASHIR_AUTOMATION_REPORT_CHANNEL with fallback to PM_DISCORD_CHANNEL)
        channel = settings.MUBASHIR_AUTOMATION_REPORT_CHANNEL or settings.PM_DISCORD_CHANNEL

        embeds = embed_payload.get("embeds", [])
        embed_title = embeds[0].get("title") if embeds else "🤖 Mubashir Automation Report"
        embed_desc = embeds[0].get("description") if embeds else ""

        notif_action = create_send_notification_action(
            target_system="discord",
            channel=channel,
            title=embed_title,
            message=embed_desc,
            level="INFO",
            fields=embeds,
            requested_by="MubashirAutomationReportGenerator",
        )
        notif_action.parameters["embeds"] = embeds

        # Ensure Discord webhook connector is registered
        if not action_engine.get_connector("discord"):
            from app.connectors.discord import DiscordWebhookConnector
            action_engine.register_connector(DiscordWebhookConnector())

        # Execute through ActionEngine
        try:
            action_res = await action_engine.execute(notif_action)
            status_val = action_res.status.value if hasattr(action_res, "status") else str(action_res.get("status", "unknown"))
            is_dry_run = getattr(action_res, "dry_run", False) or status_val in ("dry_run_simulated", "simulated") or settings.DRY_RUN
            is_success = getattr(action_res, "success", False) or (status_val in ("success", "completed", "simulated", "dry_run_simulated"))

            if not is_success or status_val in ("failed", "rejected"):
                res_status = "failed"
            elif is_dry_run:
                res_status = "simulated"
            else:
                res_status = "sent"

            # Record dispatch history for persistent idempotency if successful and enabled
            if is_success and record_history:
                self.history_repo.record_report_sent(
                    team_group=team_group,
                    report_date=date_str,
                    payload=report_data,
                    report_type="mubashir_automation_report",
                )
                logger.info(f"Recorded Mubashir Automation Report sent for team '{team_group}' on {date_str}.")

            return {
                "status": res_status,
                "date": date_str,
                "team_name": team_group,
                "total_comments": report_data.get("total_comments", 0),
                "recorded_history": bool(record_history and is_success),
                "report": report_data,
                "action_result": {
                    "status": status_val,
                    "action_id": getattr(action_res, "action_id", None),
                },
            }
        except Exception as e:
            logger.error(f"Failed to dispatch Mubashir Automation Report: {e}", exc_info=True)
            return {
                "status": "failed",
                "date": date_str,
                "team_name": team_group,
                "total_comments": report_data.get("total_comments", 0),
                "error": str(e),
                "recorded_history": False,
            }


# Global singleton generator instance
mubashir_automation_report_generator = MubashirAutomationReportGenerator()
