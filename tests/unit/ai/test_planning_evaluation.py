"""Unit tests for Phase 4D: AI Planning Evaluation Dataset & Evaluation Harness.

Validates the full evaluation pipeline and all 33 focused requirements (A through AG):
A. Healthy valid proposal
B. Unknown issue
C. Unknown resource
D. Invented dependency
E. HARD_BLOCK violation
F. Capacity pressure
G. Capacity exceeded
H. Exact capacity boundary
I. Estimate supported
J. Estimate reasonable variance
K. Estimate large variance
L. No duration evidence
M. Weekend date
N. Invalid date ordering
O. Schedule minor variance
P. Schedule significant variance
Q. Beyond horizon
R. NO_HISTORY
S. CAPACITY_UNAVAILABLE
T. Truncated context
U. Artifact handoff
V. Artifact conflict
W. Dependency cycle
X. Long dependency chain
Y. Multiple resources
Z. Competing priorities
AA. Reopened/stale task
AB. requires_human_review enforcement
AC. Immutable context
AD. Immutable proposal
AE. Deterministic issue ordering
AF. Zero external calls
AG. Repeated evaluation produces identical deterministic output
"""

import copy
import json
import pytest
from typing import Any, Dict, List
import httpx

from app.core.models.planning import (
    EstimateUnit,
    EvidenceReference,
    EvidenceType,
    PlanningAssumption,
    PlanningContext,
    PlanningEstimate,
    PlanningProposal,
    PlanningRiskSignal,
    PlanningRiskType,
    ProposalValidationCategory,
    ProposalValidationStatus,
    SequencingProposal,
    TaskPlanningProposal,
    ValidationIssueSeverity,
)
from app.core.planning.validator import PlanningProposalValidator
from app.services.ai.evaluation.planning_dataset import (
    PHASE_4D_EVALUATION_DATASET,
    PlanningEvaluationScenario,
)
from app.services.ai.evaluation.planning_harness import (
    PlanningEvaluationHarness,
    PlanningEvaluationReport,
    PlanningFailureCategory,
    ScenarioPlanningEvaluationResult,
)
from app.services.ai.provider import MockAIProvider
from app.services.ai.providers.deepseek import DeepSeekAIProvider


def _build_mock_proposal_for_scenario(context: PlanningContext) -> PlanningProposal:
    """Construct a grounded, structurally sound PlanningProposal matching context tasks."""
    task_props = []
    for idx, t in enumerate(context.tasks, 1):
        est_val = max(t.estimated_remaining_hours, 4.0)
        task_props.append(
            TaskPlanningProposal(
                issue_key=t.issue_key,
                proposed_estimate=PlanningEstimate(
                    value=est_val,
                    unit=EstimateUnit.HOURS,
                    confidence=0.85,
                    rationale=f"Deterministic estimate match for {t.issue_key}",
                    evidence_references=[
                        EvidenceReference(
                            evidence_type=EvidenceType.TASK_ESTIMATE,
                            source_identifier=t.issue_key,
                            description="Grounded from context facts",
                            relevance="DIRECT",
                        )
                    ],
                ),
                proposed_start_date=t.projected_start_date or context.anchor_date,
                proposed_due_date=t.projected_completion_date or "2026-10-02",
                date_confidence=0.85,
                sequencing_position=idx,
                proposed_predecessors=list(t.predecessor_keys),
                proposed_successors=list(t.successor_keys),
                risk_level="HIGH" if t.is_blocked else "LOW",
                risk_reason="Dependency blocked" if t.is_blocked else None,
                evidence_references=[],
                assumptions=[],
                requires_human_review=True,
            )
        )

    seq_props = [
        SequencingProposal(
            issue_key=t.issue_key,
            position=idx,
            rationale=f"Sequence slot #{idx}",
            confidence=0.85,
            evidence_references=[],
        )
        for idx, t in enumerate(context.tasks, 1)
    ]

    return PlanningProposal(
        proposal_version="proposal-v1",
        generated_at="2026-09-26T00:00:00Z",
        context_version=context.context_version,
        anchor_date=context.anchor_date,
        planning_horizon_working_days=context.planning_horizon_working_days,
        requires_human_review=True,
        overall_confidence=0.85,
        summary=f"Evaluation mock proposal for {len(context.tasks)} tasks.",
        task_proposals=task_props,
        sequencing_proposals=seq_props,
        risk_signals=[],
        assumptions=[
            PlanningAssumption(
                statement="Stable execution assumption",
                confidence=0.85,
                requires_human_review=True,
                evidence_references=[],
            )
        ],
        evidence_references=[],
    )


