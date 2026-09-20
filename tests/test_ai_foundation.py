"""Comprehensive unit tests for the PM AI Foundation domain layer."""

import pytest
from app.config.settings import settings
from app.core.actions.base import BaseAction
from app.core.models.enums import ActionStatus, ActionType
from app.database.repositories import AuditRepository, JiraIssueStateRepository
from app.services.ai.context import ContextBuilder
from app.services.ai.decision import AIDecisionService
from app.services.ai.models import (
    AIContext,
    AIDecision,
    AIDecisionType,
    AIRecommendationType,
    ProposedAction,
    TaskSummaryContext,
)
from app.services.ai.provider import MockAIProvider, NullAIProvider
from app.services.ai.safety import AISafetyGate, AISafetyViolation


def test_ai_context_validation():
    """Verify AIContext model enforces valid typing and bounded structure."""
    ctx = AIContext(
        context_id="ctx-test-1",
        timestamp="2026-09-20T04:00:00Z",
        objective="Assess overdue task risk",
        task=TaskSummaryContext(
            task_id="1001",
            key="CF7-100",
            title="Implement OAuth2 flow",
            status="In Progress",
            priority="High",
            assignee_name="Ahsan Amin",
        ),
        applicable_policies=["OverdueTaskPolicy"],
    )
    assert ctx.context_id == "ctx-test-1"
    assert ctx.task.key == "CF7-100"
    assert ctx.task.assignee_name == "Ahsan Amin"
    assert ctx.metrics == []


def test_ai_decision_validation():
    """Verify AIDecision model enforces confidence bounds and structured data."""
    decision = AIDecision(
        decision_type=AIDecisionType.PM_ATTENTION,
        recommendation=AIRecommendationType.REVIEW_TASK,
        confidence=0.88,
        evidence=["Task inactive for 48 hours", "Due date passed"],
        explanation="Task requires review due to inactivity past deadline.",
        requires_approval=True,
    )
    assert decision.confidence == 0.88
    assert decision.decision_type == AIDecisionType.PM_ATTENTION
    assert decision.requires_approval is True


def test_invalid_confidence_rejection():
    """Verify invalid confidence values outside [0.0, 1.0] are strictly rejected."""
    with pytest.raises(ValueError):
        AIDecision(
            decision_type=AIDecisionType.PM_ATTENTION,
            recommendation=AIRecommendationType.REVIEW_TASK,
            confidence=1.5,  # Out of bounds (> 1.0)
            explanation="Invalid confidence test",
        )

    with pytest.raises(ValueError):
        AIDecision(
            decision_type=AIDecisionType.PM_ATTENTION,
            recommendation=AIRecommendationType.REVIEW_TASK,
            confidence=-0.1,  # Out of bounds (< 0.0)
            explanation="Invalid confidence test",
        )


def test_context_builder_excludes_secrets(temp_db):
    """Verify ContextBuilder redacts sensitive fields like api_token, secrets, or passwords."""
    builder = ContextBuilder(manager=temp_db)
    dirty_metadata = {
        "user_query": "Check task status",
        "api_token": "secret-jira-token-12345",
        "discord_webhook_url": "https://discord.com/api/webhooks/123456/abcdef-token",
        "nested": {"password": "super-secret-password", "safe_val": 42},
    }

    ctx = builder.build_custom_context(
        objective="Verify credential redaction",
        metadata=dirty_metadata,
    )

    meta = ctx.metadata
    assert meta["user_query"] == "Check task status"
    assert meta["api_token"] == "******"
    assert meta["discord_webhook_url"] == "******"
    assert meta["nested"]["password"] == "******"
    assert meta["nested"]["safe_val"] == 42


