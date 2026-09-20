"""AI decision service orchestrating context building, provider execution, safety gating, and auditing."""

import logging
from typing import Any, Dict, Optional
from app.config.settings import settings
from app.database.connection import DatabaseManager, db_manager
from app.services.ai.context import ContextBuilder
from app.services.ai.models import AIContext, AIDecision, AIDecisionType, AIRecommendationType
from app.services.ai.provider import AIProvider, NullAIProvider
from app.services.ai.safety import AISafetyGate, AISafetyViolation
from app.services.audit_service import AuditService

logger = logging.getLogger(__name__)


class AIDecisionService:
    """Entry point for AI decision support in PM Operations Agent."""

    def __init__(
        self,
        provider: Optional[AIProvider] = None,
        manager: Optional[DatabaseManager] = None,
    ):
        self.mgr = manager or db_manager
        self.provider = provider or NullAIProvider()
        self.context_builder = ContextBuilder(self.mgr)
        self.safety_gate = AISafetyGate()
        self.audit_service = AuditService(self.mgr)

    async def evaluate(
        self,
        context: AIContext,
        actor: str = "PMOperationsAgent",
    ) -> AIDecision:
        """Run decision support pipeline with strict safety and auditing.
        
        If AI_ENABLED is False, fail closed or return deterministic no-op without invoking provider.
        """
        if not getattr(settings, "AI_ENABLED", False):
            logger.info("AI evaluation skipped: AI_ENABLED is False in configuration.")
            return AIDecision(
                decision_type=AIDecisionType.GENERAL_ANALYSIS,
                recommendation=AIRecommendationType.NO_ACTION,
                confidence=1.0,
                evidence=["AI is disabled in system configuration (AI_ENABLED=false)."],
                explanation="AI decision support is currently disabled.",
                proposed_action=None,
                requires_approval=False,
            )

        # 1. Run provider analysis
        try:
            decision = await self.provider.analyze(context)
        except Exception as e:
            logger.error(f"AI provider failed during analysis: {e}", exc_info=True)
            # Record failed evaluation in audit log
            self.audit_service.log_action(
                actor=actor,
                action="AI_EVALUATION",
                target=context.context_id,
                result="FAILED",
                details={"error": str(e), "objective": context.objective},
            )
            raise

        # 2. Safety Gate validation
        is_valid, violation_msg = self.safety_gate.validate_decision(decision)
        if not is_valid:
            logger.warning(f"AISafetyGate rejected decision for context {context.context_id}: {violation_msg}")
            self.audit_service.log_action(
                actor=actor,
                action="AI_SAFETY_VIOLATION",
                target=context.context_id,
                result="REJECTED",
                details={
                    "violation": violation_msg,
                    "decision_type": str(decision.decision_type),
                    "confidence": decision.confidence,
                },
            )
            raise AISafetyViolation(f"AI Decision rejected by safety gate: {violation_msg}")

        # 3. Log validated decision to unified audit log
        self.audit_service.log_action(
            actor=actor,
            action="AI_DECISION",
            target=context.context_id,
            result="COMPLETED",
            details={
                "decision_type": str(decision.decision_type.value),
                "recommendation": str(decision.recommendation.value),
                "confidence": decision.confidence,
                "requires_approval": decision.requires_approval,
                "has_proposed_action": bool(decision.proposed_action),
                "proposed_action_type": decision.proposed_action.action_type if decision.proposed_action else None,
                "objective": context.objective,
            },
        )

        return decision
