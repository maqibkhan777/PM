"""Unit tests for Empirical Historical Effort Benchmarking Engine (Milestone 2).

Covers:
- Support for multiple Jira projects (SMTPSUPORT, GF, etc.)
- Strict cross-project metric isolation (no cross-contamination)
- Statistical quantiles calculation (P25, P50, P75, P90, Mean, StdDev)
- Reliability threshold validation (<5: INSUFFICIENT_DATA, 5-9: LOW_CONFIDENCE, 10+: USABLE)
- Missing effort counts and data quality warnings
- Persistence to local SQLite benchmark tables
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock
import pytest

from app.connectors.jira.client import JiraClient
from app.core.intelligence.effort_benchmark_models import (
    BenchmarkReliability,
    MultiProjectBenchmarkSummary,
    ProjectEffortBenchmarkReport,
)
from app.core.intelligence.effort_benchmarking import (
    EmpiricalHistoricalEffortBenchmarkingEngine,
    calculate_quantiles_and_stats,
)
from app.core.intelligence.effort_benchmark_formatter import EffortBenchmarkReportFormatter
from app.database.connection import DatabaseManager


@pytest.fixture
def mock_jira_client():
    client = MagicMock(spec=JiraClient)
    client.search_issues = AsyncMock()
    client.close = AsyncMock()
    return client


@pytest.fixture
def mock_db_mgr(tmp_path):
    db_file = str(tmp_path / "test_bench.db")
    mgr = DatabaseManager(db_path=db_file)
    with mgr.session() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS historical_effort_benchmarks (
            id TEXT PRIMARY KEY,
            analysis_run_id TEXT NOT NULL,
            account_id TEXT,
            segmentation_tier TEXT NOT NULL,
            segment_type TEXT NOT NULL,
            segment_key TEXT NOT NULL,
            sample_count INTEGER NOT NULL DEFAULT 0,
            mean_hours REAL NOT NULL DEFAULT 0.0,
            median_hours REAL NOT NULL DEFAULT 0.0,
            p25_hours REAL NOT NULL DEFAULT 0.0,
            p75_hours REAL NOT NULL DEFAULT 0.0,
            min_hours REAL NOT NULL DEFAULT 0.0,
            max_hours REAL NOT NULL DEFAULT 0.0,
            stddev_hours REAL NOT NULL DEFAULT 0.0,
            confidence TEXT NOT NULL DEFAULT 'LOW',
            is_fallback INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """)
    return mgr


def _mock_issue(key, issue_type="Bug", priority="High", worklogs=None, time_spent=None):
    fields = {
        "summary": f"Task {key}",
        "issuetype": {"name": issue_type},
        "priority": {"name": priority},
        "labels": ["mailer"],
        "timetracking": {},
    }
    if worklogs is not None:
        fields["worklog"] = {
            "worklogs": [{"timeSpentSeconds": s} for s in worklogs]
        }
    if time_spent is not None:
        fields["timespent"] = time_spent
    return {"key": key, "fields": fields}


def test_calculate_quantiles_and_stats_thresholds():
    """Test quantiles calculation respecting sample size thresholds."""
    # < 5 samples: no P25/P75/P90
    small_dist = calculate_quantiles_and_stats([2.0, 4.0, 6.0])
    assert small_dist.sample_count == 3
    assert small_dist.mean_hours == 4.0
    assert small_dist.median_hours == 4.0
    assert small_dist.p25_hours is None
    assert small_dist.p75_hours is None
    assert small_dist.p90_hours is None

    # 5-9 samples: P25 and P75 calculated, P90 None
    med_dist = calculate_quantiles_and_stats([1.0, 2.0, 3.0, 4.0, 5.0])
    assert med_dist.sample_count == 5
    assert med_dist.median_hours == 3.0
    assert med_dist.p25_hours is not None
    assert med_dist.p75_hours is not None
    assert med_dist.p90_hours is None

    # 10+ samples: full P25, P50, P75, P90 calculated
    large_vals = [float(i) for i in range(1, 11)]  # 1.0 to 10.0
    large_dist = calculate_quantiles_and_stats(large_vals)
    assert large_dist.sample_count == 10
    assert large_dist.p25_hours is not None
    assert large_dist.median_hours == 5.5
    assert large_dist.p75_hours is not None
    assert large_dist.p90_hours is not None