def test_context_builder_deterministic_from_db(temp_db):
    """Verify ContextBuilder constructs structured task context from existing repositories."""
    issue_repo = JiraIssueStateRepository(temp_db)
    issue_repo.upsert(
        jira_issue_key="CF7-50",
        summary="Database migration script",
        status="To Do",
        priority="Medium",
        assignee="Ahsan Amin",
        project_key="CF7",
        due_date="2026-09-25",
        team_group="Mursaleen Cluster",
    )

    builder = ContextBuilder(manager=temp_db)
    ctx = builder.build_task_context("CF7-50", objective="Evaluate assignment")

    assert ctx.task is not None
    assert ctx.task.key == "CF7-50"
    assert ctx.task.title == "Database migration script"
    assert ctx.task.status == "To Do"
    assert ctx.task.priority == "Medium"
    assert ctx.team_name == "Mursaleen Cluster"
    assert ctx.resource is not None
    assert ctx.resource.display_name == "Ahsan Amin"


def test_safety_gate_validates_clean_decision():
    """Verify AISafetyGate passes standard valid decisions."""
    clean_decision = AIDecision(
        decision_type=AIDecisionType.PM_ATTENTION,
        recommendation=AIRecommendationType.REVIEW_TASK,
        confidence=0.9,
        evidence=["Evidence 1"],
        explanation="Valid decision",
        requires_approval=True,
    )
    is_valid, err = AISafetyGate.validate_decision(clean_decision)
    assert is_valid is True
    assert err is None


def test_safety_gate_rejects_unapproved_mutation():
    """Verify AISafetyGate strictly rejects any proposed action that does not require approval."""
    unapproved_mutation = AIDecision(
        decision_type=AIDecisionType.PM_ATTENTION,
        recommendation=AIRecommendationType.PROPOSE_COMMENT,
        confidence=0.9,
        explanation="Attempting mutation without approval",
        proposed_action=ProposedAction(
            action_type="AddComment",
            target_system="jira",
            target_id="CF7-100",
            parameters={"comment": "Automated comment"},
        ),
        requires_approval=False,  # Dangerous! Must be rejected
    )

    is_valid, err = AISafetyGate.validate_decision(unapproved_mutation)
    assert is_valid is False
    assert "requires_approval=True" in err

    with pytest.raises(AISafetyViolation):
        AISafetyGate.to_staged_action(unapproved_mutation)


def test_safety_gate_rejects_unrecognized_action_type():
    """Verify AISafetyGate strictly rejects actions with non-allowlisted ActionType."""
    invalid_type_decision = AIDecision(
        decision_type=AIDecisionType.PM_ATTENTION,
        recommendation=AIRecommendationType.PROPOSE_COMMENT,
        confidence=0.9,
        explanation="Attempting non-allowlisted action",
        proposed_action=ProposedAction(
            action_type="ExecuteShellCommand",
            target_system="jira",
            target_id="CF7-100",
            parameters={"cmd": "echo 1"},
        ),
        requires_approval=True,
    )

    is_valid, err = AISafetyGate.validate_decision(invalid_type_decision)
    assert is_valid is False
    assert "Unrecognized ActionType" in err

    with pytest.raises(AISafetyViolation) as exc_info:
        AISafetyGate.to_staged_action(invalid_type_decision)
    assert "Unrecognized ActionType" in str(exc_info.value)


def test_safety_gate_rejects_missing_targets():
    """Verify AISafetyGate strictly rejects actions with empty target_system or target_id."""
    missing_target_decision = AIDecision(
        decision_type=AIDecisionType.PM_ATTENTION,
        recommendation=AIRecommendationType.PROPOSE_COMMENT,
        confidence=0.9,
        explanation="Attempting action with missing target",
        proposed_action=ProposedAction(
            action_type="AddComment",
            target_system="",
            target_id="CF7-100",
            parameters={"comment": "test"},
        ),
        requires_approval=True,
    )

    is_valid, err = AISafetyGate.validate_decision(missing_target_decision)
    assert is_valid is False
    assert "Target system must not be empty" in err