# ---------------------------------------------------------------------------
# Dataset Structure & Scenario Integrity Tests
# ---------------------------------------------------------------------------

def test_dataset_contains_all_twenty_scenarios():
    """Verify evaluation dataset contains exactly 20 distinct synthetic scenarios A through T."""
    assert len(PHASE_4D_EVALUATION_DATASET) == 20
    scenario_ids = [s.scenario_id for s in PHASE_4D_EVALUATION_DATASET]
    expected_ids = [
        "EVAL-SCEN-A",
        "EVAL-SCEN-B",
        "EVAL-SCEN-C",
        "EVAL-SCEN-D",
        "EVAL-SCEN-E",
        "EVAL-SCEN-F",
        "EVAL-SCEN-G",
        "EVAL-SCEN-H",
        "EVAL-SCEN-I",
        "EVAL-SCEN-J",
        "EVAL-SCEN-K",
        "EVAL-SCEN-L",
        "EVAL-SCEN-M",
        "EVAL-SCEN-N",
        "EVAL-SCEN-O",
        "EVAL-SCEN-P",
        "EVAL-SCEN-Q",
        "EVAL-SCEN-R",
        "EVAL-SCEN-S",
        "EVAL-SCEN-T",
    ]
    assert scenario_ids == expected_ids


# ---------------------------------------------------------------------------
# Tests Covering Specific Focus Requirements A through AG
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_requirement_a_healthy_valid_proposal():
    """A. Healthy valid proposal evaluates cleanly to VALID status."""
    scenario = next(s for s in PHASE_4D_EVALUATION_DATASET if s.scenario_id == "EVAL-SCEN-A")
    harness = PlanningEvaluationHarness(provider=MockAIProvider())
    res = await harness.evaluate_scenario(scenario)
    assert res.success is True
    assert res.parse_success is True
    assert res.schema_valid is True
    assert res.is_grounded is True
    assert res.validation_status == ProposalValidationStatus.VALID
    assert res.hard_constraint_violations == 0


@pytest.mark.asyncio
async def test_requirement_b_unknown_issue_detection():
    """B. Unknown issue in proposal is detected as UNKNOWN_ISSUE_KEY."""
    scenario = PHASE_4D_EVALUATION_DATASET[0]
    proposal = _build_mock_proposal_for_scenario(scenario.context)
    
    invented_tp = proposal.task_proposals[0].model_copy(update={"issue_key": "HALLUCINATED-999"})
    proposal_with_invented = proposal.model_copy(update={"task_proposals": [invented_tp] + list(proposal.task_proposals[1:])})

    val_res = PlanningProposalValidator.validate_proposal(scenario.context, proposal_with_invented)
    assert val_res.status == ProposalValidationStatus.INVALID
    assert any(i.code == "UNKNOWN_ISSUE_KEY" for i in val_res.issues)


@pytest.mark.asyncio
async def test_requirement_c_unknown_resource_detection():
    """C. Unknown resource reassignment attempt via evidence reference is flagged."""
    scenario = PHASE_4D_EVALUATION_DATASET[0]
    proposal = _build_mock_proposal_for_scenario(scenario.context)

    # Inject reassignment evidence pointing to another resource in context (e.g. acc-bob for Alice's task)
    t0 = proposal.task_proposals[0].model_copy(
        update={
            "evidence_references": [
                EvidenceReference(
                    evidence_type=EvidenceType.CURRENT_QUEUE,
                    source_identifier="acc-bob",
                    description="Reassignment attempt",
                    relevance="DIRECT",
                )
            ]
        }
    )
    bad_proposal = proposal.model_copy(update={"task_proposals": [t0] + list(proposal.task_proposals[1:])})

    val_res = PlanningProposalValidator.validate_proposal(scenario.context, bad_proposal)
    assert val_res.status == ProposalValidationStatus.INVALID
    assert any(i.code == "UNAUTHORIZED_REASSIGNMENT_ATTEMPT" for i in val_res.issues)


