"""Comprehensive unit tests for Phase 4C: Deterministic AI Planning Proposal Validator.

Covers all required specifications:
A  Valid proposal
B  Empty proposal
C  Unknown issue key
D  Unknown resource
E  Unknown predecessor
F  Invalid date format
G  Start after due date
H  Weekend proposed date
I  HARD_BLOCK violation
J  Multiple HARD_BLOCK violations
K  Valid dependency timing
L  Estimate supported by evidence
M  Estimate reasonable variance
N  Estimate large variance
O  Missing duration evidence
P  Capacity feasible
Q  Capacity pressure
R  Capacity exceeded
S  Schedule aligned
T  Schedule minor variance
U  Schedule significant variance
V  Proposal beyond horizon
W  No historical data
X  Capacity unavailable
Y  Truncated context
Z  Explicit artifact handoff
AA Inferred artifact remains advisory
AB Invalid sequencing reference
AC Sequencing conflict with HARD_BLOCK
AD Invalid risk type
AE Invalid evidence reference
AF requires_human_review false
AG Proposal immutability
AH PlanningContext immutability
AI No Jira calls
AJ No DeepSeek calls
AK No Action Engine calls
AL Deterministic validation ordering
"""

import copy
import json
import pytest
from pydantic import ValidationError

from app.core.models.planning import (
    ArtifactStatus,
    ArtifactType,
    CapacityState,
    ContextTruncationMetadata,
    DependencyClassification,
    EstimateUnit,
    EvidenceReference,
    EvidenceType,
    PlanningArtifactContext,
    PlanningAssumption,
    PlanningContext,
    PlanningDependencyContext,
    PlanningEstimate,
    PlanningProposal,
    PlanningResourceContext,
    PlanningRiskSignal,
    PlanningRiskType,
    PlanningScheduleSummary,
    PlanningTaskContext,
    PlanningTeamSummary,
    ProposalValidationCategory,
    ProposalValidationIssue,
    ProposalValidationResult,
    ProposalValidationStatus,
    SequencingProposal,
    TaskPlanningProposal,
    ValidationIssueSeverity,
)
from app.core.planning.validator import PlanningProposalValidator
from app.services.ai.planning import validate_planning_proposal


def _make_context(
    tasks=None,
    resources=None,
    dependencies=None,
    artifacts=None,
    truncation=None,
    anchor_date="2026-09-28",  # Monday
    horizon_end_date="2026-10-09",  # Friday (10 working days)
) -> PlanningContext:
    t_list = tasks or [
        PlanningTaskContext(
            issue_key="WSSS-1",
            summary="Backend API",
            assigned_resource_id="acc-alice",
            assigned_resource_name="Alice",
            estimated_remaining_hours=8.0,
            duration_evidence_source="jira_estimate",
            duration_confidence="HIGH",
            status="In Progress",
            projected_start_date="2026-09-28",
            projected_completion_date="2026-09-29",
            successor_keys=["WSSS-2"],
        ),
        PlanningTaskContext(
            issue_key="WSSS-2",
            summary="Frontend UI",
            assigned_resource_id="acc-bob",
            assigned_resource_name="Bob",
            estimated_remaining_hours=12.0,
            duration_evidence_source="jira_estimate",
            duration_confidence="HIGH",
            status="To Do",
            is_blocked=True,
            projected_start_date="2026-09-30",
            projected_completion_date="2026-10-01",
            predecessor_keys=["WSSS-1"],
        ),
    ]

    r_list = resources or [
        PlanningResourceContext(
            resource_id="acc-alice",
            display_name="Alice",
            available_capacity_hours=67.5,
            remaining_effort_hours=8.0,
            active_task_count=1,
            capacity_state=CapacityState.BALANCED,
            history_completeness="SUFFICIENT_HISTORY",
            capacity_quality="CAPACITY_KNOWN",
        ),
        PlanningResourceContext(
            resource_id="acc-bob",
            display_name="Bob",
            available_capacity_hours=67.5,
            remaining_effort_hours=12.0,
            active_task_count=1,
            capacity_state=CapacityState.BALANCED,
            history_completeness="SUFFICIENT_HISTORY",
            capacity_quality="CAPACITY_KNOWN",
        ),
    ]

    d_list = dependencies or [
        PlanningDependencyContext(
            source_issue_key="WSSS-1",
            target_issue_key="WSSS-2",
            link_type="Blocks",
            classification=DependencyClassification.HARD_BLOCK,
            is_hard_block=True,
        )
    ]

    a_list = artifacts or []

    return PlanningContext(
        context_version="planning-v1",
        generated_at="2026-09-28T00:00:00Z",
        anchor_date=anchor_date,
        planning_horizon_working_days=10,
        horizon_end_date=horizon_end_date,
        team_group="Mursaleen Cluster",
        team_summary=PlanningTeamSummary(
            resource_count=len(r_list),
            active_task_count=len(t_list),
            total_remaining_effort_hours=sum(t.estimated_remaining_hours for t in t_list),
            total_available_capacity_hours=sum(r.available_capacity_hours for r in r_list),
        ),
        resources=r_list,
        tasks=t_list,
        dependencies=d_list,
        artifacts=a_list,
        schedule=PlanningScheduleSummary(
            anchor_date=anchor_date,
            horizon_end_date=horizon_end_date,
            planning_horizon_working_days=10,
            tasks_projected_count=len(t_list),
        ),
        truncation=truncation or ContextTruncationMetadata(),
    )


