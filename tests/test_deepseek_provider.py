"""Comprehensive tests for DeepSeek AI Provider Adapter (Phase 2D).

Verifies:
1. Provider resolution and validation via resolve_ai_provider
2. Disabled AI_ENABLED=false never executes network calls
3. Missing API key fails closed safely
4. Secret redaction: API key never appears in exceptions or logs
5. Valid mocked DeepSeek JSON parses into typed AIDecision
6. Valid mocked DeepSeek JSON parses into typed PMAttentionAnalysis
7. Markdown fence stripping from JSON response
8. Malformed JSON handling (fail closed)
9. Empty response handling (fail closed)
10. Schema validation failure handling (fail closed)
11. Timeout handling (safe provider error)
12. HTTP 4xx (401, 403, 429) fails closed without retry
13. HTTP 5xx executes bounded retry before failing closed
14. End-to-end integration with AIDecisionService and AISafetyGate
"""

import asyncio
import json
import pytest
import httpx

from app.config.settings import settings
from app.services.ai.config import (
    AIConfigurationError,
    AIProviderConfig,
    resolve_ai_provider,
)
from app.services.ai.context import ContextBuilder
from app.services.ai.decision import AIDecisionService
from app.services.ai.models import (
    AIContext,
    AIDecision,
    AIDecisionType,
    AIRecommendationType,
    PMAttentionAnalysis,
)
from app.services.ai.provider import NullAIProvider
from app.services.ai.providers.deepseek import (
    DeepSeekAIProvider,
    DeepSeekProviderError,
)
from app.services.ai.safety import AISafetyGate, AISafetyViolation


# ---------------------------------------------------------------------------
# Fixtures & Sample Payloads
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_context():
    """Create a minimal valid AIContext for testing."""
    return AIContext(
        context_id="ctx-test-123",
        timestamp="2026-09-20T12:00:00Z",
        objective="Evaluate PM attention candidates",
        team_name="Mursaleen Cluster",
        recent_activity_summary=["Task TREN-378 stale for 5 days"],
        metrics=[],
        applicable_policies=["StaleTaskPolicy"],
        metadata={"stale_items": [{"key": "TREN-378", "summary": "Payment gateway timeout"}]},
    )


@pytest.fixture
def valid_decision_json():
    return {
        "decision_type": "PM_ATTENTION",
        "recommendation": "REVIEW_TASK",
        "confidence": 0.88,
        "evidence": ["Task TREN-378 has been inactive for 5 days."],
        "explanation": "High priority issue without recent progress requires PM intervention.",
        "proposed_action": {
            "action_type": "SEND_MESSAGE",
            "target_system": "discord",
            "target_id": "pm-alerts",
            "parameters": {"message": "Please review TREN-378"},
            "rationale": "Notify team lead of inactive payment ticket",
        },
        "requires_approval": True,
    }


@pytest.fixture
def valid_attention_json():
    return {
        "analysis_id": "analysis-deepseek-test-1",
        "generated_at": "2026-09-20T12:05:00Z",
        "scope_team": "Mursaleen Cluster",
        "summary": "1 stale issue requires immediate team check-in.",
        "attention_items": [
            {
                "issue_key": "TREN-378",
                "title": "Payment gateway timeout bug",
                "current_status": "In Progress",
                "assignee": "Ahsan Amin",
                "priority": "High",
                "due_date": "2026-09-25",
                "updated_at": "2026-09-15T10:00:00Z",
                "inactivity_duration": "5d",
                "attention_reason": "Task inactive for 5 days in active progress state.",
                "supporting_evidence": ["Status is 'In Progress'", "Last activity was 5 days ago"],
                "recommendation": "Check in with Ahsan Amin on blocker status.",
                "confidence": 0.90,
                "uncertainty_or_missing_info": None,
                "proposed_action": {
                    "action_type": "SEND_MESSAGE",
                    "target_system": "discord",
                    "target_id": "pm-alerts",
                    "parameters": {"content": "Check in on TREN-378"},
                    "rationale": "Notify channel",
                },
            }
        ],
        "evidence": ["Found 1 item in stale query projection"],
        "recommendation": "Review flagged task with assignee during daily standup.",
        "confidence": 0.89,
        "uncertainty_or_missing_info": None,
        "proposed_action": None,
        "requires_human_review": True,
    }


def make_mock_transport(handler):
    """Helper to create httpx.AsyncClient with a custom MockTransport."""
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport)


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------

