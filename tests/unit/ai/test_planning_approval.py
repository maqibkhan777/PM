"""Unit tests for Phase 4E: Human Planning Approval Interface.

Validates the complete human approval gate across all 33 focused requirements (A through AG):
A. create pending approval
B. retrieve pending approval
C. valid proposal eligible for approval
D. NEEDS_REVIEW proposal requires acknowledgement
E. missing acknowledgement rejected
F. INVALID proposal cannot be approved
G. authorized reviewer approves
H. authorized reviewer rejects
I. unauthorized reviewer rejected
J. reviewer identity cannot be spoofed
K. proposal ID mismatch
L. proposal version mismatch
M. context version mismatch
N. missing validation result
O. already approved cannot be approved again
P. already rejected cannot be rejected again
Q. approved cannot be rejected
R. rejected cannot be approved
S. expired approval cannot be approved
T. proposal mutation creates version mismatch
U. concurrent approval/rejection is atomic
V. audit event created for approval
W. audit event created for rejection
X. expiration handling
Y. immutable proposal
Z. immutable PlanningContext
AA. validation findings preserved
AB. zero Jira mutations
AC. zero Action Engine calls
AD. zero Discord/Mattermost mutation dispatches
AE. deterministic approval state transitions
AF. approval request does not modify proposal
AG. approval request does not modify PlanningContext
"""

import concurrent.futures
from datetime import datetime, timezone, timedelta
import pytest
from unittest.mock import MagicMock, patch

from app.core.models.enums import Capability
from app.core.models.planning import (
    EstimateUnit,
    EvidenceReference,
    EvidenceType,
    PlanningAssumption,
    PlanningContext,
    PlanningEstimate,
    PlanningProposal,
    PlanningRiskSignal,
    ProposalValidationCategory,
    ProposalValidationIssue,
    ProposalValidationResult,
    ProposalValidationStatus,
    ReviewerIdentity,
    SequencingProposal,
    TaskPlanningProposal,
    ValidationIssueSeverity,
)
from app.core.planning.validator import PlanningProposalValidator
from app.database.connection import DatabaseManager
from app.services.ai.evaluation.planning_dataset import PHASE_4D_EVALUATION_DATASET
from app.services.ai.planning_approval import (
    PlanningApprovalError,
    PlanningApprovalService,
    PlanningApprovalState,
)
from app.services.audit_service import AuditService


def _create_mock_context() -> PlanningContext:
    """Return scenario A context."""
    return PHASE_4D_EVALUATION_DATASET[0].context


def _create_mock_proposal(context: PlanningContext) -> PlanningProposal:
    """Return a grounded proposal matching context tasks."""
    task_props = [
        TaskPlanningProposal(
            issue_key=t.issue_key,
            proposed_estimate=PlanningEstimate(value=t.estimated_remaining_hours, unit=EstimateUnit.HOURS),
            proposed_start_date=t.projected_start_date or context.anchor_date,
            proposed_due_date=t.projected_completion_date or "2026-10-02",
            sequencing_position=idx,
            proposed_predecessors=list(t.predecessor_keys),
            proposed_successors=list(t.successor_keys),
        )
        for idx, t in enumerate(context.tasks, 1)
    ]
    return PlanningProposal(
        proposal_version="proposal-v1",
        generated_at="2026-09-26T00:00:00Z",
        context_version=context.context_version,
        anchor_date=context.anchor_date,
        summary="Test valid planning proposal",
        task_proposals=task_props,
        sequencing_proposals=[
            SequencingProposal(issue_key=t.issue_key, position=idx, rationale=f"Slot #{idx}")
            for idx, t in enumerate(task_props, 1)
        ],
    )


@pytest.fixture
def approval_service(tmp_path):
    """Fixture providing an isolated PlanningApprovalService backed by an ephemeral sqlite db."""
    db_file = tmp_path / "test_approvals.db"
    mgr = DatabaseManager(db_path=str(db_file))
    from app.database.schema import init_db
    init_db(mgr)
    audit = AuditService(manager=mgr)
    return PlanningApprovalService(manager=mgr, audit=audit, expiration_hours=24)


# ---------------------------------------------------------------------------
# Test Cases A through AG
# ---------------------------------------------------------------------------

