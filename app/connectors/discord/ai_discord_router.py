"""AI Discord Router Service for conversational and analytical PM AI queries.

Routes requests through dedicated AI services:
1. General PM question -> Grounded PM context + AIDecisionService / Provider analysis
2. Attention Analysis -> PMAttentionAnalysisService / AIDecisionService.evaluate_attention
3. Planning Proposal -> Deterministic PlanningContext composition + AIPlanningService.generate_plan
4. Help / Capabilities Explanation -> Deterministic explanation of available features

CRITICAL SAFETY INVARIANTS:
- Strictly read-only: Zero Jira mutations, zero Action Engine execution, zero approvals.
- Provider-agnostic: Resolves AI provider dynamically via resolve_ai_provider without hardcoding.
- Grounded: Grounds answers in local database projections without hallucinating Jira facts.
- Bounded: Enforces input/output token and character boundaries.
"""

import logging
import re
import time
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union

from app.config.settings import settings
from app.database.connection import DatabaseManager, db_manager
from app.database.repositories import (
    EmployeeRoleRepository,
    JiraIssueStateRepository,
    JiraWorklogRepository,
    JiraIssueLinkRepository,
    ArtifactRepository,
)
from app.core.planning.dag import DependencyGraph
from app.core.planning.artifacts import ArtifactEngine
from app.core.planning.queue_composer import ResourceQueueComposer
from app.core.planning.forecaster import TeamScheduleForecaster
from app.core.planning.context import PlanningContextBuilder
from app.services.ai.config import resolve_ai_provider
from app.services.ai.decision import AIDecisionService
from app.services.ai.planning import AIPlanningService
from app.services.ai.models import (
    AIContext,
    AIDecisionType,
    AIDecision,
    PMAttentionAnalysis,
)
from app.core.models.planning import (
    PlanningContext,
    PlanningProposal,
)
from app.services.ai.report_formatter import AIAttentionReportFormatter
from app.services.ai.safety import AISafetyViolation
from app.services.audit_service import AuditService
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)


class AIRequestIntent(str, Enum):
    """Categorized user intent for Discord AI requests."""
    GENERAL_QA = "GENERAL_QA"
    ATTENTION_ANALYSIS = "ATTENTION_ANALYSIS"
    PLANNING_PROPOSAL = "PLANNING_PROPOSAL"
    HELP = "HELP"
    UNKNOWN = "UNKNOWN"


AI_HELP_MESSAGE = (
    "👋 **PM AI Operations Assistant**\n\n"
    "I can assist you with read-only PM analysis and advisory insights. Here are the capabilities I support:\n\n"
    "1. **PM Attention Analysis:** Ask about bottlenecks, stalled tasks, overdue items, or team attention signals.\n"
    "   *Example:* `@PM AI what tasks need attention right now?` or `@PM AI analyze team attention`\n\n"
    "2. **Planning Proposals:** Ask for a sprint or 2-week planning proposal / forecast based on team capacity and dependencies.\n"
    "   *Example:* `@PM AI propose a schedule plan for the team` or `@PM AI what is the projected timeline?`\n\n"
    "3. **General PM & Task Questions:** Ask about Jira ticket statuses, workload summaries, or active priorities.\n"
    "   *Example:* `@PM AI what is the status and priority of WSSS-326?`\n\n"
    "🔒 *Note: I operate strictly in read-only advisory mode. I do not execute Jira mutations, schedule changes, or approvals.*"
)


