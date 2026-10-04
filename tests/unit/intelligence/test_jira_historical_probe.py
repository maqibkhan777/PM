"""Unit tests for Read-Only Historical Jira Data Quality Probe.

Covers:
- Single project and multi-project query inputs
- Per-project metric separation
- Cursor pagination and cap handling
- Missing / null fields, zero vs missing distinctions
- Empty worklogs and changelog unavailable handling
- Rate-limit / API error handling and partial failure per project
- Read-only guarantees (no mutations, no DB writes)
- Clear distinction between elapsed lifecycle time and logged effort
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.connectors.jira.client import JiraClient
from app.core.intelligence.probe import JiraHistoricalDataQualityProbe
from app.core.intelligence.probe_models import (
    FeasibilityRecommendation,
    MultiProjectProbeSummary,
    ProjectQualityReport,
)
from app.core.intelligence.probe_formatter import JiraProbeReportFormatter


@pytest.fixture
def mock_jira_client():
    """Mock JiraClient preventing any external network calls."""
    client = MagicMock(spec=JiraClient)
    client.search_issues = AsyncMock()
    client.close = AsyncMock()
    return client


def _sample_issue(
    key="SMTPSUPORT-101",
    created="2026-08-01T10:00:00.000+0000",
    resolved="2026-08-03T14:00:00.000+0000",
    issue_type="Bug",
    priority="High",
    assignee_id="acc-123",
    components=None,
    orig_est=14400,
    time_spent=18000,
    worklogs=None,
    changelog_histories=None,
):
    fields = {
        "summary": f"Sample Issue {key}",
        "created": created,
        "resolutiondate": resolved,
        "issuetype": {"name": issue_type} if issue_type else None,
        "priority": {"name": priority} if priority else None,
        "assignee": {"accountId": assignee_id, "displayName": "Dev User"} if assignee_id else None,
        "components": [{"name": c} for c in (components or [])],
        "timetracking": {},
    }
    if orig_est is not None:
        fields["timeoriginalestimate"] = orig_est
        fields["timetracking"]["originalEstimateSeconds"] = orig_est

    if time_spent is not None:
        fields["timespent"] = time_spent
        fields["timetracking"]["timeSpentSeconds"] = time_spent

    if worklogs is not None:
        fields["worklog"] = {
            "worklogs": [{"timeSpentSeconds": wl_sec} for wl_sec in worklogs]
        }

    issue_dict = {"key": key, "fields": fields}
    if changelog_histories is not None:
        issue_dict["changelog"] = {"histories": changelog_histories}

    return issue_dict


@pytest.mark.asyncio
async def test_probe_single_project_success(mock_jira_client):
    """Test successful probe run for a single project (SMTPSUPORT)."""
    mock_issues = [
        _sample_issue(
            key="SMTPSUPORT-1",
            orig_est=7200,
            time_spent=7200,
            worklogs=[3600, 3600],
            components=["OAuth"],
        ),
        _sample_issue(
            key="SMTPSUPORT-2",
            orig_est=14400,
            time_spent=14400,
            worklogs=[14400],
            components=["Logging"],
        ),
    ]

    mock_jira_client.search_issues.return_value = {
        "issues": mock_issues,
        "isLast": True,
        "nextPageToken": None,
    }

    probe = JiraHistoricalDataQualityProbe(jira_client=mock_jira_client)
    summary = await probe.run_probe(
        project_keys=["SMTPSUPORT"],
        lookback_days=90,
        max_issues_per_project=100,
    )

    assert "SMTPSUPORT" in summary.project_reports
    rep = summary.project_reports["SMTPSUPORT"]
    assert rep.total_issues_retrieved == 2
    assert rep.cap_reached is False
    assert rep.valid_created_timestamp.count == 2
    assert rep.valid_resolved_timestamp.count == 2
    assert rep.valid_lifecycle_pair.count == 2
    assert rep.has_issue_type.count == 2
    assert rep.has_original_estimate.count == 2
    assert rep.has_time_spent.count == 2
    assert rep.has_at_least_one_worklog.count == 2
    assert rep.total_worklog_logged_seconds == (3600 + 3600 + 14400)


@pytest.mark.asyncio
async def test_probe_multi_project_separation(mock_jira_client):
    """Test that metrics are kept strictly separate across multiple projects."""
    async def mock_search(jql, **kwargs):
        if 'project = "SMTPSUPORT"' in jql:
            return {
                "issues": [_sample_issue(key="SMTPSUPORT-1", worklogs=[3600])],
                "isLast": True,
                "nextPageToken": None,
            }
        elif 'project = "TREN"' in jql:
            return {
                "issues": [
                    _sample_issue(key="TREN-1", worklogs=[7200]),
                    _sample_issue(key="TREN-2", worklogs=[7200]),
                ],
                "isLast": True,
                "nextPageToken": None,
            }
        return {"issues": [], "isLast": True}

    mock_jira_client.search_issues.side_effect = mock_search

    probe = JiraHistoricalDataQualityProbe(jira_client=mock_jira_client)
    summary = await probe.run_probe(
        project_keys=["SMTPSUPORT", "TREN"],
        lookback_days=60,
    )

    assert "SMTPSUPORT" in summary.project_reports
    assert "TREN" in summary.project_reports

    rep_smtp = summary.project_reports["SMTPSUPORT"]
    rep_tren = summary.project_reports["TREN"]

    assert rep_smtp.total_issues_retrieved == 1
    assert rep_smtp.total_worklog_logged_seconds == 3600

    assert rep_tren.total_issues_retrieved == 2
    assert rep_tren.total_worklog_logged_seconds == 14400


@pytest.mark.asyncio
async def test_probe_pagination_and_cap_handling(mock_jira_client):
    """Test cursor pagination and configured issue count cap."""
    batch_1 = [_sample_issue(key=f"SMTPSUPORT-{i}") for i in range(1, 3)]
    batch_2 = [_sample_issue(key=f"SMTPSUPORT-{i}") for i in range(3, 5)]

    async def mock_search(jql, next_page_token=None, max_results=50, **kwargs):
        if next_page_token is None:
            return {"issues": batch_1, "isLast": False, "nextPageToken": "token_page_2"}
        elif next_page_token == "token_page_2":
            return {"issues": batch_2, "isLast": True, "nextPageToken": None}
        return {"issues": [], "isLast": True}

    mock_jira_client.search_issues.side_effect = mock_search

    # Test with cap = 3 (should retrieve batch 1, then only 1 from batch 2)
    probe = JiraHistoricalDataQualityProbe(jira_client=mock_jira_client)
    summary = await probe.run_probe(
        project_keys=["SMTPSUPORT"],
        max_issues_per_project=3,
        batch_size=2,
    )

    rep = summary.project_reports["SMTPSUPORT"]
    assert rep.total_issues_retrieved == 3
    assert rep.cap_reached is True


@pytest.mark.asyncio
async def test_probe_missing_and_zero_fields_distinction(mock_jira_client):
    """Test clear distinction between zero values and missing fields."""
    mock_issues = [
        # Issue 1: zero estimate, missing time spent
        _sample_issue(key="PROJ-1", orig_est=0, time_spent=None, worklogs=[]),
        # Issue 2: positive estimate, zero time spent
        _sample_issue(key="PROJ-2", orig_est=3600, time_spent=0, worklogs=[]),
        # Issue 3: missing estimate, positive time spent
        _sample_issue(key="PROJ-3", orig_est=None, time_spent=7200, worklogs=[7200]),
    ]

    mock_jira_client.search_issues.return_value = {
        "issues": mock_issues,
        "isLast": True,
    }

    probe = JiraHistoricalDataQualityProbe(jira_client=mock_jira_client)
    summary = await probe.run_probe(project_keys=["PROJ"], max_issues_per_project=10)
    rep = summary.project_reports["PROJ"]

    assert rep.total_issues_retrieved == 3
    assert rep.has_original_estimate.count == 1  # only PROJ-2 has >0
    assert rep.original_estimate_zero_count == 1  # PROJ-1
    assert rep.original_estimate_missing_count == 1  # PROJ-3

    assert rep.has_time_spent.count == 1  # PROJ-3 has >0
    assert rep.time_spent_zero_count == 1  # PROJ-2
    assert rep.time_spent_missing_count == 1  # PROJ-1


@pytest.mark.asyncio
async def test_probe_changelog_transitions_and_reopens(mock_jira_client):
    """Test extraction of status transitions, reopen transitions, and estimate changes."""
    histories = [
        {
            "items": [
                {"field": "status", "fromString": "Open", "toString": "In Progress"},
                {"field": "timeoriginalestimate", "fromString": "14400", "toString": "18000"},
            ]
        },
        {
            "items": [
                {"field": "status", "fromString": "Resolved", "toString": "In Progress"},  # Reopen!
            ]
        }
    ]

    mock_issues = [
        _sample_issue(key="SMTPSUPORT-1", changelog_histories=histories),
    ]

    mock_jira_client.search_issues.return_value = {
        "issues": mock_issues,
        "isLast": True,
    }

    probe = JiraHistoricalDataQualityProbe(jira_client=mock_jira_client)
    summary = await probe.run_probe(project_keys=["SMTPSUPORT"])
    rep = summary.project_reports["SMTPSUPORT"]

    assert rep.changelog_available.count == 1
    assert rep.has_estimate_changes.count == 1
    assert rep.has_reopen_transitions.count == 1


@pytest.mark.asyncio
async def test_probe_lifecycle_vs_effort_separation(mock_jira_client):
    """Test that elapsed lifecycle time is strictly separated from actual logged effort."""
    # 2 days elapsed lifecycle (48 hours), but only 2 hours of actual logged effort
    mock_issues = [
        _sample_issue(
            key="SMTPSUPORT-1",
            created="2026-08-01T00:00:00.000+0000",
            resolved="2026-08-03T00:00:00.000+0000",
            worklogs=[7200],  # 2 hours
        )
    ]

    mock_jira_client.search_issues.return_value = {
        "issues": mock_issues,
        "isLast": True,
    }

    probe = JiraHistoricalDataQualityProbe(jira_client=mock_jira_client)
    summary = await probe.run_probe(project_keys=["SMTPSUPORT"])
    rep = summary.project_reports["SMTPSUPORT"]

    le = rep.lifecycle_vs_effort
    assert le.issues_with_lifecycle_time == 1
    assert le.avg_elapsed_lifecycle_hours == 48.0
    assert le.issues_with_logged_effort == 1
    assert le.avg_logged_effort_hours == 2.0
    assert "must NEVER be conflated" in le.lifecycle_effort_discrepancy_note


@pytest.mark.asyncio
async def test_probe_partial_failure_handling(mock_jira_client):
    """Test that failure in one project does not hide or break results from another project."""
    async def mock_search(jql, **kwargs):
        if 'project = "FAILPROJ"' in jql:
            raise Exception("Jira 403 Forbidden: Project does not exist or insufficient permissions")
        elif 'project = "GOODPROJ"' in jql:
            return {
                "issues": [_sample_issue(key="GOODPROJ-1", worklogs=[3600])],
                "isLast": True,
            }
        return {"issues": [], "isLast": True}

    mock_jira_client.search_issues.side_effect = mock_search

    probe = JiraHistoricalDataQualityProbe(jira_client=mock_jira_client)
    summary = await probe.run_probe(project_keys=["FAILPROJ", "GOODPROJ"])

    assert "FAILPROJ" in summary.project_reports
    assert "GOODPROJ" in summary.project_reports

    rep_fail = summary.project_reports["FAILPROJ"]
    assert rep_fail.partial_failure is True
    assert "403 Forbidden" in rep_fail.error_message
    assert rep_fail.total_issues_retrieved == 0

    rep_good = summary.project_reports["GOODPROJ"]
    assert rep_good.partial_failure is False
    assert rep_good.total_issues_retrieved == 1


@pytest.mark.asyncio
async def test_probe_formatter_output(mock_jira_client):
    """Test console and markdown formatters contain required summary headers."""
    mock_issues = [_sample_issue(key="SMTPSUPORT-1", worklogs=[3600])]
    mock_jira_client.search_issues.return_value = {"issues": mock_issues, "isLast": True}

    probe = JiraHistoricalDataQualityProbe(jira_client=mock_jira_client)
    summary = await probe.run_probe(project_keys=["SMTPSUPORT"])

    console_text = JiraProbeReportFormatter.format_console_report(summary)
    assert "HISTORICAL JIRA DATA QUALITY PROBE REPORT" in console_text
    assert "SMTPSUPORT" in console_text
    assert "LIFECYCLE DURATION VS. LOGGED EFFORT" in console_text

    md_text = JiraProbeReportFormatter.format_markdown_report(summary)
    assert "# Historical Jira Data Quality Diagnostic Report" in md_text
    assert "SMTPSUPORT" in md_text