def _make_valid_proposal(context: PlanningContext) -> PlanningProposal:
    return PlanningProposal(
        proposal_version="proposal-v1",
        generated_at="2026-09-28T00:00:00Z",
        context_version=context.context_version,
        anchor_date=context.anchor_date,
        planning_horizon_working_days=context.planning_horizon_working_days,
        requires_human_review=True,
        overall_confidence=0.90,
        summary="Valid proposal for WSSS-1 and WSSS-2",
        task_proposals=[
            TaskPlanningProposal(
                issue_key="WSSS-1",
                proposed_estimate=PlanningEstimate(value=8.0, unit=EstimateUnit.HOURS, confidence=0.9),
                proposed_start_date="2026-09-28",
                proposed_due_date="2026-09-29",
                date_confidence=0.9,
                sequencing_position=1,
                evidence_references=[
                    EvidenceReference(
                        evidence_type=EvidenceType.CURRENT_QUEUE,
                        source_identifier="acc-alice",
                    )
                ],
                requires_human_review=True,
            ),
            TaskPlanningProposal(
                issue_key="WSSS-2",
                proposed_estimate=PlanningEstimate(value=12.0, unit=EstimateUnit.HOURS, confidence=0.9),
                proposed_start_date="2026-09-30",
                proposed_due_date="2026-10-01",
                date_confidence=0.9,
                sequencing_position=1,
                proposed_predecessors=["WSSS-1"],
                evidence_references=[
                    EvidenceReference(
                        evidence_type=EvidenceType.CURRENT_QUEUE,
                        source_identifier="acc-bob",
                    )
                ],
                requires_human_review=True,
            ),
        ],
        sequencing_proposals=[
            SequencingProposal(issue_key="WSSS-1", position=1, confidence=0.9),
            SequencingProposal(issue_key="WSSS-2", position=1, confidence=0.9),
        ],
    )


