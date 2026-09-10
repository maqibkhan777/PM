"""Unit tests for JiraPoller change detection, checkpointing, and deduplication."""

import pytest
from unittest.mock import AsyncMock, patch
from datetime import datetime, timezone, timedelta
from app.config.settings import Settings
from app.connectors.jira.poller import JiraPoller
from app.connectors.jira.client import JiraClient
from app.database.repositories import JiraPollingStateRepository, JiraIssueStateRepository, EventRepository
from app.utils.time import format_iso, utc_now_iso


@pytest.fixture
def mock_jira_client():
    client = JiraClient()
    client.search_issues = AsyncMock()
    return client


@pytest.mark.asyncio
async def test_polling_skipped_when_unconfigured(temp_db, mock_jira_client):
    """Test poller skips cleanly when Jira is not configured."""
    unconfigured_settings = Settings(
        JIRA_BASE_URL="https://your-domain.atlassian.net",
        JIRA_EMAIL="pm-agent@your-domain.com",
        JIRA_API_TOKEN="placeholder_token"
    )
    with patch("app.connectors.jira.poller.settings", unconfigured_settings):
        poller = JiraPoller(client=mock_jira_client, manager=temp_db)
        result = await poller.poll()
        assert result["status"] == "skipped"
        assert result["reason"] == "jira_not_configured"
        mock_jira_client.search_issues.assert_not_called()


@pytest.mark.asyncio
async def test_polling_empty_results(temp_db, mock_jira_client):
    """Test poller advances checkpoint even when 0 issues found."""
    configured_settings = Settings(
        JIRA_BASE_URL="https://mycompany.atlassian.net",
        JIRA_EMAIL="pm@mycompany.com",
        JIRA_API_TOKEN="valid-token-123",
        JIRA_POLLING_LOOKBACK_MINUTES=5,
        JIRA_TEAM_GROUP="Engineering Team"
    )
    mock_jira_client.search_issues.return_value = {"issues": [], "total": 0}

    with patch("app.connectors.jira.poller.settings", configured_settings):
        poller = JiraPoller(client=mock_jira_client, manager=temp_db)
        result = await poller.poll()

        assert result["status"] == "completed"
        assert result["issues_scanned"] == 0
        assert result["events_generated"] == 0

        # Checkpoint was recorded
        checkpoint = poller.polling_state_repo.get_checkpoint("jira")
        assert checkpoint is not None


@pytest.mark.asyncio
async def test_polling_failure_does_not_advance_checkpoint(temp_db, mock_jira_client):
    """Test that Jira API failures do NOT advance the polling checkpoint."""
    configured_settings = Settings(
        JIRA_BASE_URL="https://mycompany.atlassian.net",
        JIRA_EMAIL="pm@mycompany.com",
        JIRA_API_TOKEN="valid-token-123",
        JIRA_TEAM_GROUP="Engineering Team"
    )
    mock_jira_client.search_issues.side_effect = RuntimeError("Jira 503 Service Unavailable")

    with patch("app.connectors.jira.poller.settings", configured_settings):
        poller = JiraPoller(client=mock_jira_client, manager=temp_db)
        # Seed an old checkpoint
        old_checkpoint = "2026-09-08T10:00:00+00:00"
        poller.polling_state_repo.update_checkpoint("jira", old_checkpoint)

        result = await poller.poll()

        assert result["status"] == "failed"
        assert "503" in result["error"]
        # Checkpoint remains unchanged!
        current_checkpoint = poller.polling_state_repo.get_checkpoint("jira")
        assert current_checkpoint == old_checkpoint


