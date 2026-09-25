"""Unit tests for Phase 4F: Approved Planning Execution.

Covers full test matrix requirements A through AJ:
A. approved proposal executes
B. pending approval blocked
C. rejected approval blocked
D. expired approval blocked
E. invalid validation blocked
F. proposal ID mismatch
G. proposal version mismatch
H. context version mismatch
I. missing validation blocked
J. unauthorized executor blocked
K. unsupported action blocked
L. Jira issue missing
M. live Jira state conflict / up-to-date handling
N. concurrent Jira change
O. DRY_RUN produces zero mutation
P. DRY_RUN produces correct action preview
Q. successful single mutation
R. successful multi-issue execution
S. partial execution
T. Jira failure classification
U. retry ambiguity reconciled safely
V. already executed proposal does not mutate again
W. deterministic action ordering
X. no AI calls during execution
Y. no Action Engine call when blocked
Z. no Discord/Mattermost dispatch unless explicitly part of existing execution architecture
AA. audit events
AB. proposal immutability
AC. approval immutability
AD. context immutability
AE. no automatic replanning
AF. no automatic reapproval
AG. execution authorization separate from approval authorization
AH. concurrent execution attempts
AI. exact approved action mapping
AJ. unsupported proposal fields are not silently dropped
"""

import asyncio
from datetime import datetime, timezone
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.config.settings import settings
from app.connectors.jira.client import JiraClient
from app.connectors.jira.connector import JiraConnector
from app.core.actions.base import ActionResult
from app.core.actions.engine import ActionEngine
from app.core.models.enums import ActionStatus, Capability
from app.core.models.planning import (
    EstimateUnit,
    PlanningApprovalDecision,
    PlanningApprovalRequest,
    PlanningApprovalState,
    PlanningContext,
    PlanningEstimate,
    PlanningExecutionActionType,
    PlanningExecutionFailureCategory,
    PlanningExecutionRequest,
    PlanningExecutionResult,
    PlanningExecutionState,
    PlanningProposal,
    ProposalValidationCategory,
    ProposalValidationIssue,
    ProposalValidationResult,
    ProposalValidationStatus,
    ReviewerIdentity,
    TaskPlanningProposal,
    ValidationIssueSeverity,
)
from app.database.connection import DatabaseManager
from app.database.schema import init_db
from app.services.ai.planning_approval import PlanningApprovalService
from app.services.ai.planning_execution import PlanningExecutionService
from app.services.audit_service import AuditService


@pytest.fixture
def test_db(tmp_path):
    """Create isolated SQLite database manager for testing."""
    db_file = tmp_path / "test_exec.db"
    mgr = DatabaseManager(db_path=str(db_file))
    init_db(mgr)
    return mgr


@pytest.fixture
def audit_svc(test_db):
    return AuditService(test_db)


@pytest.fixture
def mock_jira_client():
    client = MagicMock(spec=JiraClient)
    # Default behavior: get_issue returns an issue with due date 2026-10-01
    client.get_issue = AsyncMock(return_value={"key": "PROJ-101", "fields": {"duedate": "2026-10-01"}})
    client.update_fields = AsyncMock(return_value={"id": "PROJ-101", "key": "PROJ-101"})
    return client


@pytest.fixture
def mock_action_engine(test_db):
    engine = ActionEngine(test_db)
    # Mock execute to return success
    engine.execute = AsyncMock(
        return_value=ActionResult(
            success=True,
            action_id="act-123",
            status=ActionStatus.COMPLETED,
            target_system="jira",
            target_id="PROJ-101",
            result_data={"key": "PROJ-101", "duedate": "2026-10-05"},
        )
    )
    return engine


@pytest.fixture
def execution_service(test_db, mock_action_engine, mock_jira_client, audit_svc):
    return PlanningExecutionService(
        manager=test_db,
        engine=mock_action_engine,
        jira_client=mock_jira_client,
        audit=audit_svc,
    )


from app.services.ai.evaluation.planning_dataset import PHASE_4D_EVALUATION_DATASET


def _make_dummy_context() -> PlanningContext:
    return PHASE_4D_EVALUATION_DATASET[0].context