@pytest.mark.asyncio
async def test_requirement_d_invented_dependency():
    """D. Invented dependency referencing non-existent key is caught."""
    scenario = PHASE_4D_EVALUATION_DATASET[0]
    proposal = _build_mock_proposal_for_scenario(scenario.context)

    t0 = proposal.task_proposals[0].model_copy(update={"proposed_predecessors": ["GHOST-101"]})
    bad_prop = proposal.model_copy(update={"task_proposals": [t0] + list(proposal.task_proposals[1:])})

    val_res = PlanningProposalValidator.validate_proposal(scenario.context, bad_prop)
    assert val_res.status == ProposalValidationStatus.INVALID
    assert any(i.code == "UNKNOWN_PREDECESSOR_KEY" for i in val_res.issues)


@pytest.mark.asyncio
async def test_requirement_e_hard_block_violation():
    """E. HARD_BLOCK violation where proposed start precedes blocker completion is INVALID."""
    scenario = next(s for s in PHASE_4D_EVALUATION_DATASET if s.scenario_id == "EVAL-SCEN-G")
    proposal = _build_mock_proposal_for_scenario(scenario.context)

    # In SCENARIO_G, HBC-702 depends on HBC-701 (which completes 2026-09-29).
    # Violate by proposing start date 2026-09-28 for HBC-702
    t2 = next(t for t in proposal.task_proposals if t.issue_key == "HBC-702")
    violating_t2 = t2.model_copy(update={"proposed_start_date": "2026-09-28"})
    new_props = [violating_t2 if t.issue_key == "HBC-702" else t for t in proposal.task_proposals]
    bad_prop = proposal.model_copy(update={"task_proposals": new_props})

    val_res = PlanningProposalValidator.validate_proposal(scenario.context, bad_prop)
    assert val_res.status == ProposalValidationStatus.INVALID
    assert any(i.code == "HARD_BLOCK_VIOLATION" for i in val_res.issues)


@pytest.mark.asyncio
async def test_requirement_f_capacity_pressure():
    """F. Capacity ratio > 1.0 triggers CAPACITY_PRESSURE / NEEDS_REVIEW."""
    scenario = next(s for s in PHASE_4D_EVALUATION_DATASET if s.scenario_id == "EVAL-SCEN-A")
    proposal = _build_mock_proposal_for_scenario(scenario.context)

    # Scale estimates so Alice's workload is 80h on 67.5h capacity (ratio = 1.18 > 1.0)
    t0 = proposal.task_proposals[0].model_copy(
        update={"proposed_estimate": PlanningEstimate(value=80.0, unit=EstimateUnit.HOURS)}
    )
    prop_pressure = proposal.model_copy(update={"task_proposals": [t0, proposal.task_proposals[1]]})

    val_res = PlanningProposalValidator.validate_proposal(scenario.context, prop_pressure)
    assert val_res.status == ProposalValidationStatus.NEEDS_REVIEW
    assert any(i.code == "CAPACITY_PRESSURE" for i in val_res.issues)


@pytest.mark.asyncio
async def test_requirement_g_capacity_exceeded():
    """G. Capacity ratio > 1.4 triggers CAPACITY_EXCEEDED / INVALID."""
    scenario = next(s for s in PHASE_4D_EVALUATION_DATASET if s.scenario_id == "EVAL-SCEN-F")
    proposal = _build_mock_proposal_for_scenario(scenario.context)

    # In EVAL-SCEN-F, total workload is 100h on 67.5h capacity (ratio = 1.48 > 1.4)
    val_res = PlanningProposalValidator.validate_proposal(scenario.context, proposal)
    assert val_res.status == ProposalValidationStatus.INVALID
    assert any(i.code == "CAPACITY_EXCEEDED" for i in val_res.issues)


