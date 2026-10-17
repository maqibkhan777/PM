"""Daily consolidated overdue task digest report generation."""

import datetime
import zoneinfo
import random
import time
from typing import Any, Dict, List, Optional, Set
import httpx
from app.connectors.jira.client import JiraClient
from app.database.connection import db_manager, DatabaseManager
from app.database.repositories import (
    JiraIssueStateRepository,
    DailyReportHistoryRepository,
)
from app.connectors.discord.formatter import DiscordFormatter
from app.core.actions.types import create_send_notification_action
from app.core.actions.engine import action_engine
from app.config.settings import settings
from app.utils.time import utc_now, utc_now_iso, parse_iso_datetime
from app.utils.logger import logger


def format_date_human(date_str: str) -> str:
    """Format YYYY-MM-DD into readable date (e.g. September 10, 2026)."""
    try:
        dt = datetime.datetime.strptime(date_str[:10], "%Y-%m-%d")
        return dt.strftime("%B %d, %Y")
    except Exception:
        return date_str


def format_jira_due_date(date_str: Optional[str]) -> str:
    """Format Jira due date into YYYY-MM-DD or return '—' if unavailable."""
    if not date_str or not str(date_str).strip():
        return "—"
    s = str(date_str).strip()
    dt = parse_iso_datetime(s)
    if not dt:
        try:
            dt = datetime.datetime.strptime(s[:10], "%Y-%m-%d")
        except Exception:
            pass
    if dt:
        return dt.strftime("%Y-%m-%d")
    return s[:10] if len(s) >= 10 else s


# Alias for backward compatibility
format_display_date = format_jira_due_date



def format_jira_updated_at(date_str: Optional[str]) -> str:
    """Format Jira updated timestamp into YYYY-MM-DD HH:MM or YYYY-MM-DD, or return '—' if unavailable."""
    if not date_str or not str(date_str).strip():
        return "—"
    s = str(date_str).strip()
    dt = parse_iso_datetime(s)
    if dt:
        if dt.hour or dt.minute:
            return dt.strftime("%Y-%m-%d %H:%M")
        return dt.strftime("%Y-%m-%d")
    return s


def resolve_digest_date(target_date: Optional[str] = None) -> str:
    """Resolve report date in configured timezone (defaults to Asia/Karachi)."""
    if target_date and target_date.strip():
        return target_date.strip()[:10]
    try:
        tz = zoneinfo.ZoneInfo(settings.get_report_timezone())
        return datetime.datetime.now(tz).strftime("%Y-%m-%d")
    except Exception:
        return utc_now().strftime("%Y-%m-%d")


