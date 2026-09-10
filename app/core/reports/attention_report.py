"""Daily consolidated PM Attention Digest report generation."""

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
    """Format YYYY-MM-DD into readable date (e.g. September 11, 2026)."""
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


def calculate_inactive_for(
    updated_at_raw: Optional[str],
    reference_dt: Optional[datetime.datetime] = None
) -> str:
    """Calculate inactive duration from actual Jira updated timestamp."""
    if not updated_at_raw or not str(updated_at_raw).strip():
        return "N/A"
    dt = parse_iso_datetime(str(updated_at_raw).strip())
    if not dt:
        return "N/A"

    ref = reference_dt or utc_now()
    # Normalize timezones for comparison
    if dt.tzinfo is not None and ref.tzinfo is None:
        ref = ref.replace(tzinfo=datetime.timezone.utc)
    elif dt.tzinfo is None and ref.tzinfo is not None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)

    delta = ref - dt
    total_seconds = delta.total_seconds()
    if total_seconds < 0:
        return "0h"

    days = int(total_seconds // 86400)
    if days >= 1:
        return f"{days} day{'s' if days != 1 else ''}"

    hours = int(total_seconds // 3600)
    return f"{max(1, hours)}h"


def resolve_digest_date(target_date: Optional[str] = None) -> str:
    """Resolve report date in configured timezone (defaults to Asia/Karachi)."""
    if target_date and target_date.strip():
        return target_date.strip()[:10]
    try:
        tz = zoneinfo.ZoneInfo(settings.PM_ATTENTION_DIGEST_TIMEZONE)
        return datetime.datetime.now(tz).strftime("%Y-%m-%d")
    except Exception:
        return utc_now().strftime("%Y-%m-%d")


class DailyPMAttentionReportGenerator:
    """Generates and dispatches daily consolidated PM Attention Digest."""

    def __init__(self, manager: Optional[DatabaseManager] = None):
        self.mgr = manager or db_manager
        self.issue_state_repo = JiraIssueStateRepository(self.mgr)
        self.history_repo = DailyReportHistoryRepository(self.mgr)

    def generate_digest(self, target_date: Optional[str] = None) -> Dict[str, Any]:
        """Generate PM Attention Digest with strictly 3 categories for V1."""
        date_str = resolve_digest_date(target_date)
        formatted_date = format_date_human(date_str)
        team_group = settings.JIRA_TEAM_GROUP.strip() if settings.is_jira_team_group_configured() else "Mursaleen Cluster"

        # 1. Inactive / Stalled tasks exceeding inactivity threshold
        stale_candidates = self.issue_state_repo.get_stale_candidates(
            threshold_hours=settings.STALE_TASK_HOURS,
            team_group=team_group
        )
        inactive_stalled: List[Dict[str, Any]] = []
        for item in stale_candidates:
            tkey = item.get("jira_issue_key")
            if not tkey:
                continue
            updated_raw = item.get("updated_at") or item.get("last_activity_at")
            inactive_stalled.append({
                "key": tkey,
                "url": settings.get_jira_browse_url(tkey),
                "status": item.get("status") or "Unknown",
                "updated_at": format_display_date(updated_raw),
                "updated_at_raw": updated_raw,
                "inactive_for": calculate_inactive_for(updated_raw),
            })

        # 2. Reopened tasks (active, incomplete)
        reopened_candidates = self.issue_state_repo.get_reopened_candidates(team_group=team_group)
        reopened: List[Dict[str, Any]] = []
        for item in reopened_candidates:
            tkey = item.get("jira_issue_key")
            if not tkey:
                continue
            updated_raw = item.get("updated_at")
            reopened.append({
                "key": tkey,
                "url": settings.get_jira_browse_url(tkey),
                "status": item.get("status") or "Reopened",
                "updated_at": format_display_date(updated_raw),
                "updated_at_raw": updated_raw,
            })

        # 3. Unassigned tasks (active, incomplete)
        unassigned_candidates = self.issue_state_repo.get_unassigned_candidates(team_group=team_group)
        unassigned: List[Dict[str, Any]] = []
        for item in unassigned_candidates:
            tkey = item.get("jira_issue_key")
            if not tkey:
                continue
            updated_raw = item.get("updated_at")
            unassigned.append({
                "key": tkey,
                "url": settings.get_jira_browse_url(tkey),
                "status": item.get("status") or "Unknown",
                "updated_at": format_display_date(updated_raw),
                "updated_at_raw": updated_raw,
            })

        total_count = len(inactive_stalled) + len(reopened) + len(unassigned)

        return {
            "team_name": team_group,
            "date": date_str,
            "formatted_date": formatted_date,
            "total_count": total_count,
            "categories": {
                "inactive_stalled": {
                    "title": "🟠 Inactive / Stalled",
                    "count": len(inactive_stalled),
                    "tickets": inactive_stalled,
                },
                "reopened": {
                    "title": "🔁 Reopened",
                    "count": len(reopened),
                    "tickets": reopened,
                },
                "unassigned": {
                    "title": "📌 Unassigned",
                    "count": len(unassigned),
                    "tickets": unassigned,
                },
            }
        }

    async def send_digest_to_discord(
        self,
        target_date: Optional[str] = None,
        force: bool = False,
        record_history: bool = True
    ) -> Dict[str, Any]:
        """Generate and dispatch daily PM Attention Digest to Discord with idempotency protection."""
        date_str = resolve_digest_date(target_date)
        team_group = settings.JIRA_TEAM_GROUP.strip() if settings.is_jira_team_group_configured() else "Mursaleen Cluster"

        # Idempotency check: skip if already sent today and force is not requested
        if not force and self.history_repo.has_report_been_sent(team_group, date_str, report_type="pm_attention_digest"):
            logger.info(f"Daily PM Attention Digest for team '{team_group}' on {date_str} already dispatched. Skipping.")
            return {
                "status": "skipped",
                "reason": "already_sent_today",
                "date": date_str,
                "team_name": team_group,
            }

        # Generate report data
        report_data = self.generate_digest(target_date=date_str)

        # Format embed
        embed_payload = DiscordFormatter.format_pm_attention_digest(report_data)

        # Target Discord channel
        channel = settings.PM_ATTENTION_DIGEST_CHANNEL or settings.PM_DISCORD_CHANNEL

        # Construct notification action
        embeds = embed_payload.get("embeds", [])
        embed_title = embeds[0].get("title") if embeds else f"⚠️ {team_group} — PM Attention Digest"
        embed_desc = embeds[0].get("description") if embeds else ""

        notif_action = create_send_notification_action(
            target_system="discord",
            channel=channel,
            title=embed_title,
            message=embed_desc,
            level="WARNING" if report_data["total_count"] > 0 else "INFO",
            fields=embeds,
            requested_by="DailyPMAttentionDigestScheduler"
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
                report_type="pm_attention_digest"
            )
            logger.info(f"Recorded daily PM Attention Digest sent for team '{team_group}' on {date_str}.")

        return {
            "status": status_val,
            "date": date_str,
            "team_name": team_group,
            "total_count": report_data["total_count"],
            "recorded_history": record_history and is_success,
            "action_result": {
                "status": status_val,
                "action_id": getattr(action_res, "action_id", None),
            },
        }


# Global singleton generator instance
daily_pm_attention_report_generator = DailyPMAttentionReportGenerator()
