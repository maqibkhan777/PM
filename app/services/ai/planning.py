"""AI planning service orchestrating context intake, provider execution, safety gating, and auditing."""

import logging
import time
from typing import Any, Dict, Optional
from app.config.settings import settings
from app.core.models.planning import PlanningContext, PlanningProposal
from app.database.connection import DatabaseManager, db_manager
from app.services.ai.config import resolve_ai_provider
from app.services.ai.planning_prompt import PLANNING_PROMPT_VERSION
from app.services.ai.provider import AIProvider, NullAIProvider
from app.services.ai.safety import AISafetyGate, AISafetyViolation
from app.services.audit_service import AuditService
from app.core.planning.validator import PlanningProposalValidator
from app.core.models.planning import (
    EstimateUnit,
    EvidenceReference,
    EvidenceType,
    PlanningContext,
    PlanningEstimate,
    PlanningProposal,
    ProposalValidationResult,
)

from app.core.intelligence.effort_retrieval_service import (
    HistoricalEffortBenchmarkRetrievalService,
)
from app.core.intelligence.effort_recommendation_models import (
    BenchmarkRecommendationStatus,
    TaskEffortBenchmarkRecommendation,
)

logger = logging.getLogger(__name__)


def validate_planning_proposal(
    planning_context: PlanningContext,
    planning_proposal: PlanningProposal,
) -> ProposalValidationResult:
    """Deterministic convenience function to validate an AI planning proposal against PlanningContext."""
    return PlanningProposalValidator.validate_proposal(planning_context, planning_proposal)



