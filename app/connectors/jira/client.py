"""Jira Cloud REST API HTTP client with timeouts, authentication, and retry handling."""

import asyncio
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
                    logger.error(f"Jira authentication failed: HTTP {response.status_code} for {method} {url}")
                    response.raise_for_status()

                # Rate limiting or server errors - retry with exponential backoff
                if response.status_code in (429, 500, 502, 503, 504):
                    if attempt < max_retries:
                        sleep_time = backoff ** attempt
                        logger.warning(f"Jira API transient error {response.status_code}. Retrying in {sleep_time:.1f}s (attempt {attempt}/{max_retries})")
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
                    sleep_time = backoff ** attempt
                    logger.warning(f"Jira API timeout on {method} {url}. Retrying in {sleep_time:.1f}s (attempt {attempt}/{max_retries})")
                    await asyncio.sleep(sleep_time)
                else:
                    logger.error(f"Jira API request timed out after {max_retries} attempts: {e}")
                    raise
            except httpx.RequestError as e:
                if attempt < max_retries:
                    sleep_time = backoff ** attempt
                    logger.warning(f"Jira connection error {e}. Retrying in {sleep_time:.1f}s (attempt {attempt}/{max_retries})")
                    await asyncio.sleep(sleep_time)
                else:
                    logger.error(f"Jira API connection failed after {max_retries} attempts: {e}")
                    raise

        raise RuntimeError("Unexpected end of request retry loop")

    # --------------------------------------------------------------------------
    # API Methods
    # --------------------------------------------------------------------------

    async def get_myself(self) -> Dict[str, Any]:
        """Fetch current authenticated user profile for health check."""
        return await self._request("GET", "/rest/api/3/myself")

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
        start_at: int = 0,
        max_results: int = 50,
        expand: Optional[str] = "changelog",
        fields: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Search issues using JQL with pagination and changelog expansion."""
        params: Dict[str, Any] = {
            "jql": jql,
            "startAt": start_at,
            "maxResults": max_results
        }
        if expand:
            params["expand"] = expand
        if fields:
            params["fields"] = ",".join(fields)
        return await self._request("GET", "/rest/api/3/search", params=params)

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
        """Update arbitrary issue fields."""
        payload = {"fields": fields}
        return await self._request("PUT", f"/rest/api/3/issue/{issue_key}", json_data=payload)

    async def create_issue(
        self,
        project_key: str,
        summary: str,
        issue_type: str = "Task",
        description: Optional[str] = None
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
        return await self._request("POST", "/rest/api/3/issue", json_data={"fields": fields})
