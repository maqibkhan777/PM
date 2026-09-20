"""Provider-neutral AI configuration model and factory resolution for PM Operations Agent."""

from typing import Any, Dict, Optional
from pydantic import BaseModel, Field, field_validator
from app.services.ai.provider import AIProvider, MockAIProvider, NullAIProvider


class AIConfigurationError(Exception):
    """Raised when AI configuration is invalid, incomplete, or specifies an unsupported provider."""
    pass


class AIProviderConfig(BaseModel):
    """Validated, provider-neutral configuration parameters for AI operations.
    
    Guarantees resource bounds and secure secret handling.
    """
    enabled: bool = False
    provider: str = Field(default="mock")
    model: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    timeout_seconds: float = Field(default=30.0, gt=0.0, le=300.0)
    max_input_tokens: int = Field(default=4000, gt=0, le=128000)
    max_output_tokens: int = Field(default=2000, gt=0, le=16000)

    @field_validator("provider")
    @classmethod
    def validate_provider_name(cls, v: str) -> str:
        if not v or not str(v).strip():
            raise ValueError("AI provider name cannot be empty")
        return str(v).strip().lower()

    @field_validator("timeout_seconds")
    @classmethod
    def validate_timeout(cls, v: float) -> float:
        if v <= 0.0:
            raise ValueError("timeout_seconds must be greater than 0")
        if v > 300.0:
            raise ValueError("timeout_seconds exceeds maximum allowed limit of 300s")
        return float(v)

    @field_validator("max_input_tokens")
    @classmethod
    def validate_max_input_tokens(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("max_input_tokens must be greater than 0")
        if v > 128000:
            raise ValueError("max_input_tokens exceeds upper bound of 128,000")
        return int(v)

    @field_validator("max_output_tokens")
    @classmethod
    def validate_max_output_tokens(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("max_output_tokens must be greater than 0")
        if v > 16000:
            raise ValueError("max_output_tokens exceeds upper bound of 16,000")
        return int(v)

    @classmethod
    def from_settings(cls, app_settings: Optional[Any] = None) -> "AIProviderConfig":
        """Construct a validated AIProviderConfig directly from application Settings."""
        from app.config.settings import settings as default_settings
        cfg = app_settings or default_settings

        return cls(
            enabled=getattr(cfg, "AI_ENABLED", False),
            provider=getattr(cfg, "AI_PROVIDER", "mock") or "mock",
            model=getattr(cfg, "AI_MODEL", None),
            base_url=getattr(cfg, "AI_BASE_URL", None),
            api_key=getattr(cfg, "AI_API_KEY", None),
            timeout_seconds=float(getattr(cfg, "AI_TIMEOUT_SECONDS", 30.0) or 30.0),
            max_input_tokens=int(getattr(cfg, "AI_MAX_INPUT_TOKENS", 4000) or 4000),
            max_output_tokens=int(getattr(cfg, "AI_MAX_OUTPUT_TOKENS", 2000) or 2000),
        )


def resolve_ai_provider(
    config_or_settings: Optional[Any] = None,
    custom_mock: Optional[MockAIProvider] = None,
) -> AIProvider:
    """Deterministic factory that resolves the appropriate AIProvider.
    
    Rules:
    1. If config.enabled is False -> return NullAIProvider (never calls network, never initializes provider).
    2. If config.provider is 'null' or 'none' -> return NullAIProvider.
    3. If config.provider is 'mock' -> return MockAIProvider (or supplied custom_mock).
    4. If config.provider specifies an unsupported/unimplemented provider -> raise AIConfigurationError.
       (Never silently fall back to mock or instantiate arbitrary dynamic classes).
    """
    if isinstance(config_or_settings, AIProviderConfig):
        config = config_or_settings
    else:
        config = AIProviderConfig.from_settings(config_or_settings)

    # 1. AI disabled -> Null provider (fail closed / no-op)
    if not config.enabled:
        return NullAIProvider()

    provider_name = config.provider.lower().strip()

    # 2. Explicit null provider
    if provider_name in ("null", "none", "inactive"):
        return NullAIProvider()

    # 3. Explicit mock provider for offline development/testing
    if provider_name == "mock":
        return custom_mock or MockAIProvider()

    # 4. Known future provider keys reserving clean extension points
    # When real providers are implemented, they will be registered here explicitly.
    # In Phase 2C, attempting to select a real provider fails closed immediately.
    SUPPORTED_FUTURE_PROVIDERS = {"gemini", "openai", "anthropic", "deepseek"}
    if provider_name in SUPPORTED_FUTURE_PROVIDERS:
        raise AIConfigurationError(
            f"AI provider '{provider_name}' is not yet implemented or enabled in this phase. "
            f"Phase 2C supports 'mock' and 'null' only. Do not attempt live connection."
        )

    # 5. Unsupported provider name -> fail closed
    raise AIConfigurationError(
        f"Unsupported AI provider '{config.provider}'. Supported providers: 'mock', 'null'."
    )
