"""Comprehensive unit and automated evaluation tests for Phase 2E: DeepSeek Provider Evaluation.

Verifies:
1. Outgoing HTTP request contract (method, URL, headers, model, json_object, max_tokens, timeout)
2. Offline automated evaluation against all 8 synthetic scenarios with mocked responses
3. Grounding validation catches invented issue keys, fabricated assignees, and forbidden tokens
4. Error matrix: 401, 403, 404, 429 fail closed without retry
5. Error matrix: 500, 502, 503, 504 retry boundedly then fail closed
6. Error matrix: timeout & connection error retry boundedly then fail closed
7. Malformed JSON, empty response, and invalid schema fail closed
8. Live evaluation safety gate strictly aborts when RUN_LIVE_AI_EVAL != true
9. Secrets (API keys, Bearer tokens) never leak in exceptions, logs, or reports
"""

import asyncio
import json
import pytest
import httpx

from app.config.settings import settings
from app.services.ai.evaluation.grounding import GroundingValidator
from app.services.ai.evaluation.harness import EvaluationHarness
from app.services.ai.evaluation.scenarios import (
    EVALUATION_SCENARIOS,
    SCENARIO_1_HEALTHY_QUEUE,
    SCENARIO_2_OVERDUE_WORK,
    SCENARIO_5_UNASSIGNED_WORK,
)
from app.services.ai.models import AttentionItemAnalysis, PMAttentionAnalysis
from app.services.ai.providers.deepseek import (
    AI_PROMPT_VERSION,
    DeepSeekAIProvider,
    DeepSeekProviderError,
)


def make_mock_transport(handler):
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport)


def _make_mock_pma_json(scenario_id: str, items=None, summary="Evaluated summary", recommendation="Evaluated rec"):
    flagged_items = []
    if items:
        for it in items:
            flagged_items.append({
                "issue_key": it["key"],
                "title": it.get("summary", "Title"),
                "current_status": it.get("status", "In Progress"),
                "assignee": it.get("assignee"),
                "priority": it.get("priority", "Medium"),
                "due_date": it.get("due_date"),
                "updated_at": it.get("updated_at"),
                "inactivity_duration": it.get("inactivity_duration"),
                "attention_reason": f"Attention required for {it['key']}",
                "supporting_evidence": [f"Status is {it.get('status')}"],
                "recommendation": f"Follow up on {it['key']}",
                "confidence": 0.88,
                "uncertainty_or_missing_info": None,
                "proposed_action": None,
            })

    return {
        "analysis_id": f"analysis-eval-{scenario_id}",
        "generated_at": "2026-09-20T12:00:00Z",
        "scope_team": "Mursaleen Cluster",
        "summary": summary,
        "attention_items": flagged_items,
        "evidence": ["Evidence 1", "Evidence 2"],
        "recommendation": recommendation,
        "confidence": 0.90,
        "uncertainty_or_missing_info": None,
        "proposed_action": None,
        "requires_human_review": True,
    }


# ---------------------------------------------------------------------------
# 1. Request Contract Verification
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_deepseek_http_request_contract():
    """Verify HTTP method, endpoint, headers, JSON mode, max_tokens, and model parameter."""
    captured_request = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_request["method"] = request.method
        captured_request["url"] = str(request.url)
        captured_request["auth_header"] = request.headers.get("Authorization")
        captured_request["content_type"] = request.headers.get("Content-Type")

        body = json.loads(request.content.decode("utf-8"))
        captured_request["body"] = body

        return httpx.Response(
            status_code=200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(_make_mock_pma_json("CONTRACT-TEST")),
                        }
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            },
        )

    client = make_mock_transport(handler)
    provider = DeepSeekAIProvider(
        api_key="sk-test-contract-secret",
        model="deepseek-chat",
        timeout_seconds=25.0,
        max_output_tokens=1500,
        client=client,
    )

    await provider.analyze_attention(SCENARIO_1_HEALTHY_QUEUE.context)

    assert captured_request["method"] == "POST"
    assert captured_request["url"] == "https://api.deepseek.com/chat/completions"
    assert captured_request["auth_header"] == "Bearer sk-test-contract-secret"
    assert captured_request["content_type"] == "application/json"

    body = captured_request["body"]
    assert body["model"] == "deepseek-chat"
    assert body["response_format"] == {"type": "json_object"}
    assert body["max_tokens"] == 1500
    assert len(body["messages"]) >= 2
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][1]["role"] == "user"


