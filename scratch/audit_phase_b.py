import os
import sys
import json
import sqlite3
import re
from typing import Dict, Any, List
from collections import defaultdict

from app.config.settings import settings
from app.database.connection import db_manager
from app.database.schema import init_db
from app.database.repositories import (
    EmployeeRoleRepository,
    HistoricalIntelligenceRepository,
    PerformanceRepository,
)
from app.core.intelligence.models import (
    TaskNature,
    BaselineComparisonState,
    TrendDirection,
    WorkloadPressureLevel,
    ReviewReworkReason,
    DataCompletenessRating,
    AIReadinessStatus,
    HistoricalIntelligenceProfile,
)
from app.core.intelligence.classifier import TaskNatureClassifier
from app.core.intelligence.benchmarks import HistoricalEffortBenchmarkEngine
from app.core.intelligence.baselines import PersonalBaselineEngine
from app.core.intelligence.trends import HistoricalTrendAnalyzer
from app.core.intelligence.workload import WorkloadPressureAnalyzer
from app.core.intelligence.rework import ReviewReworkAnalyzer
from app.core.intelligence.delivery import DeliveryContextAnalyzer, BlockerHistoryAnalyzer
from app.core.intelligence.engine import HistoricalIntelligenceEngine
from app.core.performance.roles import resolve_canonical_account_id

