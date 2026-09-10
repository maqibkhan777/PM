"""Unit tests for JiraClient search_issues with Jira Cloud /rest/api/3/search/jql."""

import pytest
import httpx
from unittest.mock import AsyncMock, patch, MagicMock
from app.connectors.jira.client import JiraClient


@pytest.mark.asyncio
async def test_search_issues_initial_request():
    """Test search_issues queries /rest/api/3/search/jql without nextPageToken on first page."""
    client = JiraClient()
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.content = b'{"issues": [], "isLast": true}'
    mock_response.json.return_value = {"issues": [], "isLast": True}

    with patch.object(client, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {"issues": [], "isLast": True}

        res = await client.search_issues(jql='project = "PROJ"', max_results=25)

        assert res == {"issues": [], "isLast": True}
        mock_request.assert_called_once_with(
            "GET",
            "/rest/api/3/search/jql",
            params={
                "jql": 'project = "PROJ"',
                "maxResults": 25,
                "expand": "changelog",
                "fields": "*navigable,comment,worklog"
            }
        )


@pytest.mark.asyncio
async def test_search_issues_subsequent_page_with_token():
    """Test search_issues includes nextPageToken when provided."""
    client = JiraClient()
    with patch.object(client, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {"issues": [{"key": "P-2"}], "isLast": True}

        res = await client.search_issues(
            jql='project = "PROJ"',
            next_page_token="cursor-abc-123",
            max_results=50,
            expand="changelog,names"
        )

        assert len(res["issues"]) == 1
        mock_request.assert_called_once_with(
            "GET",
            "/rest/api/3/search/jql",
            params={
                "jql": 'project = "PROJ"',
                "maxResults": 50,
                "nextPageToken": "cursor-abc-123",
                "expand": "changelog,names",
                "fields": "*navigable,comment,worklog"
            }
        )


@pytest.mark.asyncio
async def test_search_issues_with_fields():
    """Test search_issues correctly passes fields as a comma-separated list."""
    client = JiraClient()
    with patch.object(client, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {"issues": []}

        await client.search_issues(
            jql="updated >= -1d",
            fields=["summary", "status", "assignee", "updated"]
        )

        mock_request.assert_called_once_with(
            "GET",
            "/rest/api/3/search/jql",
            params={
                "jql": "updated >= -1d",
                "maxResults": 50,
                "expand": "changelog",
                "fields": "summary,status,assignee,updated"
            }
        )


@pytest.mark.asyncio
async def test_search_issues_http_error_handling():
    """Test that JiraClient propagates HTTP errors (e.g. 410 Gone or 401 Unauthorized)."""
    client = JiraClient()

    mock_client = AsyncMock()
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 410
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "410 Gone", request=MagicMock(), response=mock_response
    )
    mock_client.request = AsyncMock(return_value=mock_response)

    with patch.object(client, "_get_client", return_value=mock_client):
        with pytest.raises(httpx.HTTPStatusError):
            await client.search_issues(jql="order by created DESC")
