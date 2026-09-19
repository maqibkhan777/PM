"""Resource Active Queue Report generator using authoritative Jira saved-filter mapping and SQLite cache fallback."""

import datetime
import random
import time
from typing import Any, Dict, List, Optional, Set, Union
import httpx

from app.config.settings import settings
from app.connectors.jira.client import JiraClient
from app.core.performance.roles import get_employee_queue_filter_id
from app.core.reports.overdue_report import format_date_human, format_jira_due_date, resolve_digest_date
from app.database.connection import DatabaseManager, db_manager
from app.database.repositories import EmployeeRoleRepository, JiraIssueStateRepository
from app.utils.logger import logger
from app.utils.time import utc_now_iso


class ResourceQueueReportGenerator:
    """Generates active queue reports for individual resources using Jira saved filters with SQLite fallback."""

    def __init__(
        self,
        manager: Optional[DatabaseManager] = None,
        jira_client: Optional[JiraClient] = None,
        role_repo: Optional[EmployeeRoleRepository] = None,
        issue_state_repo: Optional[JiraIssueStateRepository] = None,
    ):
        self.mgr = manager or db_manager
        self.issue_state_repo = issue_state_repo or JiraIssueStateRepository(self.mgr)
        self.role_repo = role_repo or EmployeeRoleRepository(self.mgr)
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
                            logger.error(f"Jira auth failure {resp.status_code} when querying queue JQL: {jql}")
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

    async def _fetch_jira_issues_async(
        self,
        jql: str,
        batch_size: int = 50,
        max_total: int = 500,
    ) -> List[Dict[str, Any]]:
        """Asynchronously query Jira Cloud REST API v3 (/rest/api/3/search/jql) with cursor pagination."""
        if not settings.is_jira_configured():
            raise RuntimeError("Jira credentials not configured")

        all_issues: List[Dict[str, Any]] = []
        next_page_token: Optional[str] = None
        seen_tokens: Set[str] = set()
        fields = ["summary", "status", "priority", "duedate", "updated", "assignee", "project", "issuetype"]

        while True:
            data = await self.jira_client.search_issues(
                jql=jql,
                next_page_token=next_page_token,
                max_results=batch_size,
                fields=fields,
            )
            if not isinstance(data, dict):
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
        """Parse Jira issue objects into normalized ticket dicts and update local SQLite issue cache."""
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
            components_raw = fields.get("components", []) if isinstance(fields.get("components"), list) else []
            components_list = [c.get("name") if isinstance(c, dict) else str(c) for c in components_raw]
            labels_list = fields.get("labels", []) if isinstance(fields.get("labels"), list) else []
            subtasks_raw = fields.get("subtasks", []) if isinstance(fields.get("subtasks"), list) else []
            orig_est_secs = fields.get("timeoriginalestimate") or (fields.get("timetracking", {}).get("originalEstimateSeconds") if isinstance(fields.get("timetracking"), dict) else None)
            time_spent_secs = fields.get("timespent") or (fields.get("timetracking", {}).get("timeSpentSeconds") if isinstance(fields.get("timetracking"), dict) else None)
            creator_obj = fields.get("creator") or fields.get("reporter") or {}
            creator_id = creator_obj.get("accountId") or creator_obj.get("name") if isinstance(creator_obj, dict) else str(creator_obj)

            # Warm/update local SQLite issue cache without throwing or deleting existing records
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
                    labels=labels_list,
                    components=components_list,
                    subtask_count=len(subtasks_raw),
                    original_estimate_seconds=orig_est_secs,
                    time_spent_seconds=time_spent_secs,
                    creator_id=creator_id,
                )
            except Exception as e:
                logger.debug(f"Non-fatal error updating local issue state cache for {tkey}: {e}")

            tickets.append({
                "key": tkey,
                "summary": summary,
                "status": status_name,
                "priority": priority_name,
                "due_date": format_jira_due_date(due_date_raw),
                "due_date_raw": due_date_raw,
                "url": settings.get_jira_browse_url(tkey),
            })

        return tickets

    def _load_sqlite_cached_tickets(
        self,
        account_id: str,
        display_name: Optional[str] = None,
        team_group: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Fall back to local SQLite repository projection when Jira query fails."""
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
        return tickets

    def generate_user_queue_report(
        self,
        account_id: str,
        display_name: Optional[str] = None,
        target_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate active queue report using authoritative Jira filter with SQLite fallback."""
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
                "filter_id": None,
                "source": "excluded",
                "cache_fallback": False,
            }

        filter_id = get_employee_queue_filter_id(
            account_id=account_id,
            display_name=display_name,
            role_repo=self.role_repo,
        )

        tickets: List[Dict[str, Any]] = []
        source = "jira_live"
        cache_fallback = False

        if filter_id:
            jql = f"filter = {filter_id}"
            try:
                raw_jira_issues = self._fetch_jira_issues_sync(jql=jql)
                tickets = self._parse_and_cache_jira_issues(raw_jira_issues, team_group=team_group)
            except Exception as e:
                logger.warning(
                    f"Live Jira queue fetch failed for resource '{display_name or account_id}' (filter: {filter_id}): {e}. "
                    f"Falling back to local SQLite cache."
                )
                tickets = self._load_sqlite_cached_tickets(account_id=account_id, display_name=display_name, team_group=team_group)
                source = "sqlite_cache"
                cache_fallback = True
        else:
            jql = settings.get_active_queue_jql(
                assignee_account_id=account_id,
                assignee_display_name=display_name,
            )
            tickets = self._load_sqlite_cached_tickets(account_id=account_id, display_name=display_name, team_group=team_group)
            source = "sqlite_cache"
            cache_fallback = True

        return {
            "account_id": account_id,
            "display_name": display_name or account_id,
            "team_name": team_group,
            "date": date_str,
            "formatted_date": formatted_date,
            "active_count": len(tickets),
            "tickets": tickets,
            "is_excluded": False,
            "jql": jql,
            "filter_id": filter_id,
            "source": source,
            "cache_fallback": cache_fallback,
        }

    async def generate_user_queue_report_async(
        self,
        account_id: str,
        display_name: Optional[str] = None,
        target_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Async version of generate_user_queue_report using JiraClient async search."""
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
                "filter_id": None,
                "source": "excluded",
                "cache_fallback": False,
            }

        filter_id = get_employee_queue_filter_id(
            account_id=account_id,
            display_name=display_name,
            role_repo=self.role_repo,
        )

        tickets: List[Dict[str, Any]] = []
        source = "jira_live"
        cache_fallback = False

        if filter_id:
            jql = f"filter = {filter_id}"
            try:
                raw_jira_issues = await self._fetch_jira_issues_async(jql=jql)
                tickets = self._parse_and_cache_jira_issues(raw_jira_issues, team_group=team_group)
            except Exception as e:
                logger.warning(
                    f"Live Jira queue async fetch failed for resource '{display_name or account_id}' (filter: {filter_id}): {e}. "
                    f"Falling back to local SQLite cache."
                )
                tickets = self._load_sqlite_cached_tickets(account_id=account_id, display_name=display_name, team_group=team_group)
                source = "sqlite_cache"
                cache_fallback = True
        else:
            jql = settings.get_active_queue_jql(
                assignee_account_id=account_id,
                assignee_display_name=display_name,
            )
            tickets = self._load_sqlite_cached_tickets(account_id=account_id, display_name=display_name, team_group=team_group)
            source = "sqlite_cache"
            cache_fallback = True

        return {
            "account_id": account_id,
            "display_name": display_name or account_id,
            "team_name": team_group,
            "date": date_str,
            "formatted_date": formatted_date,
            "active_count": len(tickets),
            "tickets": tickets,
            "is_excluded": False,
            "jql": jql,
            "filter_id": filter_id,
            "source": source,
            "cache_fallback": cache_fallback,
        }


# Global singleton instance
resource_queue_report_generator = ResourceQueueReportGenerator()
