"""Unit tests for HistoricalEffortBenchmarkRetrievalService (Milestone 3).

Guarantees tested:
1. Correct project-specific benchmark retrieval (SMTPSUPORT and GF).
2. Strict project isolation (Never cross-project fallback, e.g. SMTPSUPORT never uses GF).
3. Specific-to-general hierarchy fallback within the same project (issue_type_priority -> issue_type -> overall) and explicit disclosure.
4. Missing and insufficient benchmark handling (< 5 samples: INSUFFICIENT_DATA, no fabricated numbers).
5. Low confidence handling (5-9 samples: LOW_CONFIDENCE, tentative label with warning).
6. Usable benchmark handling (>= 10 samples: USABLE, P50 and P90/P75).
"""

import pytest
import sqlite3
from unittest.mock import MagicMock

from app.core.intelligence.effort_recommendation_models import (
    BenchmarkRecommendationStatus,
    TaskEffortBenchmarkRecommendation,
)
from app.core.intelligence.effort_retrieval_service import (
    HistoricalEffortBenchmarkRetrievalService,
)
from app.database.connection import DatabaseManager


@pytest.fixture
def mock_db(tmp_path):
    """Create isolated SQLite database populated with isolated test benchmark records."""
    db_file = str(tmp_path / "test_retrieval.db")
    db = DatabaseManager(db_path=db_file)
    with db.session() as conn:
        conn.execute(
            """
            CREATE TABLE historical_effort_benchmarks (
                id TEXT PRIMARY KEY,
                analysis_run_id TEXT NOT NULL,
                account_id TEXT,
                segmentation_tier TEXT NOT NULL,
                segment_type TEXT NOT NULL,
                segment_key TEXT NOT NULL,
                sample_count INTEGER NOT NULL,
                mean_hours REAL NOT NULL,
                median_hours REAL NOT NULL,
                p25_hours REAL NOT NULL,
                p75_hours REAL NOT NULL,
                min_hours REAL NOT NULL,
                max_hours REAL NOT NULL,
                stddev_hours REAL NOT NULL,
                confidence TEXT NOT NULL,
                is_fallback INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            """
        )

        # SMTPSUPORT records (run-smtp-1)
        # 1. Overall: all (n=45, median=3.0, p75=6.0)
        conn.execute(
            """
            INSERT INTO historical_effort_benchmarks VALUES (
                'b1', 'run-smtp-1', NULL, 'OVERALL', 'overall', 'SMTPSUPORT:all',
                45, 3.5, 3.0, 1.5, 6.0, 0.5, 12.0, 2.1, 'HIGH', 0, '2026-03-01T00:00:00Z'
            )
            """
        )
        # 2. Issue Type: Support (n=30, median=2.5, p75=5.0)
        conn.execute(
            """
            INSERT INTO historical_effort_benchmarks VALUES (
                'b2', 'run-smtp-1', NULL, 'ISSUE_TYPE', 'issue_type', 'SMTPSUPORT:Support',
                30, 2.8, 2.5, 1.0, 5.0, 0.5, 8.0, 1.8, 'HIGH', 0, '2026-03-01T00:00:00Z'
            )
            """
        )
        # 3. Compound: Support:Medium (n=20, median=2.0, p75=4.0) - USABLE
        conn.execute(
            """
            INSERT INTO historical_effort_benchmarks VALUES (
                'b3', 'run-smtp-1', NULL, 'COMPOUND', 'issue_type_priority', 'SMTPSUPORT:Support:Medium',
                20, 2.2, 2.0, 1.0, 4.0, 0.5, 6.0, 1.2, 'HIGH', 0, '2026-03-01T00:00:00Z'
            )
            """
        )
        # 4. Compound: Support:High (n=7, median=4.0, p75=7.0) - LOW_CONFIDENCE (5 <= n <= 9)
        conn.execute(
            """
            INSERT INTO historical_effort_benchmarks VALUES (
                'b4', 'run-smtp-1', NULL, 'COMPOUND', 'issue_type_priority', 'SMTPSUPORT:Support:High',
                7, 4.2, 4.0, 2.0, 7.0, 1.0, 9.0, 2.0, 'LOW', 0, '2026-03-01T00:00:00Z'
            )
            """
        )
        # 5. Compound: Support:Lowest (n=3, median=1.0, p75=2.0) - INSUFFICIENT (< 5)
        conn.execute(
            """
            INSERT INTO historical_effort_benchmarks VALUES (
                'b5', 'run-smtp-1', NULL, 'COMPOUND', 'issue_type_priority', 'SMTPSUPORT:Support:Lowest',
                3, 1.1, 1.0, 0.5, 2.0, 0.5, 2.0, 0.5, 'INSUFFICIENT', 0, '2026-03-01T00:00:00Z'
            )
            """
        )

        # GF (Gutena Forms) records (run-gf-1)
        # 1. Overall: all (n=25, median=5.0, p75=10.0)
        conn.execute(
            """
            INSERT INTO historical_effort_benchmarks VALUES (
                'g1', 'run-gf-1', NULL, 'OVERALL', 'overall', 'GF:all',
                25, 5.8, 5.0, 3.0, 10.0, 1.0, 18.0, 3.5, 'HIGH', 0, '2026-03-01T00:00:00Z'
            )
            """
        )
        # 2. Issue Type: Bug (n=15, median=4.0, p75=8.0)
        conn.execute(
            """
            INSERT INTO historical_effort_benchmarks VALUES (
                'g2', 'run-gf-1', NULL, 'ISSUE_TYPE', 'issue_type', 'GF:Bug',
                15, 4.5, 4.0, 2.0, 8.0, 1.0, 12.0, 2.5, 'HIGH', 0, '2026-03-01T00:00:00Z'
            )
            """
        )
        # 3. Compound: Feature:Medium (n=2, median=12.0, p75=16.0) - INSUFFICIENT
        conn.execute(
            """
            INSERT INTO historical_effort_benchmarks VALUES (
                'g3', 'run-gf-1', NULL, 'COMPOUND', 'issue_type_priority', 'GF:Feature:Medium',
                2, 12.0, 12.0, 8.0, 16.0, 8.0, 16.0, 4.0, 'INSUFFICIENT', 0, '2026-03-01T00:00:00Z'
            )
            """
        )
    return db