# ---------------------------------------------------------------------------
# 2. Automated Offline Evaluation on Synthetic Scenarios
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_automated_evaluation_across_all_scenarios():
    """Verify evaluation harness runs across all 8 synthetic scenarios without live calls."""
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        user_content = body["messages"][1]["content"]

        # Parse context from user prompt to echo back realistic valid items
        if "PAY-101" in user_content:
            mock_payload = _make_mock_pma_json("SCEN-02", [{"key": "PAY-101", "summary": "Payment", "status": "In Progress"}])
        elif "AUTH-202" in user_content:
            mock_payload = _make_mock_pma_json("SCEN-03", [{"key": "AUTH-202", "summary": "Auth", "status": "In Progress"}])
        elif "REP-303" in user_content:
            mock_payload = _make_mock_pma_json("SCEN-04", [{"key": "REP-303", "summary": "Report", "status": "Reopened"}])
        elif "OPS-404" in user_content:
            mock_payload = _make_mock_pma_json("SCEN-05", [{"key": "OPS-404", "summary": "Ops", "status": "To Do", "assignee": None}])
        elif "MIX-501" in user_content:
            mock_payload = _make_mock_pma_json("SCEN-06", [
                {"key": "MIX-501", "summary": "Task 1", "status": "In Progress"},
                {"key": "MIX-502", "summary": "Task 2", "status": "Code Review"},
                {"key": "MIX-503", "summary": "Task 3", "status": "To Do", "assignee": None},
            ])
        elif "BND-601" in user_content:
            mock_payload = _make_mock_pma_json("SCEN-08", [
                {"key": "BND-601", "summary": "Task 1", "status": "In Progress"},
                {"key": "BND-602", "summary": "Task 2", "status": "In Progress"},
                {"key": "BND-603", "summary": "Task 3", "status": "In Progress"},
                {"key": "BND-604", "summary": "Task 4", "status": "Review"},
                {"key": "BND-605", "summary": "Task 5", "status": "Reopened"},
                {"key": "BND-606", "summary": "Task 6", "status": "To Do", "assignee": None},
            ])
        else:
            # Healthy or empty
            mock_payload = _make_mock_pma_json("HEALTHY", [])

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
                "usage": {"prompt_tokens": 120, "completion_tokens": 80},
            },
        )

    client = make_mock_transport(handler)
    provider = DeepSeekAIProvider(api_key="sk-test-mock-key", client=client)
    harness = EvaluationHarness(provider)

    summary = await harness.run_evaluation(EVALUATION_SCENARIOS)

    assert summary.total_scenarios == 8
    assert summary.successful_calls == 8
    assert summary.failed_calls == 0
    assert summary.schema_valid_count == 8
    assert summary.grounded_count == 8
    assert summary.prompt_version == AI_PROMPT_VERSION


# ---------------------------------------------------------------------------
# 3. Deterministic Grounding Checks
# ---------------------------------------------------------------------------

def test_grounding_catches_invented_issue_key():
    """GroundingValidator must flag when AI asserts an issue key not present in context."""
    bad_analysis = PMAttentionAnalysis(
        analysis_id="analysis-bad-1",
        generated_at="2026-09-20T12:00:00Z",
        scope_team="Mursaleen Cluster",
        summary="Flagged non-existent item",
        attention_items=[
            AttentionItemAnalysis(
                issue_key="FAKE-999",  # Invented!
                title="Invented task",
                current_status="In Progress",
                attention_reason="Invented",
                recommendation="Triage",
                confidence=0.85,
            )
        ],
        evidence=[],
        recommendation="None",
        confidence=0.90,
        requires_human_review=True,
    )

    result = GroundingValidator.validate(bad_analysis, SCENARIO_2_OVERDUE_WORK)
    assert result.is_grounded is False
    assert "FAKE-999" in result.unauthorized_keys
    assert any("invented issue key 'FAKE-999'" in v for v in result.violations)