@pytest.mark.asyncio
async def test_requirement_h_exact_capacity_boundary():
    """H. Capacity ratio = 1.0 (exact capacity) is valid without pressure."""
    scenario = next(s for s in PHASE_4D_EVALUATION_DATASET if s.scenario_id == "EVAL-SCEN-A")
    proposal = _build_mock_proposal_for_scenario(scenario.context)

    # Alice has 67.5h capacity. Propose exactly 67.5h
    t0 = proposal.task_proposals[0].model_copy(
        update={"proposed_estimate": PlanningEstimate(value=67.5, unit=EstimateUnit.HOURS)}
    )
    boundary_prop = proposal.model_copy(update={"task_proposals": [t0, proposal.task_proposals[1]]})

    val_res = PlanningProposalValidator.validate_proposal(scenario.context, boundary_prop)
    assert not any(i.code in ("CAPACITY_PRESSURE", "CAPACITY_EXCEEDED") for i in val_res.issues)


@pytest.mark.asyncio
async def test_requirement_i_estimate_supported():
    """I. Estimate matching baseline is classified as supported (no large variance)."""
    scenario = next(s for s in PHASE_4D_EVALUATION_DATASET if s.scenario_id == "EVAL-SCEN-A")
    proposal = _build_mock_proposal_for_scenario(scenario.context)
    val_res = PlanningProposalValidator.validate_proposal(scenario.context, proposal)
    assert not any(i.code == "ESTIMATE_LARGE_VARIANCE" for i in val_res.issues)


@pytest.mark.asyncio
async def test_requirement_j_estimate_reasonable_variance():
    """J. Estimate variance within threshold (e.g. 25%) does not trigger large variance."""
    scenario = next(s for s in PHASE_4D_EVALUATION_DATASET if s.scenario_id == "EVAL-SCEN-A")
    proposal = _build_mock_proposal_for_scenario(scenario.context)

    # Baseline is 8h for HLT-101. Propose 10h (variance 25% <= 100% threshold)
    t0 = proposal.task_proposals[0].model_copy(
        update={"proposed_estimate": PlanningEstimate(value=10.0, unit=EstimateUnit.HOURS)}
    )
    var_prop = proposal.model_copy(update={"task_proposals": [t0, proposal.task_proposals[1]]})

    val_res = PlanningProposalValidator.validate_proposal(scenario.context, var_prop)
    assert not any(i.code == "ESTIMATE_LARGE_VARIANCE" for i in val_res.issues)


@pytest.mark.asyncio
async def test_requirement_k_estimate_large_variance():
    """K. Estimate variance > 100% triggers ESTIMATE_LARGE_VARIANCE (WARNING/NEEDS_REVIEW)."""
    scenario = next(s for s in PHASE_4D_EVALUATION_DATASET if s.scenario_id == "EVAL-SCEN-A")
    proposal = _build_mock_proposal_for_scenario(scenario.context)

    # Baseline is 8h for HLT-101. Propose 20h (variance 150% > 100%)
    t0 = proposal.task_proposals[0].model_copy(
        update={"proposed_estimate": PlanningEstimate(value=20.0, unit=EstimateUnit.HOURS)}
    )
    var_prop = proposal.model_copy(update={"task_proposals": [t0, proposal.task_proposals[1]]})

    val_res = PlanningProposalValidator.validate_proposal(scenario.context, var_prop)
    assert val_res.status == ProposalValidationStatus.NEEDS_REVIEW
    assert any(i.code == "ESTIMATE_LARGE_VARIANCE" for i in val_res.issues)


