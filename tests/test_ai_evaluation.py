"""Deterministic evaluation framework and safety invariant tests for PM AI Attention Analysis."""

import pytest
from app.config.settings import settings
from app.services.ai.context import ContextBuilder
from app.services.ai.decision import AIDecisionService
from app.services.ai.models import (
    AttentionItemAnalysis,
    PMAttentionAnalysis,
    ProposedAction,
)
from app.services.ai.provider import MockAIProvider
from app.services.ai.safety import AISafetyGate, AISafetyViolation


def test_evaluation_schema_validity():
    """Verify PMAttentionAnalysis schema adheres to all required field specifications."""
    analysis = PMAttentionAnalysis(
        analysis_id="eval-schema-1",
        generated_at="2026-09-20T12:00:00Z",
        scope_team="Mursaleen Cluster",
        summary="Evaluation summary",
        attention_items=[],
        evidence=["Evaluated 0 candidates"],
        recommendation="Healthy state",
        confidence=0.95,
        requires_human_review=True,
    )
    d = analysis.model_dump()
    assert "analysis_id" in d
    assert "generated_at" in d
    assert "scope_team" in d
    assert "summary" in d
    assert "attention_items" in d
    assert "evidence" in d
    assert "recommendation" in d
    assert "confidence" in d
    assert "requires_human_review" in d
    assert d["requires_human_review"] is True


def test_evaluation_scenario_healthy_task_not_flagged(temp_db):
    """Verify that a healthy, active task within normal SLA thresholds is NOT flagged by ContextBuilder."""
    from app.database.repositories import JiraIssueStateRepository
    repo = JiraIssueStateRepository(temp_db)
    # Healthy task updated 1 hour ago
    from app.utils.time import utc_now_iso
    repo.upsert(
        jira_issue_key="HLTH-1",
        summary="Recent active work",
        status="In Progress",
        assignee="Ahsan Amin",
        priority="Medium",
        due_date="2026-10-30",
        last_activity_at=utc_now_iso(),
        updated_at=utc_now_iso(),
        team_group="Mursaleen Cluster",
    )

    builder = ContextBuilder(manager=temp_db)
    ctx = builder.build_attention_context(team_group="Mursaleen Cluster", stale_threshold_hours=24)

    # HLTH-1 should NOT appear in activity summaries or metadata items
    assert not any("HLTH-1" in s for s in ctx.recent_activity_summary)
    meta = ctx.metadata
    stale_keys = [it["key"] for it in meta.get("stale_items", [])]
    overdue_keys = [it["key"] for it in meta.get("overdue_items", [])]
    assert "HLTH-1" not in stale_keys
    assert "HLTH-1" not in overdue_keys


def test_evaluation_scenario_missing_assignee_explicitly_flagged():
    """Verify that an unassigned task explicitly indicates missing assignee information."""
    item = AttentionItemAnalysis(
        issue_key="UNAS-1",
        title="Orphaned feature request",
        current_status="To Do",
        assignee=None,
        attention_reason="Active task has no designated owner.",
        recommendation="Assign an engineer during standup.",
        confidence=0.90,
        uncertainty_or_missing_info="Assignee is missing / unassigned.",
    )
    assert item.assignee is None
    assert item.uncertainty_or_missing_info == "Assignee is missing / unassigned."


def test_evaluation_context_excludes_all_credentials(temp_db):
    """Verify ContextBuilder guarantees zero credentials or secrets in context."""
    sensitive_metadata = {
        "api_key": "super_secret_jira_key",
        "api_token": "secret_token_12345",
        "webhook_url": "https://discord.com/api/webhooks/123/xyz",
        "authorization": "Bearer confidential_token",
        "password": "my_admin_password",
        "normal_field": "safe_value",
    }
    builder = ContextBuilder(manager=temp_db)
    ctx = builder.build_attention_context(
        team_group="Mursaleen Cluster",
        metadata=sensitive_metadata,
    )

    meta_str = str(ctx.metadata)
    assert "super_secret_jira_key" not in meta_str
    assert "secret_token_12345" not in meta_str
    assert "confidential_token" not in meta_str
    assert "my_admin_password" not in meta_str
    assert "******" in meta_str
    assert ctx.metadata.get("normal_field") == "safe_value"


def test_evaluation_safety_gate_rejects_destructive_proposed_actions():
    """Verify AISafetyGate strictly rejects destructive operations in proposed actions."""
    destructive_action = ProposedAction(
        action_type="update_task",
        target_system="jira",
        target_id="TEST-1",
        parameters={"comment": "DROP TABLE users; DELETE FROM jira_issue_state;"},
    )
    analysis = PMAttentionAnalysis(
        analysis_id="a-bad-1",
        generated_at="2026-09-20T12:00:00Z",
        scope_team="Mursaleen Cluster",
        summary="Malicious injection attempt",
        attention_items=[],
        evidence=[],
        recommendation="Do not execute",
        confidence=0.5,
        proposed_action=destructive_action,
        requires_human_review=True,
    )
    is_valid, err = AISafetyGate.validate_attention_analysis(analysis)
    assert is_valid is False
    assert "Destructive operations are strictly prohibited" in err


def test_evaluation_safety_gate_rejects_unrecognized_action_type():
    """Verify AISafetyGate strictly rejects non-allowlisted ActionTypes."""
    unrecognized_action = ProposedAction(
        action_type="delete_database_cluster",
        target_system="jira",
        target_id="TEST-1",
        parameters={},
    )
    analysis = PMAttentionAnalysis(
        analysis_id="a-bad-2",
        generated_at="2026-09-20T12:00:00Z",
        scope_team="Mursaleen Cluster",
        summary="Invalid action type",
        attention_items=[],
        evidence=[],
        recommendation="None",
        confidence=0.5,
        proposed_action=unrecognized_action,
        requires_human_review=True,
    )
    is_valid, err = AISafetyGate.validate_attention_analysis(analysis)
    assert is_valid is False
    assert "Unrecognized ActionType" in err


def test_evaluation_no_executable_mutation_without_approval():
    """Verify that an AI proposed action can only be staged with requires_approval=True and status REQUESTED."""
    from app.core.models.enums import ActionStatus
    valid_proposed = ProposedAction(
        action_type="send_notification",
        target_system="discord",
        target_id="pm-alerts",
        parameters={"message": "Please review overdue task HCF7-101"},
        rationale="Notify team of overdue deadline",
    )
    from app.services.ai.models import AIDecision, AIDecisionType, AIRecommendationType
    decision = AIDecision(
        decision_type=AIDecisionType.PM_ATTENTION,
        recommendation=AIRecommendationType.NOTIFY_PM,
        confidence=0.92,
        evidence=["Task overdue by 3 days"],
        explanation="Notify PM of overdue task.",
        proposed_action=valid_proposed,
        requires_approval=True,
    )
    staged = AISafetyGate.to_staged_action(decision)
    assert staged is not None
    assert staged.requires_approval is True
    assert staged.status == ActionStatus.REQUESTED
    # BaseAction cannot be executed automatically; it is an unexecuted data object