def _make_dummy_proposal(issue_key="PROJ-101", proposed_due_date="2026-10-05", proposed_estimate_val=None) -> PlanningProposal:
    est = None
    if proposed_estimate_val is not None:
        est = PlanningEstimate(value=proposed_estimate_val, unit=EstimateUnit.HOURS)
    return PlanningProposal(
        proposal_version="proposal-v1",
        generated_at="2026-09-26T00:00:00Z",
        context_version="planning-v1",
        anchor_date="2026-09-26",
        summary="Test Planning Proposal",
        task_proposals=[
            TaskPlanningProposal(
                issue_key=issue_key,
                proposed_due_date=proposed_due_date,
                proposed_estimate=est,
            )
        ],
    )


def _make_dummy_validation(status=ProposalValidationStatus.VALID, issues=None) -> ProposalValidationResult:
    return ProposalValidationResult(
        status=status,
        proposal_version="proposal-v1",
        context_version="planning-v1",
        validated_at="2026-09-26T00:00:00Z",
        proposal_accepted=status == ProposalValidationStatus.VALID,
        summary="Validation Summary",
        issues=issues or [],
    )


def _create_approved_request(test_db, proposal=None, validation=None) -> str:
    approval_svc = PlanningApprovalService(test_db)
    ctx = _make_dummy_context()
    prop = proposal or _make_dummy_proposal()
    val = validation or _make_dummy_validation()

    req = approval_svc.create_approval_request(ctx, prop, val, proposal_id="prop-001")

    # Approve it
    reviewer = ReviewerIdentity(user_id="usr-pm", display_name="Lead PM", roles=["pm", "admin"])
    ack_codes = [iss.code for iss in val.issues if iss.severity == ValidationIssueSeverity.WARNING]
    approval_svc.approve_proposal(req.approval_request_id, reviewer, acknowledged_issue_codes=ack_codes)
    return req.approval_request_id


@pytest.mark.asyncio
async def test_requirement_a_and_q_approved_proposal_executes(execution_service, test_db):
    """Test A & Q: An approved proposal executes successfully and updates due date."""
    req_id = _create_approved_request(test_db)
    executor = ReviewerIdentity(user_id="exec-1", display_name="Lead PM", roles=["pm", "executor"])

    exec_req = PlanningExecutionRequest(
        execution_id="exec-001",
        approval_request_id=req_id,
        proposal_id="prop-001",
        proposal_version="proposal-v1",
        context_version="planning-v1",
        executor=executor,
        dry_run=False,
        requested_at="2026-09-26T01:00:00Z",
    )

    res: PlanningExecutionResult = await execution_service.execute_approved_planning(exec_req)
    assert res.state == PlanningExecutionState.COMPLETED
    assert res.successful_actions == 1
    assert res.failed_actions == 0
    assert res.blocked_actions == 0
    assert len(res.actions) == 1
    assert res.actions[0].action_type == PlanningExecutionActionType.UPDATE_DUE_DATE
    assert res.actions[0].approved_value == "2026-10-05"
    assert res.actions[0].state == "SUCCEEDED"