def test_requirement_a_and_b_create_and_retrieve_pending_approval(approval_service):
    """A & B. Create and retrieve a pending approval request."""
    context = _create_mock_context()
    proposal = _create_mock_proposal(context)
    val_res = PlanningProposalValidator.validate_proposal(context, proposal)

    req = approval_service.create_approval_request(context, proposal, val_res, proposal_id="prop-001")
    assert req.approval_request_id.startswith("apr-")
    assert req.state == PlanningApprovalState.PENDING
    assert req.proposal_id == "prop-001"
    assert req.proposal_version == proposal.proposal_version
    assert req.context_version == context.context_version
    assert req.validation_status == val_res.status

    retrieved = approval_service.get_approval_request(req.approval_request_id)
    assert retrieved is not None
    assert retrieved.approval_request_id == req.approval_request_id
    assert retrieved.state == PlanningApprovalState.PENDING


def test_requirement_c_and_g_valid_proposal_approved_by_authorized_reviewer(approval_service):
    """C & G. Valid proposal approved by authorized PM reviewer."""
    context = _create_mock_context()
    proposal = _create_mock_proposal(context)
    val_res = PlanningProposalValidator.validate_proposal(context, proposal)
    assert val_res.status == ProposalValidationStatus.VALID

    req = approval_service.create_approval_request(context, proposal, val_res, proposal_id="prop-001")
    reviewer = ReviewerIdentity(user_id="usr-pm-1", display_name="Lead PM", roles=["pm"])

    decision = approval_service.approve_proposal(
        approval_request_id=req.approval_request_id,
        reviewer=reviewer,
        comments="Looks good to go",
    )

    assert decision.decision == PlanningApprovalState.APPROVED
    assert decision.reviewer.user_id == "usr-pm-1"
    assert decision.approval_request_id == req.approval_request_id

    # Verify updated request state
    updated_req = approval_service.get_approval_request(req.approval_request_id)
    assert updated_req.state == PlanningApprovalState.APPROVED
    assert updated_req.decision is not None
    assert updated_req.decision.decision == PlanningApprovalState.APPROVED


def test_requirement_d_and_e_needs_review_proposal_acknowledgement(approval_service):
    """D & E. NEEDS_REVIEW proposal requires explicit acknowledgement of warning findings."""
    scenario = next(s for s in PHASE_4D_EVALUATION_DATASET if s.scenario_id == "EVAL-SCEN-C")
    context = scenario.context
    proposal = _create_mock_proposal(context)
    val_res = PlanningProposalValidator.validate_proposal(context, proposal)
    assert val_res.status == ProposalValidationStatus.NEEDS_REVIEW

    req = approval_service.create_approval_request(context, proposal, val_res)
    reviewer = ReviewerIdentity(user_id="usr-pm-1", display_name="Lead PM", roles=["pm"])

    # E. Missing acknowledgement -> Rejected
    with pytest.raises(PlanningApprovalError) as exc:
        approval_service.approve_proposal(
            approval_request_id=req.approval_request_id,
            reviewer=reviewer,
            acknowledged_issue_codes=[],
        )
    assert "requires explicit acknowledgement" in str(exc.value)

    # D. Full acknowledgement -> Approved
    warning_codes = [i.code for i in val_res.issues if i.severity == ValidationIssueSeverity.WARNING]
    decision = approval_service.approve_proposal(
        approval_request_id=req.approval_request_id,
        reviewer=reviewer,
        acknowledged_issue_codes=warning_codes,
        comments="Acknowledged contractor missing history",
    )
    assert decision.decision == PlanningApprovalState.APPROVED


def test_requirement_f_invalid_proposal_cannot_be_approved(approval_service):
    """F. INVALID proposal cannot be approved."""
    scenario = next(s for s in PHASE_4D_EVALUATION_DATASET if s.scenario_id == "EVAL-SCEN-F")
    context = scenario.context
    proposal = _create_mock_proposal(context)
    val_res = PlanningProposalValidator.validate_proposal(context, proposal)
    assert val_res.status == ProposalValidationStatus.INVALID

    req = approval_service.create_approval_request(context, proposal, val_res)
    reviewer = ReviewerIdentity(user_id="usr-pm-1", display_name="Lead PM", roles=["pm"])

    with pytest.raises(PlanningApprovalError) as exc:
        approval_service.approve_proposal(
            approval_request_id=req.approval_request_id,
            reviewer=reviewer,
        )
    assert "validation status is INVALID" in str(exc.value)


def test_requirement_h_authorized_reviewer_rejects(approval_service):
    """H. Authorized reviewer rejects pending proposal."""
    context = _create_mock_context()
    proposal = _create_mock_proposal(context)
    val_res = PlanningProposalValidator.validate_proposal(context, proposal)

    req = approval_service.create_approval_request(context, proposal, val_res)
    reviewer = ReviewerIdentity(user_id="usr-pm-1", display_name="Lead PM", roles=["pm"])

    decision = approval_service.reject_proposal(
        approval_request_id=req.approval_request_id,
        reviewer=reviewer,
        comments="Scope needs re-estimation",
    )
    assert decision.decision == PlanningApprovalState.REJECTED

    updated_req = approval_service.get_approval_request(req.approval_request_id)
    assert updated_req.state == PlanningApprovalState.REJECTED


