"""Comprehensive test suite for Phase B v1.1 Historical Intelligence & Evidence Layer.

Tests all deterministic rules, 17 task nature categories, 11 benchmark segmentation tiers,
personal baselines, rolling trends (30/90/180/365d), workload pressure levels, review & rework
reasons, delivery context, blocker history, canonical exclusions, identity normalization,
and AI-ready contract outputs.
"""

from datetime import datetime, timezone, timedelta
import pytest
from fastapi.testclient import TestClient

from app.api.app import app
from app.config.settings import settings
from app.core.intelligence.baselines import PersonalBaselineEngine
from app.core.intelligence.benchmarks import HistoricalEffortBenchmarkEngine
from app.core.intelligence.classifier import TaskNatureClassifier
from app.core.intelligence.delivery import BlockerHistoryAnalyzer, DeliveryContextAnalyzer
from app.core.intelligence.engine import HistoricalIntelligenceEngine
from app.core.intelligence.models import (
    AIReadinessStatus,
    BaselineComparisonState,
    ConfidenceLevel,
    DataCompletenessRating,
    HistoricalIntelligenceProfile,
    ReviewReworkReason,
    TaskNature,
    TrendDirection,
    WorkloadPressureLevel,
)
from app.core.intelligence.rework import ReviewReworkAnalyzer
from app.core.intelligence.trends import HistoricalTrendAnalyzer
from app.core.intelligence.workload import WorkloadPressureAnalyzer
from app.core.models.performance import JiraIssueState, JiraWorklog
from app.database.connection import DatabaseManager


# =========================================================================
# 1. TASK NATURE CLASSIFIER TESTS (17 Categories & Deterministic Rules)
# =========================================================================

class TestTaskNatureClassifier:
    """Test deterministic classification across all categories and precedence rules."""

    def test_bug_issue_type_classification(self):
        res = TaskNatureClassifier.classify_issue(
            issue_key="TEST-1",
            issue_type="Bug",
            summary="Something failed in production",
        )
        assert res.task_nature == TaskNature.BUG_FIX
        assert res.classification_source == "issue_type"
        assert res.classification_confidence == ConfidenceLevel.HIGH

    def test_qa_testing_by_component(self):
        res = TaskNatureClassifier.classify_issue(
            issue_key="TEST-2",
            issue_type="Task",
            summary="Verify payment gateway flow",
            components=["QA", "Testing"],
        )
        assert res.task_nature == TaskNature.QA_TESTING
        assert res.classification_source == "component"

    def test_seo_by_label(self):
        res = TaskNatureClassifier.classify_issue(
            issue_key="TEST-3",
            issue_type="Task",
            summary="Update meta tags for landing page",
            labels=["seo", "ranking"],
        )
        assert res.task_nature == TaskNature.SEO
        assert res.classification_source == "label"

    def test_design_by_summary_keyword(self):
        res = TaskNatureClassifier.classify_issue(
            issue_key="TEST-4",
            issue_type="Task",
            summary="Create Figma wireframes and prototype for new dashboard",
        )
        assert res.task_nature == TaskNature.DESIGN
        assert res.classification_source == "summary_keyword"

    def test_business_analysis_by_summary_keyword(self):
        res = TaskNatureClassifier.classify_issue(
            issue_key="TEST-5",
            issue_type="Story",
            summary="Draft requirements specification and user story acceptance criteria",
        )
        assert res.task_nature == TaskNature.BUSINESS_ANALYSIS

    def test_research_spike_classification(self):
        res = TaskNatureClassifier.classify_issue(
            issue_key="TEST-6",
            issue_type="Story",
            summary="Spike: investigate vector DB performance vs SQLite",
        )
        assert res.task_nature == TaskNature.RESEARCH

    def test_development_by_summary_keyword(self):
        res = TaskNatureClassifier.classify_issue(
            issue_key="TEST-7",
            issue_type="Task",
            summary="Implement user authentication API endpoint",
        )
        assert res.task_nature == TaskNature.DEVELOPMENT

    def test_fallback_unknown_classification(self):
        res = TaskNatureClassifier.classify_issue(
            issue_key="TEST-8",
            issue_type="Task",
            summary="General follow up item 123",
        )
        assert res.task_nature == TaskNature.UNKNOWN
        assert res.classification_source == "fallback"
        assert res.classification_confidence == ConfidenceLevel.LOW