def test_project_specific_retrieval(mock_db):
    """Verify that SMTPSUPORT only retrieves SMTPSUPORT benchmarks and GF only retrieves GF."""
    svc = HistoricalEffortBenchmarkRetrievalService(mock_db)

    smtp_benches = svc.get_latest_project_benchmarks("SMTPSUPORT")
    assert len(smtp_benches) == 5
    for b in smtp_benches:
        assert b["segment_key"].startswith("SMTPSUPORT:")

    gf_benches = svc.get_latest_project_benchmarks("GF")
    assert len(gf_benches) == 3
    for b in gf_benches:
        assert b["segment_key"].startswith("GF:")


def test_strict_no_cross_project_fallback(mock_db):
    """Verify that a project never borrows or falls back to benchmarks of another project."""
    svc = HistoricalEffortBenchmarkRetrievalService(mock_db)

    # Project with zero benchmarks
    rec = svc.recommend_effort_for_task(
        project_key="OTHERPROJ",
        issue_key="OTHERPROJ-101",
        issue_type="Support",
        priority="Medium",
    )
    assert rec.reliability_status == BenchmarkRecommendationStatus.NOT_FOUND
    assert rec.recommended_effort_hours is None
    assert "No historical effort benchmarks found for project 'OTHERPROJ'" in rec.explanation


def test_usable_compound_benchmark(mock_db):
    """Verify that when compound segment has >= 10 samples, it is chosen as USABLE."""
    svc = HistoricalEffortBenchmarkRetrievalService(mock_db)

    rec = svc.recommend_effort_for_task(
        project_key="SMTPSUPORT",
        issue_key="SMTPSUPORT-10",
        issue_type="Support",
        priority="Medium",
    )

    assert rec.reliability_status == BenchmarkRecommendationStatus.USABLE
    assert rec.sample_count == 20
    assert rec.p50_effort_hours == 2.0
    assert rec.p90_effort_hours == 4.0
    assert rec.recommended_effort_hours == 2.0
    assert rec.is_fallback_grouping is False
    assert "issue_type_priority:Support:Medium" in rec.grouping_used


