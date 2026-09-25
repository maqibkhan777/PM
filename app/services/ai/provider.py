"""Provider protocol and deterministic mock/null implementation for PM AI decision support."""

from typing import List, Optional, Protocol, runtime_checkable
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
from app.services.ai.models import (
    AIContext,
    AIDecision,
    AIDecisionType,
    AIRecommendationType,
    AttentionItemAnalysis,
    PMAttentionAnalysis,
    ProposedAction,
)
from app.services.ai.providers.deepseek import DeepSeekAIProvider


@runtime_checkable
class AIProvider(Protocol):
    """Abstract protocol for AI decision support providers."""

    async def analyze(self, context: AIContext) -> AIDecision:
        """Analyze the supplied context and return a structured AIDecision."""
        ...

    async def analyze_attention(self, context: AIContext) -> PMAttentionAnalysis:
        """Analyze attention candidates in context and return a structured PMAttentionAnalysis."""
        ...

    async def analyze_planning(self, context: PlanningContext) -> PlanningProposal:
        """Analyze deterministic planning context and return a structured PlanningProposal."""
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

    async def analyze_attention(self, context: AIContext) -> PMAttentionAnalysis:
        return PMAttentionAnalysis(
            analysis_id=f"analysis-null-{context.context_id}",
            generated_at=context.timestamp,
            scope_team=context.team_name or "Mursaleen Cluster",
            summary="AI provider is inactive or disabled; no attention analysis generated.",
            attention_items=[],
            evidence=["AI provider is inactive or disabled."],
            recommendation="Enable an AI provider to receive automated attention recommendations.",
            confidence=1.0,
            uncertainty_or_missing_info="AI provider is disabled.",
            proposed_action=None,
            requires_human_review=True,
        )

    async def analyze_planning(self, context: PlanningContext) -> PlanningProposal:
        return PlanningProposal(
            proposal_version="proposal-v1",
            generated_at=context.generated_at,
            context_version=context.context_version,
            anchor_date=context.anchor_date,
            planning_horizon_working_days=context.planning_horizon_working_days,
            requires_human_review=True,
            overall_confidence=1.0,
            summary="AI provider is inactive or disabled; no planning proposal generated.",
            task_proposals=[],
            sequencing_proposals=[],
            risk_signals=[],
            assumptions=[],
            evidence_references=[],
        )


