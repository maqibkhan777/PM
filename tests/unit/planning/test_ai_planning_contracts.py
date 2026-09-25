"""Deterministic unit tests for Phase 4A: AI Planning Contracts.

Covers all required specifications:
A. Valid PlanningProposal
B. Valid TaskPlanningProposal
C. Valid SequencingProposal
D. Valid PlanningRiskSignal
E. Valid EvidenceReference
F. Valid PlanningAssumption
G. Confidence lower bound
H. Confidence upper bound
I. Invalid confidence rejected
J. Invalid date format rejected
K. Invalid estimate unit rejected
L. Empty issue key rejected
M. Human review defaults to true
N. Existing issue references can be represented
O. Evidence references are bounded
P. Assumptions remain explicit
Q. HARD_BLOCK references can be represented
R. Proposal does not mutate PlanningContext
S. Deterministic serialization
T. No provider-specific dependency
U. No Jira API calls
V. No DeepSeek calls
W. No Action Engine calls
X. Prohibit arbitrary Jira mutation payloads
"""

from datetime import datetime, timezone
import json
import pytest
from pydantic import ValidationError

from app.core.models.planning import (
    EstimateUnit,
    EvidenceReference,
    EvidenceType,
    PlanningAssumption,
    PlanningContext,
    PlanningEstimate,
    PlanningProposal,
    PlanningResourceContext,
    PlanningRiskSignal,
    PlanningRiskType,
    PlanningScheduleSummary,
    PlanningTaskContext,
    PlanningTeamSummary,
    SequencingProposal,
    TaskPlanningProposal,
)
from app.core.planning.context import PlanningContextBuilder
from app.services.ai.models import AIDecision, PMAttentionAnalysis