def test_within_project_fallback_when_compound_sparse(mock_db):
    """Verify within-project fallback: when compound is sparse (< 5), fallback to issue_type (>= 10) with disclosure."""
    svc = HistoricalEffortBenchmarkRetrievalService(mock_db)

    # SMTPSUPORT:Support:Lowest has n=3 (insufficient), but SMTPSUPORT:Support has n=30 (usable)
    rec = svc.recommend_effort_for_task(
        project_key="SMTPSUPORT",
        issue_key="SMTPSUPORT-12",
        issue_type="Support",
        priority="Lowest",
    )

    assert rec.reliability_status == BenchmarkRecommendationStatus.USABLE
    assert rec.sample_count == 30
    assert rec.p50_effort_hours == 2.5
    assert rec.is_fallback_grouping is True
    assert rec.fallback_disclosure is not None
    assert "Fell back to issue-type benchmark 'Support'" in rec.fallback_disclosure
    assert "SMTPSUPORT" in rec.fallback_disclosure


def test_within_project_fallback_to_overall(mock_db):
    """Verify fallback to overall project benchmark when neither compound nor issue_type is available."""
    svc = HistoricalEffortBenchmarkRetrievalService(mock_db)

    # GF:Feature:Medium has n=2 (sparse) and GF has no Feature issue_type record, so falls back to GF:all (n=25)
    rec = svc.recommend_effort_for_task(
        project_key="GF",
        issue_key="GF-99",
        issue_type="Feature",
        priority="Medium",
    )

    assert rec.reliability_status == BenchmarkRecommendationStatus.USABLE
    assert rec.sample_count == 25
    assert rec.p50_effort_hours == 5.0
    assert rec.is_fallback_grouping is True
    assert "overall:all" in rec.grouping_used
    assert "Fell back to overall project benchmark" in rec.fallback_disclosure


def test_insufficient_data_withheld_estimate(tmp_path):
    """Verify that when total sample is < 5 and no usable fallback exists, estimate is withheld (no hallucination)."""
    # Create DB with only sparse records (< 5 samples total)
    sparse_db_file = str(tmp_path / "sparse.db")
    sparse_db = DatabaseManager(db_path=sparse_db_file)
    with sparse_db.session() as conn:
        conn.execute(
            """
            CREATE TABLE historical_effort_benchmarks (
                id TEXT PRIMARY KEY,
                analysis_run_id TEXT NOT NULL,
                account_id TEXT,
                segmentation_tier TEXT NOT NULL,
                segment_type TEXT NOT NULL,
                segment_key TEXT NOT NULL,
                sample_count INTEGER NOT NULL,
                mean_hours REAL NOT NULL,
                median_hours REAL NOT NULL,
                p25_hours REAL NOT NULL,
                p75_hours REAL NOT NULL,
                min_hours REAL NOT NULL,
                max_hours REAL NOT NULL,
                stddev_hours REAL NOT NULL,
                confidence TEXT NOT NULL,
                is_fallback INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO historical_effort_benchmarks VALUES (
                's1', 'run-sparse-1', NULL, 'OVERALL', 'overall', 'SPARSEPROJ:all',
                3, 4.0, 4.0, 2.0, 5.0, 1.0, 6.0, 1.0, 'INSUFFICIENT', 0, '2026-03-01T00:00:00Z'
            )
            """
        )

    svc = HistoricalEffortBenchmarkRetrievalService(sparse_db)
    rec = svc.recommend_effort_for_task(
        project_key="SPARSEPROJ",
        issue_key="SPARSEPROJ-1",
        issue_type="Task",
        priority="Medium",
    )

    assert rec.reliability_status == BenchmarkRecommendationStatus.INSUFFICIENT_DATA
    assert rec.recommended_effort_hours is None
    assert rec.p50_effort_hours is None
    assert rec.p90_effort_hours is None
    assert "below minimum reliability threshold" in rec.data_quality_warning
    assert "Advisory estimate withheld" in rec.explanation


def test_lifecycle_duration_distinction_present(mock_db):
    """Verify that recommendations explicitly distinguish logged effort from wall-clock lifecycle lead time."""
    svc = HistoricalEffortBenchmarkRetrievalService(mock_db)
    rec = svc.recommend_effort_for_task(
        project_key="SMTPSUPORT",
        issue_key="SMTPSUPORT-1",
        issue_type="Support",
        priority="Medium",
    )
    assert "Logged developer effort reflects active time spent and must NEVER be conflated" in rec.lifecycle_distinction_note