@pytest.mark.asyncio
async def test_polling_pagination(temp_db, mock_jira_client):
    """Test pagination handling across multiple pages."""
    configured_settings = Settings(
        JIRA_BASE_URL="https://mycompany.atlassian.net",
        JIRA_EMAIL="pm@mycompany.com",
        JIRA_API_TOKEN="valid-token-123",
        JIRA_POLLING_BATCH_SIZE=1,
        JIRA_TEAM_GROUP="Engineering Team"
    )

    page1 = {
        "issues": [{"key": "PAG-1", "fields": {"summary": "Issue 1", "status": {"name": "To Do"}}}],
        "nextPageToken": "cursor-token-page-2",
        "isLast": False
    }
    page2 = {
        "issues": [{"key": "PAG-2", "fields": {"summary": "Issue 2", "status": {"name": "In Progress"}}}],
        "isLast": True
    }
    mock_jira_client.search_issues.side_effect = [page1, page2]

    with patch("app.connectors.jira.poller.settings", configured_settings), \
         patch("app.services.orchestrator.orchestrator.ingest_polled_event", new_callable=AsyncMock):
        poller = JiraPoller(client=mock_jira_client, manager=temp_db)
        result = await poller.poll()

        assert result["status"] == "completed"
        assert result["issues_scanned"] == 2
        assert mock_jira_client.search_issues.call_count == 2
        # Verify first call had no token and second call used nextPageToken
        first_call = mock_jira_client.search_issues.call_args_list[0]
        second_call = mock_jira_client.search_issues.call_args_list[1]
        assert first_call.kwargs.get("next_page_token") is None
        assert second_call.kwargs.get("next_page_token") == "cursor-token-page-2"


@pytest.mark.asyncio
async def test_change_detection_new_issue(temp_db, mock_jira_client):
    """Test change detection identifies newly created issues."""
    now_iso = utc_now_iso()
    configured_settings = Settings(
        JIRA_BASE_URL="https://mycompany.atlassian.net",
        JIRA_EMAIL="pm@mycompany.com",
        JIRA_API_TOKEN="valid-token-123",
        JIRA_TEAM_GROUP="Engineering Team"
    )
    issue_payload = {
        "id": "1001",
        "key": "NEW-1",
        "fields": {
            "summary": "Implement Auth",
            "created": now_iso,
            "updated": now_iso,
            "status": {"name": "To Do"},
            "priority": {"name": "High"},
            "assignee": {"displayName": "John Dev", "accountId": "acc-1"},
            "project": {"key": "NEW", "id": "proj-1"}
        }
    }
    mock_jira_client.search_issues.return_value = {"issues": [issue_payload], "total": 1}

    ingested_events = []
    async def mock_ingest(ev):
        ingested_events.append(ev)
        return "event-id-1"

    with patch("app.connectors.jira.poller.settings", configured_settings), \
         patch("app.services.orchestrator.orchestrator.ingest_polled_event", side_effect=mock_ingest):
        poller = JiraPoller(client=mock_jira_client, manager=temp_db)
        result = await poller.poll()

        assert result["events_generated"] == 1
        assert len(ingested_events) == 1
        ev = ingested_events[0]
        assert ev.event_type == "TaskCreated"
        assert ev.task_key == "NEW-1"
        assert ev.external_event_id == "jira:NEW-1:created"


@pytest.mark.asyncio
async def test_change_detection_changelog_status_change(temp_db, mock_jira_client):
    """Test changelog inspection generates TaskStatusChanged with deterministic ID."""
    now_iso = utc_now_iso()
    configured_settings = Settings(
        JIRA_BASE_URL="https://mycompany.atlassian.net",
        JIRA_EMAIL="pm@mycompany.com",
        JIRA_API_TOKEN="valid-token-123",
        JIRA_TEAM_GROUP="Engineering Team"
    )
    issue_payload = {
        "id": "1002",
        "key": "ST-1",
        "fields": {
            "summary": "Status Testing",
            "status": {"name": "In Progress"},
            "updated": now_iso,
            "project": {"key": "ST"}
        },
        "changelog": {
            "histories": [
                {
                    "id": "change-888",
                    "created": now_iso,
                    "author": {"displayName": "Tester", "accountId": "acc-t"},
                    "items": [
                        {
                            "field": "status",
                            "fromString": "To Do",
                            "toString": "In Progress"
                        }
                    ]
                }
            ]
        }
    }
    mock_jira_client.search_issues.return_value = {"issues": [issue_payload], "total": 1}

    ingested_events = []
    async def mock_ingest(ev):
        ingested_events.append(ev)
        return "event-id-2"

    with patch("app.connectors.jira.poller.settings", configured_settings), \
         patch("app.services.orchestrator.orchestrator.ingest_polled_event", side_effect=mock_ingest):
        poller = JiraPoller(client=mock_jira_client, manager=temp_db)
        result = await poller.poll()

        assert result["events_generated"] == 1
        ev = ingested_events[0]
        assert ev.event_type == "TaskStatusChanged"
        assert ev.old_status == "To Do"
        assert ev.new_status == "In Progress"
        assert ev.external_event_id == "jira:ST-1:status:change-888"