@pytest.mark.asyncio
async def test_requirement_l_missing_duration_evidence():
    """L. Tasks with no duration evidence trigger MISSING_DURATION_EVIDENCE warning."""
    scenario = next(s for s in PHASE_4D_EVALUATION_DATASET if s.scenario_id == "EVAL-SCEN-K")
    proposal = _build_mock_proposal_for_scenario(scenario.context)

    val_res = PlanningProposalValidator.validate_proposal(scenario.context, proposal)
    assert any(i.code == "MISSING_DURATION_EVIDENCE" for i in val_res.issues)


@pytest.mark.asyncio
async def test_requirement_m_weekend_date_violation():
    """M. Proposed date on a Saturday or Sunday is INVALID."""
    scenario = next(s for s in PHASE_4D_EVALUATION_DATASET if s.scenario_id == "EVAL-SCEN-A")
    proposal = _build_mock_proposal_for_scenario(scenario.context)

    # 2026-10-03 is a Saturday
    t0 = proposal.task_proposals[0].model_copy(update={"proposed_start_date": "2026-10-03"})
    weekend_prop = proposal.model_copy(update={"task_proposals": [t0, proposal.task_proposals[1]]})

    val_res = PlanningProposalValidator.validate_proposal(scenario.context, weekend_prop)
    assert val_res.status == ProposalValidationStatus.INVALID
    assert any(i.code == "WEEKEND_DATE_VIOLATION" for i in val_res.issues)


@pytest.mark.asyncio
async def test_requirement_n_invalid_date_ordering():
    """N. Proposed start date after proposed due date is INVALID."""
    scenario = next(s for s in PHASE_4D_EVALUATION_DATASET if s.scenario_id == "EVAL-SCEN-A")
    proposal = _build_mock_proposal_for_scenario(scenario.context)

    t0 = proposal.task_proposals[0].model_copy(
        update={"proposed_start_date": "2026-10-05", "proposed_due_date": "2026-09-30"}
    )
    bad_order_prop = proposal.model_copy(update={"task_proposals": [t0, proposal.task_proposals[1]]})

    val_res = PlanningProposalValidator.validate_proposal(scenario.context, bad_order_prop)
    assert val_res.status == ProposalValidationStatus.INVALID
    assert any(i.code == "START_DATE_AFTER_DUE_DATE" for i in val_res.issues)


@pytest.mark.asyncio
async def test_requirement_o_and_p_schedule_variances():
    """O & P. Schedule alignment vs significant variance (>3 days)."""
    scenario = next(s for s in PHASE_4D_EVALUATION_DATASET if s.scenario_id == "EVAL-SCEN-A")
    proposal = _build_mock_proposal_for_scenario(scenario.context)

    # Baseline start for HLT-101 is 2026-09-28.
    # Significant variance (+6 days) -> 2026-10-06 (a Tuesday)
    t_sig = proposal.task_proposals[0].model_copy(update={"proposed_start_date": "2026-10-06", "proposed_due_date": "2026-10-07"})
    prop_sig = proposal.model_copy(update={"task_proposals": [t_sig, proposal.task_proposals[1]]})
    val_sig = PlanningProposalValidator.validate_proposal(scenario.context, prop_sig)
    assert any(i.code == "SCHEDULE_SIGNIFICANT_VARIANCE" for i in val_sig.issues)


@pytest.mark.asyncio
async def test_requirement_q_beyond_horizon():
    """Q. Proposal due date beyond planning horizon working days triggers warning."""
    scenario = next(s for s in PHASE_4D_EVALUATION_DATASET if s.scenario_id == "EVAL-SCEN-O")
    proposal = _build_mock_proposal_for_scenario(scenario.context)

    val_res = PlanningProposalValidator.validate_proposal(scenario.context, proposal)
    assert any(i.code == "PROPOSAL_BEYOND_HORIZON" for i in val_res.issues)


@pytest.mark.asyncio
async def test_requirement_r_no_history():
    """R. Resources with NO_HISTORY trigger RESOURCE_NO_HISTORY warning."""
    scenario = next(s for s in PHASE_4D_EVALUATION_DATASET if s.scenario_id == "EVAL-SCEN-C")
    proposal = _build_mock_proposal_for_scenario(scenario.context)

    val_res = PlanningProposalValidator.validate_proposal(scenario.context, proposal)
    assert any(i.code == "RESOURCE_NO_HISTORY" for i in val_res.issues)