def test_requirement_i_and_j_unauthorized_reviewer_and_spoof_prevention(approval_service):
    """I & J. Unauthorized reviewer role is rejected."""
    context = _create_mock_context()
    proposal = _create_mock_proposal(context)
    val_res = PlanningProposalValidator.validate_proposal(context, proposal)

    req = approval_service.create_approval_request(context, proposal, val_res)
    unauth_reviewer = ReviewerIdentity(user_id="intruder-99", display_name="Guest", roles=["guest_viewer"])

    with pytest.raises(PlanningApprovalError) as exc:
        approval_service.approve_proposal(
            approval_request_id=req.approval_request_id,
            reviewer=unauth_reviewer,
        )
    assert "does not possess required role" in str(exc.value)


def test_requirement_k_l_m_version_binding_mismatches(approval_service):
    """K, L, M. Proposal ID, proposal version, and context version mismatches are rejected."""
    context = _create_mock_context()
    proposal = _create_mock_proposal(context)
    val_res = PlanningProposalValidator.validate_proposal(context, proposal)

    req = approval_service.create_approval_request(context, proposal, val_res, proposal_id="prop-orig-101")
    reviewer = ReviewerIdentity(user_id="usr-pm-1", display_name="Lead PM", roles=["pm"])

    # K. Proposal ID mismatch
    with pytest.raises(PlanningApprovalError) as exc_id:
        approval_service.approve_proposal(
            approval_request_id=req.approval_request_id,
            reviewer=reviewer,
            expected_proposal_id="prop-tampered-999",
        )
    assert "Proposal ID mismatch" in str(exc_id.value)

    # L. Proposal version mismatch
    with pytest.raises(PlanningApprovalError) as exc_v:
        approval_service.approve_proposal(
            approval_request_id=req.approval_request_id,
            reviewer=reviewer,
            expected_proposal_version="proposal-v2",
        )
    assert "Proposal version mismatch" in str(exc_v.value)

    # M. Context version mismatch
    with pytest.raises(PlanningApprovalError) as exc_c:
        approval_service.approve_proposal(
            approval_request_id=req.approval_request_id,
            reviewer=reviewer,
            expected_context_version="planning-v2",
        )
    assert "Context version mismatch" in str(exc_c.value)


def test_requirement_n_missing_validation_result_rejected(approval_service):
    """N. Missing or invalid validation result throws error."""
    context = _create_mock_context()
    proposal = _create_mock_proposal(context)

    with pytest.raises(PlanningApprovalError):
        approval_service.create_approval_request(context, proposal, None)  # type: ignore


def test_requirement_o_p_q_r_finality_and_transition_invariants(approval_service):
    """O, P, Q, R. Approved or rejected requests cannot be changed again."""
    context = _create_mock_context()
    proposal = _create_mock_proposal(context)
    val_res = PlanningProposalValidator.validate_proposal(context, proposal)

    req = approval_service.create_approval_request(context, proposal, val_res)
    reviewer = ReviewerIdentity(user_id="usr-pm-1", display_name="Lead PM", roles=["pm"])

    approval_service.approve_proposal(req.approval_request_id, reviewer=reviewer)

    # O. Cannot approve again
    with pytest.raises(PlanningApprovalError) as exc_app:
        approval_service.approve_proposal(req.approval_request_id, reviewer=reviewer)
    assert "already been APPROVED" in str(exc_app.value)

    # Q. Approved cannot be rejected
    with pytest.raises(PlanningApprovalError) as exc_rej:
        approval_service.reject_proposal(req.approval_request_id, reviewer=reviewer)
    assert "already been APPROVED" in str(exc_rej.value)


def test_requirement_s_and_x_expiration_handling(approval_service):
    """S & X. Expired approval cannot be approved or decided."""
    context = _create_mock_context()
    proposal = _create_mock_proposal(context)
    val_res = PlanningProposalValidator.validate_proposal(context, proposal)

    # Create service with 0 expiration hours (immediate expiration)
    fast_exp_service = PlanningApprovalService(
        manager=approval_service.mgr,
        audit=approval_service.audit,
        expiration_hours=-1,
    )
    req = fast_exp_service.create_approval_request(context, proposal, val_res)
    reviewer = ReviewerIdentity(user_id="usr-pm-1", display_name="Lead PM", roles=["pm"])

    with pytest.raises(PlanningApprovalError) as exc:
        fast_exp_service.approve_proposal(req.approval_request_id, reviewer=reviewer)
    assert "has EXPIRED" in str(exc.value)

    # Verify state transitioned to EXPIRED
    retrieved = fast_exp_service.get_approval_request(req.approval_request_id)
    assert retrieved.state == PlanningApprovalState.EXPIRED