class AIPlanningService:
    """Entry point for AI planning support in PM Operations Agent."""

    def __init__(
        self,
        provider: Optional[AIProvider] = None,
        manager: Optional[DatabaseManager] = None,
        retrieval_service: Optional[HistoricalEffortBenchmarkRetrievalService] = None,
    ):
        self.mgr = manager or db_manager
        self.provider = provider or resolve_ai_provider()
        self.safety_gate = AISafetyGate()
        self.audit_service = AuditService(self.mgr)
        self.retrieval_service = retrieval_service or HistoricalEffortBenchmarkRetrievalService(self.mgr)

    async def generate_plan(
        self,
        planning_context: PlanningContext,
        actor: str = "PMPlanningEngine",
    ) -> PlanningProposal:
        """Run AI planning reasoning pipeline with strict safety, human-review enforcement, and auditing.

        If AI_ENABLED is False, fail closed and return deterministic advisory no-op without calling provider.
        """
        if not planning_context or not isinstance(planning_context, PlanningContext):
            raise ValueError("A valid PlanningContext instance is required for AI planning.")

        # 1. Check AI_ENABLED configuration
        if not getattr(settings, "AI_ENABLED", False):
            logger.info("AI planning skipped: AI_ENABLED is False in configuration.")
            return await NullAIProvider().analyze_planning(planning_context)

        t0 = time.monotonic()

        # 2. Retrieve empirical effort benchmarks for tasks in context
        benchmark_recs = self.retrieval_service.recommend_effort_for_context(
            tasks=planning_context.tasks,
            default_project_key=planning_context.team_group,
        )

        # 3. Run provider planning analysis
        try:
            proposal = await self.provider.analyze_planning(planning_context)
        except Exception as e:
            logger.error(f"AI provider failed during planning analysis: {e}", exc_info=True)
            self.audit_service.log_action(
                actor=actor,
                action="AI_PLANNING",
                target=planning_context.team_group or "TeamPlanningContext",
                result="FAILED",
                details={
                    "error": str(e),
                    "prompt_version": PLANNING_PROMPT_VERSION,
                    "context_version": planning_context.context_version,
                    "anchor_date": planning_context.anchor_date,
                },
            )
            raise

        # 4. Post-process & Ground task proposals with deterministic empirical benchmarks
        if benchmark_recs:
            for tp in proposal.task_proposals:
                rec = benchmark_recs.get(tp.issue_key)
                if not rec:
                    continue

                # Add deterministic benchmark evidence reference
                if rec.reliability_status in (
                    BenchmarkRecommendationStatus.USABLE,
                    BenchmarkRecommendationStatus.LOW_CONFIDENCE,
                ):
                    evidence_desc = (
                        f"Empirical Benchmark ({rec.reliability_status.value}): {rec.grouping_used} | "
                        f"n={rec.sample_count} | Median(P50)={rec.p50_effort_hours}h"
                    )
                    if rec.p90_effort_hours is not None:
                        evidence_desc += f", P75/P90={rec.p90_effort_hours}h"
                    if rec.fallback_disclosure:
                        evidence_desc += f" | Fallback: {rec.fallback_disclosure}"
                    if rec.data_quality_warning:
                        evidence_desc += f" | Warning: {rec.data_quality_warning}"

                    # Attach to task proposal evidence references
                    tp.evidence_references.append(
                        EvidenceReference(
                            evidence_type=EvidenceType.RESOURCE_HISTORY,
                            source_identifier=rec.dimension_key or f"benchmark:{rec.project_key}",
                            description=evidence_desc[:300],
                            relevance="DIRECT",
                        )
                    )

                    # If proposal has no estimate or needs empirical grounding, provide grounded estimate
                    if tp.proposed_estimate is None and rec.recommended_effort_hours is not None:
                        tp.proposed_estimate = PlanningEstimate(
                            value=rec.recommended_effort_hours,
                            unit=EstimateUnit.HOURS,
                            confidence=0.85 if rec.reliability_status == BenchmarkRecommendationStatus.USABLE else 0.60,
                            rationale=rec.explanation,
                            evidence_references=[
                                EvidenceReference(
                                    evidence_type=EvidenceType.RESOURCE_HISTORY,
                                    source_identifier=rec.dimension_key or f"benchmark:{rec.project_key}",
                                    description=evidence_desc[:300],
                                    relevance="DIRECT",
                                )
                            ],
                        )
                    elif tp.proposed_estimate is not None:
                        # Append evidence reference to proposed_estimate
                        tp.proposed_estimate.evidence_references.append(
                            EvidenceReference(
                                evidence_type=EvidenceType.RESOURCE_HISTORY,
                                source_identifier=rec.dimension_key or f"benchmark:{rec.project_key}",
                                description=evidence_desc[:300],
                                relevance="DIRECT",
                            )
                        )
                elif rec.reliability_status == BenchmarkRecommendationStatus.INSUFFICIENT_DATA:
                    # Explicit disclosure of insufficient data (zero manufactured estimate numbers)
                    tp.evidence_references.append(
                        EvidenceReference(
                            evidence_type=EvidenceType.DATA_QUALITY,
                            source_identifier=rec.dimension_key or f"benchmark:{rec.project_key}",
                            description=f"Insufficient benchmark data: {rec.explanation}"[:300],
                            relevance="CONTEXTUAL",
                        )
                    )


        latency = round(time.monotonic() - t0, 3)

        # 3. Safety Gate validation & grounding check
        is_valid, violation_msg = self.safety_gate.validate_planning_proposal(proposal, planning_context)
        if not is_valid:
            logger.warning(
                f"AISafetyGate rejected planning proposal for context anchor {planning_context.anchor_date}: {violation_msg}"
            )
            self.audit_service.log_action(
                actor=actor,
                action="AI_SAFETY_VIOLATION",
                target=planning_context.team_group or "TeamPlanningContext",
                result="REJECTED",
                details={
                    "violation": violation_msg,
                    "target_type": "PlanningProposal",
                    "confidence": proposal.overall_confidence if hasattr(proposal, "overall_confidence") else 0.0,
                    "prompt_version": PLANNING_PROMPT_VERSION,
                },
            )
            raise AISafetyViolation(f"AI Planning Proposal rejected by safety gate: {violation_msg}")

        # 4. Mandatory enforcement: requires_human_review must remain True
        if not proposal.requires_human_review:
            raise AISafetyViolation("requires_human_review cannot be False on PlanningProposal.")

        # 5. Extract safe audit metrics (no secrets, no full raw dump)
        token_usage = getattr(self.provider, "last_usage", {})
        self.audit_service.log_action(
            actor=actor,
            action="AI_PLANNING",
            target=planning_context.team_group or "TeamPlanningContext",
            result="COMPLETED",
            details={
                "operation": "planning",
                "prompt_version": PLANNING_PROMPT_VERSION,
                "context_version": planning_context.context_version,
                "anchor_date": planning_context.anchor_date,
                "latency_seconds": latency,
                "prompt_tokens": token_usage.get("prompt_tokens"),
                "completion_tokens": token_usage.get("completion_tokens"),
                "task_proposals_count": len(proposal.task_proposals),
                "sequencing_proposals_count": len(proposal.sequencing_proposals),
                "risk_signals_count": len(proposal.risk_signals),
                "assumptions_count": len(proposal.assumptions),
                "overall_confidence": proposal.overall_confidence,
                "requires_human_review": proposal.requires_human_review,
                "is_truncated_context": bool(
                    planning_context.truncation and planning_context.truncation.is_truncated
                ),
            },
        )

        return proposal