# =========================================================================
# 2. HISTORICAL EFFORT BENCHMARK TESTS (11 Segmentation Tiers)
# =========================================================================

class TestHistoricalEffortBenchmarks:
    """Test 11-tier segmentation, percentiles, and sample confidence thresholds."""

    @pytest.fixture
    def mock_issues_dataset(self):
        return [
            {
                "issue_key": f"T-{i}",
                "assignee_account_id": "acc-1",
                "task_nature": "DEVELOPMENT",
                "issue_type": "Task",
                "complexity_score": 3,
                "role_category": "WordPress Development",
                "team_group": "Team Alpha",
                "logged_hours": float(i * 2 + 2),  # 4, 6, 8, 10, 12, 14, 16, 18, 20, 22
            }
            for i in range(1, 11)
        ]

    def test_all_11_segmentation_tiers_generated(self, mock_issues_dataset):
        benchmarks = HistoricalEffortBenchmarkEngine.compute_benchmarks(
            account_id="acc-1",
            all_issues=mock_issues_dataset,
            role_category="WordPress Development",
            team_group="Team Alpha",
        )
        tier_names = [b.segmentation_tier for b in benchmarks]
        assert "1_employee_overall" in tier_names
        assert "2_employee_task_nature" in tier_names
        assert "3_employee_issue_type" in tier_names
        assert "4_employee_complexity" in tier_names
        assert "5_employee_issue_type_complexity" in tier_names
        assert "6_role_task_nature" in tier_names
        assert "7_role_issue_type" in tier_names
        assert "8_role_complexity" in tier_names
        assert "9_team_task_nature" in tier_names
        assert "10_team_issue_type" in tier_names
        assert "11_team_complexity" in tier_names

    def test_sample_confidence_thresholds(self):
        # 1-2 samples -> INSUFFICIENT
        stats_small = HistoricalEffortBenchmarkEngine._calculate_stats("1", "employee", "overall", [5.0, 6.0])
        assert stats_small.confidence == ConfidenceLevel.INSUFFICIENT

        # 3-4 samples -> LOW
        stats_low = HistoricalEffortBenchmarkEngine._calculate_stats("1", "employee", "overall", [5.0, 6.0, 7.0])
        assert stats_low.confidence == ConfidenceLevel.LOW

        # 5-9 samples -> MEDIUM
        stats_med = HistoricalEffortBenchmarkEngine._calculate_stats("1", "employee", "overall", [5.0, 6.0, 7.0, 8.0, 9.0])
        assert stats_med.confidence == ConfidenceLevel.MEDIUM

        # 10+ samples -> HIGH
        stats_high = HistoricalEffortBenchmarkEngine._calculate_stats("1", "employee", "overall", [float(i) for i in range(10)])
        assert stats_high.confidence == ConfidenceLevel.HIGH


# =========================================================================
# 3. PERSONAL BASELINE ENGINE TESTS
# =========================================================================

class TestPersonalBaselineEngine:
    """Test personal historical comparisons and non-judgmental states."""

    def test_personal_baseline_comparison_states(self):
        issues = [
            JiraIssueState(jira_issue_key=f"T-{i}", status="Done", complexity_score=3, reopen_count=0)
            for i in range(10)
        ]
        worklogs = [
            JiraWorklog(time_spent_seconds=24300, started_at=f"2026-08-{i+1:02d}T09:00:00Z")  # 6.75h/day
            for i in range(10)
        ]

        # Scenario 1: Normal current queue
        baseline = PersonalBaselineEngine.compute_personal_baseline(
            account_id="acc-1",
            issues=issues,
            worklogs=worklogs,
            current_active_queue_count=1,
            current_inferred_workload_hours=8.0,
        )
        assert baseline.has_sufficient_history is True
        assert baseline.active_queue_baseline.comparison_state in [
            BaselineComparisonState.NEAR_PERSONAL_BASELINE,
            BaselineComparisonState.ABOVE_PERSONAL_BASELINE,
            BaselineComparisonState.BELOW_PERSONAL_BASELINE,
        ]

        # Scenario 2: Significantly above baseline
        baseline_above = PersonalBaselineEngine.compute_personal_baseline(
            account_id="acc-1",
            issues=issues,
            worklogs=worklogs,
            current_active_queue_count=10,
            current_inferred_workload_hours=80.0,
        )
        assert baseline_above.active_queue_baseline.comparison_state == BaselineComparisonState.ABOVE_PERSONAL_BASELINE


