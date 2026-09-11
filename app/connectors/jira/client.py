"""Jira Cloud REST API HTTP client with timeouts, authentication, and retry handling."""

import asyncio
import random
import time
from typing import Any, Dict, List, Optional
import httpx
from app.config.settings import settings
from app.utils.logger import logger


class JiraClient:
    """Async HTTP Client for Jira Cloud REST API v3."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        email: Optional[str] = None,
        api_token: Optional[str] = None,
        timeout: Optional[float] = None
    ):
        self.base_url = (base_url or settings.JIRA_BASE_URL).rstrip("/")
        self.email = email or settings.JIRA_EMAIL
        self.api_token = api_token or settings.JIRA_API_TOKEN
        self.timeout = timeout or settings.REQUEST_TIMEOUT_SECONDS
        self._client: Optional[httpx.AsyncClient] = None
        self._myself_cache: Optional[Dict[str, Any]] = None
        self._myself_cache_expires_at: float = 0.0

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            auth = httpx.BasicAuth(self.email, self.api_token)
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "PM-Operations-Agent/0.1.0"
            }
            self._client = httpx.AsyncClient(
                auth=auth,
                headers=headers,
                timeout=self.timeout
            )
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client session."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _request(
        self,
        method: str,
        path: str,
        json_data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Execute an HTTP request against Jira REST API with retry handling."""
        client = self._get_client()
        url = f"{self.base_url}{path}"
        max_retries = settings.MAX_RETRIES
        backoff = settings.RETRY_BACKOFF_FACTOR

        for attempt in range(1, max_retries + 1):
            try:
                response = await client.request(
                    method=method,
                    url=url,
                    json=json_data,
                    params=params
                )

                # Permanent auth failure - do not retry
                if response.status_code in (401, 403):
                    logger.error(f"Jira authentication failed: HTTP {response.status_code} for {method} {path}")
                    response.raise_for_status()

                # Rate limiting (429) or transient server errors (5xx)
                if response.status_code in (429, 500, 502, 503, 504):
                    if attempt < max_retries:
                        retry_after = None
                        header_val = response.headers.get("Retry-After") or response.headers.get("retry-after")
                        if header_val:
                            try:
                                retry_after = float(header_val)
                            except ValueError:
                                pass

                        base_sleep = retry_after if retry_after is not None else (backoff ** attempt)
                        jitter = random.uniform(0.1, 0.5)
                        sleep_time = min(max(base_sleep, backoff ** attempt) + jitter, 60.0)

                        if response.status_code == 429:
                            logger.warning(
                                f"Jira API rate-limited (HTTP 429) on {method} {path}. "
                                f"Retry-After: {retry_after or 'N/A'}s. "
                                f"Backing off for {sleep_time:.2f}s (attempt {attempt}/{max_retries})"
                            )
                        else:
                            logger.warning(
                                f"Jira API transient error (HTTP {response.status_code}) on {method} {path}. "
                                f"Retrying in {sleep_time:.2f}s (attempt {attempt}/{max_retries})"
                            )

                        await asyncio.sleep(sleep_time)
                        continue
                    else:
                        response.raise_for_status()

                response.raise_for_status()
                if response.status_code == 204 or not response.content:
                    return {}
                return response.json()

            except httpx.TimeoutException as e:
                if attempt < max_retries:
                    jitter = random.uniform(0.1, 0.5)
                    sleep_time = min((backoff ** attempt) + jitter, 60.0)
                    logger.warning(f"Jira API timeout on {method} {path}. Retrying in {sleep_time:.2f}s (attempt {attempt}/{max_retries})")
                    await asyncio.sleep(sleep_time)
                else:
                    logger.error(f"Jira API request timed out after {max_retries} attempts on {method} {path}: {e}")
                    raise
            except httpx.RequestError as e:
                if attempt < max_retries:
                    jitter = random.uniform(0.1, 0.5)
                    sleep_time = min((backoff ** attempt) + jitter, 60.0)
                    logger.warning(f"Jira connection error on {method} {path}: {e}. Retrying in {sleep_time:.2f}s (attempt {attempt}/{max_retries})")
                    await asyncio.sleep(sleep_time)
                else:
                    logger.error(f"Jira API connection failed after {max_retries} attempts on {method} {path}: {e}")
                    raise

        raise RuntimeError("Unexpected end of request retry loop")

    # --------------------------------------------------------------------------
    # API Methods
    # --------------------------------------------------------------------------

    async def get_myself(self, force_refresh: bool = False) -> Dict[str, Any]:
        """Fetch current authenticated user profile for health check with short TTL cache."""
        now = time.time()
        if not force_refresh and self._myself_cache and now < self._myself_cache_expires_at:
            return self._myself_cache

        data = await self._request("GET", "/rest/api/3/myself")
        if isinstance(data, dict):
            self._myself_cache = data
            self._myself_cache_expires_at = now + 60.0
        return data

    async def get_issue(self, issue_key_or_id: str) -> Dict[str, Any]:
        """Fetch issue details including changelog."""
        return await self._request(
            "GET",
            f"/rest/api/3/issue/{issue_key_or_id}",
            params={"expand": "changelog,names,schema"}
        )

    async def search_issues(
        self,
        jql: str,
        next_page_token: Optional[str] = None,
        max_results: int = 50,
        expand: Optional[str] = "changelog",
        fields: Optional[List[str]] = None,
        **kwargs: Any
    ) -> Dict[str, Any]:
        """Search issues using JQL via Jira Cloud /rest/api/3/search/jql with cursor pagination and changelog expansion."""
        params: Dict[str, Any] = {
            "jql": jql,
            "maxResults": max_results
        }
        if next_page_token:
            params["nextPageToken"] = next_page_token
        if expand:
            params["expand"] = expand
        if fields is None:
            fields = ["*navigable", "comment", "worklog"]
        if fields:
            params["fields"] = ",".join(fields)
        return await self._request("GET", "/rest/api/3/search/jql", params=params)

    async def get_issue_comments(self, issue_key_or_id: str) -> List[Dict[str, Any]]:
        """Fetch comments for a specific issue."""
        res = await self._request("GET", f"/rest/api/3/issue/{issue_key_or_id}/comment")
        return res.get("comments", []) if isinstance(res, dict) else []

    async def get_issue_worklogs(self, issue_key_or_id: str) -> List[Dict[str, Any]]:
        """Fetch worklogs for a specific issue."""
        res = await self._request("GET", f"/rest/api/3/issue/{issue_key_or_id}/worklog")
        return res.get("worklogs", []) if isinstance(res, dict) else []

    async def get_projects(self) -> List[Dict[str, Any]]:
        """Fetch list of accessible Jira projects."""
        res = await self._request("GET", "/rest/api/3/project")
        return res if isinstance(res, list) else []

    async def get_users(self, query: str = "%") -> List[Dict[str, Any]]:
        """Search Jira users."""
        res = await self._request("GET", "/rest/api/3/users/search", params={"query": query})
        return res if isinstance(res, list) else []

    async def get_transitions(self, issue_key: str) -> List[Dict[str, Any]]:
        """Fetch available status transitions for an issue."""
        res = await self._request("GET", f"/rest/api/3/issue/{issue_key}/transitions")
        return res.get("transitions", [])

    async def transition_issue(self, issue_key: str, transition_id: str) -> Dict[str, Any]:
        """Execute a status transition on an issue."""
        payload = {"transition": {"id": transition_id}}
        return await self._request("POST", f"/rest/api/3/issue/{issue_key}/transitions", json_data=payload)

    async def assign_issue(self, issue_key: str, account_id: Optional[str]) -> Dict[str, Any]:
        """Assign issue to a user by Atlassian accountId (or None to unassign)."""
        payload = {"accountId": account_id}
        return await self._request("PUT", f"/rest/api/3/issue/{issue_key}/assignee", json_data=payload)

    async def add_comment(self, issue_key: str, body: str) -> Dict[str, Any]:
        """Add a comment to an issue in Atlassian Document Format."""
        payload = {
            "body": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [
                            {
                                "type": "text",
                                "text": body
                            }
                        ]
                    }
                ]
            }
        }
        return await self._request("POST", f"/rest/api/3/issue/{issue_key}/comment", json_data=payload)

    async def update_priority(self, issue_key: str, priority_name: str) -> Dict[str, Any]:
        """Update issue priority."""
        payload = {
            "fields": {
                "priority": {"name": priority_name}
            }
        }
        return await self._request("PUT", f"/rest/api/3/issue/{issue_key}", json_data=payload)

    async def update_fields(self, issue_key: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        """Update arbitrary issue fields, formatting description to ADF when provided as plain text."""
        formatted_fields: Dict[str, Any] = {}
        for k, v in fields.items():
            if k == "description" and isinstance(v, str):
                formatted_fields["description"] = {
                    "type": "doc",
                    "version": 1,
                    "content": [{"type": "paragraph", "content": [{"type": "text", "text": v}]}]
                }
            elif k == "priority" and isinstance(v, str):
                formatted_fields["priority"] = {"name": v}
            elif k in ("assignee", "account_id") and isinstance(v, str):
                formatted_fields["assignee"] = {"accountId": v}
            else:
                formatted_fields[k] = v
        payload = {"fields": formatted_fields}
        return await self._request("PUT", f"/rest/api/3/issue/{issue_key}", json_data=payload)

    async def create_issue(
        self,
        project_key: str,
        summary: str,
        issue_type: str = "Task",
        description: Optional[str] = None,
        assignee: Optional[str] = None,
        priority: Optional[str] = None,
        labels: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Create a new Jira issue."""
        fields: Dict[str, Any] = {
            "project": {"key": project_key},
            "summary": summary,
            "issuetype": {"name": issue_type}
        }
        if description:
            fields["description"] = {
                "type": "doc",
                "version": 1,
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": description}]}]
            }
        if assignee:
            fields["assignee"] = {"accountId": assignee}
        if priority:
            fields["priority"] = {"name": priority}
        if labels:
            fields["labels"] = labels
        return await self._request("POST", "/rest/api/3/issue", json_data={"fields": fields})

    async def get_issue_worklogs(self, issue_key_or_id: str) -> List[Dict[str, Any]]:
        """Fetch all worklogs logged on a specific Jira issue."""
        res = await self._request("GET", f"/rest/api/3/issue/{issue_key_or_id}/worklog")
        if isinstance(res, dict):
            return res.get("worklogs", [])
        return []

    async def get_group_members(self, groupname: str, max_results: int = 50) -> List[Dict[str, Any]]:
        """Retrieve members of a Jira group."""
        params = {"groupname": groupname, "maxResults": max_results}
        try:
            res = await self._request("GET", "/rest/api/3/group/member", params=params)
            if isinstance(res, dict):
                return res.get("values", [])
            return []
        except Exception as e:
            logger.warning(f"Could not retrieve members for group '{groupname}': {e}")
            return []

