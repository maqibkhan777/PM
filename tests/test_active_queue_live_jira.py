"""Unit and integration tests for Phase 2: Live Jira Saved-Filter Retrieval for Active Queue."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
from app.config.settings import settings
from app.database.schema import init_db
from app.database.repositories import EmployeeRoleRepository, JiraIssueStateRepository
from app.core.reports.queue_report import ResourceQueueReportGenerator


@pytest.fixture
def mock_settings(monkeypatch):
    """Ensure standard settings for testing."""
    monkeypatch.setattr(settings, "JIRA_BASE_URL", "https://example.atlassian.net")
    monkeypatch.setattr(settings, "JIRA_EMAIL", "test@example.com")
    monkeypatch.setattr(settings, "JIRA_API_TOKEN", "fake_token")
    monkeypatch.setattr(settings, "JIRA_TEAM_GROUP", "Mursaleen Cluster")
    monkeypatch.setattr(settings, "DAILY_WORKLOG_EXCLUDED_ACCOUNT_IDS", "excluded-user-123")


def _sample_jira_issue(key: str, summary: str, status: str = "In Progress", duedate: str = "2026-09-20"):
    return {
        "key": key,
        "fields": {
            "summary": summary,
            "status": {"name": status},
            "priority": {"name": "High"},
            "duedate": duedate,
            "updated": "2026-09-15T12:00:00.000+0000",
            "assignee": {"accountId": "712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de", "displayName": "Ahsan Amin"},
            "project": {"key": "PROJ"},
            "issuetype": {"name": "Task"},
        },
    }


def test_saved_filter_present_queries_exact_filter_id(temp_db, mock_settings):
    """Test that a resource with a mapped filter ID queries 'filter = <id>' and returns live tickets."""
    init_db(temp_db)
    gen = ResourceQueueReportGenerator(manager=temp_db)

    captured_urls = []
    captured_params = []

    def mock_get(url, params=None, **kwargs):
        captured_urls.append(url)
        captured_params.append(params)
        return MagicMock(
            status_code=200,
            json=lambda: {
                "issues": [
                    _sample_jira_issue("PROJ-101", "Fix checkout button", "In Progress", "2026-09-22"),
                    _sample_jira_issue("PROJ-102", "Optimize page speed", "Code Review", "2026-09-23"),
                ],
                "isLast": True,
            },
            raise_for_status=lambda: None,
        )

    with patch("httpx.Client.get", side_effect=mock_get):
        report = gen.generate_user_queue_report(
            account_id="712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de",
            display_name="Ahsan Amin",
        )

    assert report["filter_id"] == "15370"
    assert report["jql"] == "filter = 15370"
    assert report["source"] == "jira_live"
    assert report["cache_fallback"] is False
    assert report["active_count"] == 2
    assert len(report["tickets"]) == 2
    assert report["tickets"][0]["key"] == "PROJ-101"
    assert report["tickets"][0]["summary"] == "Fix checkout button"
    assert report["tickets"][0]["due_date"] == "2026-09-22"
    assert captured_params[0]["jql"] == "filter = 15370"

    # Verify SQLite cache was warmed
    issue_repo = JiraIssueStateRepository(temp_db)
    cached = issue_repo.get("PROJ-101")
    assert cached is not None
    assert cached["summary"] == "Fix checkout button"


def test_saved_filter_returns_more_than_sqlite_count(temp_db, mock_settings):
    """Verify live Jira query can return a larger set of tickets than previously cached in SQLite."""
    init_db(temp_db)
    gen = ResourceQueueReportGenerator(manager=temp_db)

    # Seed 1 old issue in SQLite
    issue_repo = JiraIssueStateRepository(temp_db)
    issue_repo.upsert(
        jira_issue_key="PROJ-1",
        summary="Old cached issue",
        status="In Progress",
        assignee="Ahsan Amin",
        team_group="Mursaleen Cluster",
    )

    # Jira returns 5 live tickets
    jira_issues = [_sample_jira_issue(f"PROJ-{i}", f"Task {i}") for i in range(1, 6)]

    with patch("httpx.Client.get", return_value=MagicMock(
        status_code=200,
        json=lambda: {"issues": jira_issues, "isLast": True},
        raise_for_status=lambda: None,
    )):
        report = gen.generate_user_queue_report(
            account_id="712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de",
            display_name="Ahsan Amin",
        )

    assert report["active_count"] == 5
    assert len(report["tickets"]) == 5
    assert report["source"] == "jira_live"


def test_no_saved_filter_falls_back_to_canonical_jql(temp_db, mock_settings):
    """Verify Mubashir Butt (no saved filter) uses canonical fallback behavior."""
    init_db(temp_db)
    gen = ResourceQueueReportGenerator(manager=temp_db)

    # Seed issue in SQLite for Mubashir Butt
    issue_repo = JiraIssueStateRepository(temp_db)
    issue_repo.upsert(
        jira_issue_key="SUPP-10",
        summary="Customer ticket",
        status="Open",
        assignee="Mubashir Butt",
        team_group="Mursaleen Cluster",
        raw_reference={"assignee": {"accountId": "712020:e268bcd8-d981-4b4d-992d-d5694745df8b", "displayName": "Mubashir Butt"}},
    )

    report = gen.generate_user_queue_report(
        account_id="712020:e268bcd8-d981-4b4d-992d-d5694745df8b",
        display_name="Mubashir Butt",
    )

    assert report["filter_id"] is None
    assert "assignee" in report["jql"]
    assert report["source"] == "sqlite_cache"
    assert report["cache_fallback"] is True
    assert report["active_count"] == 1
    assert report["tickets"][0]["key"] == "SUPP-10"


def test_jira_failure_falls_back_to_sqlite_cache(temp_db, mock_settings):
    """Verify network or HTTP failure in Jira safely falls back to local SQLite cache."""
    init_db(temp_db)
    gen = ResourceQueueReportGenerator(manager=temp_db)

    # Seed an issue in SQLite
    issue_repo = JiraIssueStateRepository(temp_db)
    issue_repo.upsert(
        jira_issue_key="FALLBACK-101",
        summary="Cached fallback task",
        status="Doing",
        assignee="Daniyal Raza",
        due_date="2026-09-25",
        team_group="Mursaleen Cluster",
    )

    # Simulate network failure on Jira HTTP call
    with patch("httpx.Client.get", side_effect=httpx.ConnectError("Connection refused")):
        report = gen.generate_user_queue_report(
            account_id="63e362bd790148a180977179",
            display_name="Daniyal Raza",
        )

    assert report["source"] == "sqlite_cache"
    assert report["cache_fallback"] is True
    assert report["active_count"] == 1
    assert report["tickets"][0]["key"] == "FALLBACK-101"
    assert report["tickets"][0]["summary"] == "Cached fallback task"


def test_jira_empty_result_does_not_fall_back_to_stale_cache(temp_db, mock_settings):
    """Verify that a successful empty result from Jira returns 0 tickets and does NOT use stale cache."""
    init_db(temp_db)
    gen = ResourceQueueReportGenerator(manager=temp_db)

    # Seed an old issue in SQLite
    issue_repo = JiraIssueStateRepository(temp_db)
    issue_repo.upsert(
        jira_issue_key="STALE-1",
        summary="Stale completed task in SQLite",
        status="In Progress",
        assignee="Ahsan Amin",
        team_group="Mursaleen Cluster",
    )

    # Jira returns empty list (all tickets finished)
    with patch("httpx.Client.get", return_value=MagicMock(
        status_code=200,
        json=lambda: {"issues": [], "isLast": True},
        raise_for_status=lambda: None,
    )):
        report = gen.generate_user_queue_report(
            account_id="712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de",
            display_name="Ahsan Amin",
        )

    assert report["source"] == "jira_live"
    assert report["cache_fallback"] is False
    assert report["active_count"] == 0
    assert report["tickets"] == []


def test_account_resource_filter_isolation(temp_db, mock_settings):
    """Verify each resource receives their exact mapped filter and cannot receive another resource's filter."""
    init_db(temp_db)
    gen = ResourceQueueReportGenerator(manager=temp_db)

    with patch("httpx.Client.get", return_value=MagicMock(
        status_code=200,
        json=lambda: {"issues": [], "isLast": True},
        raise_for_status=lambda: None,
    )):
        # Daniyal Raza -> 16826
        rep_daniyal = gen.generate_user_queue_report("63e362bd790148a180977179", "Daniyal Raza")
        assert rep_daniyal["filter_id"] == "16826"
        assert rep_daniyal["jql"] == "filter = 16826"

        # Usman -> 17124
        rep_usman = gen.generate_user_queue_report("5f83e3937d9637006ffd0436", "Usman")
        assert rep_usman["filter_id"] == "17124"
        assert rep_usman["jql"] == "filter = 17124"

        # Tahir Ali -> 16828
        rep_tahir = gen.generate_user_queue_report("638855b85fce844d606bb422", "Tahir Ali")
        assert rep_tahir["filter_id"] == "16828"
        assert rep_tahir["jql"] == "filter = 16828"