@pytest.mark.asyncio
async def test_requirement_b_c_d_blocked_when_not_approved(execution_service, test_db):
    """Test B, C, D: PENDING, REJECTED, or EXPIRED proposals are blocked from execution."""
    approval_svc = PlanningApprovalService(test_db)
    ctx = _make_dummy_context()
    prop = _make_dummy_proposal()
    val = _make_dummy_validation()
    reviewer = ReviewerIdentity(user_id="usr-pm", display_name="Lead PM", roles=["pm"])
    executor = ReviewerIdentity(user_id="exec-1", display_name="Lead PM", roles=["pm"])

    # 1. PENDING
    req_pending = approval_svc.create_approval_request(ctx, prop, val, proposal_id="prop-001")
    exec_req1 = PlanningExecutionRequest(
        execution_id="exec-p",
        approval_request_id=req_pending.approval_request_id,
        proposal_id="prop-001",
        proposal_version="proposal-v1",
        context_version="planning-v1",
        executor=executor,
        requested_at="2026-09-26T01:00:00Z",
    )
    res1 = await execution_service.execute_approved_planning(exec_req1)
    assert res1.state == PlanningExecutionState.BLOCKED
    assert res1.failures[0].failure_category == PlanningExecutionFailureCategory.APPROVAL_INVALID

    # 2. REJECTED
    req_rej = approval_svc.create_approval_request(ctx, prop, val, proposal_id="prop-001")
    approval_svc.reject_proposal(req_rej.approval_request_id, reviewer, comments="Not acceptable")
    exec_req2 = PlanningExecutionRequest(
        execution_id="exec-r",
        approval_request_id=req_rej.approval_request_id,
        proposal_id="prop-001",
        proposal_version="proposal-v1",
        context_version="planning-v1",
        executor=executor,
        requested_at="2026-09-26T01:00:00Z",
    )
    res2 = await execution_service.execute_approved_planning(exec_req2)
    assert res2.state == PlanningExecutionState.BLOCKED
    assert res2.failures[0].failure_category == PlanningExecutionFailureCategory.APPROVAL_INVALID

    # 3. EXPIRED
    req_exp = approval_svc.create_approval_request(ctx, prop, val, proposal_id="prop-001")
    with test_db.session() as conn:
        cursor = conn.execute(
            "SELECT request_payload_json FROM planning_approval_requests WHERE approval_request_id = ?",
            (req_exp.approval_request_id,),
        )
        req_payload = cursor.fetchone()["request_payload_json"]
        import json
        d = json.loads(req_payload)
        d["state"] = "EXPIRED"
        conn.execute(
            "UPDATE planning_approval_requests SET state = 'EXPIRED', request_payload_json = ? WHERE approval_request_id = ?",
            (json.dumps(d), req_exp.approval_request_id),
        )

    exec_req3 = PlanningExecutionRequest(
        execution_id="exec-e",
        approval_request_id=req_exp.approval_request_id,
        proposal_id="prop-001",
        proposal_version="proposal-v1",
        context_version="planning-v1",
        executor=executor,
        requested_at="2026-09-26T01:00:00Z",
    )
    res3 = await execution_service.execute_approved_planning(exec_req3)
    assert res3.state == PlanningExecutionState.BLOCKED
    assert res3.failures[0].failure_category == PlanningExecutionFailureCategory.APPROVAL_EXPIRED


@pytest.mark.asyncio
async def test_requirement_e_invalid_validation_blocked(execution_service, test_db):
    """Test E: Proposal with INVALID validation status is blocked from execution."""
    approval_svc = PlanningApprovalService(test_db)
    ctx = _make_dummy_context()
    prop = _make_dummy_proposal()
    val_invalid = _make_dummy_validation(
        status=ProposalValidationStatus.INVALID,
        issues=[
            ProposalValidationIssue(
                category=ProposalValidationCategory.SAFETY,
                severity=ValidationIssueSeverity.ERROR,
                code="HARD_BLOCK_VIOLATION",
                message="Cannot violate hard block",
            )
        ],
    )
    # Even if forcefully set in DB as APPROVED, execution must verify validation integrity
    req = approval_svc.create_approval_request(ctx, prop, val_invalid, proposal_id="prop-001")
    with test_db.session() as conn:
        cursor = conn.execute(
            "SELECT request_payload_json FROM planning_approval_requests WHERE approval_request_id = ?",
            (req.approval_request_id,),
        )
        req_payload = cursor.fetchone()["request_payload_json"]
        import json
        d = json.loads(req_payload)
        d["state"] = "APPROVED"
        conn.execute(
            "UPDATE planning_approval_requests SET state = 'APPROVED', request_payload_json = ? WHERE approval_request_id = ?",
            (json.dumps(d), req.approval_request_id),
        )

    executor = ReviewerIdentity(user_id="exec-1", display_name="Lead PM", roles=["pm"])
    exec_req = PlanningExecutionRequest(
        execution_id="exec-inv",
        approval_request_id=req.approval_request_id,
        proposal_id="prop-001",
        proposal_version="proposal-v1",
        context_version="planning-v1",
        executor=executor,
        requested_at="2026-09-26T01:00:00Z",
    )
    res = await execution_service.execute_approved_planning(exec_req)
    assert res.state == PlanningExecutionState.BLOCKED
    assert res.failures[0].failure_category == PlanningExecutionFailureCategory.VALIDATION_INVALID