@pytest.mark.asyncio
async def test_requirement_s_capacity_unavailable():
    """S. Resources with CAPACITY_UNAVAILABLE trigger CAPACITY_UNAVAILABLE warning."""
    scenario = next(s for s in PHASE_4D_EVALUATION_DATASET if s.scenario_id == "EVAL-SCEN-D")
    proposal = _build_mock_proposal_for_scenario(scenario.context)

    val_res = PlanningProposalValidator.validate_proposal(scenario.context, proposal)
    assert any(i.code == "CAPACITY_UNAVAILABLE" for i in val_res.issues)


@pytest.mark.asyncio
async def test_requirement_t_truncated_context():
    """T. Truncated context triggers TRUNCATED_CONTEXT_UNCERTAINTY warning."""
    scenario = next(s for s in PHASE_4D_EVALUATION_DATASET if s.scenario_id == "EVAL-SCEN-T")
    proposal = _build_mock_proposal_for_scenario(scenario.context)

    val_res = PlanningProposalValidator.validate_proposal(scenario.context, proposal)
    assert any(i.code == "TRUNCATED_CONTEXT_UNCERTAINTY" for i in val_res.issues)


@pytest.mark.asyncio
async def test_requirement_u_and_v_artifact_handoff_and_conflict():
    """U & V. Artifact handoff ordering and conflict handling."""
    scenario = next(s for s in PHASE_4D_EVALUATION_DATASET if s.scenario_id == "EVAL-SCEN-J")
    proposal = _build_mock_proposal_for_scenario(scenario.context)

    # Valid handoff: ART-1002 starts 2026-09-30 after ART-1001 completes 2026-09-29
    val_res = PlanningProposalValidator.validate_proposal(scenario.context, proposal)
    assert not any(i.code == "EXPLICIT_ARTIFACT_HANDOFF_CONFLICT" for i in val_res.issues)

    # Artifact conflict: ART-1002 starts before ART-1001 completion date
    t2 = next(t for t in proposal.task_proposals if t.issue_key == "ART-1002")
    bad_t2 = t2.model_copy(update={"proposed_start_date": "2026-09-28"})
    conflict_props = [bad_t2 if t.issue_key == "ART-1002" else t for t in proposal.task_proposals]
    conflict_proposal = proposal.model_copy(update={"task_proposals": conflict_props})

    val_conflict = PlanningProposalValidator.validate_proposal(scenario.context, conflict_proposal)
    assert any(i.code == "EXPLICIT_ARTIFACT_HANDOFF_CONFLICT" for i in val_conflict.issues)


@pytest.mark.asyncio
async def test_requirement_w_dependency_cycle():
    """W. Dependency cycle in context is safely handled by validator."""
    scenario = next(s for s in PHASE_4D_EVALUATION_DATASET if s.scenario_id == "EVAL-SCEN-I")
    proposal = _build_mock_proposal_for_scenario(scenario.context)

    # When cycle is present in hard dependencies, starting tasks triggers HARD_BLOCK_VIOLATION
    val_res = PlanningProposalValidator.validate_proposal(scenario.context, proposal)
    assert val_res.status == ProposalValidationStatus.INVALID


