"""Comprehensive unit and integration tests for PM AI Phase 2A: PM Attention Analysis."""

import pytest
from app.config.settings import settings
from app.database.repositories import AuditRepository, JiraIssueStateRepository
from app.services.ai.context import ContextBuilder
from app.services.ai.decision import AIDecisionService
from app.services.ai.models import (
    AttentionItemAnalysis,
    PMAttentionAnalysis,
    ProposedAction,
)
from app.services.ai.provider import MockAIProvider, NullAIProvider
from app.services.ai.report_formatter import AIAttentionReportFormatter
from app.services.ai.safety import AISafetyGate, AISafetyViolation


def test_attention_item_analysis_model():
    """Verify AttentionItemAnalysis validates bounds and required fields."""
    item = AttentionItemAnalysis(
        issue_key="TREN-378",
        title="Payment gateway timeout bug",
        current_status="In Progress",
        assignee="Ahsan Amin",
        priority="High",
        due_date="2026-09-18",
        updated_at="2026-09-15T10:00:00Z",
        inactivity_duration="5d",
        attention_reason="Task inactive for 5 days past due date.",
        supporting_evidence=["Status is 'In Progress'", "Due date was 2026-09-18"],
        recommendation="Check in with Ahsan on blocker status.",
        confidence=0.89,
        uncertainty_or_missing_info=None,
    )
    assert item.issue_key == "TREN-378"
    assert item.confidence == 0.89
    assert item.current_status == "In Progress"
    assert len(item.supporting_evidence) == 2


def test_attention_item_invalid_confidence_rejected():
    """Verify confidence values outside [0.0, 1.0] are strictly rejected."""
    with pytest.raises(ValueError):
        AttentionItemAnalysis(
            issue_key="TREN-378",
            title="Test",
            current_status="Open",
            attention_reason="Reason",
            recommendation="Rec",
            confidence=1.5,
        )


def test_pm_attention_analysis_model():
    """Verify PMAttentionAnalysis enforces human review and structured items."""
    analysis = PMAttentionAnalysis(
        analysis_id="analysis-test-1",
        generated_at="2026-09-20T12:00:00Z",
        scope_team="Mursaleen Cluster",
        summary="Found 1 overdue issue requiring intervention.",
        attention_items=[
            AttentionItemAnalysis(
                issue_key="HCF7-101",
                title="API auth token expiry",
                current_status="In Progress",
                attention_reason="Task is overdue",
                recommendation="Reassign or reschedule",
                confidence=0.91,
            )
        ],
        evidence=["Overdue date reached"],
        recommendation="Review at morning standup",
        confidence=0.90,
        requires_human_review=True,
    )
    assert analysis.analysis_id == "analysis-test-1"
    assert analysis.requires_human_review is True
    assert len(analysis.attention_items) == 1


def test_build_attention_context_from_db(temp_db):
    """Verify ContextBuilder.build_attention_context queries existing Jira projections deterministically."""
    issue_repo = JiraIssueStateRepository(temp_db)
    # Stale candidate
    issue_repo.upsert(
        jira_issue_key="PROJ-1",
        summary="Stale feature ticket",
        status="In Progress",
        assignee="Ahsan Amin",
        priority="Medium",
        due_date="2026-09-30",
        last_activity_at="2026-09-10T00:00:00Z",
        updated_at="2026-09-10T00:00:00Z",
        team_group="Mursaleen Cluster",
    )
    # Overdue candidate
    issue_repo.upsert(
        jira_issue_key="PROJ-2",
        summary="Overdue bugfix",
        status="To Do",
        assignee="Abdul Subhan",
        priority="High",
        due_date="2026-09-15",
        last_activity_at="2026-09-18T00:00:00Z",
        updated_at="2026-09-18T00:00:00Z",
        team_group="Mursaleen Cluster",
    )
    # Unassigned candidate
    issue_repo.upsert(
        jira_issue_key="PROJ-3",
        summary="Unassigned triage ticket",
        status="To Do",
        assignee=None,
        priority="Low",
        due_date="2026-10-01",
        last_activity_at="2026-09-19T00:00:00Z",
        updated_at="2026-09-19T00:00:00Z",
        team_group="Mursaleen Cluster",
    )

    builder = ContextBuilder(manager=temp_db)
    ctx = builder.build_attention_context(team_group="Mursaleen Cluster", stale_threshold_hours=48)

    assert ctx.team_name == "Mursaleen Cluster"
    metric_map = {m.metric_name: m.value for m in ctx.metrics}
    assert metric_map["stale_count"] >= 1
    assert metric_map["overdue_count"] >= 1
    assert metric_map["unassigned_count"] >= 1
    assert any("PROJ-1" in s for s in ctx.recent_activity_summary)
    assert any("PROJ-2" in s for s in ctx.recent_activity_summary)
    assert any("PROJ-3" in s for s in ctx.recent_activity_summary)


def test_mock_provider_analyze_attention(temp_db):
    """Verify MockAIProvider generates a structured PMAttentionAnalysis from context."""
    builder = ContextBuilder(manager=temp_db)
    ctx = builder.build_attention_context(
        team_group="Mursaleen Cluster",
        metadata={
            "stale_items": [
                {
                    "key": "CF7-200",
                    "summary": "Implement checkout validation",
                    "status": "In Progress",
                    "assignee": "Ahsan Amin",
                    "inactivity_duration": "4 days",
                }
            ],
            "overdue_items": [
                {
                    "key": "CF7-201",
                    "summary": "Fix mobile checkout crash",
                    "status": "In Progress",
                    "assignee": "Daniyal Raza",
                    "due_date": "2026-09-18",
                }
            ],
        },
    )

    provider = MockAIProvider()
    import asyncio
    analysis = asyncio.run(provider.analyze_attention(ctx))

    assert isinstance(analysis, PMAttentionAnalysis)
    assert analysis.scope_team == "Mursaleen Cluster"
    assert len(analysis.attention_items) == 2
    keys = [it.issue_key for it in analysis.attention_items]
    assert "CF7-200" in keys
    assert "CF7-201" in keys
    assert analysis.requires_human_review is True