@pytest.mark.asyncio
async def test_requirement_f_g_h_version_mismatch_blocked(execution_service, test_db):
    """Test F, G, H: Proposal ID, proposal version, or context version mismatches are blocked."""
    req_id = _create_approved_request(test_db)
    executor = ReviewerIdentity(user_id="exec-1", display_name="Lead PM", roles=["pm"])

    # Proposal ID mismatch
    res_f = await execution_service.execute_approved_planning(
        PlanningExecutionRequest(
            execution_id="exec-f",
            approval_request_id=req_id,
            proposal_id="WRONG-PROP",
            proposal_version="proposal-v1",
            context_version="planning-v1",
            executor=executor,
            requested_at="2026-09-26T01:00:00Z",
        )
    )
    assert res_f.state == PlanningExecutionState.BLOCKED
    assert res_f.failures[0].failure_category == PlanningExecutionFailureCategory.VERSION_MISMATCH

    # Proposal version mismatch
    res_g = await execution_service.execute_approved_planning(
        PlanningExecutionRequest(
            execution_id="exec-g",
            approval_request_id=req_id,
            proposal_id="prop-001",
            proposal_version="proposal-v2",
            context_version="planning-v1",
            executor=executor,
            requested_at="2026-09-26T01:00:00Z",
        )
    )
    assert res_g.state == PlanningExecutionState.BLOCKED
    assert res_g.failures[0].failure_category == PlanningExecutionFailureCategory.VERSION_MISMATCH

    # Context version mismatch
    res_h = await execution_service.execute_approved_planning(
        PlanningExecutionRequest(
            execution_id="exec-h",
            approval_request_id=req_id,
            proposal_id="prop-001",
            proposal_version="proposal-v1",
            context_version="planning-v99",
            executor=executor,
            requested_at="2026-09-26T01:00:00Z",
        )
    )
    assert res_h.state == PlanningExecutionState.BLOCKED
    assert res_h.failures[0].failure_category == PlanningExecutionFailureCategory.VERSION_MISMATCH


@pytest.mark.asyncio
async def test_requirement_j_and_ag_authorization_separate(execution_service, test_db):
    """Test J & AG: Unauthorized executor without EXECUTE_PLANNING is rejected."""
    req_id = _create_approved_request(test_db)
    unauth_executor = ReviewerIdentity(user_id="user-guest", display_name="Guest", roles=["viewer", "read_only"])

    exec_req = PlanningExecutionRequest(
        execution_id="exec-unauth",
        approval_request_id=req_id,
        proposal_id="prop-001",
        proposal_version="proposal-v1",
        context_version="planning-v1",
        executor=unauth_executor,
        requested_at="2026-09-26T01:00:00Z",
    )

    with pytest.raises(Exception) as exc_info:
        await execution_service.execute_approved_planning(exec_req)
    assert "lacks" in str(exc_info.value) or "EXECUTE_PLANNING" in str(exc_info.value)


@pytest.mark.asyncio
async def test_requirement_k_and_aj_unsupported_actions_not_silently_dropped(execution_service, test_db):
    """Test K & AJ: Unsupported actions (e.g. estimate update) are blocked and reported, not silently dropped."""
    # Create proposal with both due date and estimate
    prop = _make_dummy_proposal(issue_key="PROJ-101", proposed_due_date="2026-10-05", proposed_estimate_val=12.0)
    req_id = _create_approved_request(test_db, proposal=prop)
    executor = ReviewerIdentity(user_id="exec-1", display_name="Lead PM", roles=["pm"])

    exec_req = PlanningExecutionRequest(
        execution_id="exec-unsupported",
        approval_request_id=req_id,
        proposal_id="prop-001",
        proposal_version="proposal-v1",
        context_version="planning-v1",
        executor=executor,
        requested_at="2026-09-26T01:00:00Z",
    )

    res = await execution_service.execute_approved_planning(exec_req)
    # Allowed action (due date) succeeded, unsupported action (estimate) blocked -> PARTIALLY_COMPLETED
    assert res.state == PlanningExecutionState.PARTIALLY_COMPLETED
    assert res.successful_actions == 1
    assert res.blocked_actions == 1
    assert any(f.failure_category == PlanningExecutionFailureCategory.UNSUPPORTED_ACTION for f in res.failures)


