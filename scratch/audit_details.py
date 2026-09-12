import json
import sqlite3
from typing import Dict, Any, List
from collections import defaultdict

from app.config.settings import settings
from app.database.connection import db_manager
from app.database.repositories import HistoricalIntelligenceRepository
from app.core.intelligence.models import (
    TaskNature,
    BaselineComparisonState,
    TrendDirection,
    WorkloadPressureLevel,
    ReviewReworkReason,
    DataCompletenessRating,
    AIReadinessStatus,
)
from app.core.intelligence.classifier import TaskNatureClassifier
from app.core.intelligence.benchmarks import HistoricalEffortBenchmarkEngine

def run_detailed_audit():
    print("=" * 80)
    print("DETAILED PHASE B LOGIC & CONTRACT AUDIT")
    print("=" * 80)

    # 1. TASK NATURE CLASSIFICATION SAMPLE TESTS
    print("\n--- AUDIT: TASK NATURE CLASSIFIER SAMPLE TESTS ---")
    sample_tests = [
        {"key": "BUG-1", "issue_type": "Bug", "summary": "Fix login crash", "components": [], "labels": [], "expected": TaskNature.BUG_FIX},
        {"key": "QA-1", "issue_type": "Task", "summary": "Regression test pass", "components": ["QA", "Testing"], "labels": [], "expected": TaskNature.QA_TESTING},
        {"key": "SEO-1", "issue_type": "Task", "summary": "Optimize meta tags", "components": [], "labels": ["seo-audit"], "expected": TaskNature.SEO},
        {"key": "DES-1", "issue_type": "Task", "summary": "Create Figma wireframes for dashboard", "components": [], "labels": [], "expected": TaskNature.DESIGN},
        {"key": "BA-1", "issue_type": "Task", "summary": "Write SRS requirements specification", "components": [], "labels": [], "expected": TaskNature.BUSINESS_ANALYSIS},
        {"key": "DEV-1", "issue_type": "Task", "summary": "Build backend REST API endpoint", "components": [], "labels": [], "expected": TaskNature.DEVELOPMENT},
        {"key": "DOC-1", "issue_type": "Task", "summary": "Write user guide documentation", "components": [], "labels": [], "expected": TaskNature.DOCUMENTATION},
        {"key": "UNK-1", "issue_type": "Task", "summary": "12345", "components": [], "labels": [], "expected": TaskNature.UNKNOWN},
    ]
    for st in sample_tests:
        res = TaskNatureClassifier.classify_issue(
            issue_key=st["key"],
            issue_type=st["issue_type"],
            summary=st["summary"],
            components=st["components"],
            labels=st["labels"],
        )
        passed = (res.task_nature == st["expected"])
        print(f"[{'PASS' if passed else 'FAIL'}] {st['key']:6} | Type: {st['issue_type']:5} | Summary: {st['summary']:35} -> Nature: {res.task_nature.value:20} (Rule: {res.classification_source})")

    # 2. EFFORT BENCHMARK FALLBACK CONFIGURATION
    print("\n--- AUDIT: EFFORT BENCHMARK FALLBACK VALUES ---")
    fallbacks = {i: settings.get_fallback_hours_for_complexity(i) for i in range(1, 6)}
    print(f"Configured Fallback values: {fallbacks}")
    expected_fallbacks = {1: 1.5, 2: 3.0, 3: 5.0, 4: 8.0, 5: 14.0}
    fallbacks_match = (fallbacks == expected_fallbacks)
    print(f"Fallback values match specification: {'PASS' if fallbacks_match else 'FAIL'}")

    # 3. DATABASE SCHEMA & TABLE STRUCTURE
    print("\n--- AUDIT: 7 PHASE B PERSISTENCE TABLES SCHEMA ---")
    conn = sqlite3.connect(settings.get_database_path())
    conn.row_factory = sqlite3.Row
    phase_b_tables = [
        "historical_intelligence_profiles",
        "historical_task_mix",
        "historical_effort_benchmarks",
        "historical_trends",
        "historical_workload_snapshots",
        "historical_delivery_context",
        "historical_evidence",
    ]
    for tbl in phase_b_tables:
        cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({tbl})").fetchall()]
        idx_count = len(conn.execute(f"PRAGMA index_list({tbl})").fetchall())
        count = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        print(f"Table '{tbl:34}': Columns={len(cols):2}, Indexes={idx_count:2}, Total Rows={count:4}")

    # 4. EVIDENCE LEDGER AUDIT
    print("\n--- AUDIT: EVIDENCE LEDGER INTEGRITY ---")
    repo = HistoricalIntelligenceRepository()
    latest_run_id = repo.get_latest_run_id()
    evidence_records = repo.get_evidence_records(run_id=latest_run_id, limit=1000)
    print(f"Evidence records for run '{latest_run_id}': {len(evidence_records)}")
    
    # Check for empty sources or explanations
    missing_source = sum(1 for e in evidence_records if not e.get("source"))
    missing_explanation = sum(1 for e in evidence_records if not e.get("explanation"))
    missing_account = sum(1 for e in evidence_records if not e.get("account_id"))
    print(f"Records with missing source: {missing_source} [{'PASS' if missing_source == 0 else 'FAIL'}]")
    print(f"Records with missing explanation: {missing_explanation} [{'PASS' if missing_explanation == 0 else 'FAIL'}]")
    print(f"Records with missing account_id: {missing_account} [{'PASS' if missing_account == 0 else 'FAIL'}]")

    print("\n" + "=" * 80)
    print("DETAILED LOGIC AUDIT COMPLETED")
    print("=" * 80)

if __name__ == "__main__":
    run_detailed_audit()