@pytest.mark.asyncio
async def test_multi_project_effort_benchmarking(mock_jira_client, mock_db_mgr):
    """Test that SMTPSUPORT and GF benchmarks are computed with complete isolation."""
    smtp_issues = [
        _mock_issue(f"SMTPSUPORT-{i}", issue_type="Bug", priority="High", worklogs=[7200]) # 2h each
        for i in range(1, 12)  # 11 issues (USABLE)
    ]
    gf_issues = [
        _mock_issue(f"GF-{i}", issue_type="Feature", priority="Medium", worklogs=[14400]) # 4h each
        for i in range(1, 7)   # 6 issues (LOW_CONFIDENCE)
    ] + [
        _mock_issue("GF-7", issue_type="Feature", priority="Medium", worklogs=[]) # missing effort
    ]

    async def mock_search(jql, **kwargs):
        if 'project = "SMTPSUPORT"' in jql:
            return {"issues": smtp_issues, "isLast": True}
        elif 'project = "GF"' in jql:
            return {"issues": gf_issues, "isLast": True}
        return {"issues": [], "isLast": True}

    mock_jira_client.search_issues.side_effect = mock_search

    engine = EmpiricalHistoricalEffortBenchmarkingEngine(
        jira_client=mock_jira_client,
        db_mgr=mock_db_mgr,
    )

    summary = await engine.compute_multi_project_benchmarks(
        project_keys=["SMTPSUPORT", "GF"],
        lookback_days=90,
        persist=True,
    )

    assert "SMTPSUPORT" in summary.reports_by_project
    assert "GF" in summary.reports_by_project

    rep_smtp = summary.reports_by_project["SMTPSUPORT"]
    rep_gf = summary.reports_by_project["GF"]

    # SMTPSUPORT verification
    assert rep_smtp.total_completed_issues == 11
    assert rep_smtp.issues_with_logged_effort == 11
    assert rep_smtp.issues_missing_effort == 0
    assert rep_smtp.overall_benchmark.distribution.median_hours == 2.0
    assert rep_smtp.overall_benchmark.reliability == BenchmarkReliability.USABLE
    assert "Bug" in rep_smtp.by_issue_type
    assert rep_smtp.by_issue_type["Bug"].reliability == BenchmarkReliability.USABLE

    # GF verification
    assert rep_gf.total_completed_issues == 7
    assert rep_gf.issues_with_logged_effort == 6
    assert rep_gf.issues_missing_effort == 1
    assert rep_gf.overall_benchmark.distribution.median_hours == 4.0
    assert rep_gf.overall_benchmark.reliability == BenchmarkReliability.LOW_CONFIDENCE
    assert "Feature" in rep_gf.by_issue_type
    assert rep_gf.by_issue_type["Feature"].reliability == BenchmarkReliability.LOW_CONFIDENCE

    # Check persistence
    with mock_db_mgr.session() as conn:
        c = conn.execute("SELECT COUNT(*) as cnt FROM historical_effort_benchmarks")
        cnt = c.fetchone()["cnt"]
        assert cnt > 0


@pytest.mark.asyncio
async def test_effort_benchmark_formatter():
    """Test text and Markdown formatters for benchmark outputs."""
    summary = MultiProjectBenchmarkSummary(
        summary_id="test_run",
        generated_at="2026-09-30T12:00:00Z",
        lookback_days=90,
        projects=["SMTPSUPORT", "GF"],
    )
    rep_smtp = ProjectEffortBenchmarkReport(
        project_key="SMTPSUPORT",
        date_range_start="2026-07-02",
        date_range_end="2026-09-30",
        total_completed_issues=10,
        issues_with_logged_effort=10,
    )
    from app.core.intelligence.effort_benchmark_models import SegmentedProjectBenchmark
    rep_smtp.overall_benchmark = SegmentedProjectBenchmark(
        project_key="SMTPSUPORT",
        dimension_type="overall",
        dimension_key="all",
        sample_count=10,
        distribution=calculate_quantiles_and_stats([2.0] * 10),
        reliability=BenchmarkReliability.USABLE,
    )
    summary.reports_by_project["SMTPSUPORT"] = rep_smtp

    console_out = EffortBenchmarkReportFormatter.format_console_report(summary)
    assert "EMPIRICAL HISTORICAL EFFORT BENCHMARK REPORT" in console_out
    assert "SMTPSUPORT" in console_out

    md_out = EffortBenchmarkReportFormatter.format_markdown_report(summary)
    assert "# Empirical Historical Effort Benchmarking Report" in md_out
    assert "SMTPSUPORT" in md_out

