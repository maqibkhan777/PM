"""Live and automated evaluation test suite for Phase 4B: DeepSeek AI Planning Engine.

Validates all 8 synthetic planning scenarios against structural and grounding constraints:
1. Balanced team
2. Overloaded resource
3. HARD_BLOCK chain
4. Cross-resource dependency
5. Low-confidence estimates
6. Missing capacity/history
7. Artifact handoff
8. Truncated context

Live evaluation is strictly opt-in and gated behind RUN_LIVE_AI_PLANNING_EVAL=true.
"""

import json
import os
import pytest
from typing import List
import httpx

from app.config.settings import settings
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
    SequencingProposal,
    TaskPlanningProposal,
)
from app.services.ai.evaluation.planning_scenarios import (
    PLANNING_EVALUATION_SCENARIOS,
    PlanningScenario,
)
from app.services.ai.planning_prompt import PLANNING_PROMPT_VERSION
from app.services.ai.providers.deepseek import DeepSeekAIProvider, DeepSeekProviderError
from app.services.ai.safety import AISafetyGate


def _build_grounded_mock_proposal(context: PlanningContext) -> dict:
    """Build a valid grounded mock proposal dictionary for the given context."""
    task_props = []
    for idx, t in enumerate(context.tasks, 1):
        task_props.append(
            {
                "issue_key": t.issue_key,
                "proposed_estimate": {
                    "value": max(t.estimated_remaining_hours, 1.0),
                    "unit": "hours",
                    "confidence": 0.85,
                    "rationale": f"Estimated based on context workload ({t.estimated_remaining_hours}h)",
                    "evidence_references": [
                        {
                            "evidence_type": "TASK_ESTIMATE",
                            "source_identifier": t.issue_key,
                            "description": "Grounded from context task facts",
                            "relevance": "DIRECT",
                        }
                    ],
                },
                "proposed_start_date": t.projected_start_date or context.anchor_date,
                "proposed_due_date": t.projected_completion_date or "2026-10-02",
                "date_confidence": 0.85,
                "sequencing_position": idx,
                "proposed_predecessors": list(t.predecessor_keys),
                "proposed_successors": list(t.successor_keys),
                "risk_level": "HIGH" if t.is_blocked else "LOW",
                "risk_reason": "Blocked by dependency" if t.is_blocked else None,
                "evidence_references": [],
                "assumptions": [],
                "requires_human_review": True,
            }
        )

    seq_props = [
        {
            "issue_key": t.issue_key,
            "position": idx,
            "rationale": f"Sequenced position #{idx}",
            "confidence": 0.85,
            "evidence_references": [],
        }
        for idx, t in enumerate(context.tasks, 1)
    ]

    return {
        "proposal_version": "proposal-v1",
        "generated_at": "2026-09-26T00:00:00Z",
        "context_version": context.context_version,
        "anchor_date": context.anchor_date,
        "planning_horizon_working_days": context.planning_horizon_working_days,
        "requires_human_review": True,
        "overall_confidence": 0.85,
        "summary": f"Evaluated scenario proposal for {len(context.tasks)} tasks.",
        "task_proposals": task_props,
        "sequencing_proposals": seq_props,
        "risk_signals": [],
        "assumptions": [
            {
                "statement": "Assuming capacity remains stable over planning horizon",
                "confidence": 0.85,
                "requires_human_review": True,
                "evidence_references": [],
            }
        ],
        "evidence_references": [],
    }


# ---------------------------------------------------------------------------
# Offline Mock Evaluation Across All 8 Scenarios
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_offline_planning_evaluation_across_all_scenarios():
    """Verify DeepSeekAIProvider evaluates all 8 planning scenarios with offline mock transport."""
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        user_content = body["messages"][1]["content"]

        matched_scenario = None
        for scen in PLANNING_EVALUATION_SCENARIOS:
            if scen.context.tasks and scen.context.tasks[0].issue_key in user_content:
                matched_scenario = scen
                break

        if not matched_scenario:
            matched_scenario = PLANNING_EVALUATION_SCENARIOS[0]

        mock_payload = _build_grounded_mock_proposal(matched_scenario.context)

        return httpx.Response(
            status_code=200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(mock_payload),
                        }
                    }
                ],
                "usage": {"prompt_tokens": 150, "completion_tokens": 90},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = DeepSeekAIProvider(api_key="sk-test-mock-key", client=client)

    for scenario in PLANNING_EVALUATION_SCENARIOS:
        proposal = await provider.analyze_planning(scenario.context)
        assert isinstance(proposal, PlanningProposal)
        assert proposal.requires_human_review is True

        is_valid, err = AISafetyGate.validate_planning_proposal(proposal, scenario.context)
        assert is_valid is True, f"Scenario {scenario.scenario_id} failed validation: {err}"
        assert len(proposal.task_proposals) == len(scenario.context.tasks)


# ---------------------------------------------------------------------------
# Phase 4D: Harness Evaluation Across 20 Scenarios (Mock & Live)
# ---------------------------------------------------------------------------

from app.services.ai.evaluation.planning_dataset import PHASE_4D_EVALUATION_DATASET
from app.services.ai.evaluation.planning_harness import PlanningEvaluationHarness

@pytest.mark.asyncio
async def test_offline_planning_evaluation_phase_4d_harness():
    """Verify DeepSeekAIProvider evaluates all 20 Phase 4D scenarios via PlanningEvaluationHarness."""
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        user_content = body["messages"][1]["content"]

        matched_scenario = None
        for scen in PHASE_4D_EVALUATION_DATASET:
            if scen.context.tasks and scen.context.tasks[0].issue_key in user_content:
                matched_scenario = scen
                break

        if not matched_scenario:
            matched_scenario = PHASE_4D_EVALUATION_DATASET[0]

        mock_payload = _build_grounded_mock_proposal(matched_scenario.context)

        return httpx.Response(
            status_code=200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(mock_payload),
                        }
                    }
                ],
                "usage": {"prompt_tokens": 150, "completion_tokens": 90},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = DeepSeekAIProvider(api_key="sk-test-mock-key", client=client)
    harness = PlanningEvaluationHarness(provider=provider)

    report = await harness.run_evaluation(PHASE_4D_EVALUATION_DATASET)
    assert report.total_scenarios == 20
    assert report.successful_calls == 20
    assert report.failed_calls == 0
    assert report.grounded_count == 20
    assert report.parse_success_count == 20


# ---------------------------------------------------------------------------
# Live Evaluation Gate (Opt-In Only)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_live_deepseek_planning_evaluation():
    """Opt-in live evaluation against DeepSeek API. Strictly skipped by default."""
    if os.getenv("RUN_LIVE_AI_PLANNING_EVAL", "").strip().lower() != "true":
        pytest.skip("Skipping live AI planning evaluation: RUN_LIVE_AI_PLANNING_EVAL is not 'true'")

    api_key = os.getenv("AI_API_KEY") or getattr(settings, "AI_API_KEY", None)
    if not api_key:
        pytest.skip("AI_API_KEY is not configured")

    provider = DeepSeekAIProvider(api_key=api_key)
    harness = PlanningEvaluationHarness(provider=provider)

    report = await harness.run_evaluation(PHASE_4D_EVALUATION_DATASET)
    assert report.total_scenarios == 20
    assert report.successful_calls > 0

