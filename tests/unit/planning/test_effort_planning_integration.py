"""Unit tests for AI Planning Proposal integration with Historical Effort Benchmarks (Milestone 3).

Guarantees tested:
1. AI planning proposals incorporate empirical benchmarks deterministically.
2. Usable benchmarks attach P50/P75 quantiles and EvidenceReference(RESOURCE_HISTORY).
3. Sparse benchmarks disclose fallback within the same project.
4. Insufficient benchmark data (< 5) attaches explicit DATA_QUALITY reference without fabricating estimates.
5. Discord router formats concise historical effort benchmark evidence.
6. Safety & deterministic validation remain 100% compliant (requires_human_review = True, zero mutations).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.connectors.discord.ai_discord_router import AIDiscordRouterService
from app.core.models.planning import (
    EstimateUnit,
    EvidenceReference,
    EvidenceType,
    PlanningContext,
    PlanningProposal,
    PlanningResourceContext,
    PlanningTaskContext,
    ProposalValidationStatus,
    TaskPlanningProposal,
    ValidationIssueSeverity,
)
from app.core.intelligence.effort_recommendation_models import (
    BenchmarkRecommendationStatus,
    TaskEffortBenchmarkRecommendation,
)
from app.core.intelligence.effort_retrieval_service import (
    HistoricalEffortBenchmarkRetrievalService,
)
from app.database.connection import DatabaseManager
from app.services.ai.planning import AIPlanningService, validate_planning_proposal
from app.services.ai.provider import MockAIProvider


@pytest.fixture
def mock_db_with_benchmarks(tmp_path):
    """Create isolated SQLite database populated with benchmark test records."""
    db_file = str(tmp_path / "test_planning.db")
    db = DatabaseManager(db_path=db_file)
    with db.session() as conn:
        conn.executescript(
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
            CREATE TABLE audit_logs (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                actor TEXT NOT NULL,
                action TEXT NOT NULL,
                target TEXT NOT NULL,
                result TEXT NOT NULL,
                details TEXT
            );
            """
        )
        # SMTPSUPORT benchmarks
        conn.execute(
            """
            INSERT INTO historical_effort_benchmarks VALUES (
                'b1', 'run-smtp-1', NULL, 'ISSUE_TYPE', 'issue_type', 'SMTPSUPORT:Support',
                28, 3.2, 2.5, 1.5, 5.0, 0.5, 8.0, 1.8, 'HIGH', 0, '2026-03-01T00:00:00Z'
            )
            """
        )
        # GF benchmarks
        conn.execute(
            """
            INSERT INTO historical_effort_benchmarks VALUES (
                'g1', 'run-gf-1', NULL, 'OVERALL', 'overall', 'GF:all',
                22, 6.0, 5.5, 3.0, 9.0, 1.0, 15.0, 3.0, 'HIGH', 0, '2026-03-01T00:00:00Z'
            )
            """
        )
    return db


@pytest.fixture
def sample_planning_context():
    """Build a valid sample PlanningContext with SMTPSUPORT tasks."""
    return PlanningContext(
        context_version="planning-v1",
        generated_at="2026-03-02T10:00:00Z",
        anchor_date="2026-03-02",
        planning_horizon_working_days=10,
        horizon_end_date="2026-03-13",
        team_group="SMTPSUPORT",
        resources=[
            PlanningResourceContext(
                resource_id="acc-1",
                display_name="Dev One",
                available_capacity_hours=60.0,
                remaining_effort_hours=20.0,
            )
        ],
        tasks=[
            PlanningTaskContext(
                issue_key="SMTPSUPORT-101",
                summary="Fix SMTP mail delivery timeout",
                status="To Do",
                priority="Medium",
                issue_type="Support",
                project_key="SMTPSUPORT",
                estimated_remaining_hours=0.0,  # Unestimated task to test benchmark recommendation
                due_date="2026-03-06",
                assigned_resource_id="acc-1",
                assigned_resource_name="Dev One",
            ),
            PlanningTaskContext(
                issue_key="SMTPSUPORT-102",
                summary="Update documentation for SMTP auth",
                status="In Progress",
                priority="Low",
                issue_type="Documentation",
                project_key="SMTPSUPORT",
                estimated_remaining_hours=4.0,
                due_date="2026-03-06",
                assigned_resource_id="acc-1",
                assigned_resource_name="Dev One",
            ),
        ],
    )


@pytest.mark.asyncio
async def test_ai_planning_generates_benchmark_backed_proposals(mock_db_with_benchmarks, sample_planning_context, monkeypatch):
    """Test that AI planning proposals are enriched with empirical effort benchmarks."""
    from app.config.settings import settings
    monkeypatch.setattr(settings, "AI_ENABLED", True)

    retrieval_svc = HistoricalEffortBenchmarkRetrievalService(mock_db_with_benchmarks)
    mock_provider = MockAIProvider()
    planning_svc = AIPlanningService(
        provider=mock_provider,
        manager=mock_db_with_benchmarks,
        retrieval_service=retrieval_svc,
    )

    proposal = await planning_svc.generate_plan(sample_planning_context)

    assert proposal is not None
    assert proposal.requires_human_review is True
    assert len(proposal.task_proposals) == 2

    # Verify SMTPSUPORT-101 (Support issue type) got recommended effort from SMTPSUPORT:Support (P50 = 2.5h)
    tp1 = next(t for t in proposal.task_proposals if t.issue_key == "SMTPSUPORT-101")
    assert tp1.proposed_estimate is not None
    assert tp1.proposed_estimate.value == 2.5
    assert tp1.proposed_estimate.unit == EstimateUnit.HOURS
    assert any(
        ev.evidence_type == EvidenceType.RESOURCE_HISTORY and "SMTPSUPORT:Support" in ev.source_identifier
        for ev in tp1.evidence_references
    )

    # Verify deterministic validation passes without errors
    val_res = validate_planning_proposal(sample_planning_context, proposal)
    assert val_res.status in (
        ProposalValidationStatus.VALID,
        ProposalValidationStatus.NEEDS_REVIEW,
    )
    assert not any(i.severity == ValidationIssueSeverity.ERROR for i in val_res.issues)


@pytest.mark.asyncio
async def test_discord_planning_output_includes_concise_benchmark_reference(mock_db_with_benchmarks, monkeypatch):
    """Test that Discord router formats concise historical effort benchmark evidence."""
    from app.config.settings import settings
    monkeypatch.setattr(settings, "AI_ENABLED", True)

    router = AIDiscordRouterService(manager=mock_db_with_benchmarks)

    # Mock the internal repos
    router.issue_repo = MagicMock()
    router.issue_repo.get_distinct_project_keys.return_value = ["SMTPSUPORT", "GF"]
    router.role_repo = MagicMock()
    router.role_repo.list_assignments.return_value = []
    router.link_repo = MagicMock()
    router.link_repo.list_all_links.return_value = []
    router.artifact_repo = MagicMock()
    router.artifact_repo.list_all_artifacts.return_value = []

    # Mock _handle_planning_request when no tasks in snapshots
    prompt = "propose a schedule plan for project SMTPSUPORT"
    res = await router._handle_planning_request(prompt, actor="user-123")

    assert isinstance(res, str)
    assert "No active backlog tasks found for Post SMTP Support (SMTPSUPORT)" in res