def test_safety_gate_rejects_destructive_parameters():
    """Verify AISafetyGate strictly rejects actions with destructive keywords."""
    destructive_decision = AIDecision(
        decision_type=AIDecisionType.PM_ATTENTION,
        recommendation=AIRecommendationType.PROPOSE_COMMENT,
        confidence=0.9,
        explanation="Attempting destructive operation",
        proposed_action=ProposedAction(
            action_type="AddComment",
            target_system="jira",
            target_id="CF7-100",
            parameters={"query": "DROP TABLE users;"},
        ),
        requires_approval=True,
    )

    is_valid, err = AISafetyGate.validate_decision(destructive_decision)
    assert is_valid is False
    assert "Destructive operations are strictly prohibited" in err


def test_safety_gate_creates_staged_base_action():
    """Verify AISafetyGate converts a valid proposed action into an unexecuted BaseAction."""
    valid_proposed = AIDecision(
        decision_type=AIDecisionType.PM_ATTENTION,
        recommendation=AIRecommendationType.PROPOSE_COMMENT,
        confidence=0.95,
        explanation="Recommend posting reminder comment",
        proposed_action=ProposedAction(
            action_type="AddComment",
            target_system="jira",
            target_id="CF7-100",
            parameters={"comment": "Please provide an update on this task."},
        ),
        requires_approval=True,
    )

    staged_action = AISafetyGate.to_staged_action(valid_proposed, requested_by="AIEngine")
    assert staged_action is not None
    assert isinstance(staged_action, BaseAction)
    assert staged_action.action_type == ActionType.ADD_COMMENT
    assert staged_action.target_system == "jira"
    assert staged_action.target_id == "CF7-100"
    assert staged_action.parameters == {"comment": "Please provide an update on this task."}
    assert staged_action.requires_approval is True
    assert staged_action.status == ActionStatus.REQUESTED
    assert staged_action.idempotency_key is not None


@pytest.mark.asyncio
async def test_ai_disabled_prevents_provider_invocation(temp_db):
    """Verify AI_ENABLED=false short-circuits evaluation without invoking external provider."""
    prev_ai = settings.AI_ENABLED
    settings.AI_ENABLED = False

    class SpyProvider:
        called = False

        async def analyze(self, ctx):
            self.called = True
            return NullAIProvider().analyze(ctx)

    spy = SpyProvider()
    service = AIDecisionService(provider=spy, manager=temp_db)
    ctx = AIContext(
        context_id="ctx-disabled-test",
        timestamp="2026-09-20T04:00:00Z",
        objective="Disabled test",
    )

    try:
        decision = await service.evaluate(ctx)
        assert spy.called is False
        assert decision.recommendation == AIRecommendationType.NO_ACTION
        assert "AI is disabled in system configuration" in decision.evidence[0]
    finally:
        settings.AI_ENABLED = prev_ai


@pytest.mark.asyncio
async def test_ai_decision_service_with_mock_and_audit(temp_db):
    """Verify AIDecisionService logs validated decisions to the unified audit trail."""
    prev_ai = settings.AI_ENABLED
    settings.AI_ENABLED = True

    mock_provider = MockAIProvider(
        decision_type=AIDecisionType.PM_ATTENTION,
        recommendation=AIRecommendationType.REVIEW_TASK,
        confidence=0.87,
        evidence=["Mock evidence: sprint ends tomorrow"],
        explanation="Sprint boundary approaching.",
    )

    service = AIDecisionService(provider=mock_provider, manager=temp_db)
    ctx = AIContext(
        context_id="ctx-audit-test",
        timestamp="2026-09-20T04:00:00Z",
        objective="Audit logging verification",
    )

    audit_repo = AuditRepository(temp_db)

    try:
        decision = await service.evaluate(ctx, actor="PM_AI_Agent")
        assert decision.confidence == 0.87
        assert decision.decision_type == AIDecisionType.PM_ATTENTION

        # Verify unified audit record exists in database
        logs = audit_repo.list_logs(limit=10)
        ai_logs = [l for l in logs if l.get("action") == "AI_DECISION"]
        assert len(ai_logs) >= 1
        latest = ai_logs[0]
        assert latest["actor"] == "PM_AI_Agent"
        assert latest["target"] == "ctx-audit-test"
        assert latest["result"] == "COMPLETED"
    finally:
        settings.AI_ENABLED = prev_ai