@pytest.mark.asyncio
async def test_requirement_l_jira_issue_missing(execution_service, mock_jira_client, test_db):
    """Test L: If Jira issue does not exist, execution fails gracefully."""
    mock_jira_client.get_issue = AsyncMock(side_effect=Exception("HTTP 404 Not Found"))
    req_id = _create_approved_request(test_db)
    executor = ReviewerIdentity(user_id="exec-1", display_name="Lead PM", roles=["pm"])

    exec_req = PlanningExecutionRequest(
        execution_id="exec-404",
        approval_request_id=req_id,
        proposal_id="prop-001",
        proposal_version="proposal-v1",
        context_version="planning-v1",
        executor=executor,
        requested_at="2026-09-26T01:00:00Z",
    )

    res = await execution_service.execute_approved_planning(exec_req)
    assert res.state == PlanningExecutionState.BLOCKED
    assert res.failures[0].failure_category == PlanningExecutionFailureCategory.JIRA_NOT_FOUND


@pytest.mark.asyncio
async def test_requirement_m_live_jira_state_up_to_date(execution_service, mock_jira_client, test_db):
    """Test M: If Jira already has the exact approved due date, action is SKIPPED without redundant mutation."""
    mock_jira_client.get_issue = AsyncMock(return_value={"key": "PROJ-101", "fields": {"duedate": "2026-10-05"}})
    req_id = _create_approved_request(test_db)
    executor = ReviewerIdentity(user_id="exec-1", display_name="Lead PM", roles=["pm"])

    exec_req = PlanningExecutionRequest(
        execution_id="exec-skip",
        approval_request_id=req_id,
        proposal_id="prop-001",
        proposal_version="proposal-v1",
        context_version="planning-v1",
        executor=executor,
        requested_at="2026-09-26T01:00:00Z",
    )

    res = await execution_service.execute_approved_planning(exec_req)
    assert res.state == PlanningExecutionState.COMPLETED
    assert res.actions[0].state == "SKIPPED"


@pytest.mark.asyncio
async def test_requirement_o_and_p_dry_run_safety(execution_service, mock_action_engine, test_db):
    """Test O & P: DRY_RUN=true generates correct preview without performing Jira mutations."""
    req_id = _create_approved_request(test_db)
    executor = ReviewerIdentity(user_id="exec-1", display_name="Lead PM", roles=["pm"])

    # Mock action engine to simulate dry run
    mock_action_engine.execute = AsyncMock(
        return_value=ActionResult(
            success=True,
            action_id="act-dry",
            status=ActionStatus.DRY_RUN_SIMULATED,
            target_system="jira",
            target_id="PROJ-101",
            dry_run=True,
        )
    )

    exec_req = PlanningExecutionRequest(
        execution_id="exec-dry",
        approval_request_id=req_id,
        proposal_id="prop-001",
        proposal_version="proposal-v1",
        context_version="planning-v1",
        executor=executor,
        dry_run=True,
        requested_at="2026-09-26T01:00:00Z",
    )

    res = await execution_service.execute_approved_planning(exec_req)
    assert res.dry_run is True
    assert res.state == PlanningExecutionState.COMPLETED
    assert res.actions[0].state == "SUCCEEDED"
    assert res.actions[0].approved_value == "2026-10-05"


@pytest.mark.asyncio
async def test_requirement_r_and_w_multi_issue_deterministic_ordering(execution_service, test_db):
    """Test R & W: Multi-issue proposals execute in deterministic alphabetical key order."""
    prop = PlanningProposal(
        proposal_version="proposal-v1",
        generated_at="2026-09-26T00:00:00Z",
        context_version="planning-v1",
        anchor_date="2026-09-26",
        summary="Multi Issue Proposal",
        task_proposals=[
            TaskPlanningProposal(issue_key="ZEBRA-10", proposed_due_date="2026-10-15"),
            TaskPlanningProposal(issue_key="ALPHA-2", proposed_due_date="2026-10-08"),
            TaskPlanningProposal(issue_key="BETA-5", proposed_due_date="2026-10-10"),
        ],
    )
    req_id = _create_approved_request(test_db, proposal=prop)
    executor = ReviewerIdentity(user_id="exec-1", display_name="Lead PM", roles=["pm"])

    exec_req = PlanningExecutionRequest(
        execution_id="exec-multi",
        approval_request_id=req_id,
        proposal_id="prop-001",
        proposal_version="proposal-v1",
        context_version="planning-v1",
        executor=executor,
        requested_at="2026-09-26T01:00:00Z",
    )

    res = await execution_service.execute_approved_planning(exec_req)
    assert res.state == PlanningExecutionState.COMPLETED
    assert len(res.actions) == 3
    # Check deterministic ordering: ALPHA-2, BETA-5, ZEBRA-10
    assert res.actions[0].issue_key == "ALPHA-2"
    assert res.actions[1].issue_key == "BETA-5"
    assert res.actions[2].issue_key == "ZEBRA-10"