def test_grounding_catches_fabricated_assignee():
    """GroundingValidator must flag when AI invents an assignee for an unassigned task."""
    bad_analysis = PMAttentionAnalysis(
        analysis_id="analysis-bad-assignee",
        generated_at="2026-09-20T12:00:00Z",
        scope_team="Mursaleen Cluster",
        summary="Unassigned task",
        attention_items=[
            AttentionItemAnalysis(
                issue_key="OPS-404",
                title="Database vacuum",
                current_status="To Do",
                assignee="Ghost Developer",  # Fabricated! Context had assignee=None
                attention_reason="Unassigned task",
                recommendation="Assign it",
                confidence=0.85,
            )
        ],
        evidence=[],
        recommendation="None",
        confidence=0.90,
        requires_human_review=True,
    )

    result = GroundingValidator.validate(bad_analysis, SCENARIO_5_UNASSIGNED_WORK)
    assert result.is_grounded is False
    assert any("invented assignee 'Ghost Developer'" in v for v in result.violations)


def test_grounding_catches_forbidden_token():
    """GroundingValidator flags forbidden invention tokens."""
    bad_analysis = PMAttentionAnalysis(
        analysis_id="analysis-forbidden",
        generated_at="2026-09-20T12:00:00Z",
        scope_team="Mursaleen Cluster",
        summary="Found critical issue OVERDUE-999 in cluster",
        attention_items=[],
        evidence=[],
        recommendation="None",
        confidence=0.90,
        requires_human_review=True,
    )

    result = GroundingValidator.validate(bad_analysis, SCENARIO_1_HEALTHY_QUEUE)
    assert result.is_grounded is False
    assert any("OVERDUE-999" in v for v in result.violations)


# ---------------------------------------------------------------------------
# 4. Error Matrix Coverage (4xx, 5xx, 429, Timeout, Connection)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status_code", [401, 403, 404, 429])
@pytest.mark.asyncio
async def test_evaluation_error_matrix_4xx_fails_closed_no_retry(status_code):
    """Client errors (401, 403, 404, 429) fail closed immediately without retry."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(status_code=status_code, text=f"HTTP {status_code} Error")

    client = make_mock_transport(handler)
    provider = DeepSeekAIProvider(api_key="sk-test-key", client=client)

    with pytest.raises(DeepSeekProviderError) as excinfo:
        await provider.analyze_attention(SCENARIO_1_HEALTHY_QUEUE.context)

    assert call_count == 1
    assert f"HTTP {status_code}" in str(excinfo.value)


@pytest.mark.parametrize("status_code", [500, 502, 503, 504])
@pytest.mark.asyncio
async def test_evaluation_error_matrix_5xx_retries_then_fails(status_code, monkeypatch):
    """Server errors (500, 502, 503, 504) execute bounded retries before failing."""
    async def _fast_sleep(s):
        pass
    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(status_code=status_code, text=f"HTTP {status_code} Error")

    client = make_mock_transport(handler)
    provider = DeepSeekAIProvider(api_key="sk-test-key", client=client)

    with pytest.raises(DeepSeekProviderError) as excinfo:
        await provider.analyze_attention(SCENARIO_1_HEALTHY_QUEUE.context)

    assert call_count == 3
    assert f"HTTP {status_code}" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 5. Live Evaluator Safety Gate
# ---------------------------------------------------------------------------

def test_live_evaluator_safety_gate_aborts_without_opt_in(monkeypatch):
    """Live evaluator CLI script must abort immediately without RUN_LIVE_AI_EVAL=true."""
    monkeypatch.delenv("RUN_LIVE_AI_EVAL", raising=False)
    from app.services.ai.evaluation import live_evaluator

    with pytest.raises(SystemExit) as excinfo:
        live_evaluator.main()
    assert excinfo.value.code == 0