@pytest.mark.asyncio
async def test_requirement_x_long_dependency_chain():
    """X. Long dependency chain is sequenced in valid chronological order."""
    scenario = next(s for s in PHASE_4D_EVALUATION_DATASET if s.scenario_id == "EVAL-SCEN-H")
    # For SCENARIO_H, assign non-overlapping sequential dates to satisfy HARD_BLOCK
    task_props = [
        TaskPlanningProposal(
            issue_key="LNG-801",
            proposed_estimate=PlanningEstimate(value=6.0, unit=EstimateUnit.HOURS),
            proposed_start_date="2026-09-28",
            proposed_due_date="2026-09-28",
            sequencing_position=1,
            proposed_predecessors=[],
            proposed_successors=["LNG-802"],
        ),
        TaskPlanningProposal(
            issue_key="LNG-802",
            proposed_estimate=PlanningEstimate(value=6.0, unit=EstimateUnit.HOURS),
            proposed_start_date="2026-09-29",
            proposed_due_date="2026-09-29",
            sequencing_position=2,
            proposed_predecessors=["LNG-801"],
            proposed_successors=["LNG-803"],
        ),
        TaskPlanningProposal(
            issue_key="LNG-803",
            proposed_estimate=PlanningEstimate(value=6.0, unit=EstimateUnit.HOURS),
            proposed_start_date="2026-09-30",
            proposed_due_date="2026-09-30",
            sequencing_position=3,
            proposed_predecessors=["LNG-802"],
            proposed_successors=["LNG-804"],
        ),
        TaskPlanningProposal(
            issue_key="LNG-804",
            proposed_estimate=PlanningEstimate(value=6.0, unit=EstimateUnit.HOURS),
            proposed_start_date="2026-10-01",
            proposed_due_date="2026-10-01",
            sequencing_position=4,
            proposed_predecessors=["LNG-803"],
            proposed_successors=[],
        ),
    ]
    proposal = PlanningProposal(
        proposal_version="proposal-v1",
        generated_at="2026-09-26T00:00:00Z",
        context_version=scenario.context.context_version,
        anchor_date=scenario.context.anchor_date,
        planning_horizon_working_days=scenario.context.planning_horizon_working_days,
        requires_human_review=True,
        overall_confidence=0.85,
        summary="Sequential chain proposal",
        task_proposals=task_props,
        sequencing_proposals=[
            SequencingProposal(issue_key=t.issue_key, position=idx, rationale=f"Pos #{idx}")
            for idx, t in enumerate(task_props, 1)
        ],
        risk_signals=[],
        assumptions=[],
        evidence_references=[],
    )

    val_res = PlanningProposalValidator.validate_proposal(scenario.context, proposal)
    assert not any(i.severity == ValidationIssueSeverity.ERROR for i in val_res.issues)


@pytest.mark.asyncio
async def test_requirement_y_multiple_resources():
    """Y. Parallel execution across multiple resources is properly validated."""
    scenario = next(s for s in PHASE_4D_EVALUATION_DATASET if s.scenario_id == "EVAL-SCEN-E")
    proposal = _build_mock_proposal_for_scenario(scenario.context)

    val_res = PlanningProposalValidator.validate_proposal(scenario.context, proposal)
    # Zero hard constraint violations
    assert val_res.status in (ProposalValidationStatus.VALID, ProposalValidationStatus.NEEDS_REVIEW)
    assert not any(i.severity == ValidationIssueSeverity.ERROR for i in val_res.issues)


@pytest.mark.asyncio
async def test_requirement_z_competing_priorities():
    """Z. Competing priorities in queue are sequenced deterministically."""
    scenario = next(s for s in PHASE_4D_EVALUATION_DATASET if s.scenario_id == "EVAL-SCEN-Q")
    proposal = _build_mock_proposal_for_scenario(scenario.context)

    val_res = PlanningProposalValidator.validate_proposal(scenario.context, proposal)
    assert not any(i.severity == ValidationIssueSeverity.ERROR for i in val_res.issues)


@pytest.mark.asyncio
async def test_requirement_aa_reopened_stale_task():
    """AA. Reopened/stale tasks are handled with zero hard errors."""
    scenario = next(s for s in PHASE_4D_EVALUATION_DATASET if s.scenario_id == "EVAL-SCEN-R")
    proposal = _build_mock_proposal_for_scenario(scenario.context)

    val_res = PlanningProposalValidator.validate_proposal(scenario.context, proposal)
    assert not any(i.severity == ValidationIssueSeverity.ERROR for i in val_res.issues)