def test_safety_gate_validates_attention_analysis():
    """Verify AISafetyGate passes standard valid PMAttentionAnalysis."""
    analysis = PMAttentionAnalysis(
        analysis_id="a-1",
        generated_at="2026-09-20T12:00:00Z",
        scope_team="Mursaleen Cluster",
        summary="Analysis summary",
        attention_items=[
            AttentionItemAnalysis(
                issue_key="CF7-10",
                title="Task 10",
                current_status="In Progress",
                attention_reason="Overdue by 2 days",
                recommendation="Check timeline",
                confidence=0.85,
            )
        ],
        evidence=["Overdue candidate"],
        recommendation="Review timeline",
        confidence=0.88,
        requires_human_review=True,
    )
    is_valid, err = AISafetyGate.validate_attention_analysis(analysis)
    assert is_valid is True
    assert err is None


def test_safety_gate_rejects_non_human_review():
    """Verify AISafetyGate strictly rejects any PMAttentionAnalysis with requires_human_review=False."""
    analysis = PMAttentionAnalysis(
        analysis_id="a-2",
        generated_at="2026-09-20T12:00:00Z",
        scope_team="Mursaleen Cluster",
        summary="Analysis summary",
        attention_items=[],
        evidence=[],
        recommendation="Action without human review",
        confidence=0.8,
        requires_human_review=False,
    )
    is_valid, err = AISafetyGate.validate_attention_analysis(analysis)
    assert is_valid is False
    assert "requires_human_review=True" in err


def test_ai_decision_service_evaluate_attention_disabled_by_default(temp_db, monkeypatch):
    """Verify evaluate_attention returns advisory no-op when AI_ENABLED is False (default)."""
    monkeypatch.setattr(settings, "AI_ENABLED", False)

    service = AIDecisionService(manager=temp_db)
    import asyncio
    analysis = asyncio.run(service.evaluate_attention())

    assert "disabled" in analysis.summary.lower()
    assert analysis.attention_items == []
    assert analysis.requires_human_review is True


def test_ai_decision_service_evaluate_attention_enabled_with_mock(temp_db, monkeypatch):
    """Verify evaluate_attention executes provider, passes safety gate, and records audit when AI_ENABLED=True."""
    monkeypatch.setattr(settings, "AI_ENABLED", True)

    provider = MockAIProvider()
    service = AIDecisionService(provider=provider, manager=temp_db)

    ctx = service.context_builder.build_attention_context(
        metadata={
            "stale_items": [
                {
                    "key": "HCF7-333",
                    "summary": "Fix webhook retry backoff",
                    "status": "In Progress",
                    "assignee": "Ahsan Amin",
                    "inactivity_duration": "3 days",
                }
            ]
        }
    )

    import asyncio
    analysis = asyncio.run(service.evaluate_attention(context=ctx))

    assert isinstance(analysis, PMAttentionAnalysis)
    assert len(analysis.attention_items) == 1
    assert analysis.attention_items[0].issue_key == "HCF7-333"

    # Confirm audit logging occurred
    audit_repo = AuditRepository(temp_db)
    logs = audit_repo.list_logs(limit=10)
    attention_logs = [l for l in logs if l["action"] == "AI_ATTENTION_ANALYSIS" and l["result"] == "COMPLETED"]
    assert len(attention_logs) == 1
    details = attention_logs[0]["details"]
    assert details["attention_items_count"] == 1
    assert "HCF7-333" in details["flagged_issue_keys"]


def test_human_readable_report_formatting():
    """Verify AIAttentionReportFormatter clearly separates facts from AI analysis."""
    analysis = PMAttentionAnalysis(
        analysis_id="analysis-fmt-1",
        generated_at="2026-09-20T14:30:00Z",
        scope_team="Mursaleen Cluster",
        summary="Identified 1 stalled item requiring follow-up.",
        attention_items=[
            AttentionItemAnalysis(
                issue_key="TREN-400",
                title="Refactor auth middleware",
                current_status="In Progress",
                assignee="Abdul Subhan",
                priority="High",
                due_date="2026-09-22",
                inactivity_duration="3 days",
                updated_at="Sep 17, 2026",
                attention_reason="No activity for 3 days while in active development.",
                supporting_evidence=["Status is 'In Progress'", "Last activity was 3 days ago"],
                recommendation="Verify whether Abdul is blocked by external dependencies.",
                confidence=0.88,
                uncertainty_or_missing_info=None,
            )
        ],
        evidence=["Evaluated 1 candidate in Mursaleen Cluster"],
        recommendation="Discuss during daily standup.",
        confidence=0.88,
        requires_human_review=True,
    )

    text = AIAttentionReportFormatter.format_text_report(analysis)

    assert "# 🤖 PM AI Attention Analysis — Mursaleen Cluster" in text
    assert "**Human Review Required:** Yes" in text
    assert "### 1. TREN-400 — Refactor auth middleware" in text
    assert "**Facts:**" in text
    assert "- Status: `In Progress`" in text
    assert "- Assignee: Abdul Subhan" in text
    assert "- Inactivity: 3 days" in text
    assert "**AI Analysis:**" in text
    assert "- Attention Reason: No activity for 3 days while in active development." in text
    assert "- Recommendation: Verify whether Abdul is blocked by external dependencies." in text
    assert "## 💡 Consolidated AI Recommendation" in text
    assert "Discuss during daily standup." in text
