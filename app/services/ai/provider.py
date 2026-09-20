"""Provider protocol and deterministic mock/null implementation for PM AI decision support."""

from typing import Protocol, runtime_checkable
from app.services.ai.models import (
    AIContext,
    AIDecision,
    AIDecisionType,
    AIRecommendationType,
    ProposedAction,
)


@runtime_checkable
class AIProvider(Protocol):
    """Abstract protocol for AI decision support providers."""

    async def analyze(self, context: AIContext) -> AIDecision:
        """Analyze the supplied context and return a structured AIDecision."""
        ...


class NullAIProvider:
    """Default inactive/fallback provider when AI is unconfigured or disabled."""

    async def analyze(self, context: AIContext) -> AIDecision:
        return AIDecision(
            decision_type=AIDecisionType.GENERAL_ANALYSIS,
            recommendation=AIRecommendationType.NO_ACTION,
            confidence=1.0,
            evidence=["AI provider is inactive or disabled."],
            explanation="Null provider returns deterministic no-op decision.",
            proposed_action=None,
            requires_approval=False,
        )


class MockAIProvider:
    """Deterministic mock provider for testing and offline development."""

    def __init__(
        self,
        decision_type: AIDecisionType = AIDecisionType.PM_ATTENTION,
        recommendation: AIRecommendationType = AIRecommendationType.REVIEW_TASK,
        confidence: float = 0.85,
        evidence: list[str] = None,
        explanation: str = "Deterministic mock decision based on provided context.",
        proposed_action: ProposedAction = None,
        requires_approval: bool = True,
    ):
        self.decision_type = decision_type
        self.recommendation = recommendation
        self.confidence = confidence
        self.evidence = evidence or ["Mock evidence sample: task updated over threshold."]
        self.explanation = explanation
        self.proposed_action = proposed_action
        self.requires_approval = requires_approval

    async def analyze(self, context: AIContext) -> AIDecision:
        return AIDecision(
            decision_type=self.decision_type,
            recommendation=self.recommendation,
            confidence=self.confidence,
            evidence=self.evidence,
            explanation=f"{self.explanation} (Context objective: {context.objective})",
            proposed_action=self.proposed_action,
            requires_approval=self.requires_approval,
        )