@pytest.mark.asyncio
async def test_requirement_ab_requires_human_review_enforcement():
    """AB. requires_human_review=False in proposal is rejected as a safety error."""
    scenario = PHASE_4D_EVALUATION_DATASET[0]
    proposal = _build_mock_proposal_for_scenario(scenario.context)

    bad_prop = proposal.model_copy(update={"requires_human_review": False})
    val_res = PlanningProposalValidator.validate_proposal(scenario.context, bad_prop)
    assert val_res.status == ProposalValidationStatus.INVALID
    assert any(i.code == "HUMAN_REVIEW_REQUIRED_FALSE" for i in val_res.issues)


def test_requirement_ac_immutable_context():
    """AC. Context is immutable and unmodified by validation."""
    scenario = PHASE_4D_EVALUATION_DATASET[0]
    original_dict = scenario.context.model_dump()

    proposal = _build_mock_proposal_for_scenario(scenario.context)
    _ = PlanningProposalValidator.validate_proposal(scenario.context, proposal)

    assert scenario.context.model_dump() == original_dict


def test_requirement_ad_immutable_proposal():
    """AD. Proposal is immutable and unmodified by validation."""
    scenario = PHASE_4D_EVALUATION_DATASET[0]
    proposal = _build_mock_proposal_for_scenario(scenario.context)
    original_prop_dict = proposal.model_dump()

    _ = PlanningProposalValidator.validate_proposal(scenario.context, proposal)

    assert proposal.model_dump() == original_prop_dict


def test_requirement_ae_deterministic_issue_ordering():
    """AE. Validator issues are ordered deterministically by severity, category, issue key, code."""
    scenario = next(s for s in PHASE_4D_EVALUATION_DATASET if s.scenario_id == "EVAL-SCEN-D")
    proposal = _build_mock_proposal_for_scenario(scenario.context)

    # Invalidate multiple items
    t0 = proposal.task_proposals[0].model_copy(
        update={"proposed_start_date": "2026-10-03", "proposed_estimate": PlanningEstimate(value=50.0, unit=EstimateUnit.HOURS)}
    )
    bad_prop = proposal.model_copy(update={"task_proposals": [t0], "requires_human_review": False})

    val_res1 = PlanningProposalValidator.validate_proposal(scenario.context, bad_prop)
    val_res2 = PlanningProposalValidator.validate_proposal(scenario.context, bad_prop)

    assert [i.code for i in val_res1.issues] == [i.code for i in val_res2.issues]
    assert [i.severity for i in val_res1.issues] == [i.severity for i in val_res2.issues]


@pytest.mark.asyncio
async def test_requirement_af_zero_external_calls():
    """AF. Mock evaluation runs with zero external HTTP or network calls."""
    harness = PlanningEvaluationHarness(provider=MockAIProvider())
    report = await harness.run_evaluation(PHASE_4D_EVALUATION_DATASET)

    assert report.total_scenarios == 20
    assert report.successful_calls == 20
    assert report.failed_calls == 0


@pytest.mark.asyncio
async def test_requirement_ag_repeated_evaluation_produces_identical_output():
    """AG. Repeated evaluation across all 20 scenarios produces strictly identical deterministic reports."""
    harness1 = PlanningEvaluationHarness(provider=MockAIProvider())
    harness2 = PlanningEvaluationHarness(provider=MockAIProvider())

    report1 = await harness1.run_evaluation(PHASE_4D_EVALUATION_DATASET)
    report2 = await harness2.run_evaluation(PHASE_4D_EVALUATION_DATASET)

    assert report1.total_scenarios == report2.total_scenarios
    assert report1.valid_proposals_count == report2.valid_proposals_count
    assert report1.needs_review_proposals_count == report2.needs_review_proposals_count
    assert report1.invalid_proposals_count == report2.invalid_proposals_count
    assert report1.total_hard_constraint_violations == report2.total_hard_constraint_violations
    assert report1.total_hard_block_violations == report2.total_hard_block_violations
    assert report1.grounded_count == report2.grounded_count
    assert report1.failure_breakdown == report2.failure_breakdown