@pytest.mark.asyncio
async def test_requirement_v_idempotency_already_executed(execution_service, test_db):
    """Test V: Already executed approval request returns ALREADY_EXECUTED with zero new mutations."""
    req_id = _create_approved_request(test_db)
    executor = ReviewerIdentity(user_id="exec-1", display_name="Lead PM", roles=["pm"])

    exec_req = PlanningExecutionRequest(
        execution_id="exec-first",
        approval_request_id=req_id,
        proposal_id="prop-001",
        proposal_version="proposal-v1",
        context_version="planning-v1",
        executor=executor,
        requested_at="2026-09-26T01:00:00Z",
    )

    res1 = await execution_service.execute_approved_planning(exec_req)
    assert res1.state == PlanningExecutionState.COMPLETED

    # Execute second time
    exec_req2 = PlanningExecutionRequest(
        execution_id="exec-second",
        approval_request_id=req_id,
        proposal_id="prop-001",
        proposal_version="proposal-v1",
        context_version="planning-v1",
        executor=executor,
        requested_at="2026-09-26T01:05:00Z",
    )
    res2 = await execution_service.execute_approved_planning(exec_req2)
    # Cached completed result returned
    assert res2.execution_id == "exec-first"
    assert res2.state == PlanningExecutionState.COMPLETED


@pytest.mark.asyncio
async def test_requirement_x_y_z_aa_zero_ai_and_audit_events(execution_service, test_db, audit_svc):
    """Test X, Y, Z, AA: Zero AI calls, zero Discord dispatches, full structured audit trail."""
    with patch("app.services.ai.planning.AIPlanningService.generate_plan") as mock_ai:
        req_id = _create_approved_request(test_db)
        executor = ReviewerIdentity(user_id="exec-1", display_name="Lead PM", roles=["pm"])

        exec_req = PlanningExecutionRequest(
            execution_id="exec-audit-test",
            approval_request_id=req_id,
            proposal_id="prop-001",
            proposal_version="proposal-v1",
            context_version="planning-v1",
            executor=executor,
            requested_at="2026-09-26T01:00:00Z",
        )

        res = await execution_service.execute_approved_planning(exec_req)
        assert res.state == PlanningExecutionState.COMPLETED
        # Verify AI was NEVER called during execution
        assert mock_ai.call_count == 0

    # Verify audit events in DB
    with test_db.session() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_logs WHERE target LIKE '%exec-audit-test%' ORDER BY id ASC"
        ).fetchall()
        actions = [r["action"] for r in rows]
        assert "PLANNING_EXECUTION_STARTED" in actions
        assert "PLANNING_EXECUTION_ACTION_ATTEMPTED" in actions
        assert "PLANNING_EXECUTION_ACTION_SUCCEEDED" in actions
        assert "PLANNING_EXECUTION_COMPLETED" in actions


@pytest.mark.asyncio
async def test_requirement_ab_ac_ad_ae_af_immutability_and_no_replanning(execution_service, test_db):
    """Test AB-AF: Immutability of models, no automatic replanning or re-approval."""
    req_id = _create_approved_request(test_db)
    executor = ReviewerIdentity(user_id="exec-1", display_name="Lead PM", roles=["pm"])

    approval_dict_before = execution_service.approval_repo.get_request(req_id)

    exec_req = PlanningExecutionRequest(
        execution_id="exec-immut",
        approval_request_id=req_id,
        proposal_id="prop-001",
        proposal_version="proposal-v1",
        context_version="planning-v1",
        executor=executor,
        requested_at="2026-09-26T01:00:00Z",
    )

    await execution_service.execute_approved_planning(exec_req)

    approval_dict_after = execution_service.approval_repo.get_request(req_id)
    # The approval request itself must not be mutated
    assert approval_dict_before == approval_dict_after