@pytest.mark.asyncio
async def test_deterministic_comment_and_worklog_detection(temp_db, mock_jira_client):
    """Test that comments and worklogs are only generated with deterministic IDs."""
    now_iso = utc_now_iso()
    configured_settings = Settings(
        JIRA_BASE_URL="https://mycompany.atlassian.net",
        JIRA_EMAIL="pm@mycompany.com",
        JIRA_API_TOKEN="valid-token-123",
        JIRA_TEAM_GROUP="Engineering Team"
    )
    issue_payload = {
        "id": "1003",
        "key": "CW-1",
        "fields": {
            "summary": "Comment & Worklog Test",
            "status": {"name": "In Progress"},
            "updated": now_iso,
            "project": {"key": "CW"},
            "comment": {
                "comments": [
                    {
                        "id": "comment-555",
                        "created": now_iso,
                        "author": {"displayName": "Dev Guy", "accountId": "acc-dev"},
                        "body": "Fixed the bug in module A"
                    }
                ]
            },
            "worklog": {
                "worklogs": [
                    {
                        "id": "worklog-777",
                        "created": now_iso,
                        "author": {"displayName": "Dev Guy", "accountId": "acc-dev"},
                        "timeSpentSeconds": 3600,
                        "timeSpent": "1h"
                    }
                ]
            }
        }
    }
    mock_jira_client.search_issues.return_value = {"issues": [issue_payload], "total": 1}

    ingested_events = []
    async def mock_ingest(ev):
        ingested_events.append(ev)
        return "event-id-3"

    with patch("app.connectors.jira.poller.settings", configured_settings), \
         patch("app.services.orchestrator.orchestrator.ingest_polled_event", side_effect=mock_ingest):
        poller = JiraPoller(client=mock_jira_client, manager=temp_db)
        result = await poller.poll()

        assert result["events_generated"] == 2
        comment_ev = next(e for e in ingested_events if e.event_type == "TaskCommentAdded")
        assert comment_ev.external_event_id == "jira:CW-1:comment:comment-555"
        assert comment_ev.comment_body == "Fixed the bug in module A"

        worklog_ev = next(e for e in ingested_events if e.event_type == "TaskWorklogged")
        assert worklog_ev.external_event_id == "jira:CW-1:worklog:worklog-777"
        assert worklog_ev.time_spent_seconds == 3600


@pytest.mark.asyncio
async def test_duplicate_event_skipping_in_overlapping_window(temp_db, mock_jira_client):
    """Test that overlapping lookback window does not generate duplicate events."""
    now_iso = utc_now_iso()
    configured_settings = Settings(
        JIRA_BASE_URL="https://mycompany.atlassian.net",
        JIRA_EMAIL="pm@mycompany.com",
        JIRA_API_TOKEN="valid-token-123",
        JIRA_TEAM_GROUP="Engineering Team"
    )
    issue_payload = {
        "id": "1004",
        "key": "DUP-1",
        "fields": {
            "summary": "Duplicate Test",
            "status": {"name": "In Progress"},
            "updated": now_iso,
            "project": {"key": "DUP"},
            "comment": {
                "comments": [
                    {"id": "comment-999", "created": now_iso, "body": "Already ingested"}
                ]
            }
        }
    }
    mock_jira_client.search_issues.return_value = {"issues": [issue_payload], "total": 1}

    # Pre-seed event repository with this external event ID
    event_repo = EventRepository(temp_db)
    event_repo.insert(
        event_type="TaskCommentAdded",
        source="jira",
        external_event_id="jira:DUP-1:comment:comment-999",
        timestamp=now_iso,
        payload=issue_payload
    )

    with patch("app.connectors.jira.poller.settings", configured_settings):
        poller = JiraPoller(client=mock_jira_client, manager=temp_db)
        result = await poller.poll()

        assert result["issues_scanned"] == 1
        assert result["events_generated"] == 0
        assert result["duplicates_skipped"] == 1
