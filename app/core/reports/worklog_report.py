"""Daily team worklog report generation from normalized Jira worklog records."""

import datetime
import zoneinfo
from typing import Any, Dict, List, Optional, Set
from collections import defaultdict
from app.database.connection import db_manager, DatabaseManager
from app.database.repositories import (
    JiraWorklogRepository,
    JiraIssueStateRepository,
    DailyReportHistoryRepository,
)
from app.connectors.jira.client import JiraClient
from app.connectors.jira.normalizer import JiraEventNormalizer
from app.connectors.discord.formatter import DiscordFormatter
from app.core.actions.types import create_send_notification_action
from app.core.actions.engine import action_engine
from app.config.settings import settings
from app.core.performance.roles import resolve_canonical_account_id
from app.utils.time import utc_now, utc_now_iso
from app.utils.logger import logger


def format_seconds(seconds: int) -> str:
    """Format seconds into human-readable duration (e.g. 1h 55m, 30m, 0m)."""
    if seconds <= 0:
        return "0m"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours > 0 and minutes > 0:
        return f"{hours}h {minutes:02d}m"
    elif hours > 0:
        return f"{hours}h"
    else:
        return f"{minutes}m"


def format_date_human(date_str: str) -> str:
    """Format YYYY-MM-DD into readable date (e.g. September 10, 2026)."""
    try:
        dt = datetime.datetime.strptime(date_str[:10], "%Y-%m-%d")
        return dt.strftime("%B %d, %Y")
    except Exception:
        return date_str


def resolve_report_date(target_date: Optional[str] = None) -> str:
    """Resolve report date in configured timezone (defaults to Asia/Karachi)."""
    if target_date and target_date.strip():
        return target_date.strip()[:10]
    try:
        tz = zoneinfo.ZoneInfo(settings.DAILY_WORKLOG_REPORT_TIMEZONE)
        return datetime.datetime.now(tz).strftime("%Y-%m-%d")
    except Exception:
        return utc_now().strftime("%Y-%m-%d")


