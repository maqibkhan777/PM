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
        from app.services.ai.config import resolve_ai_provider
        self.mgr = manager or db_manager
        self.provider = provider or resolve_ai_provider()
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

    async def evaluate_attention(
        self,
        context: Optional[AIContext] = None,
        team_group: Optional[str] = None,
        actor: str = "PMOperationsAgent",
    ) -> "PMAttentionAnalysis":
        """Run attention analysis with strict safety, human-review enforcement, and unified auditing.
        
        If AI_ENABLED is False, fail closed and return deterministic advisory no-op without calling provider.
        """
        from app.services.ai.models import PMAttentionAnalysis

        ctx = context or self.context_builder.build_attention_context(team_group=team_group)

        if not getattr(settings, "AI_ENABLED", False):
            logger.info("AI attention analysis skipped: AI_ENABLED is False in configuration.")
            return PMAttentionAnalysis(
                analysis_id=f"analysis-disabled-{ctx.context_id}",
                generated_at=ctx.timestamp,
                scope_team=ctx.team_name or "Mursaleen Cluster",
                summary="AI attention analysis is disabled (AI_ENABLED=false).",
                attention_items=[],
                evidence=["AI disabled in settings."],
                recommendation="Enable AI_ENABLED to generate automated attention recommendations.",
                confidence=1.0,
                uncertainty_or_missing_info="AI_ENABLED=false",
                proposed_action=None,
                requires_human_review=True,
            )

        # 1. Run provider attention analysis
        try:
            analysis = await self.provider.analyze_attention(ctx)
        except Exception as e:
            logger.error(f"AI provider failed during attention analysis: {e}", exc_info=True)
            self.audit_service.log_action(
                actor=actor,
                action="AI_ATTENTION_ANALYSIS",
                target=ctx.context_id,
                result="FAILED",
                details={"error": str(e), "objective": ctx.objective},
            )
            raise

        # 2. Safety Gate validation
        is_valid, violation_msg = self.safety_gate.validate_attention_analysis(analysis)
        if not is_valid:
            logger.warning(f"AISafetyGate rejected attention analysis for context {ctx.context_id}: {violation_msg}")
            self.audit_service.log_action(
                actor=actor,
                action="AI_SAFETY_VIOLATION",
                target=ctx.context_id,
                result="REJECTED",
                details={
                    "violation": violation_msg,
                    "target_type": "PMAttentionAnalysis",
                    "confidence": analysis.confidence,
                },
            )
            raise AISafetyViolation(f"AI Attention Analysis rejected by safety gate: {violation_msg}")

        # 3. Log validated analysis to unified audit log
        self.audit_service.log_action(
            actor=actor,
            action="AI_ATTENTION_ANALYSIS",
            target=analysis.analysis_id,
            result="COMPLETED",
            details={
                "scope_team": analysis.scope_team,
                "attention_items_count": len(analysis.attention_items),
                "confidence": analysis.confidence,
                "requires_human_review": analysis.requires_human_review,
                "has_proposed_action": bool(analysis.proposed_action),
                "proposed_action_type": analysis.proposed_action.action_type if analysis.proposed_action else None,
                "flagged_issue_keys": [it.issue_key for it in analysis.attention_items],
            },
        )

        return analysis