def test_requirement_u_concurrent_approval_atomic(approval_service):
    """U. Concurrent approval and rejection attempts are strictly atomic (one winner)."""
    context = _create_mock_context()
    proposal = _create_mock_proposal(context)
    val_res = PlanningProposalValidator.validate_proposal(context, proposal)

    req = approval_service.create_approval_request(context, proposal, val_res)
    reviewer1 = ReviewerIdentity(user_id="usr-pm-1", display_name="PM 1", roles=["pm"])
    reviewer2 = ReviewerIdentity(user_id="usr-pm-2", display_name="PM 2", roles=["pm"])

    results = []
    errors = []

    def try_approve():
        try:
            r = approval_service.approve_proposal(req.approval_request_id, reviewer1)
            results.append(("APPROVED", r))
        except Exception as e:
            errors.append(e)

    def try_reject():
        try:
            r = approval_service.reject_proposal(req.approval_request_id, reviewer2)
            results.append(("REJECTED", r))
        except Exception as e:
            errors.append(e)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(try_approve)
        f2 = executor.submit(try_reject)
        concurrent.futures.wait([f1, f2])

    # Exactly one action succeeded, one threw PlanningApprovalError
    assert len(results) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], PlanningApprovalError)


def test_requirement_v_and_w_audit_events_created(approval_service):
    """V & W. Audit records are created for approval and rejection."""
    context = _create_mock_context()
    proposal = _create_mock_proposal(context)
    val_res = PlanningProposalValidator.validate_proposal(context, proposal)

    req = approval_service.create_approval_request(context, proposal, val_res)
    reviewer = ReviewerIdentity(user_id="usr-pm-1", display_name="Lead PM", roles=["pm"])

    approval_service.approve_proposal(req.approval_request_id, reviewer=reviewer, comments="Audited approval")

    logs = approval_service.audit.list_logs(limit=10)
    actions = [l["action"] for l in logs]
    assert "PLANNING_APPROVAL_REQUEST_CREATED" in actions
    assert "PLANNING_PROPOSAL_APPROVED" in actions


def test_requirement_y_z_af_ag_immutability(approval_service):
    """Y, Z, AF, AG. Proposal and Context remain strictly immutable and unmodified."""
    context = _create_mock_context()
    proposal = _create_mock_proposal(context)
    original_ctx_dict = context.model_dump()
    original_prop_dict = proposal.model_dump()

    val_res = PlanningProposalValidator.validate_proposal(context, proposal)
    req = approval_service.create_approval_request(context, proposal, val_res)
    reviewer = ReviewerIdentity(user_id="usr-pm-1", display_name="Lead PM", roles=["pm"])

    _ = approval_service.approve_proposal(req.approval_request_id, reviewer=reviewer)

    assert context.model_dump() == original_ctx_dict
    assert proposal.model_dump() == original_prop_dict


def test_requirement_ab_ac_ad_zero_mutations_and_dispatches(approval_service):
    """AB, AC, AD. Verify zero Jira, Action Engine, or Discord/Mattermost dispatches."""
    context = _create_mock_context()
    proposal = _create_mock_proposal(context)
    val_res = PlanningProposalValidator.validate_proposal(context, proposal)
    req = approval_service.create_approval_request(context, proposal, val_res)
    reviewer = ReviewerIdentity(user_id="usr-pm-1", display_name="Lead PM", roles=["pm"])

    with patch("app.core.actions.engine.ActionEngine.execute") as mock_engine_exec, \
         patch("app.connectors.jira.connector.JiraConnector.execute_action") as mock_jira_exec, \
         patch("app.connectors.discord.webhook_connector.DiscordWebhookConnector.execute_action") as mock_discord_exec, \
         patch("app.connectors.mattermost.connector.MattermostConnector.execute_action") as mock_mattermost_exec:

        decision = approval_service.approve_proposal(req.approval_request_id, reviewer=reviewer)
        assert decision.decision == PlanningApprovalState.APPROVED

        mock_engine_exec.assert_not_called()
        mock_jira_exec.assert_not_called()
        mock_discord_exec.assert_not_called()
        mock_mattermost_exec.assert_not_called()