def test_canonical_exclusion_blocks_queue_report(temp_db, mock_settings):
    """Verify canonical excluded accounts immediately return excluded without calling Jira."""
    init_db(temp_db)
    gen = ResourceQueueReportGenerator(manager=temp_db)

    with patch("httpx.Client.get") as mock_get:
        report = gen.generate_user_queue_report(
            account_id="excluded-user-123",
            display_name="Excluded User",
        )
        assert mock_get.call_count == 0

    assert report["is_excluded"] is True
    assert report["active_count"] == 0
    assert report["tickets"] == []
    assert report["source"] == "excluded"


def test_cursor_pagination_fetches_all_pages(temp_db, mock_settings):
    """Verify multi-page cursor pagination traverses all pages until isLast=True."""
    init_db(temp_db)
    gen = ResourceQueueReportGenerator(manager=temp_db)

    page1 = {
        "issues": [_sample_jira_issue("PAGE-1", "Task 1"), _sample_jira_issue("PAGE-2", "Task 2")],
        "nextPageToken": "token_cursor_page_2",
        "isLast": False,
    }
    page2 = {
        "issues": [_sample_jira_issue("PAGE-3", "Task 3"), _sample_jira_issue("PAGE-4", "Task 4")],
        "nextPageToken": None,
        "isLast": True,
    }

    call_count = 0

    def mock_paginated_get(url, params=None, **kwargs):
        nonlocal call_count
        call_count += 1
        if params.get("nextPageToken") == "token_cursor_page_2":
            return MagicMock(status_code=200, json=lambda: page2, raise_for_status=lambda: None)
        return MagicMock(status_code=200, json=lambda: page1, raise_for_status=lambda: None)

    with patch("httpx.Client.get", side_effect=mock_paginated_get):
        report = gen.generate_user_queue_report(
            account_id="712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de",
            display_name="Ahsan Amin",
        )

    assert call_count == 2
    assert report["active_count"] == 4
    keys = [t["key"] for t in report["tickets"]]
    assert keys == ["PAGE-1", "PAGE-2", "PAGE-3", "PAGE-4"]


@pytest.mark.asyncio
async def test_async_queue_report_generation(temp_db, mock_settings):
    """Verify generate_user_queue_report_async works seamlessly with JiraClient async search."""
    init_db(temp_db)
    mock_jira_client = MagicMock()
    mock_jira_client.search_issues = AsyncMock(return_value={
        "issues": [_sample_jira_issue("ASYNC-1", "Async task")],
        "isLast": True,
    })

    gen = ResourceQueueReportGenerator(manager=temp_db, jira_client=mock_jira_client)
    report = await gen.generate_user_queue_report_async(
        account_id="712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de",
        display_name="Ahsan Amin",
    )

    assert report["source"] == "jira_live"
    assert report["active_count"] == 1
    assert report["tickets"][0]["key"] == "ASYNC-1"
    mock_jira_client.search_issues.assert_called_once()