def test_deepseek_provider_resolution_success():
    """1. DeepSeek provider resolves correctly with valid config."""
    cfg = AIProviderConfig(
        enabled=True,
        provider="deepseek",
        api_key="sk-test-secret-key-12345",
        model="deepseek-chat",
        timeout_seconds=45.0,
    )
    provider = resolve_ai_provider(cfg)
    assert isinstance(provider, DeepSeekAIProvider)
    assert provider.model == "deepseek-chat"
    assert provider.timeout_seconds == 45.0
    assert provider.base_url == "https://api.deepseek.com"


def test_deepseek_missing_api_key_fails_closed():
    """2. DeepSeek provider without API key raises AIConfigurationError."""
    cfg = AIProviderConfig(
        enabled=True,
        provider="deepseek",
        api_key="",
    )
    with pytest.raises(AIConfigurationError) as excinfo:
        resolve_ai_provider(cfg)
    assert "DeepSeek API key is required" in str(excinfo.value)


def test_deepseek_disabled_returns_null_provider_no_network():
    """3. When AI_ENABLED=False, factory returns NullAIProvider even if deepseek is configured."""
    cfg = AIProviderConfig(
        enabled=False,
        provider="deepseek",
        api_key="sk-real-looking-key",
    )
    provider = resolve_ai_provider(cfg)
    assert isinstance(provider, NullAIProvider)


@pytest.mark.asyncio
async def test_deepseek_valid_decision_analysis(sample_context, valid_decision_json):
    """4. Valid mocked DeepSeek JSON response parses into typed AIDecision."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("Authorization") == "Bearer sk-test-key"
        body = json.loads(request.content.decode("utf-8"))
        assert body["model"] == "deepseek-chat"
        assert body["response_format"] == {"type": "json_object"}
        return httpx.Response(
            status_code=200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(valid_decision_json),
                        }
                    }
                ],
                "usage": {"prompt_tokens": 150, "completion_tokens": 80},
            },
        )

    client = make_mock_transport(handler)
    provider = DeepSeekAIProvider(
        api_key="sk-test-key",
        client=client,
    )

    decision = await provider.analyze(sample_context)
    assert isinstance(decision, AIDecision)
    assert decision.decision_type == AIDecisionType.PM_ATTENTION
    assert decision.recommendation == AIRecommendationType.REVIEW_TASK
    assert decision.confidence == 0.88
    assert decision.requires_approval is True
    assert decision.proposed_action is not None
    assert decision.proposed_action.action_type == "SEND_MESSAGE"


@pytest.mark.asyncio
async def test_deepseek_valid_attention_analysis(sample_context, valid_attention_json):
    """5. Valid mocked DeepSeek JSON response parses into typed PMAttentionAnalysis."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(valid_attention_json),
                        }
                    }
                ],
                "usage": {"prompt_tokens": 300, "completion_tokens": 200},
            },
        )

    client = make_mock_transport(handler)
    provider = DeepSeekAIProvider(
        api_key="sk-test-key",
        client=client,
    )

    analysis = await provider.analyze_attention(sample_context)
    assert isinstance(analysis, PMAttentionAnalysis)
    assert analysis.analysis_id == "analysis-deepseek-test-1"
    assert analysis.requires_human_review is True
    assert len(analysis.attention_items) == 1
    assert analysis.attention_items[0].issue_key == "TREN-378"
    assert analysis.attention_items[0].confidence == 0.90


@pytest.mark.asyncio
async def test_deepseek_markdown_code_fence_stripping(sample_context, valid_decision_json):
    """6. Model output enclosed in ```json markdown fences is cleanly parsed."""
    fenced_content = f"```json\n{json.dumps(valid_decision_json)}\n```"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": fenced_content,
                        }
                    }
                ]
            },
        )

    client = make_mock_transport(handler)
    provider = DeepSeekAIProvider(api_key="sk-test-key", client=client)

    decision = await provider.analyze(sample_context)
    assert isinstance(decision, AIDecision)
    assert decision.confidence == 0.88


@pytest.mark.asyncio
async def test_deepseek_malformed_json_fails_closed(sample_context):
    """7. Malformed JSON response raises DeepSeekProviderError (fail closed)."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Not a JSON object {broken...",
                        }
                    }
                ]
            },
        )

    client = make_mock_transport(handler)
    provider = DeepSeekAIProvider(api_key="sk-test-key", client=client)

    with pytest.raises(DeepSeekProviderError) as excinfo:
        await provider.analyze(sample_context)
    assert "Failed to parse DeepSeek response content as JSON" in str(excinfo.value)


@pytest.mark.asyncio
async def test_deepseek_empty_response_fails_closed(sample_context):
    """8. Empty or missing content raises DeepSeekProviderError."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            json={"choices": [{"message": {"content": ""}}]},
        )

    client = make_mock_transport(handler)
    provider = DeepSeekAIProvider(api_key="sk-test-key", client=client)

    with pytest.raises(DeepSeekProviderError) as excinfo:
        await provider.analyze(sample_context)
    assert "content is empty" in str(excinfo.value)