def run_comprehensive_audit():
    print("=" * 80)
    print("PHASE B FINAL QA, DATA QUALITY, ARCHITECTURE & AI-READINESS AUDIT")
    print("=" * 80)

    db_path = settings.get_database_path()
    print(f"Target SQLite DB: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # ---------------------------------------------------------
    # 1. FILE & MODULE VERIFICATION
    # ---------------------------------------------------------
    phase_b_files = [
        "app/core/intelligence/models.py",
        "app/core/intelligence/classifier.py",
        "app/core/intelligence/benchmarks.py",
        "app/core/intelligence/baselines.py",
        "app/core/intelligence/trends.py",
        "app/core/intelligence/workload.py",
        "app/core/intelligence/rework.py",
        "app/core/intelligence/delivery.py",
        "app/core/intelligence/engine.py",
        "app/core/intelligence/__init__.py",
        "app/api/routes/intelligence.py",
        "tests/test_historical_intelligence.py",
    ]
    print("\n--- PHASE 1: FILE EXISTENCE & WIRING AUDIT ---")
    all_files_exist = True
    for f in phase_b_files:
        exists = os.path.exists(os.path.join(r"d:\PM", f.replace("/", os.sep)))
        if not exists:
            all_files_exist = False
        print(f"[{'EXISTS' if exists else 'MISSING'}] {f}")

    # ---------------------------------------------------------
    # 2. KEYWORD SEARCH FOR PROHIBITED AI / SCORING TERMS
    # ---------------------------------------------------------
    print("\n--- PHASE 2: PROHIBITED AI / SCORING SEARCH ---")
    prohibited_terms = [
        "openai", "anthropic", "gemini", "llm", "langchain",
        "productivity_score", "performance_score", "ranking", "rank",
        "pip", "promotion", "termination", "reward", "employee_score"
    ]
    intel_dir = r"d:\PM\app\core\intelligence"
    matches = defaultdict(list)
    for root, _, files in os.walk(intel_dir):
        for fname in files:
            if fname.endswith(".py"):
                fpath = os.path.join(root, fname)
                with open(fpath, "r", encoding="utf-8") as rf:
                    content = rf.read()
                    for term in prohibited_terms:
                        found_lines = []
                        for idx, line in enumerate(content.splitlines(), start=1):
                            # Case insensitive search
                            if re.search(r'\b' + re.escape(term) + r'\b', line, re.IGNORECASE):
                                found_lines.append((idx, line.strip()))
                        if found_lines:
                            matches[term].append((fname, found_lines))

    for term in prohibited_terms:
        if term in matches:
            print(f"Found keyword '{term}' in intelligence modules:")
            for fname, flines in matches[term]:
                for lno, ltext in flines:
                    print(f"  - {fname}:{lno} -> {ltext}")
        else:
            print(f"Keyword '{term}': NONE FOUND (CLEAN)")

    # ---------------------------------------------------------
    # 3. IDENTITY AUDIT & AHSAN ALIAS UNIFICATION
    # ---------------------------------------------------------
    print("\n--- PHASE 3: IDENTITY & AHSAN ALIAS AUDIT ---")
    canonical_ahsan = "712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de"
    role_repo = EmployeeRoleRepository(db_manager)
    
    test_aliases = [
        ("ahsan.amin", None),
        ("jira-user-ahsan", None),
        (canonical_ahsan, "Ahsan Amin"),
        (None, "Ahsan Amin"),
    ]
    for raw_acc, disp_name in test_aliases:
        resolved = resolve_canonical_account_id(raw_acc, display_name=disp_name, role_repo=role_repo)
        print(f"Alias ('{raw_acc}', '{disp_name}') -> Resolved: '{resolved}' [{'PASS' if resolved == canonical_ahsan else 'FAIL'}]")

    # ---------------------------------------------------------
    # 4. CANONICAL EXCLUSIONS AUDIT
    # ---------------------------------------------------------
    print("\n--- PHASE 4: CANONICAL EXCLUSIONS AUDIT ---")
    excl_set = settings.CANONICAL_EXCLUDED_ACCOUNT_IDS
    print(f"Canonical Excluded IDs Count: {len(excl_set)}")
    for eid in sorted(excl_set):
        print(f"  - {eid}")

    # ---------------------------------------------------------
    # 5. AUTHORITATIVE EMPLOYEE POPULATION AUDIT
    # ---------------------------------------------------------
    print("\n--- PHASE 5: AUTHORITATIVE EMPLOYEE POPULATION AUDIT ---")
    auth_roles = role_repo.list_assignments()
    print(f"Total Seeded Authoritative Employees: {len(auth_roles)}")
    for r in auth_roles:
        print(f"  - {r['display_name']:25} | Role: {r['role_category']:20} | Designation: {r.get('designation', ''):35} | ID: {r['account_id']}")

    # ---------------------------------------------------------
    # 6. HISTORICAL COVERAGE AUDIT
    # ---------------------------------------------------------
    print("\n--- PHASE 6: HISTORICAL COVERAGE AUDIT ---")
    wl_dates = conn.execute("SELECT MIN(started_at), MAX(started_at), COUNT(*) FROM jira_worklogs").fetchone()
    min_wl, max_wl, count_wl = wl_dates[0], wl_dates[1], wl_dates[2]
    print(f"Worklogs: Total={count_wl}, Min Date={min_wl}, Max Date={max_wl}")

    issue_dates = conn.execute("SELECT MIN(updated_at), MAX(updated_at), COUNT(*) FROM jira_issue_state").fetchone()
    min_iss, max_iss, count_iss = issue_dates[0], issue_dates[1], issue_dates[2]
    print(f"Issues: Total={count_iss}, Min Updated={min_iss}, Max Updated={max_iss}")

    # Calculate days
    from datetime import datetime
    try:
        earliest_dt = datetime.fromisoformat(min_wl)
        latest_dt = datetime.fromisoformat(max_wl)
        history_days = (latest_dt - earliest_dt).total_seconds() / 86400.0
        print(f"Exact Historical Span (Days): {history_days:.2f}")
    except Exception as e:
        print(f"Error parsing dates: {e}")

    # ---------------------------------------------------------
    # 7. PHASE B LIVE DATABASE INSPECTION (Latest Run)
    # ---------------------------------------------------------
    print("\n--- PHASE B LATEST PERSISTED RUN AUDIT ---")
    intel_repo = HistoricalIntelligenceRepository(db_manager)
    latest_run_id = intel_repo.get_latest_run_id()
    print(f"Latest Analysis Run ID in DB: {latest_run_id}")

    if latest_run_id:
        profiles = intel_repo.list_profiles(run_id=latest_run_id)
        evidence = intel_repo.get_evidence_records(run_id=latest_run_id, limit=1000)
        benchmarks = intel_repo.get_effort_benchmarks(run_id=latest_run_id)
        print(f"Persisted Profiles: {len(profiles)}")
        print(f"Persisted Evidence Records: {len(evidence)}")
        print(f"Persisted Effort Benchmarks: {len(benchmarks)}")

        # Audit excluded users in profiles & evidence
        profile_accounts = {p["account_id"] for p in profiles}
        evidence_accounts = {e["account_id"] for e in evidence}
        for excl in excl_set:
            p_found = excl in profile_accounts
            e_found = excl in evidence_accounts
            print(f"Excluded '{excl}' in profiles: {'LEAK (FAIL)' if p_found else 'ABSENT (PASS)'}, in evidence: {'LEAK (FAIL)' if e_found else 'ABSENT (PASS)'}")

        # Benchmark segmentation tier breakdown
        tier_counts = defaultdict(int)
        for b in benchmarks:
            tier_counts[b["segmentation_tier"]] += 1
        print("\n--- BENCHMARK SEGMENTATION TIERS BREAKDOWN ---")
        for t, count in sorted(tier_counts.items()):
            print(f"  Tier {t:28}: {count:3} benchmarks")

        # Evidence category breakdown
        ev_types = defaultdict(int)
        for e in evidence:
            ev_types[e["evidence_type"]] += 1
        print("\n--- EVIDENCE TYPE BREAKDOWN ---")
        for et, count in sorted(ev_types.items()):
            print(f"  {et:25}: {count:3} records")

    # ---------------------------------------------------------
    # 8. DATA INTEGRITY & SANITY CHECKS
    # ---------------------------------------------------------
    print("\n--- DATA INTEGRITY & ANOMALY CHECKS ---")
    # Check for negative worklog hours
    neg_wl = conn.execute("SELECT COUNT(*) FROM jira_worklogs WHERE time_spent_seconds < 0").fetchone()[0]
    print(f"Negative Worklogs Count: {neg_wl} [{'PASS' if neg_wl == 0 else 'FAIL'}]")

    # Check for >24h single worklogs
    giant_wl = conn.execute("SELECT COUNT(*) FROM jira_worklogs WHERE time_spent_seconds > 86400").fetchone()[0]
    print(f">24h Worklogs Count: {giant_wl} [{'PASS' if giant_wl == 0 else 'FAIL'}]")

    # Check for negative profile hours
    neg_prof = conn.execute("SELECT COUNT(*) FROM historical_intelligence_profiles WHERE total_logged_hours < 0").fetchone()[0]
    print(f"Negative Profile Hours: {neg_prof} [{'PASS' if neg_prof == 0 else 'FAIL'}]")

    print("\n" + "=" * 80)
    print("AUDIT SCRIPT EXECUTION COMPLETED")
    print("=" * 80)

if __name__ == "__main__":
    run_comprehensive_audit()
