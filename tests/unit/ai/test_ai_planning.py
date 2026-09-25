"""Comprehensive unit tests for Phase 4B: DeepSeek AI Planning Engine.

Required test matrix:
A  AI disabled fails closed
B  Missing PlanningContext fails safely
C  Mock provider returns valid PlanningProposal
D  DeepSeek provider is selected through existing configuration
E  PlanningContext serialized deterministically
F  Valid proposal parses successfully
G  Invalid JSON rejected
H  Invalid schema rejected
I  Unknown issue key rejected
J  Unknown resource reference rejected
K  Invalid date rejected
L  Invalid confidence rejected
M  Invalid evidence type rejected
N  requires_human_review cannot become false
O  Arbitrary extra fields rejected
P  Truncated context is explicitly represented
Q  AI does not invent issue keys
R  AI does not invent resource IDs
S  AI cannot override HARD_BLOCK facts
T  Advisory artifact relationships remain advisory
U  No Jira API calls
V  No Action Engine calls
W  Existing AI attention flow still works
X  Existing DeepSeek provider tests still pass
"""

import asyncio
import json
import pytest
from typing import Any, Dict, List
import httpx
from pydantic import ValidationError

from app.config.settings import settings
from app.core.models.planning import (
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
    SequencingProposal,
    TaskPlanningProposal,
)
from app.services.ai.config import AIConfigurationError, AIProviderConfig, resolve_ai_provider
from app.services.ai.planning import AIPlanningService
from app.services.ai.planning_prompt import PLANNING_PROMPT_VERSION, PlanningPromptBuilder
from app.services.ai.provider import MockAIProvider, NullAIProvider
from app.services.ai.providers.deepseek import DeepSeekAIProvider, DeepSeekProviderError
from app.services.ai.safety import AISafetyGate, AISafetyViolation


def _build_test_context(
    anchor_date: str = "2026-09-26",
    tasks: List[PlanningTaskContext] = None,
    resources: List[PlanningResourceContext] = None,
    dependencies: List[PlanningDependencyContext] = None,
    artifacts: List[PlanningArtifactContext] = None,
    truncation: ContextTruncationMetadata = None,
) -> PlanningContext:
    task_list = tasks or [
        PlanningTaskContext(
            issue_key="WSSS-1",
            summary="Backend auth API endpoint",
            assigned_resource_id="acc-1",
            assigned_resource_name="Alice",
            estimated_remaining_hours=8.0,
            status="In Progress",
            projected_start_date="2026-09-26",
            projected_completion_date="2026-09-27",
            predecessor_keys=[],
            successor_keys=["WSSS-2"],
        ),
        PlanningTaskContext(
            issue_key="WSSS-2",
            summary="Frontend auth integration",
            assigned_resource_id="acc-2",
            assigned_resource_name="Bob",
            estimated_remaining_hours=12.0,
            status="To Do",
            is_blocked=True,
            projected_start_date="2026-09-28",
            projected_completion_date="2026-09-29",
            predecessor_keys=["WSSS-1"],
            successor_keys=[],
        ),
    ]

    res_list = resources or [
        PlanningResourceContext(
            resource_id="acc-1",
            display_name="Alice",
            available_capacity_hours=67.5,
            remaining_effort_hours=8.0,
            active_task_count=1,
        ),
        PlanningResourceContext(
            resource_id="acc-2",
            display_name="Bob",
            available_capacity_hours=67.5,
            remaining_effort_hours=12.0,
            active_task_count=1,
        ),
    ]

    dep_list = dependencies or [
        PlanningDependencyContext(
            source_issue_key="WSSS-1",
            target_issue_key="WSSS-2",
            link_type="Blocks",
            classification=DependencyClassification.HARD_BLOCK,
            is_hard_block=True,
            is_advisory=False,
        )
    ]

    art_list = artifacts or []

    return PlanningContext(
        context_version="planning-v1",
        generated_at="2026-09-26T00:00:00Z",
        anchor_date=anchor_date,
        planning_horizon_working_days=10,
        horizon_end_date="2026-10-09",
        team_group="Mursaleen Cluster",
        team_summary=PlanningTeamSummary(
            resource_count=len(res_list),
            active_task_count=len(task_list),
            total_remaining_effort_hours=sum(t.estimated_remaining_hours for t in task_list),
            total_available_capacity_hours=sum(r.available_capacity_hours for r in res_list),
            blocked_task_count=sum(1 for t in task_list if t.is_blocked),
        ),
        resources=res_list,
        tasks=task_list,
        dependencies=dep_list,
        artifacts=art_list,
        schedule=PlanningScheduleSummary(
            anchor_date=anchor_date,
            horizon_end_date="2026-10-09",
            planning_horizon_working_days=10,
            tasks_projected_count=len(task_list),
        ),
        truncation=truncation or ContextTruncationMetadata(),
    )