class DailyWorklogReportGenerator:
    """Generates and dispatches daily team worklog reports based on actual Jira time-log data."""

    def __init__(
        self,
        manager: Optional[DatabaseManager] = None,
        client: Optional[JiraClient] = None
    ):
        self.mgr = manager or db_manager
        self.worklog_repo = JiraWorklogRepository(self.mgr)
        self.issue_state_repo = JiraIssueStateRepository(self.mgr)
        self.history_repo = DailyReportHistoryRepository(self.mgr)
        self.client = client or JiraClient()

    async def sync_jira_worklogs_for_date(self, target_date: str) -> Dict[str, Any]:
        """Fetch latest worklogs from Jira for issues worked on target date across ALL issue types and save to SQLite.

        Raises RuntimeError if Jira communication fails, ensuring caller does not fall back silently to an empty report.
        """
        if not settings.is_jira_configured() or not settings.is_jira_team_group_configured():
            logger.warning("Jira credentials or JIRA_TEAM_GROUP not configured; skipping worklog sync.")
            return {"synced_count": 0, "issues_scanned": 0, "worklogs_found": 0, "jql": None}

        team_group = settings.JIRA_TEAM_GROUP.strip()
        jql = f'worklogAuthor in membersOf("{team_group}") AND worklogDate >= "{target_date}" AND worklogDate <= "{target_date}"'
        logger.info(f"Syncing Jira worklogs for team '{team_group}' on {target_date} with JQL: {jql}")

        synced_count = 0
        issues_scanned = 0
        worklogs_found = 0

        try:
            res = await self.client.search_issues(jql=jql, max_results=50, fields=["summary", "worklog", "assignee"])
            issues = res.get("issues", [])
            issues_scanned = len(issues)
            for issue in issues:
                task_key = issue.get("key")
                fields = issue.get("fields", {})
                wl_section = fields.get("worklog", {})
                worklogs_list = wl_section.get("worklogs", []) if isinstance(wl_section, dict) else []

                # If issue has more worklogs than embedded, fetch full worklog list
                total_wls = wl_section.get("total", len(worklogs_list)) if isinstance(wl_section, dict) else len(worklogs_list)
                if total_wls > len(worklogs_list):
                    try:
                        worklogs_list = await self.client.get_issue_worklogs(task_key)
                    except Exception as e:
                        logger.warning(f"Could not fetch full worklogs for {task_key}: {e}")

                # Cache summary into issue_state_repo if missing
                summary = fields.get("summary") or ""
                if summary:
                    existing_state = self.issue_state_repo.get(task_key)
                    if not existing_state:
                        self.issue_state_repo.upsert(
                            jira_issue_key=task_key,
                            summary=summary,
                            status=(fields.get("status") or {}).get("name") or "Unknown",
                            assignee=(fields.get("assignee") or {}).get("displayName"),
                            last_seen_at=utc_now_iso(),
                            team_group=team_group
                        )

                for w in worklogs_list:
                    worklogs_found += 1
                    wid = str(w.get("id"))
                    author = w.get("author", {})
                    time_secs = int(w.get("timeSpentSeconds", 0))
                    w_started = w.get("started") or w.get("created") or utc_now_iso()
                    w_comment_raw = w.get("comment", "")
                    w_comment = JiraEventNormalizer._extract_adf_text(w_comment_raw) if isinstance(w_comment_raw, dict) else str(w_comment_raw or "")

                    self.worklog_repo.upsert_worklog(
                        worklog_id=wid,
                        jira_issue_key=task_key,
                        jira_issue_id=str(issue.get("id")),
                        author_account_id=author.get("accountId"),
                        author_display_name=author.get("displayName"),
                        time_spent_seconds=time_secs,
                        started_at=w_started,
                        created_at=w.get("created"),
                        updated_at=w.get("updated"),
                        comment=w_comment,
                        team_group=team_group,
                        source="jira"
                    )
                    synced_count += 1
            logger.info(
                f"Successfully synced {synced_count} worklogs from Jira across {issues_scanned} issues "
                f"for date {target_date} (total worklogs scanned: {worklogs_found})."
            )
            return {
                "synced_count": synced_count,
                "issues_scanned": issues_scanned,
                "worklogs_found": worklogs_found,
                "jql": jql
            }
        except Exception as e:
            logger.error(f"Error syncing worklogs from Jira for date {target_date}: {e}", exc_info=True)
            raise RuntimeError(f"Failed to sync Jira worklogs for date {target_date}: {e}") from e

    async def generate_report(
        self,
        target_date: Optional[str] = None,
        sync_jira: bool = True
    ) -> Dict[str, Any]:
        """Generate consolidated daily worklog metrics for a specific date (YYYY-MM-DD)."""
        date_str = resolve_report_date(target_date)
        formatted_date = format_date_human(date_str)
        team_group = settings.JIRA_TEAM_GROUP.strip() if settings.is_jira_team_group_configured() else "Team"
        excluded_ids: Set[str] = settings.get_daily_worklog_excluded_account_ids()

        sync_performed = False
        sync_jql = None
        issues_found = 0
        worklogs_found = 0
        worklogs_persisted = 0

        if sync_jira:
            sync_info = await self.sync_jira_worklogs_for_date(date_str)
            sync_performed = True
            sync_jql = sync_info.get("jql")
            issues_found = sync_info.get("issues_scanned", 0)
            worklogs_found = sync_info.get("worklogs_found", 0)
            worklogs_persisted = sync_info.get("synced_count", 0)

        # Retrieve reportable team members from Jira group
        group_members: List[Dict[str, Any]] = []
        if settings.is_jira_configured() and settings.is_jira_team_group_configured():
            try:
                group_members = await self.client.get_group_members(team_group)
            except Exception as e:
                logger.warning(f"Could not retrieve members for group '{team_group}': {e}")

        # Filter out inactive accounts and excluded members
        reportable_group_members: Dict[str, str] = {}
        for gm in group_members:
            gid = gm.get("accountId")
            gname = gm.get("displayName") or "Unknown"
            is_active = gm.get("active", True)
            if not is_active or not gid:
                continue
            if gid in excluded_ids:
                continue
            reportable_group_members[gid] = gname

        # Retrieve worklogs from local SQLite database for the target date
        worklogs = self.worklog_repo.get_worklogs_for_date(
            date_str,
            team_group=team_group if settings.is_jira_team_group_configured() else None
        )

        # Seed member stats with all reportable group members (0m / 0 tickets by default)
        member_stats: Dict[str, Dict[str, Any]] = {}
        for gid, gname in reportable_group_members.items():
            member_stats[gid] = {
                "account_id": gid,
                "display_name": gname,
                "seconds": 0,
                "tickets": set(),
            }

        # Ticket stats dictionary: issue_key -> {seconds, worklogs_count, summary}
        ticket_stats: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {"seconds": 0, "worklogs_count": 0, "summary": ""}
        )

        for w in worklogs:
            raw_author_id = w.get("author_account_id")
            author_name = w.get("author_display_name") or raw_author_id or "Unknown"

            # Check exclusion: excluded users MUST NOT contribute to anything
            if raw_author_id and raw_author_id in excluded_ids:
                continue

            # Deterministically resolve canonical account ID for legacy usernames/aliases
            author_id = resolve_canonical_account_id(raw_author_id, display_name=author_name) or raw_author_id
            if author_id and author_id in excluded_ids:
                continue

            time_secs = int(w.get("time_spent_seconds", 0))
            tkey = w.get("jira_issue_key")

            # Determine member key (prefer account_id, fallback to display_name)
            mem_key = author_id or author_name
            if mem_key not in member_stats:
                member_stats[mem_key] = {
                    "account_id": author_id or "",
                    "display_name": author_name,
                    "seconds": 0,
                    "tickets": set(),
                }
            elif author_name and member_stats[mem_key]["display_name"] == "Unknown":
                member_stats[mem_key]["display_name"] = author_name

            member_stats[mem_key]["seconds"] += time_secs
            if tkey:
                member_stats[mem_key]["tickets"].add(tkey)
                ticket_stats[tkey]["seconds"] += time_secs
                ticket_stats[tkey]["worklogs_count"] += 1
                if not ticket_stats[tkey]["summary"]:
                    cached_issue = self.issue_state_repo.get(tkey)
                    if cached_issue:
                        ticket_stats[tkey]["summary"] = cached_issue.get("summary") or ""

        # Total team seconds and member counts from reportable population only
        total_seconds = sum(m["seconds"] for m in member_stats.values())
        members_with_time = [m for m in member_stats.values() if m["seconds"] > 0]
        members_without_time = [m for m in member_stats.values() if m["seconds"] == 0]

        members_logged_count = len(members_with_time)
        reportable_members_count = len(member_stats)

        # Sort members: highest logged time first; 0m sorted alphabetically by display_name
        sorted_members_with_time = sorted(members_with_time, key=lambda x: x["seconds"], reverse=True)
        sorted_members_without_time = sorted(members_without_time, key=lambda x: x["display_name"].lower())
        all_sorted_members = sorted_members_with_time + sorted_members_without_time

        formatted_members = [
            {
                "display_name": m["display_name"],
                "account_id": m["account_id"],
                "time_logged_seconds": m["seconds"],
                "time_logged_human": format_seconds(m["seconds"]),
                "tickets_count": len(m["tickets"]),
                "tickets": sorted(list(m["tickets"])),
            }
            for m in all_sorted_members
        ]

        zero_worklog_members = [
            {
                "account_id": m["account_id"],
                "display_name": m["display_name"],
            }
            for m in sorted_members_without_time
        ]

        sorted_tickets = [
            {
                "issue_key": tkey,
                "summary": tval["summary"],
                "time_logged_seconds": tval["seconds"],
                "time_logged_human": format_seconds(tval["seconds"]),
                "worklogs_count": tval["worklogs_count"],
            }
            for tkey, tval in sorted(ticket_stats.items(), key=lambda x: x[1]["seconds"], reverse=True)
        ]

        # Comprehensive diagnostic logging as required by Task 1
        logger.info(
            f"📊 Daily Worklog Report Summary: "
            f"report_date={date_str}, "
            f"jira_sync_performed={sync_performed}, "
            f"jira_jql='{sync_jql}', "
            f"jira_issues_found={issues_found}, "
            f"jira_worklogs_found={worklogs_found}, "
            f"worklogs_persisted={worklogs_persisted}, "
            f"eligible_members={reportable_members_count}, "
            f"total_seconds={total_seconds} ({format_seconds(total_seconds)}), "
            f"members_logged={members_logged_count}/{reportable_members_count}, "
            f"tickets_worked={len(ticket_stats)}"
        )

        report = {
            "team_name": team_group,
            "report_date": date_str,
            "formatted_date": formatted_date,
            "total_time_seconds": total_seconds,
            "total_time_human": format_seconds(total_seconds),
            "members_logged_count": members_logged_count,
            "reportable_members_count": reportable_members_count,
            "members_logged_ratio": f"{members_logged_count} / {reportable_members_count}",
            "tickets_worked_count": len(ticket_stats),
            "members": formatted_members,
            "tickets": sorted_tickets,
            "zero_worklog_members": zero_worklog_members,
            "excluded_account_ids": list(excluded_ids),
        }
        return report

    async def send_report_to_discord(
        self,
        target_date: Optional[str] = None,
        force: bool = False,
        sync_jira: bool = True
    ) -> Dict[str, Any]:
        """Generate and dispatch daily worklog report to Discord via ActionEngine with idempotency."""
        date_str = resolve_report_date(target_date)
        team_group = settings.JIRA_TEAM_GROUP.strip() if settings.is_jira_team_group_configured() else "Team"

        # Check idempotency: avoid sending duplicate daily reports on restart
        if not force and self.history_repo.has_report_been_sent(team_group, date_str):
            logger.info(f"Daily worklog report for team '{team_group}' on {date_str} was already sent today. Skipping duplicate dispatch.")
            return {
                "status": "skipped",
                "reason": "already_sent_today",
                "report_date": date_str,
                "team_group": team_group
            }

        # Generate report with synchronization
        report_data = await self.generate_report(date_str, sync_jira=sync_jira)
        embed_payload = DiscordFormatter.format_daily_worklog_report(report_data)

        # Dispatch through ActionEngine -> DiscordConnector
        if not action_engine.get_connector("discord"):
            from app.connectors.discord import DiscordWebhookConnector
            action_engine.register_connector(DiscordWebhookConnector())

        channel = settings.DAILY_WORKLOG_REPORT_CHANNEL or settings.PM_DISCORD_CHANNEL
        action = create_send_notification_action(
            target_system="discord",
            channel=channel,
            title=f"📊 {team_group} — Daily Worklog",
            message=f"Daily worklog report for {report_data.get('formatted_date', date_str)} (Team Total: {report_data['total_time_human']})",
            level="INFO",
            fields=embed_payload.get("embeds", [{}])[0].get("fields"),
            requested_by="DailyWorklogReportGenerator"
        )
        action.parameters["embeds"] = embed_payload.get("embeds")

        res = await action_engine.execute(action)

        # Record dispatch history for idempotency
        self.history_repo.record_report_sent(team_group, date_str, report_data)
        logger.info(f"Recorded daily worklog report sent for team '{team_group}' on {date_str}.")

        return {
            "status": "sent",
            "report": report_data,
            "action_result": res.model_dump()
        }


# Global instance
daily_worklog_report_generator = DailyWorklogReportGenerator()
