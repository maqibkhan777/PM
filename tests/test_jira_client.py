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


@pytest.mark.asyncio
async def test_jira_client_429_respects_retry_after():
    """Test that JiraClient parses Retry-After header and sleeps accordingly on HTTP 429."""
    client = JiraClient()

    mock_client = AsyncMock()

    # Attempt 1: 429 with Retry-After: 4
    resp_429 = MagicMock(spec=httpx.Response)
    resp_429.status_code = 429
    resp_429.headers = {"Retry-After": "4"}
    resp_429.content = b'{"errorMessages":["Rate limit exceeded"]}'

    # Attempt 2: 200 OK
    resp_200 = MagicMock(spec=httpx.Response)
    resp_200.status_code = 200
    resp_200.content = b'{"accountId": "acc-123", "displayName": "Aqib"}'
    resp_200.json.return_value = {"accountId": "acc-123", "displayName": "Aqib"}

    mock_client.request = AsyncMock(side_effect=[resp_429, resp_200])

    with patch.object(client, "_get_client", return_value=mock_client):
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            res = await client.get_myself(force_refresh=True)

            assert res["accountId"] == "acc-123"
            assert mock_client.request.call_count == 2
            mock_sleep.assert_awaited_once()
            slept_duration = mock_sleep.call_args[0][0]
            # Should be Retry-After (4.0) + jitter (0.1 to 0.5)
            assert 4.0 <= slept_duration <= 4.6


@pytest.mark.asyncio
async def test_jira_client_get_myself_caching():
    """Test that get_myself caches user profile with TTL and force_refresh bypasses cache."""
    client = JiraClient()

    with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = {"accountId": "user-999", "displayName": "Test User"}

        # First call: makes HTTP request
        user1 = await client.get_myself()
        assert user1["accountId"] == "user-999"
        assert mock_req.call_count == 1

        # Second call: returns cached result without making HTTP request
        user2 = await client.get_myself()
        assert user2["accountId"] == "user-999"
        assert mock_req.call_count == 1

        # Third call with force_refresh: makes new HTTP request
        user3 = await client.get_myself(force_refresh=True)
        assert user3["accountId"] == "user-999"
        assert mock_req.call_count == 2