class AIDiscordRouterService:
    """Application router connecting Discord mention requests to PM AI services."""

    def __init__(
        self,
        manager: Optional[DatabaseManager] = None,
        ai_decision_service: Optional[AIDecisionService] = None,
        ai_planning_service: Optional[AIPlanningService] = None,
        audit_service: Optional[AuditService] = None,
    ):
        self.mgr = manager or db_manager
        self.issue_repo = JiraIssueStateRepository(self.mgr)
        self.role_repo = EmployeeRoleRepository(self.mgr)
        self.worklog_repo = JiraWorklogRepository(self.mgr)
        self.link_repo = JiraIssueLinkRepository(self.mgr)
        self.artifact_repo = ArtifactRepository(self.mgr)

        self.ai_decision_service = ai_decision_service or AIDecisionService(manager=self.mgr)
        self.ai_planning_service = ai_planning_service or AIPlanningService(manager=self.mgr)
        self.audit_service = audit_service or AuditService(self.mgr)

    def classify_intent(self, prompt: str) -> AIRequestIntent:
        """Classify user's natural language prompt into supported PM AI capabilities."""
        if not prompt or not prompt.strip():
            return AIRequestIntent.HELP

        p = prompt.strip().lower()

        # 1. Check for Help request
        if p in ("help", "commands", "what can you do", "capabilities", "features", "?", "--help", "-h"):
            return AIRequestIntent.HELP
        if re.search(r"\b(how do i use|what can you do|help me|list capabilities)\b", p):
            return AIRequestIntent.HELP

        # 2. Check for Attention / Bottleneck / Stalled request
        if re.search(r"\b(attention|stalled|inactive|overdue|reopened|unassigned|blocked|blocker|blockers|digest|risks|risk signals)\b", p):
            # Check if it is primarily asking for planning
            if re.search(r"\b(plan|proposal|schedule|sequence|timeline|forecast)\b", p):
                return AIRequestIntent.PLANNING_PROPOSAL
            return AIRequestIntent.ATTENTION_ANALYSIS

        # 3. Check for Planning Proposal / Timeline / Scheduling request
        if re.search(r"\b(plan|proposal|planning|schedule|scheduling|sequence|sequencing|timeline|forecast|roadmap|capacity plan)\b", p):
            return AIRequestIntent.PLANNING_PROPOSAL

        # 4. General PM / Ticket Query
        return AIRequestIntent.GENERAL_QA

    async def route_request(
        self,
        prompt: str,
        actor_id: str,
        channel_id: str,
        message_id: str,
        thread_context: Optional[List[str]] = None,
    ) -> Union[str, Dict[str, Any]]:
        """Route a user request to the appropriate read-only AI service and return formatted response."""
        t0 = time.monotonic()
        intent = self.classify_intent(prompt)
        provider_name = getattr(settings, "AI_PROVIDER", "mock")
        model_name = getattr(settings, "AI_MODEL", None)
        provider_id = f"{provider_name}:{model_name}" if model_name else provider_name
        actor = f"discord:{actor_id}"

        # If AI is globally disabled
        if not getattr(settings, "AI_ENABLED", False):
            self.audit_service.log_action(
                actor=actor,
                action="AI_DISCORD_REQUEST",
                target=channel_id,
                result="AI_DISABLED",
                details={
                    "message_id": message_id,
                    "channel_id": channel_id,
                    "intent": intent.value,
                    "prompt_length": len(prompt),
                    "error_category": "AI_DISABLED",
                    "duration_ms": round((time.monotonic() - t0) * 1000, 2),
                },
            )
            return (
                "ℹ️ PM AI assistant is currently disabled (`AI_ENABLED=false`). "
                "Contact an administrator to enable AI capabilities."
            )

        try:
            if intent == AIRequestIntent.HELP:
                res = AI_HELP_MESSAGE
                outcome = "COMPLETED"

            elif intent == AIRequestIntent.ATTENTION_ANALYSIS:
                res = await self._handle_attention_request(prompt, actor=actor)
                outcome = "COMPLETED"

            elif intent == AIRequestIntent.PLANNING_PROPOSAL:
                res = await self._handle_planning_request(prompt, actor=actor)
                outcome = "COMPLETED"

            else:  # GENERAL_QA
                res = await self._handle_general_qa(prompt, actor=actor, thread_context=thread_context)
                outcome = "COMPLETED"

            duration_ms = round((time.monotonic() - t0) * 1000, 2)
            self.audit_service.log_action(
                actor=actor,
                action="AI_DISCORD_REQUEST",
                target=channel_id,
                result=outcome,
                details={
                    "message_id": message_id,
                    "channel_id": channel_id,
                    "intent": intent.value,
                    "provider": provider_id,
                    "prompt_length": len(prompt),
                    "duration_ms": duration_ms,
                    "outcome": outcome,
                },
            )
            return res

        except AISafetyViolation as sv:
            logger.warning(f"AISafetyViolation during Discord mention processing: {sv}")
            duration_ms = round((time.monotonic() - t0) * 1000, 2)
            self.audit_service.log_action(
                actor=actor,
                action="AI_DISCORD_REQUEST",
                target=channel_id,
                result="SAFETY_REJECTED",
                details={
                    "message_id": message_id,
                    "channel_id": channel_id,
                    "intent": intent.value,
                    "provider": provider_id,
                    "error_category": "SAFETY_VIOLATION",
                    "violation": str(sv),
                    "duration_ms": duration_ms,
                },
            )
            return f"❌ AI response failed safety verification: {sv}"

        except Exception as e:
            logger.error(f"Error handling Discord AI mention request: {e}", exc_info=True)
            duration_ms = round((time.monotonic() - t0) * 1000, 2)
            self.audit_service.log_action(
                actor=actor,
                action="AI_DISCORD_REQUEST",
                target=channel_id,
                result="FAILED",
                details={
                    "message_id": message_id,
                    "channel_id": channel_id,
                    "intent": intent.value,
                    "provider": provider_id,
                    "error_category": "PROVIDER_ERROR",
                    "error": str(e),
                    "duration_ms": duration_ms,
                },
            )
            return "❌ An error occurred while processing your AI request. Please check system logs."

    async def _handle_attention_request(self, prompt: str, actor: str) -> Union[str, Dict[str, Any]]:
        """Handle attention analysis query using AIDecisionService."""
        team_group = settings.JIRA_TEAM_GROUP.strip() if settings.is_jira_team_group_configured() else "Mursaleen Cluster"
        analysis: PMAttentionAnalysis = await self.ai_decision_service.evaluate_attention(
            team_group=team_group,
            actor=actor,
        )
        return AIAttentionReportFormatter.format_discord_embeds(analysis)

    async def _handle_planning_request(self, prompt: str, actor: str) -> str:
        """Handle planning proposal request using deterministic context composition + AIPlanningService."""
        # 1. Deterministic upstream composition
        team_group = settings.JIRA_TEAM_GROUP.strip() if settings.is_jira_team_group_configured() else "Mursaleen Cluster"
        
        # Load resources for team
        all_assignments = self.role_repo.list_assignments()
        target_assignments = [
            a for a in all_assignments
            if not team_group or a.get("team_group") == team_group
        ] or all_assignments

        composer = ResourceQueueComposer(manager=self.mgr)
        snapshots = []
        for a in target_assignments:
            acc_id = a.get("account_id")
            disp_name = a.get("display_name")
            if acc_id:
                snap = composer.compose_snapshot(account_id=acc_id, display_name=disp_name, team_group=team_group)
                snapshots.append(snap)

        # Build Dependency Graph
        dep_links = self.link_repo.list_all_links(active_only=True)
        dep_graph = DependencyGraph(include_only_hard_blocks=True)
        for link in dep_links:
            src = link.get("source_issue_key", "")
            tgt = link.get("target_issue_key", "")
            ltype = link.get("link_type_name", "blocks")
            class_str = link.get("classification", "HARD_BLOCK")
            try:
                from app.core.models.planning import DependencyClassification
                classification = DependencyClassification(class_str)
            except Exception:
                classification = DependencyClassification.HARD_BLOCK
            if src and tgt:
                dep_graph.add_edge(source_key=src, target_key=tgt, link_type=ltype, classification=classification)

        # Build Artifacts
        art_records = []
        art_rels = []
        # If any artifact records exist in DB
        try:
            raw_arts = self.artifact_repo.list_all_artifacts(active_only=True)
            for ra in raw_arts:
                from app.core.models.planning import ArtifactRecord, ArtifactRelationshipRecord, ArtifactType, ArtifactStatus, ArtifactProvenance
                try:
                    atype = ArtifactType(ra.get("artifact_type", "GENERIC"))
                except Exception:
                    atype = ArtifactType.GENERIC
                try:
                    astatus = ArtifactStatus(ra.get("status", "PLANNED"))
                except Exception:
                    astatus = ArtifactStatus.PLANNED
                try:
                    aprov = ArtifactProvenance(ra.get("provenance", "EXPLICIT_JIRA_LABEL"))
                except Exception:
                    aprov = ArtifactProvenance.EXPLICIT_JIRA_LABEL

                art_records.append(
                    ArtifactRecord(
                        id=ra.get("id", ""),
                        name=ra.get("name", ""),
                        project_key=ra.get("project_key", ""),
                        artifact_type=atype,
                        status=astatus,
                        producer_issue_key=ra.get("producer_issue_key"),
                        provenance=aprov,
                        confidence=ra.get("confidence", "HIGH"),
                        first_seen_at=ra.get("first_seen_at", utc_now_iso()),
                        last_seen_at=ra.get("last_seen_at", utc_now_iso()),
                        is_active=bool(ra.get("is_active", 1)),
                    )
                )
        except Exception:
            pass

        # Build Forecaster schedule
        forecaster = TeamScheduleForecaster(manager=self.mgr)
        schedule = forecaster.forecast_team_schedule(
            team_snapshots=snapshots,
            dependency_graph=dep_graph,
            horizon_working_days=settings.PLANNING_HORIZON_WORKING_DAYS,
        )

        # Build PlanningContext
        builder = PlanningContextBuilder()
        ctx = builder.build_context(
            team_snapshots=snapshots,
            schedule_projection=schedule,
            dependency_graph=dep_graph,
            artifact_records=art_records,
            artifact_relationships=art_rels,
            team_group=team_group,
            horizon_working_days=settings.PLANNING_HORIZON_WORKING_DAYS,
        )

        # 2. Run AI Planning Service
        proposal: PlanningProposal = await self.ai_planning_service.generate_plan(ctx, actor=actor)

        # Format proposal strictly as read-only advisory proposal
        lines = [
            "📋 **AI Planning Proposal (Advisory Only)**",
            f"**Anchor Date:** {proposal.anchor_date} | **Horizon:** {proposal.planning_horizon_working_days} working days",
            f"**Confidence:** {proposal.overall_confidence:.0%}",
            "",
            f"**Summary:** {proposal.summary}",
            "",
            f"**Proposed Tasks ({len(proposal.task_proposals)}):**",
        ]

        for tp in proposal.task_proposals[:10]:
            est_str = f" (~{tp.proposed_estimate.value}{tp.proposed_estimate.unit.value})" if tp.proposed_estimate else ""
            date_str = f" [Due: {tp.proposed_due_date}]" if tp.proposed_due_date else ""
            risk_badge = f" ⚠️ {tp.risk_level}" if tp.risk_level in ("HIGH", "MEDIUM") else ""
            lines.append(f"• **{tp.issue_key}**{est_str}{date_str}{risk_badge}")

        if len(proposal.task_proposals) > 10:
            lines.append(f"• ... and {len(proposal.task_proposals) - 10} more tasks.")

        if proposal.risk_signals:
            lines.append("")
            lines.append(f"**Key Risk Signals ({len(proposal.risk_signals)}):**")
            for rs in proposal.risk_signals[:3]:
                lines.append(f"• [{rs.severity}] {rs.explanation}")

        lines.append("")
        lines.append("⚠️ *This proposal is for review only. It has not been approved or executed.*")
        return "\n".join(lines)

    async def _handle_general_qa(
        self,
        prompt: str,
        actor: str,
        thread_context: Optional[List[str]] = None,
    ) -> str:
        """Handle general PM question by grounding context and querying provider."""
        # 1. Detect if a specific Jira ticket is mentioned (e.g. WSSS-326, TREN-378, PROJ-123)
        ticket_match = re.search(r"\b([A-Z][A-Z0-9_]+-\d+)\b", prompt)
        
        ctx_builder = self.ai_decision_service.context_builder
        if ticket_match:
            ticket_key = ticket_match.group(1).upper()
            ai_ctx = ctx_builder.build_task_context(
                task_key=ticket_key,
                objective=f"Answer user PM question: {prompt}",
                metadata={"user_prompt": prompt, "thread_context": thread_context or []},
            )
        else:
            team_group = settings.JIRA_TEAM_GROUP.strip() if settings.is_jira_team_group_configured() else "Mursaleen Cluster"
            ai_ctx = ctx_builder.build_attention_context(
                team_group=team_group,
                objective=f"Answer user PM question: {prompt}",
                metadata={"user_prompt": prompt, "thread_context": thread_context or []},
            )

        decision: AIDecision = await self.ai_decision_service.evaluate(ai_ctx, actor=actor)

        # Format grounded answer
        res_lines = [
            f"🤖 **PM AI Insight**",
            f"{decision.explanation}",
        ]
        if decision.evidence:
            res_lines.append("")
            res_lines.append("**Evidence / Facts:**")
            for ev in decision.evidence[:4]:
                res_lines.append(f"• {ev}")

        if decision.recommendation and decision.recommendation.value != "NO_ACTION":
            res_lines.append("")
            res_lines.append(f"**Recommendation:** {decision.recommendation.value}")

        return "\n".join(res_lines)


# Global singleton router
ai_discord_router = AIDiscordRouterService()
