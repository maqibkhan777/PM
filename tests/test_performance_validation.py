"""Comprehensive test suite for Phase A Data Quality & Analytics Validation.

Tests population integrity, canonical exclusions, identity audit, dynamic historical coverage,
worklog sanity, task mix, 7-tier expected-effort hierarchy, role categories, capacity validation,
blocker evidence, anomaly flags, data completeness, strict no-ranking assertion, and REST API semantics.
"""

from datetime import datetime, timedelta, timezone
import json
import os
import tempfile
import pytest
from fastapi.testclient import TestClient

from app.api.app import app
from app.config.settings import settings
from app.core.models.performance import RiskLevel, RoleCategory
from app.core.models.validation import (
    AnomalyFlagType,
    DataCompletenessState,
    DataQualityValidationReport,
    ValidationRecommendation,
)
from app.core.performance.engine import PerformanceAnalysisEngine
from app.core.performance.validator import DataQualityValidator
from app.database.connection import DatabaseManager
from app.database.repositories import (
    AuditRepository,
    EmployeeRoleRepository,
    JiraIssueStateRepository,
    JiraWorklogRepository,
    PerformanceRepository,
    PerformanceValidationRepository,
)
from app.database.schema import init_db
from app.utils.time import format_iso, utc_now, utc_now_iso