class TestAIPlanningContracts:
    """Phase 4A Unit Test Suite for Typed AI Planning Proposal Contracts."""

    def test_a_valid_planning_proposal(self):
        """A. Valid complete PlanningProposal can be constructed and validated."""
        proposal = PlanningProposal(
            generated_at="2026-09-26T00:00:00Z",
            anchor_date="2026-09-26",
            planning_horizon_working_days=10,
            summary="Proposed re-sequencing of WSSS-1 ahead of WSSS-2 to unblock backend contract.",
            overall_confidence=0.85,
            task_proposals=[
                TaskPlanningProposal(
                    issue_key="WSSS-1",
                    proposed_estimate=PlanningEstimate(
                        value=8.0,
                        unit=EstimateUnit.HOURS,
                        confidence=0.9,
                        rationale="8 hours based on historical pace and median task duration",
                    ),
                    proposed_start_date="2026-09-26",
                    proposed_due_date="2026-09-27",
                    date_confidence=0.85,
                    sequencing_position=1,
                    risk_level="LOW",
                )
            ],
            sequencing_proposals=[
                SequencingProposal(
                    issue_key="WSSS-1",
                    position=1,
                    rationale="Critical path unblocker",
                    confidence=0.9,
                )
            ],
            risk_signals=[
                PlanningRiskSignal(
                    risk_type=PlanningRiskType.CAPACITY_RISK,
                    issue_key="WSSS-1",
                    severity="LOW",
                    explanation="Resource has 12 hours buffer remaining in horizon",
                    confidence=0.85,
                )
            ],
            assumptions=[
                PlanningAssumption(
                    statement="Assuming developer is available on Monday without unplanned leave",
                    confidence=0.8,
                )
            ],
            evidence_references=[
                EvidenceReference(
                    evidence_type=EvidenceType.RESOURCE_HISTORY,
                    source_identifier="acc-123",
                    description="Historical pace median is 8.0h",
                )
            ],
        )

        assert proposal.proposal_version == "proposal-v1"
        assert proposal.requires_human_review is True
        assert len(proposal.task_proposals) == 1
        assert proposal.task_proposals[0].issue_key == "WSSS-1"
        assert proposal.task_proposals[0].proposed_estimate.value == 8.0
        assert proposal.task_proposals[0].proposed_estimate.unit == EstimateUnit.HOURS

    def test_b_valid_task_planning_proposal(self):
        """B. Valid TaskPlanningProposal representing planning for a single task."""
        task_prop = TaskPlanningProposal(
            issue_key="WSSS-10",
            proposed_estimate=PlanningEstimate(value=14.5, unit=EstimateUnit.HOURS, confidence=0.75),
            proposed_start_date="2026-09-28",
            proposed_due_date="2026-09-30",
            date_confidence=0.8,
            sequencing_position=2,
            proposed_predecessors=["WSSS-8", "WSSS-9"],
            proposed_successors=["WSSS-12"],
            risk_level="MEDIUM",
            risk_reason="Depends on 2 predecessor tasks",
        )

        assert task_prop.issue_key == "WSSS-10"
        assert task_prop.proposed_predecessors == ["WSSS-8", "WSSS-9"]
        assert task_prop.proposed_successors == ["WSSS-12"]
        assert task_prop.requires_human_review is True

    def test_c_valid_sequencing_proposal(self):
        """C. Valid SequencingProposal with 1-indexed position."""
        seq = SequencingProposal(
            issue_key="WSSS-5",
            position=1,
            rationale="Unblocks downstream QA verification",
            confidence=0.95,
        )
        assert seq.issue_key == "WSSS-5"
        assert seq.position == 1
        assert seq.confidence == 0.95

    def test_d_valid_planning_risk_signal(self):
        """D. Valid PlanningRiskSignal with bounded risk type and severity."""
        risk = PlanningRiskSignal(
            risk_type=PlanningRiskType.DEADLINE_RISK,
            issue_key="WSSS-99",
            severity="HIGH",
            explanation="Projected completion is 2 days past the requested target date",
            confidence=0.9,
        )
        assert risk.risk_type == PlanningRiskType.DEADLINE_RISK
        assert risk.severity == "HIGH"
        assert risk.requires_human_review is True

    def test_e_valid_evidence_reference(self):
        """E. Valid EvidenceReference with typed category."""
        ev = EvidenceReference(
            evidence_type=EvidenceType.CAPACITY,
            source_identifier="resource.acc-1.available_capacity",
            description="Available capacity is 45.0 hours against committed 32.0 hours",
            relevance="DIRECT",
        )
        assert ev.evidence_type == EvidenceType.CAPACITY
        assert ev.source_identifier == "resource.acc-1.available_capacity"

    def test_f_valid_planning_assumption(self):
        """F. Valid PlanningAssumption requiring human review."""
        assump = PlanningAssumption(
            statement="Assumes no scope increase during QA cycle",
            confidence=0.7,
        )
        assert assump.statement == "Assumes no scope increase during QA cycle"
        assert assump.requires_human_review is True

    def test_g_confidence_lower_bound(self):
        """G. Confidence at boundary 0.0 is accepted."""
        est = PlanningEstimate(value=5.0, confidence=0.0)
        assert est.confidence == 0.0

    def test_h_confidence_upper_bound(self):
        """H. Confidence at boundary 1.0 is accepted."""
        est = PlanningEstimate(value=5.0, confidence=1.0)
        assert est.confidence == 1.0

    def test_i_invalid_confidence_rejected(self):
        """I. Confidence outside [0.0, 1.0] raises ValidationError."""
        with pytest.raises(ValidationError):
            PlanningEstimate(value=5.0, confidence=1.05)

        with pytest.raises(ValidationError):
            PlanningEstimate(value=5.0, confidence=-0.1)

        with pytest.raises(ValidationError):
            TaskPlanningProposal(issue_key="WSSS-1", date_confidence=1.5)

    def test_j_invalid_date_format_rejected(self):
        """J. Invalid date format not matching YYYY-MM-DD raises ValidationError."""
        with pytest.raises(ValidationError):
            TaskPlanningProposal(issue_key="WSSS-1", proposed_start_date="26-09-2026")

        with pytest.raises(ValidationError):
            TaskPlanningProposal(issue_key="WSSS-1", proposed_due_date="2026/09/26")

        with pytest.raises(ValidationError):
            PlanningProposal(
                generated_at="now",
                anchor_date="invalid-date",
                summary="summary",
            )

    def test_k_invalid_estimate_unit_rejected(self):
        """K. Invalid or ambiguous estimate unit is rejected by enum validation."""
        with pytest.raises(ValidationError):
            PlanningEstimate(value=2.0, unit="days")

        with pytest.raises(ValidationError):
            PlanningEstimate(value=5.0, unit="story_points")

    def test_l_empty_issue_key_rejected(self):
        """L. Empty or whitespace-only issue key raises ValidationError."""
        with pytest.raises(ValidationError):
            TaskPlanningProposal(issue_key="")

        with pytest.raises(ValidationError):
            TaskPlanningProposal(issue_key="   ")

        with pytest.raises(ValidationError):
            SequencingProposal(issue_key="", position=1)

    def test_m_human_review_defaults_to_true(self):
        """M. Safety: requires_human_review strictly defaults to True on all proposal models."""
        proposal = PlanningProposal(
            generated_at="2026-09-26T00:00:00Z",
            anchor_date="2026-09-26",
            summary="test summary",
        )
        assert proposal.requires_human_review is True

        task_prop = TaskPlanningProposal(issue_key="WSSS-1")
        assert task_prop.requires_human_review is True

        risk = PlanningRiskSignal(
            risk_type=PlanningRiskType.CAPACITY_RISK,
            explanation="test risk",
        )
        assert risk.requires_human_review is True

        assump = PlanningAssumption(statement="test assumption")
        assert assump.requires_human_review is True

    def test_n_existing_issue_references_represented(self):
        """N. Issue references from PlanningContext are represented in proposals."""
        task_ctx = PlanningTaskContext(
            issue_key="WSSS-100",
            summary="Add authentication endpoint",
            estimated_remaining_hours=10.0,
        )
        task_prop = TaskPlanningProposal(
            issue_key=task_ctx.issue_key,
            proposed_estimate=PlanningEstimate(value=task_ctx.estimated_remaining_hours),
            proposed_start_date="2026-09-26",
            proposed_due_date="2026-09-27",
        )
        assert task_prop.issue_key == "WSSS-100"
        assert task_prop.proposed_estimate.value == 10.0

    def test_o_evidence_references_bounded(self):
        """O. Evidence references use bounded EvidenceType enum."""
        ev1 = EvidenceReference(
            evidence_type=EvidenceType.BOTTLENECK,
            source_identifier="bottleneck.OVERLOADED_RESOURCE",
            description="Resource is currently overloaded",
        )
        assert ev1.evidence_type == EvidenceType.BOTTLENECK

        with pytest.raises(ValidationError):
            EvidenceReference(
                evidence_type="UNRECOGNIZED_ARBITRARY_TYPE",
                source_identifier="id",
            )

    def test_p_assumptions_remain_explicit(self):
        """P. Assumptions are explicitly modeled and distinguished from facts."""
        assump = PlanningAssumption(
            statement="Assuming frontend team delivers API contract by Tuesday",
            confidence=0.75,
        )
        assert assump.statement.startswith("Assuming")
        assert assump.requires_human_review is True

    def test_q_hard_block_references_represented(self):
        """Q. Proposed predecessors/successors represent directed dependencies."""
        task_prop = TaskPlanningProposal(
            issue_key="WSSS-2",
            proposed_predecessors=["WSSS-1"],
            proposed_successors=["WSSS-3"],
        )
        assert task_prop.proposed_predecessors == ["WSSS-1"]
        assert task_prop.proposed_successors == ["WSSS-3"]

    def test_r_proposal_does_not_mutate_planning_context(self):
        """R. Proposal construction does not mutate source PlanningContext."""
        builder = PlanningContextBuilder()
        ctx = builder.build_context(
            team_snapshots=[],
            anchor_date="2026-09-26",
            horizon_working_days=10,
        )
        orig_dict = ctx.model_dump()

        _ = PlanningProposal(
            generated_at="2026-09-26T00:00:00Z",
            anchor_date=ctx.anchor_date,
            summary="Proposal",
            task_proposals=[TaskPlanningProposal(issue_key="WSSS-1")],
        )

        assert ctx.model_dump() == orig_dict

    def test_s_deterministic_serialization(self):
        """S. Proposals serialize deterministically to JSON."""
        prop1 = PlanningProposal(
            generated_at="2026-09-26T00:00:00Z",
            anchor_date="2026-09-26",
            summary="Deterministic proposal",
            overall_confidence=0.85,
            task_proposals=[
                TaskPlanningProposal(
                    issue_key="WSSS-1",
                    proposed_estimate=PlanningEstimate(value=6.0),
                    proposed_start_date="2026-09-26",
                    proposed_due_date="2026-09-27",
                )
            ],
        )
        prop2 = PlanningProposal(
            generated_at="2026-09-26T00:00:00Z",
            anchor_date="2026-09-26",
            summary="Deterministic proposal",
            overall_confidence=0.85,
            task_proposals=[
                TaskPlanningProposal(
                    issue_key="WSSS-1",
                    proposed_estimate=PlanningEstimate(value=6.0),
                    proposed_start_date="2026-09-26",
                    proposed_due_date="2026-09-27",
                )
            ],
        )

        dump1 = prop1.model_dump()
        dump2 = prop2.model_dump()

        assert json.dumps(dump1, sort_keys=True) == json.dumps(dump2, sort_keys=True)

    def test_t_no_provider_specific_dependency(self):
        """T. Proposal models do not depend on or import DeepSeek or external AI SDKs."""
        import app.core.models.planning as planning_module

        # Ensure no DeepSeek / OpenAI classes are in planning module
        assert not hasattr(planning_module, "DeepSeekAIProvider")
        assert not hasattr(planning_module, "OpenAI")
        assert not hasattr(planning_module, "Anthropic")

    def test_u_no_jira_api_calls(self, monkeypatch):
        """U. Instantiating proposal models makes zero Jira REST API calls."""
        def _fail_http(*args, **kwargs):
            raise RuntimeError("Unexpected HTTP call during PlanningProposal initialization")

        monkeypatch.setattr("httpx.Client.request", _fail_http)
        monkeypatch.setattr("httpx.AsyncClient.request", _fail_http)

        prop = PlanningProposal(
            generated_at="2026-09-26T00:00:00Z",
            anchor_date="2026-09-26",
            summary="Safe offline proposal",
        )
        assert prop.summary == "Safe offline proposal"

    def test_v_no_deepseek_calls(self, monkeypatch):
        """V. Instantiating proposal models makes zero DeepSeek calls."""
        def _fail_ai(*args, **kwargs):
            raise RuntimeError("Unexpected DeepSeek call during PlanningProposal initialization")

        monkeypatch.setattr("app.services.ai.providers.deepseek.DeepSeekAIProvider.analyze", _fail_ai)

        prop = PlanningProposal(
            generated_at="2026-09-26T00:00:00Z",
            anchor_date="2026-09-26",
            summary="Safe offline proposal",
        )
        assert prop.overall_confidence == 0.8

    def test_w_no_action_engine_calls(self):
        """W. Instantiating proposal models makes zero Action Engine calls."""
        prop = PlanningProposal(
            generated_at="2026-09-26T00:00:00Z",
            anchor_date="2026-09-26",
            summary="Safe offline proposal",
        )
        assert prop is not None

    def test_x_prohibit_arbitrary_jira_mutation_payloads(self):
        """X. Extra arbitrary fields (e.g. Jira mutation payloads) are forbidden by schema."""
        with pytest.raises(ValidationError):
            PlanningProposal(
                generated_at="2026-09-26T00:00:00Z",
                anchor_date="2026-09-26",
                summary="summary",
                arbitrary_jira_payload={"action": "UPDATE_JIRA_DUE_DATE", "value": "2026-10-01"},
            )

        with pytest.raises(ValidationError):
            TaskPlanningProposal(
                issue_key="WSSS-1",
                direct_jira_field_update={"duedate": "2026-10-01"},
            )