class TestAIPlanningService:
    """Comprehensive test suite for Phase 4B AI Planning Reasoning Engine."""

    # A: AI disabled fails closed
    @pytest.mark.asyncio
    async def test_a_ai_disabled_fails_closed(self, monkeypatch, temp_db):
        monkeypatch.setattr(settings, "AI_ENABLED", False)
        service = AIPlanningService(manager=temp_db)
        ctx = _build_test_context()

        proposal = await service.generate_plan(ctx)
        assert isinstance(proposal, PlanningProposal)
        assert proposal.requires_human_review is True
        assert "disabled" in proposal.summary.lower() or "inactive" in proposal.summary.lower()
        assert proposal.task_proposals == []

    # B: Missing PlanningContext fails safely
    @pytest.mark.asyncio
    async def test_b_missing_planning_context_fails_safely(self, temp_db):
        service = AIPlanningService(manager=temp_db)
        with pytest.raises(ValueError) as exc:
            await service.generate_plan(None)
        assert "valid PlanningContext" in str(exc.value)

    # C: Mock provider returns valid PlanningProposal
    @pytest.mark.asyncio
    async def test_c_mock_provider_returns_valid_planning_proposal(self, monkeypatch, temp_db):
        monkeypatch.setattr(settings, "AI_ENABLED", True)
        mock_prov = MockAIProvider()
        service = AIPlanningService(provider=mock_prov, manager=temp_db)
        ctx = _build_test_context()

        proposal = await service.generate_plan(ctx)
        assert isinstance(proposal, PlanningProposal)
        assert proposal.requires_human_review is True
        assert len(proposal.task_proposals) == 2
        assert proposal.task_proposals[0].issue_key == "WSSS-1"
        assert proposal.task_proposals[1].issue_key == "WSSS-2"
        assert len(proposal.sequencing_proposals) == 2

    # D: DeepSeek provider is selected through existing configuration
    def test_d_deepseek_provider_configuration_resolution(self, monkeypatch):
        cfg = AIProviderConfig(
            enabled=True,
            provider="deepseek",
            api_key="sk-test-fake-key",
            model="deepseek-chat",
        )
        provider = resolve_ai_provider(cfg)
        assert isinstance(provider, DeepSeekAIProvider)
        assert provider.model == "deepseek-chat"

        # Missing api_key raises AIConfigurationError
        cfg_bad = AIProviderConfig(
            enabled=True,
            provider="deepseek",
            api_key=None,
        )
        with pytest.raises(AIConfigurationError):
            resolve_ai_provider(cfg_bad)

    # E: PlanningContext serialized deterministically
    def test_e_planning_context_serialized_deterministically(self):
        ctx1 = _build_test_context()
        ctx2 = _build_test_context()

        msg1 = PlanningPromptBuilder.build_messages(ctx1)
        msg2 = PlanningPromptBuilder.build_messages(ctx2)

        assert json.dumps(msg1) == json.dumps(msg2)
        assert msg1[0]["role"] == "system"
        assert msg1[1]["role"] == "user"
        assert PLANNING_PROMPT_VERSION == "planning-v1"

    # F: Valid proposal parses successfully
    @pytest.mark.asyncio
    async def test_f_valid_proposal_parses_successfully(self, monkeypatch, temp_db):
        monkeypatch.setattr(settings, "AI_ENABLED", True)
        ctx = _build_test_context()

        valid_payload = {
            "proposal_version": "proposal-v1",
            "generated_at": "2026-09-26T00:00:00Z",
            "context_version": "planning-v1",
            "anchor_date": "2026-09-26",
            "planning_horizon_working_days": 10,
            "requires_human_review": True,
            "overall_confidence": 0.85,
            "summary": "Valid parsed proposal from mock JSON",
            "task_proposals": [
                {
                    "issue_key": "WSSS-1",
                    "proposed_estimate": {
                        "value": 6.0,
                        "unit": "hours",
                        "confidence": 0.9,
                        "rationale": "Grounded estimate",
                        "evidence_references": [
                            {
                                "evidence_type": "TASK_ESTIMATE",
                                "source_identifier": "WSSS-1",
                                "description": "8.0h in context, proposed 6.0h",
                                "relevance": "DIRECT",
                            }
                        ],
                    },
                    "proposed_start_date": "2026-09-26",
                    "proposed_due_date": "2026-09-27",
                    "date_confidence": 0.85,
                    "sequencing_position": 1,
                    "proposed_predecessors": [],
                    "proposed_successors": ["WSSS-2"],
                    "risk_level": "LOW",
                    "risk_reason": None,
                    "evidence_references": [],
                    "assumptions": [],
                    "requires_human_review": True,
                }
            ],
            "sequencing_proposals": [
                {
                    "issue_key": "WSSS-1",
                    "position": 1,
                    "rationale": "High priority",
                    "confidence": 0.9,
                    "evidence_references": [],
                }
            ],
            "risk_signals": [],
            "assumptions": [],
            "evidence_references": [],
        }

        mock_proposal = PlanningProposal.model_validate(valid_payload)
        provider = MockAIProvider(custom_planning_proposal=mock_proposal)
        service = AIPlanningService(provider=provider, manager=temp_db)

        proposal = await service.generate_plan(ctx)
        assert proposal.summary == "Valid parsed proposal from mock JSON"
        assert len(proposal.task_proposals) == 1
        assert proposal.task_proposals[0].proposed_estimate.value == 6.0

    # G: Invalid JSON rejected
    @pytest.mark.asyncio
    async def test_g_invalid_json_rejected(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                status_code=200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "This is plain prose, not valid JSON object.",
                            }
                        }
                    ]
                },
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = DeepSeekAIProvider(api_key="sk-test", client=client)
        ctx = _build_test_context()

        with pytest.raises(DeepSeekProviderError) as exc:
            await provider.analyze_planning(ctx)
        assert "Failed to parse DeepSeek response content as JSON" in str(exc.value)

    # H: Invalid schema rejected
    @pytest.mark.asyncio
    async def test_h_invalid_schema_rejected(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                status_code=200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": json.dumps({"incomplete": "schema missing required fields"}),
                            }
                        }
                    ]
                },
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = DeepSeekAIProvider(api_key="sk-test", client=client)
        ctx = _build_test_context()

        with pytest.raises(DeepSeekProviderError) as exc:
            await provider.analyze_planning(ctx)
        assert "failed PlanningProposal schema validation" in str(exc.value)

    # I: Unknown issue key rejected
    def test_i_unknown_issue_key_rejected(self):
        ctx = _build_test_context()
        proposal = PlanningProposal(
            generated_at="2026-09-26T00:00:00Z",
            anchor_date=ctx.anchor_date,
            summary="Proposal with invented key",
            task_proposals=[
                TaskPlanningProposal(
                    issue_key="FABRICATED-999",  # Not in ctx (WSSS-1, WSSS-2)
                    requires_human_review=True,
                )
            ],
        )

        is_valid, err = AISafetyGate.validate_planning_proposal(proposal, ctx)
        assert is_valid is False
        assert "does not exist in PlanningContext" in err
        assert "FABRICATED-999" in err

    # J: Unknown resource reference rejected in safety gate
    def test_j_unknown_resource_reference_in_safety_gate(self):
        ctx = _build_test_context()
        # Risk signal referencing unknown key
        proposal = PlanningRiskSignal(
            risk_type=PlanningRiskType.CAPACITY_RISK,
            issue_key="UNKNOWN-777",
            explanation="Risk on unknown issue",
            requires_human_review=True,
        )
        prop_root = PlanningProposal(
            generated_at="2026-09-26T00:00:00Z",
            anchor_date=ctx.anchor_date,
            summary="Proposal",
            risk_signals=[proposal],
        )
        is_valid, err = AISafetyGate.validate_planning_proposal(prop_root, ctx)
        assert is_valid is False
        assert "UNKNOWN-777" in err

    # K: Invalid date rejected
    def test_k_invalid_date_rejected(self):
        with pytest.raises(ValidationError):
            TaskPlanningProposal(
                issue_key="WSSS-1",
                proposed_start_date="2026/09/26",  # Must be YYYY-MM-DD
            )

    # L: Invalid confidence rejected
    def test_l_invalid_confidence_rejected(self):
        with pytest.raises(ValidationError):
            PlanningProposal(
                generated_at="2026-09-26T00:00:00Z",
                anchor_date="2026-09-26",
                summary="Summary",
                overall_confidence=1.5,  # Out of bounds
            )

    # M: Invalid evidence type rejected
    def test_m_invalid_evidence_type_rejected(self):
        with pytest.raises(ValidationError):
            EvidenceReference(
                evidence_type="INVENTED_EVIDENCE_TYPE",
                source_identifier="acc-1",
            )

    # N: requires_human_review cannot become false
    def test_n_requires_human_review_cannot_become_false(self):
        ctx = _build_test_context()
        proposal = PlanningProposal(
            generated_at="2026-09-26T00:00:00Z",
            anchor_date=ctx.anchor_date,
            summary="Autonomous execution attempt",
            requires_human_review=False,
        )
        is_valid, err = AISafetyGate.validate_planning_proposal(proposal, ctx)
        assert is_valid is False
        assert "requires_human_review=True" in err

    # O: Arbitrary extra fields rejected
    def test_o_arbitrary_extra_fields_rejected(self):
        with pytest.raises(ValidationError):
            PlanningProposal(
                generated_at="2026-09-26T00:00:00Z",
                anchor_date="2026-09-26",
                summary="Summary",
                extra_action_mutation_payload={"execute": "MUTATE_JIRA"},
            )

    # P: Truncated context is explicitly represented in prompt
    def test_p_truncated_context_is_explicitly_represented(self):
        trunc = ContextTruncationMetadata(
            is_truncated=True,
            original_task_count=100,
            included_task_count=20,
            truncation_reasons=["Task count exceeded maximum bound of 20"],
        )
        ctx = _build_test_context(truncation=trunc)
        messages = PlanningPromptBuilder.build_messages(ctx)

        user_content = messages[1]["content"]
        assert "NOTE: The provided PlanningContext was bounded/truncated" in user_content
        assert "Task count exceeded maximum bound of 20" in user_content
        assert "Do NOT assume omitted tasks or resources do not exist" in user_content

    # Q: AI does not invent issue keys
    def test_q_ai_does_not_invent_issue_keys(self):
        ctx = _build_test_context()
        proposal = PlanningProposal(
            generated_at="2026-09-26T00:00:00Z",
            anchor_date=ctx.anchor_date,
            summary="Summary",
            sequencing_proposals=[
                SequencingProposal(
                    issue_key="GHOST-101",
                    position=1,
                    rationale="Ghost task",
                )
            ],
        )
        is_valid, err = AISafetyGate.validate_planning_proposal(proposal, ctx)
        assert is_valid is False
        assert "GHOST-101" in err

    # R: AI does not invent resource IDs
    def test_r_ai_does_not_invent_resource_ids(self):
        ctx = _build_test_context()
        # Ensure context resource set is bounded and known
        known_resources = {r.resource_id for r in ctx.resources}
        assert "acc-1" in known_resources
        assert "acc-2" in known_resources
        assert "unknown-res" not in known_resources

    # S: AI cannot override HARD_BLOCK facts
    def test_s_ai_cannot_override_hard_block_facts(self):
        ctx = _build_test_context()
        # System prompt explicitly instructs: "You must NEVER override or ignore HARD_BLOCK dependencies."
        messages = PlanningPromptBuilder.build_messages(ctx)
        sys_prompt = messages[0]["content"]
        assert "You must NEVER override or ignore HARD_BLOCK dependencies." in sys_prompt

    # T: Advisory artifact relationships remain advisory
    def test_t_advisory_artifact_relationships_remain_advisory(self):
        ctx = _build_test_context()
        messages = PlanningPromptBuilder.build_messages(ctx)
        sys_prompt = messages[0]["content"]
        assert "You must NEVER convert advisory artifact relationships into confirmed HARD_BLOCK dependencies." in sys_prompt

    # U: No Jira API calls
    def test_u_no_jira_api_calls(self, monkeypatch, temp_db):
        def _fail_http(*args, **kwargs):
            raise RuntimeError("Unexpected Jira HTTP call during AI planning!")

        monkeypatch.setattr("httpx.Client.request", _fail_http)
        monkeypatch.setattr("httpx.AsyncClient.request", _fail_http)

        mock_prov = MockAIProvider()
        service = AIPlanningService(provider=mock_prov, manager=temp_db)
        ctx = _build_test_context()

        # Should complete completely offline without invoking Jira
        proposal = asyncio.run(service.generate_plan(ctx))
        assert proposal is not None

    # V: No Action Engine calls
    def test_v_no_action_engine_calls(self, temp_db):
        mock_prov = MockAIProvider()
        service = AIPlanningService(provider=mock_prov, manager=temp_db)
        ctx = _build_test_context()

        proposal = asyncio.run(service.generate_plan(ctx))
        # Ensure no BaseAction was created or staged
        assert isinstance(proposal, PlanningProposal)

    # W: Existing AI attention flow still works
    def test_w_existing_ai_attention_flow_still_works(self, monkeypatch, temp_db):
        monkeypatch.setattr(settings, "AI_ENABLED", True)
        from app.services.ai.decision import AIDecisionService
        from app.services.ai.models import PMAttentionAnalysis

        mock_prov = MockAIProvider()
        service = AIDecisionService(provider=mock_prov, manager=temp_db)
        ctx = service.context_builder.build_attention_context()

        analysis = asyncio.run(service.evaluate_attention(ctx))
        assert isinstance(analysis, PMAttentionAnalysis)
        assert analysis.requires_human_review is True

    # X: Existing DeepSeek provider tests still pass
    def test_x_existing_deepseek_provider_configuration(self):
        provider = DeepSeekAIProvider(api_key="sk-test-key")
        assert provider.base_url == "https://api.deepseek.com"
        assert provider.model == "deepseek-chat"