@pytest.fixture
def temp_db():
    """Create a temporary SQLite database for isolated test execution."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    mgr = DatabaseManager(db_path=path)
    init_db(mgr)
    yield mgr
    try:
        os.unlink(path)
    except Exception:
        pass


@pytest.fixture
def test_client(temp_db):
    """FastAPI test client pointing to temporary database."""
    from app.api.routes.performance import engine, perf_repo, role_repo, val_repo, validator
    engine.mgr = temp_db
    engine.perf_repo = PerformanceRepository(temp_db)
    engine.issue_repo = JiraIssueStateRepository(temp_db)
    engine.worklog_repo = JiraWorklogRepository(temp_db)
    engine.audit_repo = AuditRepository(temp_db)
    engine.role_repo = EmployeeRoleRepository(temp_db)

    perf_repo.mgr = temp_db
    role_repo.mgr = temp_db
    val_repo.mgr = temp_db

    validator.mgr = temp_db
    validator.engine = engine
    validator.perf_repo = PerformanceRepository(temp_db)
    validator.role_repo = EmployeeRoleRepository(temp_db)
    validator.worklog_repo = JiraWorklogRepository(temp_db)
    validator.issue_repo = JiraIssueStateRepository(temp_db)
    validator.val_repo = val_repo

    client = TestClient(app)
    return client


# =============================================================================
# 1. Validation Engine Execution & Report Structure Tests
# =============================================================================

def test_validation_engine_execution(temp_db):
    """Verify validator executes deterministically and constructs a complete validation report."""
    validator = DataQualityValidator(manager=temp_db)
    report = validator.validate_team(history_days=365)

    assert isinstance(report, DataQualityValidationReport)
    assert report.validation_id.startswith("val_")
    assert report.recommendation in [
        ValidationRecommendation.READY_FOR_AI_FOUNDATION,
        ValidationRecommendation.READY_WITH_DATA_QUALITY_LIMITATIONS,
    ]
    assert "authoritative_designated_count" in report.executive_summary
    assert report.team_population_validation["authoritative_designated_count"] == 18


# =============================================================================
# 2. Strict NO Ranking, NO Scoring, NO HR Decisions Assertion (Requirement 2 & 10)
# =============================================================================

def test_strict_no_ranking_and_no_score_assertion(temp_db):
    """Verify report contains zero ranking, scores, leaderboards, or employment verdicts."""
    validator = DataQualityValidator(manager=temp_db)
    report = validator.validate_team(history_days=365)
    report_dict = report.model_dump()
    raw_json_str = json.dumps(report_dict).lower()

    forbidden_terms = [
        "best_employee",
        "worst_employee",
        "leaderboard",
        "productivity_score",
        "performance_score",
        "pip_candidate",
        "reward_candidate",
        "termination_candidate",
        "promotion_candidate",
    ]

    for term in forbidden_terms:
        assert term not in raw_json_str, f"Forbidden term '{term}' found in validation report!"

    assert report.executive_summary.get("is_ranking_absent") is True
    assert report.executive_summary.get("is_score_absent") is True


# =============================================================================
# 3. Population & Canonical Exclusion Integrity Tests (Requirements 3 & 9)
# =============================================================================

def test_population_and_exclusion_integrity_validation(temp_db):
    """Verify 18 authoritative designations mapped and 4 global exclusions verified absent."""
    worklog_repo = JiraWorklogRepository(temp_db)

    # Insert worklogs for regular developer (Awais) and canonical excluded (Aqib Khan)
    worklog_repo.upsert_worklog(
        worklog_id="wl_awais_val",
        jira_issue_key="WSSS-101",
        author_account_id="62d556ad67b2d561571221bb",
        author_display_name="Awais",
        time_spent_seconds=14400,
        started_at="2026-09-01T10:00:00Z",
    )
    worklog_repo.upsert_worklog(
        worklog_id="wl_aqib_val",
        jira_issue_key="WSSS-102",
        author_account_id="712020:566cad70-4a54-42bc-bf36-0c6132fe3cf0",
        author_display_name="Aqib Khan",
        time_spent_seconds=14400,
        started_at="2026-09-01T10:00:00Z",
    )

    validator = DataQualityValidator(manager=temp_db)
    report = validator.validate_team(history_days=365)
    pop_val = report.team_population_validation

    assert pop_val["authoritative_designated_count"] == 18
    assert pop_val["globally_excluded_resources_count"] >= 4
    assert pop_val["globally_excluded_ids_verified_absent"] is True
    assert len(pop_val["excluded_violations"]) == 0


# =============================================================================
# 4. Identity Integrity Audit & Legacy Alias Unification (Requirements 4 & 8)
# =============================================================================

def test_identity_integrity_audit_and_legacy_unification(temp_db):
    """Verify identity audit correctly classifies authoritative seeds, legacy aliases, and unmapped users."""
    worklog_repo = JiraWorklogRepository(temp_db)
    issue_repo = JiraIssueStateRepository(temp_db)

    # 1. Ahsan Amin worklog under canonical account ID
    worklog_repo.upsert_worklog(
        worklog_id="wl_ahsan_canonical",
        jira_issue_key="CF7-101",
        author_account_id="712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de",
        author_display_name="Ahsan Amin",
        time_spent_seconds=7200,
        started_at=utc_now_iso(),
    )

    # 2. Legacy alias 'jira-user-ahsan' issue state
    raw_ahsan_legacy = {
        "key": "CF7-102",
        "fields": {
            "summary": "Legacy ticket",
            "status": {"name": "Done"},
            "assignee": {"accountId": "jira-user-ahsan", "displayName": "Ahsan Amin"},
            "issuetype": {"name": "Task"},
            "created": (utc_now() - timedelta(days=10)).isoformat(),
            "updated": utc_now_iso(),
        },
    }
    issue_repo.upsert(
        jira_issue_key="CF7-102",
        summary="Legacy ticket",
        status="Done",
        assignee="Ahsan Amin",
        raw_reference=raw_ahsan_legacy,
    )

    # 3. Unmapped contractor
    worklog_repo.upsert_worklog(
        worklog_id="wl_unmapped_user",
        jira_issue_key="CF7-103",
        author_account_id="jira-user-unmapped-999",
        author_display_name="New Contractor",
        time_spent_seconds=3600,
        started_at=utc_now_iso(),
    )

    validator = DataQualityValidator(manager=temp_db)
    report = validator.validate_team(history_days=365)
    ident_records = {r.raw_identifier: r for r in report.identity_integrity_audit}

    # Verify Ahsan's legacy alias
    assert "jira-user-ahsan" in ident_records
    assert ident_records["jira-user-ahsan"].resolved_canonical_account_id == "712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de"
    assert ident_records["jira-user-ahsan"].resolution_method == "AUTHORITATIVE_MAP"
    assert ident_records["jira-user-ahsan"].inclusion_status == "INCLUDED_AUTHORITATIVE"

    # Verify unmapped user
    assert "jira-user-unmapped-999" in ident_records
    assert ident_records["jira-user-unmapped-999"].inclusion_status == "UNRESOLVED"

    # Verify anomaly flagged for unmapped user
    unresolved_anoms = [
        a for a in report.anomalies_requiring_review
        if a.flag == AnomalyFlagType.UNRESOLVED_IDENTITY and a.account_id == "jira-user-unmapped-999"
    ]
    assert len(unresolved_anoms) >= 1


# =============================================================================
# 5. Dynamic Historical Coverage Validation (Requirement 5)
# =============================================================================

def test_dynamic_historical_coverage_validation(temp_db):
    """Verify actual_available_history_days is dynamically calculated and rolling windows evaluated."""
    worklog_repo = JiraWorklogRepository(temp_db)

    # Seed an item 45 days in the past
    past_45d = (utc_now() - timedelta(days=45)).isoformat()
    worklog_repo.upsert_worklog(
        worklog_id="wl_hist_1",
        jira_issue_key="TEST-1",
        author_account_id="638855b85fce844d606bb422",
        author_display_name="Tahir Ali",
        time_spent_seconds=7200,
        started_at=past_45d,
    )

    validator = DataQualityValidator(manager=temp_db)
    report = validator.validate_team(history_days=365)
    hist = report.historical_coverage_validation

    assert hist["requested_history_days"] == 365
    assert hist["actual_available_history_days"] >= 45
    assert hist["rolling_windows"]["30d"]["is_sufficient"] is True
    assert hist["rolling_windows"]["90d"]["is_sufficient"] is False  # 45 < 90


# =============================================================================
# 6. Worklog Quality & Negative Duration Checks (Requirement 6)
# =============================================================================

def test_worklog_quality_and_negative_duration_checks(temp_db):
    """Verify validator flags negative time spent and large entries as anomalies."""
    worklog_repo = JiraWorklogRepository(temp_db)

    # 1. Normal worklog
    worklog_repo.upsert_worklog(
        worklog_id="wl_normal",
        jira_issue_key="TEST-1",
        author_account_id="638855b85fce844d606bb422",
        author_display_name="Tahir Ali",
        time_spent_seconds=14400,
        started_at=utc_now_iso(),
    )

    # 2. Negative worklog
    worklog_repo.upsert_worklog(
        worklog_id="wl_negative",
        jira_issue_key="TEST-2",
        author_account_id="638855b85fce844d606bb422",
        author_display_name="Tahir Ali",
        time_spent_seconds=-3600,
        started_at=utc_now_iso(),
    )

    # 3. Large worklog (> 12h = 43200s)
    worklog_repo.upsert_worklog(
        worklog_id="wl_large",
        jira_issue_key="TEST-3",
        author_account_id="638855b85fce844d606bb422",
        author_display_name="Tahir Ali",
        time_spent_seconds=50400, # 14h
        started_at=utc_now_iso(),
    )

    validator = DataQualityValidator(manager=temp_db)
    report = validator.validate_team(history_days=365)
    wl_val = report.worklog_quality_validation

    assert wl_val["negative_durations_count"] >= 1
    assert wl_val["large_entries_count"] >= 1
    assert wl_val["data_quality_status"] == "ANOMALIES_DETECTED"

    patterns = [a for a in report.anomalies_requiring_review if a.flag == AnomalyFlagType.UNUSUAL_WORKLOG_PATTERN]
    assert len(patterns) >= 2


# =============================================================================
# 7. Expected-Effort Hierarchy & Sanity Checks (Requirements 8 & 9)
# =============================================================================

def test_expected_effort_source_hierarchy_and_sanity(temp_db):
    """Verify expected-effort 7-tier source distribution and sanity checks."""
    issue_repo = JiraIssueStateRepository(temp_db)
    worklog_repo = JiraWorklogRepository(temp_db)

    # Seed an active unestimated task for Tahir Ali
    raw_issue = {
        "key": "TEST-10",
        "fields": {
            "summary": "Active Task Without Estimate",
            "status": {"name": "In Progress"},
            "assignee": {"accountId": "638855b85fce844d606bb422", "displayName": "Tahir Ali"},
            "issuetype": {"name": "Task"},
            "priority": {"name": "Medium"},
            "created": (utc_now() - timedelta(days=2)).isoformat(),
            "updated": utc_now_iso(),
            "timeoriginalestimate": None,
            "timeestimate": None,
        },
    }
    issue_repo.upsert(
        jira_issue_key="TEST-10",
        summary="Active Task Without Estimate",
        status="In Progress",
        assignee="Tahir Ali",
        raw_reference=raw_issue,
    )
    worklog_repo.upsert_worklog(
        worklog_id="wl_tahir_act",
        jira_issue_key="TEST-10",
        author_account_id="638855b85fce844d606bb422",
        author_display_name="Tahir Ali",
        time_spent_seconds=7200,
        started_at=utc_now_iso(),
    )

    validator = DataQualityValidator(manager=temp_db)
    report = validator.validate_team(history_days=365)
    effort_val = report.expected_effort_quality_validation

    assert effort_val["hierarchy_verified"] is True
    assert effort_val["total_active_forecasts"] >= 1
    assert "source_distribution" in effort_val
    assert "confidence_distribution" in effort_val


# =============================================================================
# 8. Role-Aware Category Aggregation (Requirement 10)
# =============================================================================

def test_role_aware_category_aggregation(temp_db):
    """Verify analytics group across all 10 normalized role categories."""
    validator = DataQualityValidator(manager=temp_db)
    report = validator.validate_team(history_days=365)
    summaries = report.role_aware_analysis

    assert len(summaries) == 10
    categories = [s.role_category for s in summaries]
    assert RoleCategory.WORDPRESS_DEVELOPMENT.value in categories
    assert RoleCategory.BUSINESS_ANALYSIS.value in categories
    assert RoleCategory.QA.value in categories
    assert RoleCategory.CUSTOMER_SUPPORT.value in categories
    assert RoleCategory.DESIGN.value in categories


# =============================================================================
# 9. Capacity Boundaries & Overload Validation (Requirement 11)
# =============================================================================

def test_capacity_boundaries_validation(temp_db):
    """Verify nominal baseline (6.75h) and forecast bounds (6.5h-7.0h) are validated."""
    validator = DataQualityValidator(manager=temp_db)
    report = validator.validate_team(history_days=365)
    cap = report.capacity_validation

    assert cap["nominal_daily_capacity_baseline_hours"] == 6.75
    assert cap["forecast_daily_capacity_bounds"] == "6.5h - 7.0h"
    assert "capacity_interpretation_rule" in cap


# =============================================================================
# 10. Blocker Evidence Explicit Audit (Requirement 12)
# =============================================================================

def test_blocker_evidence_explicit_audit(temp_db):
    """Verify blocker detection validates explicit changelog/flag evidence only."""
    validator = DataQualityValidator(manager=temp_db)
    report = validator.validate_team(history_days=365)
    blk = report.blocker_validation

    assert blk["false_positive_audit_status"] == "VERIFIED_EXPLICIT"
    assert "total_blocker_events" in blk


# =============================================================================
# 11. Data Completeness States per Employee (Requirement 14)
# =============================================================================

def test_data_completeness_states_per_employee(temp_db):
    """Verify discrete completeness states (GOOD, PARTIAL, INSUFFICIENT, UNAVAILABLE) per member."""
    worklog_repo = JiraWorklogRepository(temp_db)
    worklog_repo.upsert_worklog(
        worklog_id="wl_tahir_comp",
        jira_issue_key="TEST-1",
        author_account_id="638855b85fce844d606bb422",
        author_display_name="Tahir Ali",
        time_spent_seconds=14400,
        started_at=utc_now_iso(),
    )

    validator = DataQualityValidator(manager=temp_db)
    report = validator.validate_team(history_days=365)
    tahir_prof = next((p for p in report.employee_profiles if p.account_id == "638855b85fce844d606bb422"), None)

    assert tahir_prof is not None
    assert tahir_prof.data_completeness["identity"] == DataCompletenessState.GOOD
    assert tahir_prof.data_completeness["designation"] == DataCompletenessState.GOOD
    assert tahir_prof.data_completeness["role"] == DataCompletenessState.GOOD


# =============================================================================
# 12. REST API Validation Endpoints (Requirement 17)
# =============================================================================

def test_api_validation_endpoints(test_client):
    """Test GET /performance/validation/latest, POST /performance/validation, and GET /performance/validation."""
    # 1. Trigger fresh validation via POST
    resp_post = test_client.post("/performance/validation?history_days=365")
    assert resp_post.status_code == 200
    data_post = resp_post.json()
    assert "validation_id" in data_post
    assert "recommendation" in data_post
    assert "team_population_validation" in data_post

    # 2. Retrieve latest via GET /performance/validation/latest
    resp_latest = test_client.get("/performance/validation/latest")
    assert resp_latest.status_code == 200
    data_latest = resp_latest.json()
    assert "validation_id" in data_latest or "summary" in data_latest

    # 3. Retrieve via GET /performance/validation
    resp_get = test_client.get("/performance/validation")
    assert resp_get.status_code == 200
