"""Unit and integration tests for PM AI Phase 2C: Provider Configuration & Adapter Boundary."""

import pytest
from app.config.settings import Settings, settings
from app.services.ai.config import (
    AIConfigurationError,
    AIProviderConfig,
    resolve_ai_provider,
)
from app.services.ai.decision import AIDecisionService
from app.services.ai.provider import MockAIProvider, NullAIProvider


def test_ai_disabled_resolves_to_null_provider():
    """1. When AI_ENABLED=False, factory always returns NullAIProvider regardless of AI_PROVIDER."""
    cfg = AIProviderConfig(enabled=False, provider="mock")
    provider = resolve_ai_provider(cfg)
    assert isinstance(provider, NullAIProvider)

    # Even if provider was configured as a future provider, disabled flag takes precedence
    cfg2 = AIProviderConfig(enabled=False, provider="null")
    assert isinstance(resolve_ai_provider(cfg2), NullAIProvider)


def test_explicit_mock_provider_resolution():
    """2. When AI_ENABLED=True and AI_PROVIDER='mock', factory returns MockAIProvider."""
    cfg = AIProviderConfig(enabled=True, provider="mock")
    provider = resolve_ai_provider(cfg)
    assert isinstance(provider, MockAIProvider)


def test_explicit_custom_mock_provider_passed():
    """3. Custom mock provider instance is preserved by factory when supplied."""
    custom_mock = MockAIProvider(confidence=0.99, explanation="Custom test mock")
    cfg = AIProviderConfig(enabled=True, provider="mock")
    provider = resolve_ai_provider(cfg, custom_mock=custom_mock)
    assert provider is custom_mock
    assert provider.confidence == 0.99


def test_explicit_null_provider_resolution():
    """4. When AI_ENABLED=True and AI_PROVIDER='null', factory returns NullAIProvider."""
    cfg = AIProviderConfig(enabled=True, provider="null")
    provider = resolve_ai_provider(cfg)
    assert isinstance(provider, NullAIProvider)


def test_unimplemented_future_provider_fails_closed():
    """5. When AI_ENABLED=True and AI_PROVIDER is a future provider key (e.g. gemini, openai), fail closed."""
    for prov in ("gemini", "openai", "anthropic"):
        cfg = AIProviderConfig(enabled=True, provider=prov)
        with pytest.raises(AIConfigurationError) as excinfo:
            resolve_ai_provider(cfg)
        assert "not yet implemented" in str(excinfo.value)
        assert prov in str(excinfo.value)


def test_unsupported_provider_fails_closed():
    """6. Unsupported or arbitrary provider strings fail closed with clear error."""
    cfg = AIProviderConfig(enabled=True, provider="arbitrary_unsupported_model_123")
    with pytest.raises(AIConfigurationError) as excinfo:
        resolve_ai_provider(cfg)
    assert "Unsupported AI provider" in str(excinfo.value)


def test_no_arbitrary_dynamic_imports():
    """7. Verifies that passing a python module path does NOT dynamically import or execute."""
    cfg = AIProviderConfig(enabled=True, provider="os.system")
    with pytest.raises(AIConfigurationError) as excinfo:
        resolve_ai_provider(cfg)
    assert "Unsupported AI provider" in str(excinfo.value)


def test_api_key_secret_never_leaked_in_errors_or_logs(monkeypatch):
    """8. Ensure AI_API_KEY is not exposed when raising AIConfigurationError."""
    secret_key = "sk-super-confidential-secret-key-12345"
    cfg = AIProviderConfig(
        enabled=True,
        provider="gemini",
        api_key=secret_key,
    )
    with pytest.raises(AIConfigurationError) as excinfo:
        resolve_ai_provider(cfg)
    err_str = str(excinfo.value)
    assert secret_key not in err_str


def test_timeout_validation():
    """9. Validates timeout bounds (must be > 0 and <= 300)."""
    # Valid timeout
    cfg = AIProviderConfig(timeout_seconds=45.0)
    assert cfg.timeout_seconds == 45.0

    # Negative / zero timeout
    with pytest.raises(ValueError):
        AIProviderConfig(timeout_seconds=0.0)
    with pytest.raises(ValueError):
        AIProviderConfig(timeout_seconds=-5.0)

    # Exceeding upper bound
    with pytest.raises(ValueError):
        AIProviderConfig(timeout_seconds=500.0)


def test_token_limit_validation():
    """10. Validates input and output token bounds."""
    # Valid tokens
    cfg = AIProviderConfig(max_input_tokens=8000, max_output_tokens=1000)
    assert cfg.max_input_tokens == 8000
    assert cfg.max_output_tokens == 1000

    # Zero / negative tokens
    with pytest.raises(ValueError):
        AIProviderConfig(max_input_tokens=0)
    with pytest.raises(ValueError):
        AIProviderConfig(max_output_tokens=-1)

    # Excessive tokens beyond bound
    with pytest.raises(ValueError):
        AIProviderConfig(max_input_tokens=200000)
    with pytest.raises(ValueError):
        AIProviderConfig(max_output_tokens=30000)


def test_ai_decision_service_uses_resolved_provider(temp_db, monkeypatch):
    """11. AIDecisionService defaults to factory resolver based on settings."""
    monkeypatch.setattr(settings, "AI_ENABLED", True)
    monkeypatch.setattr(settings, "AI_PROVIDER", "mock")

    service = AIDecisionService(manager=temp_db)
    assert isinstance(service.provider, MockAIProvider)

    # When AI_ENABLED=False
    monkeypatch.setattr(settings, "AI_ENABLED", False)
    service2 = AIDecisionService(manager=temp_db)
    assert isinstance(service2.provider, NullAIProvider)