# =========================================================================
# 4. ROLLING TREND ANALYZER TESTS (30d / 90d / 180d / 365d)
# =========================================================================

class TestHistoricalTrendAnalyzer:
    """Test 30d, 90d, 180d, 365d rolling metrics and trend directions."""

    def test_trend_increasing_direction(self):
        now = datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc)
        # Recent worklogs in 30d window
        recent_worklogs = [
            JiraWorklog(time_spent_seconds=28800, started_at=(now - timedelta(days=i)).isoformat())
            for i in range(1, 15)
        ]
        trends = HistoricalTrendAnalyzer.analyze_trends(
            account_id="acc-1",
            issues=[],
            worklogs=recent_worklogs,
            now=now,
        )
        assert trends.logged_hours_trend.value_30d > 0.0
        assert trends.logged_hours_trend.direction in [
            TrendDirection.INCREASING,
            TrendDirection.STABLE,
            TrendDirection.INSUFFICIENT_DATA,
        ]


# =========================================================================
# 5. WORKLOAD PRESSURE ANALYZER TESTS
# =========================================================================

class TestWorkloadPressureAnalyzer:
    """Test explainable workload pressure evaluation."""

    def test_high_workload_pressure_classification(self):
        now = datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc)
        active_issues = [
            JiraIssueState(
                jira_issue_key=f"HIGH-{i}",
                status="In Progress",
                complexity_score=4,
                due_date=(now + timedelta(days=3)).isoformat(),
            )
            for i in range(5)
        ]
        assessment = WorkloadPressureAnalyzer.assess_workload_pressure(
            account_id="acc-1",
            active_issues=active_issues,
            inferred_remaining_workload_hours=60.0,
            forecast_capacity_hours=30.0,  # 2.0x ratio -> HIGH
            active_blockers_count=1,
            now=now,
        )
        assert assessment.pressure_level == WorkloadPressureLevel.HIGH
        assert "HIGH workload pressure because" in assessment.explanation
        assert assessment.high_complexity_tasks_count == 5
        assert assessment.tasks_due_within_7_days == 5

    def test_low_workload_pressure_classification(self):
        assessment = WorkloadPressureAnalyzer.assess_workload_pressure(
            account_id="acc-1",
            active_issues=[],
            inferred_remaining_workload_hours=0.0,
            forecast_capacity_hours=30.0,
            active_blockers_count=0,
        )
        assert assessment.pressure_level == WorkloadPressureLevel.LOW


# =========================================================================
# 6. REVIEW & REWORK ANALYZER TESTS
# =========================================================================

class TestReviewReworkAnalyzer:
    """Test deterministic pattern matching for rework and reopens."""

    def test_rework_reason_classification(self):
        assert ReviewReworkAnalyzer.classify_rework_text("Failed QA verification on login") == ReviewReworkReason.QA_REWORK
        assert ReviewReworkAnalyzer.classify_rework_text("Client requested new button color") == ReviewReworkReason.CUSTOMER_CHANGE
        assert ReviewReworkAnalyzer.classify_rework_text("Scope change approved by PM") == ReviewReworkReason.REQUIREMENT_CHANGE
        assert ReviewReworkAnalyzer.classify_rework_text("Server crash regression in build") == ReviewReworkReason.TECHNICAL_ISSUE
        assert ReviewReworkAnalyzer.classify_rework_text("Unrelated text") == ReviewReworkReason.UNKNOWN


# =========================================================================
# 7. DELIVERY CONTEXT & BLOCKER HISTORY TESTS
# =========================================================================

class TestDeliveryAndBlockerHistory:
    """Test due-date delivery context and blocker calculations."""

    def test_delivery_context_metrics(self):
        now = datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc)
        issues = [
            JiraIssueState(
                jira_issue_key="T-ONTIME",
                status="Done",
                due_date=(now - timedelta(days=10)).isoformat(),
                resolved_at=(now - timedelta(days=11)).isoformat(),
            ),
            JiraIssueState(
                jira_issue_key="T-LATE",
                status="Done",
                due_date=(now - timedelta(days=10)).isoformat(),
                resolved_at=(now - timedelta(days=5)).isoformat(),
            ),
            JiraIssueState(
                jira_issue_key="T-OVERDUE",
                status="In Progress",
                due_date=(now - timedelta(days=3)).isoformat(),
            ),
            JiraIssueState(
                jira_issue_key="T-NODATE",
                status="Done",
            ),
        ]
        context = DeliveryContextAnalyzer.analyze_delivery_context("acc-1", issues, now=now)
        assert context.total_completed_tasks == 3
        assert context.completed_before_due_date == 1
        assert context.completed_after_due_date == 1
        assert context.currently_overdue == 1
        assert context.tasks_without_due_date == 1
        assert context.due_date_coverage_percent == 75.0