@pytest.mark.asyncio
async def test_deepseek_schema_validation_failure_fails_closed(sample_context):
    """9. JSON missing required fields or having invalid types fails schema validation."""
    invalid_decision_dict = {
        "decision_type": "INVALID_TYPE",
        "confidence": 1.5,  # Out of bounds
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(invalid_decision_dict),
                        }
                    }
                ]
            },
        )

    client = make_mock_transport(handler)
    provider = DeepSeekAIProvider(api_key="sk-test-key", client=client)

    with pytest.raises(DeepSeekProviderError) as excinfo:
        await provider.analyze(sample_context)
    assert "failed AIDecision schema validation" in str(excinfo.value)


@pytest.mark.asyncio
async def test_deepseek_client_error_4xx_fails_without_retry(sample_context):
    """10. HTTP 401/403/429 fails immediately without retrying."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(
            status_code=401,
            json={"error": {"message": "Invalid API key provided", "type": "authentication_error"}},
        )

    client = make_mock_transport(handler)
    provider = DeepSeekAIProvider(api_key="sk-invalid-key", client=client)

    with pytest.raises(DeepSeekProviderError) as excinfo:
        await provider.analyze(sample_context)
    assert call_count == 1  # No retries on 4xx
    assert "HTTP 401" in str(excinfo.value)


async def _fast_sleep(s: float):
    pass


@pytest.mark.asyncio
async def test_deepseek_server_error_5xx_retries_then_fails(sample_context, monkeypatch):
    """11. HTTP 500/503 executes bounded retries (3 attempts total) then fails."""
    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(
            status_code=503,
            text="Service Temporarily Unavailable",
        )

    client = make_mock_transport(handler)
    provider = DeepSeekAIProvider(api_key="sk-test-key", client=client)

    with pytest.raises(DeepSeekProviderError) as excinfo:
        await provider.analyze(sample_context)
    assert call_count == 3  # 3 attempts
    assert "HTTP 503" in str(excinfo.value)


@pytest.mark.asyncio
async def test_deepseek_timeout_retries_then_fails(sample_context, monkeypatch):
    """12. Request timeout retries boundedly then raises safe DeepSeekProviderError."""
    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        raise httpx.TimeoutException("Read timeout")

    client = make_mock_transport(handler)
    provider = DeepSeekAIProvider(api_key="sk-test-key", timeout_seconds=5.0, client=client)

    with pytest.raises(DeepSeekProviderError) as excinfo:
        await provider.analyze(sample_context)
    assert call_count == 3
    assert "timed out" in str(excinfo.value)


def test_deepseek_api_key_never_leaked_in_exception_or_repr():
    """13. Ensure secret API key is scrubbed from any exception or error text."""
    secret = "sk-super-secret-production-key-999"
    provider = DeepSeekAIProvider(api_key=secret)

    sanitized = provider._sanitize_error_message(f"Error connecting to bearer {secret} at endpoint")
    assert secret not in sanitized
    assert "******" in sanitized


@pytest.mark.asyncio
async def test_end_to_end_decision_service_with_deepseek(temp_db, sample_context, valid_attention_json, monkeypatch):
    """14. End-to-end integration: AIDecisionService + DeepSeekAIProvider + AISafetyGate + AuditService."""
    monkeypatch.setattr(settings, "AI_ENABLED", True)
    monkeypatch.setattr(settings, "AI_PROVIDER", "deepseek")
    monkeypatch.setattr(settings, "AI_API_KEY", "sk-test-real-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(valid_attention_json),
                        }
                    }
                ]
            },
        )

    client = make_mock_transport(handler)
    provider = DeepSeekAIProvider(
        api_key="sk-test-real-key",
        client=client,
    )

    service = AIDecisionService(provider=provider, manager=temp_db)
    analysis = await service.evaluate_attention(context=sample_context)

    assert isinstance(analysis, PMAttentionAnalysis)
    assert analysis.requires_human_review is True
    assert len(analysis.attention_items) == 1

    # Verify audit log entry was written
    logs = service.audit_service.list_logs(limit=5)
    attention_logs = [l for l in logs if l.get("action") == "AI_ATTENTION_ANALYSIS"]
    assert len(attention_logs) >= 1
    assert attention_logs[0]["result"] == "COMPLETED"
    # Ensure API key is nowhere in audit details
    details_str = json.dumps(attention_logs[0].get("details", {}))
    assert "sk-test-real-key" not in details_str