class DailyOverdueReportGenerator:
    """Generates and dispatches daily consolidated overdue task digests."""

    def __init__(
        self,
        manager: Optional[DatabaseManager] = None,
        issue_state_repo: Optional[JiraIssueStateRepository] = None,
        history_repo: Optional[DailyReportHistoryRepository] = None,
        jira_client: Optional[JiraClient] = None,
    ):
        self.mgr = manager or db_manager
        self.issue_state_repo = issue_state_repo or JiraIssueStateRepository(self.mgr)
        self.history_repo = history_repo or DailyReportHistoryRepository(self.mgr)
        self.jira_client = jira_client or JiraClient()

    def _fetch_jira_issues_sync(
        self,
        jql: str,
        batch_size: int = 50,
        max_total: int = 500,
    ) -> List[Dict[str, Any]]:
        """Synchronously query Jira Cloud REST API v3 (/rest/api/3/search/jql) with cursor pagination."""
        if not settings.is_jira_configured():
            raise RuntimeError("Jira credentials not configured")

        base_url = settings.JIRA_BASE_URL.rstrip("/")
        url = f"{base_url}/rest/api/3/search/jql"
        auth = httpx.BasicAuth(settings.JIRA_EMAIL, settings.JIRA_API_TOKEN)
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "PM-Operations-Agent/0.1.0",
        }
        timeout = settings.REQUEST_TIMEOUT_SECONDS
        max_retries = settings.MAX_RETRIES
        backoff = settings.RETRY_BACKOFF_FACTOR
        fields = ["summary", "status", "priority", "duedate", "updated", "assignee", "project", "issuetype"]

        all_issues: List[Dict[str, Any]] = []
        next_page_token: Optional[str] = None
        seen_tokens: Set[str] = set()

        with httpx.Client(auth=auth, headers=headers, timeout=timeout) as client:
            while True:
                params: Dict[str, Any] = {
                    "jql": jql,
                    "maxResults": batch_size,
                    "fields": ",".join(fields),
                }
                if next_page_token:
                    params["nextPageToken"] = next_page_token

                data: Optional[Dict[str, Any]] = None
                for attempt in range(1, max_retries + 1):
                    try:
                        resp = client.get(url, params=params)
                        if resp.status_code in (401, 403):
                            logger.error(f"Jira auth failure {resp.status_code} when querying overdue JQL: {jql}")
                            resp.raise_for_status()

                        if resp.status_code in (429, 500, 502, 503, 504):
                            if attempt < max_retries:
                                retry_after = None
                                hv = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
                                if hv:
                                    try:
                                        retry_after = float(hv)
                                    except ValueError:
                                        pass
                                base_sleep = retry_after if retry_after is not None else (backoff ** attempt)
                                jitter = random.uniform(0.1, 0.5)
                                sleep_time = min(max(base_sleep, backoff ** attempt) + jitter, 30.0)
                                time.sleep(sleep_time)
                                continue
                            else:
                                resp.raise_for_status()

                        resp.raise_for_status()
                        data = resp.json()
                        break
                    except (httpx.TimeoutException, httpx.RequestError) as e:
                        if attempt < max_retries:
                            jitter = random.uniform(0.1, 0.5)
                            sleep_time = min((backoff ** attempt) + jitter, 30.0)
                            time.sleep(sleep_time)
                        else:
                            raise

                if not data or not isinstance(data, dict):
                    break

                issues = data.get("issues", [])
                all_issues.extend(issues)

                if len(all_issues) >= max_total:
                    break

                is_last = data.get("isLast", True if not data.get("nextPageToken") else False)
                next_page_token = data.get("nextPageToken")

                if is_last or not next_page_token or next_page_token in seen_tokens:
                    break
                seen_tokens.add(next_page_token)

        return all_issues

    def _parse_and_cache_jira_issues(
        self,
        raw_issues: List[Dict[str, Any]],
        team_group: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Parse raw Jira issue objects into normalized ticket dicts and update local SQLite issue cache."""
        tickets: List[Dict[str, Any]] = []
        now_str = utc_now_iso()

        for item in raw_issues:
            tkey = item.get("key")
            if not tkey:
                continue

            fields = item.get("fields", {})
            summary = fields.get("summary") or "Untitled"
            status_name = fields.get("status", {}).get("name") or "To Do"
            priority_name = fields.get("priority", {}).get("name") or "Medium"
            due_date_raw = fields.get("duedate")
            updated_str = fields.get("updated")
            assignee_obj = fields.get("assignee") or {}
            assignee_name = assignee_obj.get("displayName") or assignee_obj.get("name") or assignee_obj.get("accountId")
            project_obj = fields.get("project") or {}
            project_key = project_obj.get("key")
            issuetype_obj = fields.get("issuetype") or {}

            # Warm/update local SQLite issue cache without throwing
            try:
                self.issue_state_repo.upsert(
                    jira_issue_key=tkey,
                    summary=summary,
                    status=status_name,
                    assignee=assignee_name,
                    priority=priority_name,
                    due_date=due_date_raw,
                    updated_at=updated_str,
                    last_seen_at=now_str,
                    project_key=project_key,
                    raw_reference=None,
                    team_group=team_group,
                    issue_type=issuetype_obj.get("name", "Task") if isinstance(issuetype_obj, dict) else "Task",
                )
            except Exception as e:
                logger.debug(f"Non-fatal error updating local issue state cache for {tkey}: {e}")

            tickets.append({
                "key": tkey,
                "summary": summary,
                "assignee": (assignee_name or "").strip() or "—",
                "url": settings.get_jira_browse_url(tkey),
                "due_date": format_jira_due_date(due_date_raw),
                "due_date_raw": due_date_raw,
                "updated_at": format_jira_updated_at(updated_str),
                "updated_at_raw": updated_str,
            })

        return tickets

    def generate_digest(
        self,
        target_date: Optional[str] = None,
        live_jira: bool = False,
    ) -> Dict[str, Any]:
        """Generate overdue task digest data. When live_jira=True, queries live Jira API with SQLite fallback."""
        date_str = resolve_digest_date(target_date)
        formatted_date = format_date_human(date_str)
        team_group = settings.JIRA_TEAM_GROUP.strip() if settings.is_jira_team_group_configured() else "Mursaleen Cluster"

        tickets: List[Dict[str, Any]] = []
        live_fetch_success = False

        if live_jira and settings.is_jira_configured():
            try:
                # Live JQL query: Authoritative current due date < now, not done, within team group
                jql = f'duedate < now() AND statusCategory != Done AND assignee in membersOf("{team_group}") ORDER BY duedate ASC'
                logger.info(f"Querying live Jira for team overdue tasks: {jql}")
                raw_issues = self._fetch_jira_issues_sync(jql=jql)
                tickets = self._parse_and_cache_jira_issues(raw_issues, team_group=team_group)
                live_fetch_success = True
                logger.info(f"Retrieved {len(tickets)} live overdue tickets for team '{team_group}' from Jira.")
            except Exception as e:
                logger.warning(f"Error querying live Jira for overdue digest ({e}). Falling back to local SQLite state cache.")

        if not live_fetch_success:
            # Fall back to local SQLite repository projection
            candidates = self.issue_state_repo.get_overdue_candidates(team_group=team_group)
            for item in candidates:
                tkey = item.get("jira_issue_key")
                if not tkey:
                    continue

                due_date_raw = item.get("due_date")
                updated_at_raw = item.get("updated_at")
                summary = item.get("summary") or "No summary"
                assignee = (item.get("assignee") or "").strip() or "—"

                tickets.append({
                    "key": tkey,
                    "summary": summary,
                    "assignee": assignee,
                    "url": settings.get_jira_browse_url(tkey),
                    "due_date": format_jira_due_date(due_date_raw),
                    "due_date_raw": due_date_raw,
                    "updated_at": format_jira_updated_at(updated_at_raw),
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

    def generate_user_overdue_digest(
        self,
        account_id: str,
        display_name: Optional[str] = None,
        target_date: Optional[str] = None,
        live_jira: bool = False,
    ) -> Dict[str, Any]:
        """Generate overdue task digest data for a specific individual resource."""
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
                "overdue_count": 0,
                "tickets": [],
                "is_excluded": True,
            }

        from app.core.performance.roles import get_account_aliases, resolve_canonical_account_id
        aliases = set(get_account_aliases(account_id)) if account_id else set()
        if account_id:
            aliases.add(account_id)

        tickets: List[Dict[str, Any]] = []
        live_fetch_success = False

        if live_jira and settings.is_jira_configured():
            try:
                # Live JQL query: Authoritative overdue query for this specific user
                user_clauses = [f'assignee = "{account_id}"']
                if display_name and display_name.strip():
                    user_clauses.append(f'assignee = "{display_name.strip()}"')
                assignee_filter = f"({' OR '.join(user_clauses)})"
                jql = f"duedate < now() AND statusCategory != Done AND {assignee_filter} ORDER BY duedate ASC"
                logger.info(f"Querying live Jira for user overdue tasks: {jql}")
                raw_issues = self._fetch_jira_issues_sync(jql=jql)
                tickets = self._parse_and_cache_jira_issues(raw_issues, team_group=team_group)
                live_fetch_success = True
                logger.info(f"Retrieved {len(tickets)} live overdue tickets for user '{display_name or account_id}' from Jira.")
            except Exception as e:
                logger.warning(f"Error querying live Jira for user overdue digest ({e}). Falling back to local SQLite state cache.")

        if not live_fetch_success:
            # Fall back to local SQLite repository projection
            candidates = self.issue_state_repo.get_overdue_candidates(team_group=team_group)
            for item in candidates:
                tkey = item.get("jira_issue_key")
                if not tkey:
                    continue

                assignee_raw = (item.get("assignee") or "").strip()
                raw_ref = item.get("raw_reference")
                assignee_acc = None
                if isinstance(raw_ref, dict):
                    assignee_acc = raw_ref.get("fields", {}).get("assignee", {}).get("accountId")

                can_assignee = resolve_canonical_account_id(assignee_acc or assignee_raw, display_name=assignee_raw) or assignee_raw
                is_match = (
                    (can_assignee and can_assignee in aliases)
                    or (assignee_raw and assignee_raw in aliases)
                    or (assignee_acc and assignee_acc in aliases)
                    or (display_name and assignee_raw.lower() == display_name.strip().lower())
                )
                if not is_match:
                    continue

                due_date_raw = item.get("due_date")
                updated_at_raw = item.get("updated_at")
                summary = item.get("summary") or "No summary"

                tickets.append({
                    "key": tkey,
                    "summary": summary,
                    "assignee": display_name or assignee_raw or "—",
                    "url": settings.get_jira_browse_url(tkey),
                    "due_date": format_jira_due_date(due_date_raw),
                    "due_date_raw": due_date_raw,
                    "updated_at": format_jira_updated_at(updated_at_raw),
                    "updated_at_raw": updated_at_raw,
                })

        return {
            "account_id": account_id,
            "display_name": display_name or account_id,
            "team_name": team_group,
            "date": date_str,
            "formatted_date": formatted_date,
            "overdue_count": len(tickets),
            "tickets": tickets,
            "is_excluded": False,
        }


# Global singleton generator instance
daily_overdue_report_generator = DailyOverdueReportGenerator()