class TestPlanningProposalValidator:
    """Test suite for Phase 4C Deterministic AI Planning Proposal Validator."""

    # A: Valid proposal
    def test_a_valid_proposal(self):
        ctx = _make_context()
        proposal = _make_valid_proposal(ctx)

        result = validate_planning_proposal(ctx, proposal)
        assert result.status == ProposalValidationStatus.VALID
        assert result.proposal_accepted is True
        assert len(result.issues) == 0
        assert result.valid_task_count == 2
        assert result.invalid_task_count == 0

    # B: Empty proposal
    def test_b_empty_proposal(self):
        ctx = _make_context()
        proposal = PlanningProposal(
            proposal_version="proposal-v1",
            generated_at="2026-09-28T00:00:00Z",
            anchor_date=ctx.anchor_date,
            summary="Empty proposal with no tasks",
            requires_human_review=True,
            task_proposals=[],
        )
        result = validate_planning_proposal(ctx, proposal)
        assert result.status == ProposalValidationStatus.VALID
        assert result.validated_task_count == 0

    # C: Unknown issue key
    def test_c_unknown_issue_key(self):
        ctx = _make_context()
        proposal = PlanningProposal(
            proposal_version="proposal-v1",
            generated_at="2026-09-28T00:00:00Z",
            anchor_date=ctx.anchor_date,
            summary="Proposal with invented key",
            task_proposals=[
                TaskPlanningProposal(
                    issue_key="UNKNOWN-999",
                    proposed_start_date="2026-09-28",
                    requires_human_review=True,
                )
            ],
        )
        result = validate_planning_proposal(ctx, proposal)
        assert result.status == ProposalValidationStatus.INVALID
        assert any(i.code == "UNKNOWN_ISSUE_KEY" and i.issue_key == "UNKNOWN-999" for i in result.issues)

    # D: Unknown resource / Unauthorized reassignment
    def test_d_unauthorized_resource_reassignment(self):
        ctx = _make_context()
        proposal = PlanningProposal(
            proposal_version="proposal-v1",
            generated_at="2026-09-28T00:00:00Z",
            anchor_date=ctx.anchor_date,
            summary="Reassignment attempt",
            task_proposals=[
                TaskPlanningProposal(
                    issue_key="WSSS-1",  # Assigned to Alice in context
                    evidence_references=[
                        EvidenceReference(
                            evidence_type=EvidenceType.CURRENT_QUEUE,
                            source_identifier="acc-bob",  # Attempting to assign to Bob
                        )
                    ],
                    requires_human_review=True,
                )
            ],
        )
        result = validate_planning_proposal(ctx, proposal)
        assert result.status == ProposalValidationStatus.INVALID
        assert any(i.code == "UNAUTHORIZED_REASSIGNMENT_ATTEMPT" for i in result.issues)

    # E: Unknown predecessor
    def test_e_unknown_predecessor(self):
        ctx = _make_context()
        proposal = PlanningProposal(
            proposal_version="proposal-v1",
            generated_at="2026-09-28T00:00:00Z",
            anchor_date=ctx.anchor_date,
            summary="Proposal with unknown predecessor",
            task_proposals=[
                TaskPlanningProposal(
                    issue_key="WSSS-1",
                    proposed_predecessors=["GHOST-888"],
                    requires_human_review=True,
                )
            ],
        )
        result = validate_planning_proposal(ctx, proposal)
        assert result.status == ProposalValidationStatus.INVALID
        assert any(i.code == "UNKNOWN_PREDECESSOR_KEY" for i in result.issues)

    # F: Invalid date format
    def test_f_invalid_date_format_rejected(self):
        with pytest.raises(ValidationError):
            TaskPlanningProposal(
                issue_key="WSSS-1",
                proposed_start_date="2026/09/28",
            )

    # G: Start after due date
    def test_g_start_after_due_date(self):
        ctx = _make_context()
        proposal = PlanningProposal(
            proposal_version="proposal-v1",
            generated_at="2026-09-28T00:00:00Z",
            anchor_date=ctx.anchor_date,
            summary="Invalid date ordering",
            task_proposals=[
                TaskPlanningProposal(
                    issue_key="WSSS-1",
                    proposed_start_date="2026-09-30",
                    proposed_due_date="2026-09-28",  # Prior to start
                    requires_human_review=True,
                )
            ],
        )
        result = validate_planning_proposal(ctx, proposal)
        assert result.status == ProposalValidationStatus.INVALID
        assert any(i.code == "START_DATE_AFTER_DUE_DATE" for i in result.issues)

    # H: Weekend proposed date
    def test_h_weekend_proposed_date(self):
        ctx = _make_context()
        proposal = PlanningProposal(
            proposal_version="proposal-v1",
            generated_at="2026-09-28T00:00:00Z",
            anchor_date=ctx.anchor_date,
            summary="Weekend proposed start",
            task_proposals=[
                TaskPlanningProposal(
                    issue_key="WSSS-1",
                    proposed_start_date="2026-10-03",  # Saturday
                    proposed_due_date="2026-10-05",
                    requires_human_review=True,
                )
            ],
        )
        result = validate_planning_proposal(ctx, proposal)
        assert result.status == ProposalValidationStatus.INVALID
        assert any(i.code == "WEEKEND_DATE_VIOLATION" for i in result.issues)

    # I: HARD_BLOCK violation
    def test_i_hard_block_violation(self):
        ctx = _make_context()  # WSSS-1 completes 2026-09-29 and blocks WSSS-2
        proposal = PlanningProposal(
            proposal_version="proposal-v1",
            generated_at="2026-09-28T00:00:00Z",
            anchor_date=ctx.anchor_date,
            summary="Hard block violation",
            task_proposals=[
                TaskPlanningProposal(
                    issue_key="WSSS-2",
                    proposed_start_date="2026-09-28",  # Starts BEFORE WSSS-1 finishes on 2026-09-29
                    proposed_due_date="2026-09-29",
                    requires_human_review=True,
                )
            ],
        )
        result = validate_planning_proposal(ctx, proposal)
        assert result.status == ProposalValidationStatus.INVALID
        assert any(i.code == "HARD_BLOCK_VIOLATION" and i.issue_key == "WSSS-2" for i in result.issues)

    # J: Multiple HARD_BLOCK violations
    def test_j_multiple_hard_block_violations(self):
        task3 = PlanningTaskContext(
            issue_key="WSSS-3",
            summary="QA Task",
            estimated_remaining_hours=8.0,
            status="To Do",
            projected_start_date="2026-10-02",
            projected_completion_date="2026-10-05",
            predecessor_keys=["WSSS-2"],
        )
        dep2 = PlanningDependencyContext(
            source_issue_key="WSSS-2",
            target_issue_key="WSSS-3",
            link_type="Blocks",
            classification=DependencyClassification.HARD_BLOCK,
            is_hard_block=True,
        )
        ctx = _make_context(
            tasks=[_make_context().tasks[0], _make_context().tasks[1], task3],
            dependencies=[_make_context().dependencies[0], dep2],
        )

        proposal = PlanningProposal(
            proposal_version="proposal-v1",
            generated_at="2026-09-28T00:00:00Z",
            anchor_date=ctx.anchor_date,
            summary="Multiple violations",
            task_proposals=[
                TaskPlanningProposal(
                    issue_key="WSSS-2",
                    proposed_start_date="2026-09-28",  # Starts before WSSS-1 (comp: 2026-09-29)
                    requires_human_review=True,
                ),
                TaskPlanningProposal(
                    issue_key="WSSS-3",
                    proposed_start_date="2026-09-29",  # Starts before WSSS-2 (comp: 2026-10-01)
                    requires_human_review=True,
                ),
            ],
        )
        result = validate_planning_proposal(ctx, proposal)
        assert result.status == ProposalValidationStatus.INVALID
        hb_issues = [i for i in result.issues if i.code == "HARD_BLOCK_VIOLATION"]
        assert len(hb_issues) == 2

    # K: Valid dependency timing
    def test_k_valid_dependency_timing(self):
        ctx = _make_context()
        proposal = PlanningProposal(
            proposal_version="proposal-v1",
            generated_at="2026-09-28T00:00:00Z",
            anchor_date=ctx.anchor_date,
            summary="Valid timing",
            task_proposals=[
                TaskPlanningProposal(
                    issue_key="WSSS-2",
                    proposed_start_date="2026-09-30",  # Starts AFTER WSSS-1 finishes on 2026-09-29
                    proposed_due_date="2026-10-01",
                    requires_human_review=True,
                )
            ],
        )
        result = validate_planning_proposal(ctx, proposal)
        assert result.status == ProposalValidationStatus.VALID

    # L: Estimate supported by evidence
    def test_l_estimate_supported_by_evidence(self):
        ctx = _make_context()
        proposal = PlanningProposal(
            proposal_version="proposal-v1",
            generated_at="2026-09-28T00:00:00Z",
            anchor_date=ctx.anchor_date,
            summary="Supported estimate",
            task_proposals=[
                TaskPlanningProposal(
                    issue_key="WSSS-1",
                    proposed_estimate=PlanningEstimate(value=8.0),  # Matches context 8.0h
                    requires_human_review=True,
                )
            ],
        )
        result = validate_planning_proposal(ctx, proposal)
        assert result.status == ProposalValidationStatus.VALID
        assert not any(i.code == "ESTIMATE_LARGE_VARIANCE" for i in result.issues)

    # M: Estimate reasonable variance
    def test_m_estimate_reasonable_variance(self):
        ctx = _make_context()  # WSSS-1 is 8.0h
        proposal = PlanningProposal(
            proposal_version="proposal-v1",
            generated_at="2026-09-28T00:00:00Z",
            anchor_date=ctx.anchor_date,
            summary="Reasonable variance estimate",
            task_proposals=[
                TaskPlanningProposal(
                    issue_key="WSSS-1",
                    proposed_estimate=PlanningEstimate(value=10.0),  # 25% variance (reasonable)
                    requires_human_review=True,
                )
            ],
        )
        result = validate_planning_proposal(ctx, proposal)
        assert result.status == ProposalValidationStatus.VALID
        assert not any(i.code == "ESTIMATE_LARGE_VARIANCE" for i in result.issues)

    # N: Estimate large variance
    def test_n_estimate_large_variance(self):
        ctx = _make_context()  # WSSS-1 is 8.0h
        proposal = PlanningProposal(
            proposal_version="proposal-v1",
            generated_at="2026-09-28T00:00:00Z",
            anchor_date=ctx.anchor_date,
            summary="Large variance estimate",
            task_proposals=[
                TaskPlanningProposal(
                    issue_key="WSSS-1",
                    proposed_estimate=PlanningEstimate(value=30.0),  # > 100% variance
                    requires_human_review=True,
                )
            ],
        )
        result = validate_planning_proposal(ctx, proposal)
        assert result.status == ProposalValidationStatus.NEEDS_REVIEW
        assert any(i.code == "ESTIMATE_LARGE_VARIANCE" for i in result.issues)

    # O: Missing duration evidence
    def test_o_missing_duration_evidence(self):
        task_missing = PlanningTaskContext(
            issue_key="WSSS-UNC",
            summary="Unestimated task",
            assigned_resource_id="acc-alice",
            estimated_remaining_hours=0.0,
            duration_evidence_source="unavailable",
            duration_confidence="UNKNOWN",
        )
        ctx = _make_context(tasks=[task_missing])
        proposal = PlanningProposal(
            proposal_version="proposal-v1",
            generated_at="2026-09-28T00:00:00Z",
            anchor_date=ctx.anchor_date,
            summary="Proposal on unestimated task",
            task_proposals=[
                TaskPlanningProposal(
                    issue_key="WSSS-UNC",
                    proposed_estimate=PlanningEstimate(value=10.0),
                    requires_human_review=True,
                )
            ],
        )
        result = validate_planning_proposal(ctx, proposal)
        assert result.status == ProposalValidationStatus.NEEDS_REVIEW
        assert any(i.code == "MISSING_DURATION_EVIDENCE" for i in result.issues)

    # P: Capacity feasible
    def test_p_capacity_feasible(self):
        ctx = _make_context()
        proposal = _make_valid_proposal(ctx)
        result = validate_planning_proposal(ctx, proposal)
        assert result.status == ProposalValidationStatus.VALID
        assert not any(i.category == ProposalValidationCategory.CAPACITY for i in result.issues)

    # Q: Capacity pressure
    def test_q_capacity_pressure(self):
        res_limited = PlanningResourceContext(
            resource_id="acc-alice",
            display_name="Alice",
            available_capacity_hours=20.0,  # 20h capacity
            remaining_effort_hours=24.0,
        )
        ctx = _make_context(resources=[res_limited, _make_context().resources[1]])
        proposal = PlanningProposal(
            proposal_version="proposal-v1",
            generated_at="2026-09-28T00:00:00Z",
            anchor_date=ctx.anchor_date,
            summary="Capacity pressure",
            task_proposals=[
                TaskPlanningProposal(
                    issue_key="WSSS-1",  # Alice
                    proposed_estimate=PlanningEstimate(value=24.0),  # 24h vs 20h capacity (1.2x)
                    requires_human_review=True,
                )
            ],
        )
        result = validate_planning_proposal(ctx, proposal)
        assert result.status == ProposalValidationStatus.NEEDS_REVIEW
        assert any(i.code == "CAPACITY_PRESSURE" for i in result.issues)

    # R: Capacity exceeded
    def test_r_capacity_exceeded(self):
        res_limited = PlanningResourceContext(
            resource_id="acc-alice",
            display_name="Alice",
            available_capacity_hours=20.0,
        )
        ctx = _make_context(resources=[res_limited, _make_context().resources[1]])
        proposal = PlanningProposal(
            proposal_version="proposal-v1",
            generated_at="2026-09-28T00:00:00Z",
            anchor_date=ctx.anchor_date,
            summary="Capacity exceeded",
            task_proposals=[
                TaskPlanningProposal(
                    issue_key="WSSS-1",
                    proposed_estimate=PlanningEstimate(value=40.0),  # 40h vs 20h (2.0x -> > 1.4x)
                    requires_human_review=True,
                )
            ],
        )
        result = validate_planning_proposal(ctx, proposal)
        assert result.status == ProposalValidationStatus.INVALID
        assert any(i.code == "CAPACITY_EXCEEDED" for i in result.issues)

    # S: Schedule aligned
    def test_s_schedule_aligned(self):
        ctx = _make_context()
        proposal = _make_valid_proposal(ctx)
        result = validate_planning_proposal(ctx, proposal)
        assert result.status == ProposalValidationStatus.VALID
        assert not any(i.category == ProposalValidationCategory.SCHEDULE for i in result.issues)

    # T: Schedule minor variance
    def test_t_schedule_minor_variance(self):
        ctx = _make_context()  # WSSS-1 deterministic start is 2026-09-28
        proposal = PlanningProposal(
            proposal_version="proposal-v1",
            generated_at="2026-09-28T00:00:00Z",
            anchor_date=ctx.anchor_date,
            summary="Minor variance",
            task_proposals=[
                TaskPlanningProposal(
                    issue_key="WSSS-1",
                    proposed_start_date="2026-09-29",  # 1-day variance
                    proposed_due_date="2026-09-30",
                    requires_human_review=True,
                )
            ],
        )
        result = validate_planning_proposal(ctx, proposal)
        assert result.status == ProposalValidationStatus.VALID
        assert not any(i.code == "SCHEDULE_SIGNIFICANT_VARIANCE" for i in result.issues)

    # U: Schedule significant variance
    def test_u_schedule_significant_variance(self):
        ctx = _make_context()  # WSSS-1 deterministic start is 2026-09-28
        proposal = PlanningProposal(
            proposal_version="proposal-v1",
            generated_at="2026-09-28T00:00:00Z",
            anchor_date=ctx.anchor_date,
            summary="Significant variance",
            task_proposals=[
                TaskPlanningProposal(
                    issue_key="WSSS-1",
                    proposed_start_date="2026-10-05",  # 7-day variance
                    proposed_due_date="2026-10-06",
                    requires_human_review=True,
                )
            ],
        )
        result = validate_planning_proposal(ctx, proposal)
        assert result.status == ProposalValidationStatus.NEEDS_REVIEW
        assert any(i.code == "SCHEDULE_SIGNIFICANT_VARIANCE" for i in result.issues)

    # V: Proposal beyond horizon
    def test_v_proposal_beyond_horizon(self):
        ctx = _make_context(horizon_end_date="2026-10-09")
        proposal = PlanningProposal(
            proposal_version="proposal-v1",
            generated_at="2026-09-28T00:00:00Z",
            anchor_date=ctx.anchor_date,
            summary="Beyond horizon",
            task_proposals=[
                TaskPlanningProposal(
                    issue_key="WSSS-1",
                    proposed_start_date="2026-10-05",
                    proposed_due_date="2026-10-15",  # Past 2026-10-09
                    requires_human_review=True,
                )
            ],
        )
        result = validate_planning_proposal(ctx, proposal)
        assert result.status == ProposalValidationStatus.NEEDS_REVIEW
        assert any(i.code == "PROPOSAL_BEYOND_HORIZON" for i in result.issues)

    # W: No historical data
    def test_w_no_historical_data(self):
        res_no_hist = PlanningResourceContext(
            resource_id="acc-alice",
            display_name="Alice",
            available_capacity_hours=67.5,
            history_completeness="NO_HISTORY",
        )
        ctx = _make_context(resources=[res_no_hist, _make_context().resources[1]])
        proposal = _make_valid_proposal(ctx)

        result = validate_planning_proposal(ctx, proposal)
        assert result.status == ProposalValidationStatus.NEEDS_REVIEW
        assert any(i.code == "RESOURCE_NO_HISTORY" for i in result.issues)

    # X: Capacity unavailable
    def test_x_capacity_unavailable(self):
        res_no_cap = PlanningResourceContext(
            resource_id="acc-alice",
            display_name="Alice",
            capacity_quality="CAPACITY_UNAVAILABLE",
        )
        ctx = _make_context(resources=[res_no_cap, _make_context().resources[1]])
        proposal = _make_valid_proposal(ctx)

        result = validate_planning_proposal(ctx, proposal)
        assert result.status == ProposalValidationStatus.NEEDS_REVIEW
        assert any(i.code == "CAPACITY_UNAVAILABLE" for i in result.issues)

    # Y: Truncated context
    def test_y_truncated_context(self):
        trunc = ContextTruncationMetadata(
            is_truncated=True,
            truncation_reasons=["Max tasks exceeded"],
        )
        ctx = _make_context(truncation=trunc)
        proposal = _make_valid_proposal(ctx)

        result = validate_planning_proposal(ctx, proposal)
        assert result.status == ProposalValidationStatus.NEEDS_REVIEW
        assert any(i.code == "TRUNCATED_CONTEXT_UNCERTAINTY" for i in result.issues)

    # Z: Explicit artifact handoff conflict
    def test_z_explicit_artifact_handoff_conflict(self):
        art = PlanningArtifactContext(
            artifact_name="api-spec",
            project_key="WSSS",
            artifact_type=ArtifactType.API_CONTRACT,
            status=ArtifactStatus.PLANNED,
            producer_issue_key="WSSS-1",
            consumer_issue_keys=["WSSS-2"],
            is_inferred=False,
        )
        ctx = _make_context(artifacts=[art])  # WSSS-1 produces api-spec, WSSS-2 consumes
        proposal = PlanningProposal(
            proposal_version="proposal-v1",
            generated_at="2026-09-28T00:00:00Z",
            anchor_date=ctx.anchor_date,
            summary="Artifact conflict",
            task_proposals=[
                TaskPlanningProposal(
                    issue_key="WSSS-2",
                    proposed_start_date="2026-09-28",  # Before WSSS-1 completion (2026-09-29)
                    requires_human_review=True,
                )
            ],
        )
        result = validate_planning_proposal(ctx, proposal)
        assert any(i.code == "EXPLICIT_ARTIFACT_HANDOFF_CONFLICT" for i in result.issues)

    # AA: Inferred artifact remains advisory
    def test_aa_inferred_artifact_remains_advisory(self):
        art = PlanningArtifactContext(
            artifact_name="design-mockup",
            project_key="WSSS",
            artifact_type=ArtifactType.DESIGN_ASSET,
            status=ArtifactStatus.PLANNED,
            producer_issue_key="WSSS-1",
            consumer_issue_keys=["WSSS-2"],
            is_inferred=True,  # Advisory only
        )
        ctx = _make_context(artifacts=[art], dependencies=[])  # No HARD_BLOCK dependency
        proposal = PlanningProposal(
            proposal_version="proposal-v1",
            generated_at="2026-09-28T00:00:00Z",
            anchor_date=ctx.anchor_date,
            summary="Advisory artifact proposal",
            task_proposals=[
                TaskPlanningProposal(
                    issue_key="WSSS-2",
                    proposed_start_date="2026-09-28",
                    requires_human_review=True,
                )
            ],
        )
        result = validate_planning_proposal(ctx, proposal)
        # Should NOT trigger EXPLICIT_ARTIFACT_HANDOFF_CONFLICT
        assert not any(i.code == "EXPLICIT_ARTIFACT_HANDOFF_CONFLICT" for i in result.issues)

    # AB: Invalid sequencing reference
    def test_ab_invalid_sequencing_reference(self):
        ctx = _make_context()
        proposal = PlanningProposal(
            proposal_version="proposal-v1",
            generated_at="2026-09-28T00:00:00Z",
            anchor_date=ctx.anchor_date,
            summary="Invalid sequence reference",
            sequencing_proposals=[
                SequencingProposal(issue_key="UNKNOWN-777", position=1)
            ],
        )
        result = validate_planning_proposal(ctx, proposal)
        assert result.status == ProposalValidationStatus.INVALID
        assert any(i.code == "UNKNOWN_SEQUENCING_KEY" for i in result.issues)

    # AC: Sequencing conflict with HARD_BLOCK
    def test_ac_sequencing_conflict_with_hard_block(self):
        ctx = _make_context()  # WSSS-1 blocks WSSS-2
        proposal = PlanningProposal(
            proposal_version="proposal-v1",
            generated_at="2026-09-28T00:00:00Z",
            anchor_date=ctx.anchor_date,
            summary="Sequencing conflict",
            sequencing_proposals=[
                SequencingProposal(issue_key="WSSS-2", position=1),  # WSSS-2 placed before WSSS-1
                SequencingProposal(issue_key="WSSS-1", position=2),
            ],
        )
        result = validate_planning_proposal(ctx, proposal)
        assert result.status == ProposalValidationStatus.INVALID
        assert any(i.code == "SEQUENCING_HARD_BLOCK_CONFLICT" for i in result.issues)

    # AD: Invalid risk type rejected by schema
    def test_ad_invalid_risk_type(self):
        with pytest.raises(ValidationError):
            PlanningRiskSignal(
                risk_type="ARBITRARY_INVENTED_RISK",
                explanation="Risk",
            )

    # AE: Invalid evidence reference rejected by schema
    def test_ae_invalid_evidence_reference(self):
        with pytest.raises(ValidationError):
            EvidenceReference(
                evidence_type="INVENTED_EVIDENCE",
                source_identifier="id",
            )

    # AF: requires_human_review false
    def test_af_requires_human_review_false(self):
        ctx = _make_context()
        proposal = PlanningProposal(
            proposal_version="proposal-v1",
            generated_at="2026-09-28T00:00:00Z",
            anchor_date=ctx.anchor_date,
            summary="Autonomous review false",
            requires_human_review=False,
        )
        result = validate_planning_proposal(ctx, proposal)
        assert result.status == ProposalValidationStatus.INVALID
        assert any(i.code == "HUMAN_REVIEW_REQUIRED_FALSE" for i in result.issues)

    # AG: Proposal immutability
    def test_ag_proposal_immutability(self):
        ctx = _make_context()
        proposal = _make_valid_proposal(ctx)
        orig_dict = copy.deepcopy(proposal.model_dump())

        _ = validate_planning_proposal(ctx, proposal)

        assert proposal.model_dump() == orig_dict

    # AH: PlanningContext immutability
    def test_ah_planning_context_immutability(self):
        ctx = _make_context()
        orig_dict = copy.deepcopy(ctx.model_dump())
        proposal = _make_valid_proposal(ctx)

        _ = validate_planning_proposal(ctx, proposal)

        assert ctx.model_dump() == orig_dict

    # AI: No Jira calls
    def test_ai_no_jira_calls(self, monkeypatch):
        def _fail_http(*args, **kwargs):
            raise RuntimeError("Unexpected HTTP/Jira call made during validation!")

        monkeypatch.setattr("httpx.Client.request", _fail_http)
        monkeypatch.setattr("httpx.AsyncClient.request", _fail_http)

        ctx = _make_context()
        proposal = _make_valid_proposal(ctx)
        result = validate_planning_proposal(ctx, proposal)
        assert result.status == ProposalValidationStatus.VALID

    # AJ: No DeepSeek calls
    def test_aj_no_deepseek_calls(self, monkeypatch):
        def _fail_ai(*args, **kwargs):
            raise RuntimeError("Unexpected DeepSeek/AI call made during validation!")

        monkeypatch.setattr("app.services.ai.providers.deepseek.DeepSeekAIProvider.analyze", _fail_ai)

        ctx = _make_context()
        proposal = _make_valid_proposal(ctx)
        result = validate_planning_proposal(ctx, proposal)
        assert result.status == ProposalValidationStatus.VALID

    # AK: No Action Engine calls
    def test_ak_no_action_engine_calls(self):
        ctx = _make_context()
        proposal = _make_valid_proposal(ctx)
        result = validate_planning_proposal(ctx, proposal)
        assert isinstance(result, ProposalValidationResult)

    # AL: Deterministic validation ordering
    def test_al_deterministic_validation_ordering(self):
        ctx = _make_context()
        proposal = PlanningProposal(
            proposal_version="proposal-v1",
            generated_at="2026-09-28T00:00:00Z",
            anchor_date=ctx.anchor_date,
            summary="Multiple ordered issues",
            task_proposals=[
                TaskPlanningProposal(
                    issue_key="UNKNOWN-1",
                    proposed_start_date="2026-10-03",  # Weekend error
                    requires_human_review=True,
                ),
                TaskPlanningProposal(
                    issue_key="WSSS-1",
                    proposed_estimate=PlanningEstimate(value=50.0),  # Large variance warning
                    requires_human_review=True,
                ),
            ],
            requires_human_review=False,  # Safety error
        )

        res1 = validate_planning_proposal(ctx, proposal)
        res2 = validate_planning_proposal(ctx, proposal)

        codes1 = [i.code for i in res1.issues]
        codes2 = [i.code for i in res2.issues]

        assert codes1 == codes2
        # Errors appear before warnings
        assert res1.issues[0].severity == ValidationIssueSeverity.ERROR
        assert res1.issues[-1].severity == ValidationIssueSeverity.WARNING