class MockAIProvider:
    """Deterministic mock provider for testing and offline development."""

    def __init__(
        self,
        decision_type: AIDecisionType = AIDecisionType.PM_ATTENTION,
        recommendation: AIRecommendationType = AIRecommendationType.REVIEW_TASK,
        confidence: float = 0.85,
        evidence: Optional[List[str]] = None,
        explanation: str = "Deterministic mock decision based on provided context.",
        proposed_action: Optional[ProposedAction] = None,
        requires_approval: bool = True,
        custom_attention_analysis: Optional[PMAttentionAnalysis] = None,
        custom_planning_proposal: Optional[PlanningProposal] = None,
    ):
        self.decision_type = decision_type
        self.recommendation = recommendation
        self.confidence = confidence
        self.evidence = evidence or ["Mock evidence sample: task updated over threshold."]
        self.explanation = explanation
        self.proposed_action = proposed_action
        self.requires_approval = requires_approval
        self.custom_attention_analysis = custom_attention_analysis
        self.custom_planning_proposal = custom_planning_proposal


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

    async def analyze_attention(self, context: AIContext) -> PMAttentionAnalysis:
        if self.custom_attention_analysis:
            return self.custom_attention_analysis

        items: List[AttentionItemAnalysis] = []
        meta = context.metadata or {}

        # 1. Stale candidates
        for st in meta.get("stale_items", []):
            k = st.get("key")
            if not k:
                continue
            inact = st.get("inactivity_duration") or "unknown inactivity"
            items.append(
                AttentionItemAnalysis(
                    issue_key=k,
                    title=st.get("summary") or "Untitled task",
                    current_status=st.get("status") or "In Progress",
                    assignee=st.get("assignee"),
                    priority=st.get("priority"),
                    due_date=st.get("due_date"),
                    updated_at=st.get("updated_at"),
                    inactivity_duration=inact,
                    attention_reason=f"Task inactive for {inact} while in active progress state.",
                    supporting_evidence=[
                        f"Status is '{st.get('status')}'",
                        f"Last activity recorded {inact} ago",
                    ],
                    recommendation=f"Request progress update from {st.get('assignee') or 'assignee'}.",
                    confidence=0.85,
                    uncertainty_or_missing_info=None if st.get("assignee") else "Assignee is missing.",
                )
            )

        # 2. Overdue candidates
        for ov in meta.get("overdue_items", []):
            k = ov.get("key")
            if not k:
                continue
            items.append(
                AttentionItemAnalysis(
                    issue_key=k,
                    title=ov.get("summary") or "Untitled task",
                    current_status=ov.get("status") or "In Progress",
                    assignee=ov.get("assignee"),
                    priority=ov.get("priority"),
                    due_date=ov.get("due_date"),
                    updated_at=ov.get("updated_at"),
                    attention_reason=f"Due date {ov.get('due_date')} has passed without completion.",
                    supporting_evidence=[
                        f"Due date is {ov.get('due_date')}",
                        f"Current status is '{ov.get('status')}'",
                    ],
                    recommendation="Review timeline with assignee and reschedule or expedite.",
                    confidence=0.90,
                    uncertainty_or_missing_info=None if ov.get("due_date") else "Due date not specified.",
                )
            )

        # 3. Reopened candidates
        for ro in meta.get("reopened_items", []):
            k = ro.get("key")
            if not k:
                continue
            items.append(
                AttentionItemAnalysis(
                    issue_key=k,
                    title=ro.get("summary") or "Untitled task",
                    current_status=ro.get("status") or "Reopened",
                    assignee=ro.get("assignee"),
                    priority=ro.get("priority"),
                    updated_at=ro.get("updated_at"),
                    attention_reason="Task was reopened after prior resolution.",
                    supporting_evidence=[
                        f"Current status is '{ro.get('status')}'",
                        "Appears in reopened event/status stream",
                    ],
                    recommendation="Verify cause of regression and triage urgency.",
                    confidence=0.88,
                    uncertainty_or_missing_info=None if ro.get("assignee") else "Assignee is missing on reopened task.",
                )
            )

        # 4. Unassigned candidates
        for un in meta.get("unassigned_items", []):
            k = un.get("key")
            if not k:
                continue
            items.append(
                AttentionItemAnalysis(
                    issue_key=k,
                    title=un.get("summary") or "Untitled task",
                    current_status=un.get("status") or "To Do",
                    assignee=None,
                    priority=un.get("priority"),
                    updated_at=un.get("updated_at"),
                    attention_reason="Active task has no designated owner or assignee.",
                    supporting_evidence=[
                        "Assignee field is empty or unassigned",
                        f"Current status is '{un.get('status')}'",
                    ],
                    recommendation="Assign task to an appropriate team member during daily standup.",
                    confidence=0.92,
                    uncertainty_or_missing_info="Task owner is currently unassigned.",
                )
            )

        # Default fallback item if context had no candidate items in metadata
        if not items:
            summary = "No attention items flagged in current evaluation context."
            recommendation = "Maintain standard workflow monitoring."
            evidence = ["Zero items exceeded stale, overdue, reopened, or unassigned thresholds."]
            conf = 1.0
        else:
            summary = f"Flagged {len(items)} items requiring PM review across Mursaleen Cluster."
            recommendation = "Review highlighted tasks and confirm next steps with team members."
            evidence = [f"Found {len(items)} actionable items across standard PM health categories."]
            conf = 0.88

        return PMAttentionAnalysis(
            analysis_id=f"analysis-{context.context_id}",
            generated_at=context.timestamp,
            scope_team=context.team_name or "Mursaleen Cluster",
            summary=summary,
            attention_items=items,
            evidence=evidence,
            recommendation=recommendation,
            confidence=conf,
            uncertainty_or_missing_info=None,
            proposed_action=self.proposed_action,
            requires_human_review=True,
        )

    async def analyze_planning(self, context: PlanningContext) -> PlanningProposal:
        if self.custom_planning_proposal:
            return self.custom_planning_proposal

        # Deterministically build task proposals from context tasks
        task_proposals: List[TaskPlanningProposal] = []
        for idx, task in enumerate(context.tasks, 1):
            evidence_refs = [
                EvidenceReference(
                    evidence_type=EvidenceType.TASK_ESTIMATE,
                    source_identifier=task.issue_key,
                    description=f"Initial estimated remaining hours: {task.estimated_remaining_hours}",
                    relevance="DIRECT",
                )
            ]
            if task.assigned_resource_id:
                evidence_refs.append(
                    EvidenceReference(
                        evidence_type=EvidenceType.CURRENT_QUEUE,
                        source_identifier=task.assigned_resource_id,
                        description=f"Assigned resource: {task.assigned_resource_name or task.assigned_resource_id}",
                        relevance="DIRECT",
                    )
                )

            estimate = None
            if task.estimated_remaining_hours > 0:
                estimate = PlanningEstimate(
                    value=task.estimated_remaining_hours,
                    unit=EstimateUnit.HOURS,
                    confidence=0.85,
                    rationale=f"Grounded in task estimated remaining workload ({task.estimated_remaining_hours}h).",
                    evidence_references=evidence_refs,
                )

            risk_level = "HIGH" if task.is_blocked or task.is_overdue else "LOW"
            risk_reason = "Task is blocked or overdue in context." if (task.is_blocked or task.is_overdue) else None

            task_proposals.append(
                TaskPlanningProposal(
                    issue_key=task.issue_key,
                    proposed_estimate=estimate,
                    proposed_start_date=task.projected_start_date or context.anchor_date,
                    proposed_due_date=task.projected_completion_date or task.due_date,
                    date_confidence=0.85,
                    sequencing_position=idx,
                    proposed_predecessors=list(task.predecessor_keys),
                    proposed_successors=list(task.successor_keys),
                    risk_level=risk_level,
                    risk_reason=risk_reason,
                    evidence_references=evidence_refs,
                    assumptions=[],
                    requires_human_review=True,
                )
            )

        sequencing_proposals: List[SequencingProposal] = [
            SequencingProposal(
                issue_key=tp.issue_key,
                position=idx,
                rationale=f"Deterministic sequence priority #{idx}",
                confidence=0.85,
                evidence_references=[],
            )
            for idx, tp in enumerate(task_proposals, 1)
        ]

        risk_signals: List[PlanningRiskSignal] = []
        for task in context.tasks:
            if task.is_blocked:
                risk_signals.append(
                    PlanningRiskSignal(
                        risk_type=PlanningRiskType.DEPENDENCY_RISK,
                        issue_key=task.issue_key,
                        severity="HIGH",
                        explanation=f"Task {task.issue_key} is blocked by predecessors.",
                        confidence=0.90,
                        requires_human_review=True,
                        evidence_references=[
                            EvidenceReference(
                                evidence_type=EvidenceType.DEPENDENCY,
                                source_identifier=task.issue_key,
                                description="Task has active blocking dependencies.",
                                relevance="DIRECT",
                            )
                        ],
                    )
                )

        assumptions: List[PlanningAssumption] = [
            PlanningAssumption(
                statement=f"Resource capacity and schedules remain stable across {context.planning_horizon_working_days} working day horizon.",
                confidence=0.85,
                requires_human_review=True,
                evidence_references=[],
            )
        ]

        summary = (
            f"Generated deterministic mock planning proposal for {len(task_proposals)} tasks "
            f"across {len(context.resources)} resources."
        )

        return PlanningProposal(
            proposal_version="proposal-v1",
            generated_at=context.generated_at,
            context_version=context.context_version,
            anchor_date=context.anchor_date,
            planning_horizon_working_days=context.planning_horizon_working_days,
            requires_human_review=True,
            overall_confidence=0.85,
            summary=summary,
            task_proposals=task_proposals,
            sequencing_proposals=sequencing_proposals,
            risk_signals=risk_signals,
            assumptions=assumptions,
            evidence_references=[],
        )