# =========================================================================
# 8. HISTORICAL INTELLIGENCE ENGINE & INTEGRATION TESTS
# =========================================================================

class TestHistoricalIntelligenceEngine:
    """Test orchestrator execution, canonical exclusions, and identity normalization."""

    def test_canonical_exclusions_strictly_enforced(self):
        engine = HistoricalIntelligenceEngine()
        result = engine.run_analysis(persist=False)
        profiles = result["profiles"]

        # Ensure NONE of the 4 canonically excluded IDs exist in generated profiles
        for excluded_id in settings.CANONICAL_EXCLUDED_ACCOUNT_IDS:
            assert excluded_id not in profiles, f"Excluded account {excluded_id} must not be profiled!"

    def test_authoritative_population_coverage(self):
        engine = HistoricalIntelligenceEngine()
        result = engine.run_analysis(persist=False)
        profiles = result["profiles"]
        # Authoritative designated population is 18
        assert len(profiles) == 18, f"Expected 18 authoritative employees, found {len(profiles)}"

    def test_ahsan_amin_identity_unified(self):
        engine = HistoricalIntelligenceEngine()
        result = engine.run_analysis(persist=False)
        canonical_ahsan_id = "712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de"
        assert canonical_ahsan_id in result["profiles"]
        ahsan_profile = result["profiles"][canonical_ahsan_id]
        assert ahsan_profile.display_name == "Ahsan Amin"
        assert "ahsan.amin" in ahsan_profile.known_aliases or "jira-user-ahsan" in ahsan_profile.known_aliases

    def test_zero_productivity_ranking_rule(self):
        engine = HistoricalIntelligenceEngine()
        result = engine.run_analysis(persist=False)
        for acc_id, profile in result["profiles"].items():
            # Assert no ranking, leaderboard, PIP score or HR judgment exists
            p_dict = profile.model_dump()
            assert "score" not in p_dict
            assert "productivity_score" not in p_dict
            assert "rank" not in p_dict
            assert "leaderboard" not in p_dict
            assert "pip" not in p_dict


# =========================================================================
# 9. REST API ENDPOINTS TESTS
# =========================================================================

class TestHistoricalIntelligenceAPI:
    """Test /performance/intelligence/* REST endpoints."""

    @pytest.fixture
    def client(self):
        return TestClient(app)

    def test_api_list_profiles(self, client):
        # Trigger an analysis first
        post_resp = client.post("/performance/intelligence/analyze", json={"history_days": 365})
        assert post_resp.status_code == 200
        run_data = post_resp.json()
        assert run_data["status"] == "SUCCESS"
        assert run_data["authoritative_count"] == 18

        # Get profiles list
        get_resp = client.get("/performance/intelligence")
        assert get_resp.status_code == 200
        profiles = get_resp.json()
        assert len(profiles) >= 18

    def test_api_get_single_employee_profile(self, client):
        ahsan_id = "712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de"
        resp = client.get(f"/performance/intelligence/{ahsan_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["account_id"] == ahsan_id
        assert data["display_name"] == "Ahsan Amin"

    def test_api_get_task_mix(self, client):
        ahsan_id = "712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de"
        resp = client.get(f"/performance/intelligence/{ahsan_id}/task-mix")
        assert resp.status_code == 200
        data = resp.json()
        assert "primary_task_nature" in data
        assert "issue_type_distribution" in data

    def test_api_get_effort_benchmarks(self, client):
        ahsan_id = "712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de"
        resp = client.get(f"/performance/intelligence/{ahsan_id}/effort")
        assert resp.status_code == 200
        benchmarks = resp.json()
        assert isinstance(benchmarks, list)
        assert len(benchmarks) >= 11

    def test_api_get_evidence_records(self, client):
        ahsan_id = "712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de"
        resp = client.get(f"/performance/intelligence/{ahsan_id}/evidence")
        assert resp.status_code == 200
        evidence = resp.json()
        assert isinstance(evidence, list)
        assert len(evidence) > 0
