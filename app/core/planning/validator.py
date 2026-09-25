"""Deterministic AI Planning Proposal Validator for Phase 4C.

Evaluates an AI-generated PlanningProposal against a deterministic PlanningContext.

CRITICAL INVARIANTS:
1. ZERO AI / LLM calls.
2. ZERO Jira API calls or mutations.
3. ZERO Action Engine executions.
4. ZERO automatic rescheduling, reassignments, or due-date writes.
5. ZERO proposal rewriting or mutation (proposals and contexts remain strictly immutable).
6. Multi-layer deterministic evaluation returning structured ProposalValidationResult.
"""

from datetime import datetime, date, timezone
import math
from typing import Any, Dict, List, Optional, Set, Tuple

from app.core.models.planning import (
    CapacityState,
    EstimateUnit,
    EvidenceType,
    PlanningContext,
    PlanningProposal,
    PlanningRiskType,
    ProposalValidationCategory,
    ProposalValidationIssue,
    ProposalValidationResult,
    ProposalValidationStatus,
    TaskPlanningProposal,
    ValidationIssueSeverity,
)
from app.core.performance.capacity import CapacityCalculator
from app.utils.time import parse_iso_datetime, utc_now_iso


# Deterministic estimate thresholds
ESTIMATE_TOLERANCE_RATIO = 0.50  # Up to 50% variance is considered REASONABLE_VARIANCE
ESTIMATE_LARGE_VARIANCE_RATIO = 1.0  # > 100% variance is considered LARGE_VARIANCE

# Deterministic schedule date variance thresholds (in working days)
SCHEDULE_ALIGNMENT_THRESHOLD_DAYS = 1
SCHEDULE_SIGNIFICANT_VARIANCE_DAYS = 3


def _parse_date_str(d_str: Optional[str]) -> Optional[date]:
    """Safely parse YYYY-MM-DD into a datetime.date."""
    if not d_str or not isinstance(d_str, str):
        return None
    try:
        return datetime.strptime(d_str.strip()[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _is_weekend(d: date) -> bool:
    """Check if date is Saturday (5) or Sunday (6)."""
    return d.weekday() >= 5


class PlanningProposalValidator:
    """Deterministic, layered validator for AI planning proposals."""

    @classmethod
    def validate_proposal(
        cls,
        planning_context: PlanningContext,
        planning_proposal: PlanningProposal,
    ) -> ProposalValidationResult:
        """Run all 11 deterministic validation layers and produce a typed ProposalValidationResult."""
        if not isinstance(planning_context, PlanningContext):
            raise ValueError("A valid PlanningContext instance is required for validation.")
        if not isinstance(planning_proposal, PlanningProposal):
            raise ValueError("A valid PlanningProposal instance is required for validation.")

        validated_at = utc_now_iso()
        issues: List[ProposalValidationIssue] = []
        checks_summary: Dict[str, Any] = {}

        # -------------------------------------------------------------------------
        # Layer 1: Structure & Proposal Metadata
        # -------------------------------------------------------------------------
        if not planning_proposal.requires_human_review:
            issues.append(
                ProposalValidationIssue(
                    code="HUMAN_REVIEW_REQUIRED_FALSE",
                    severity=ValidationIssueSeverity.ERROR,
                    category=ProposalValidationCategory.SAFETY,
                    message="PlanningProposal requires_human_review must be True.",
                    evidence=f"requires_human_review was {planning_proposal.requires_human_review}",
                )
            )

        if not planning_proposal.summary or not planning_proposal.summary.strip():
            issues.append(
                ProposalValidationIssue(
                    code="EMPTY_PROPOSAL_SUMMARY",
                    severity=ValidationIssueSeverity.ERROR,
                    category=ProposalValidationCategory.STRUCTURE,
                    message="Proposal summary cannot be empty.",
                    evidence="summary is empty string or whitespace.",
                )
            )

        # -------------------------------------------------------------------------
        # Layer 2: Grounding Validation (Tasks, Predecessors, Evidence)
        # -------------------------------------------------------------------------
        context_tasks_map = {t.issue_key.strip().upper(): t for t in planning_context.tasks}
        context_resources_map = {r.resource_id.strip().lower(): r for r in planning_context.resources}

        proposed_task_keys: Set[str] = set()
        for idx, tp in enumerate(planning_proposal.task_proposals):
            t_key = tp.issue_key.strip().upper()
            proposed_task_keys.add(t_key)

            if t_key not in context_tasks_map:
                issues.append(
                    ProposalValidationIssue(
                        code="UNKNOWN_ISSUE_KEY",
                        severity=ValidationIssueSeverity.ERROR,
                        category=ProposalValidationCategory.GROUNDING,
                        issue_key=tp.issue_key,
                        field="issue_key",
                        message=f"Proposed task '{tp.issue_key}' does not exist in PlanningContext.",
                        evidence=f"Known issue keys in context: {sorted(list(context_tasks_map.keys()))}",
                    )
                )

            # Check predecessors / successors exist in context
            for pred in tp.proposed_predecessors:
                if pred.strip().upper() not in context_tasks_map:
                    issues.append(
                        ProposalValidationIssue(
                            code="UNKNOWN_PREDECESSOR_KEY",
                            severity=ValidationIssueSeverity.ERROR,
                            category=ProposalValidationCategory.GROUNDING,
                            issue_key=tp.issue_key,
                            field="proposed_predecessors",
                            message=f"Proposed predecessor '{pred}' for task '{tp.issue_key}' does not exist in context.",
                            evidence=f"Known issue keys: {sorted(list(context_tasks_map.keys()))}",
                        )
                    )

            for succ in tp.proposed_successors:
                if succ.strip().upper() not in context_tasks_map:
                    issues.append(
                        ProposalValidationIssue(
                            code="UNKNOWN_SUCCESSOR_KEY",
                            severity=ValidationIssueSeverity.ERROR,
                            category=ProposalValidationCategory.GROUNDING,
                            issue_key=tp.issue_key,
                            field="proposed_successors",
                            message=f"Proposed successor '{succ}' for task '{tp.issue_key}' does not exist in context.",
                            evidence=f"Known issue keys: {sorted(list(context_tasks_map.keys()))}",
                        )
                    )

        # Check sequencing proposal grounding
        for idx, sp in enumerate(planning_proposal.sequencing_proposals):
            s_key = sp.issue_key.strip().upper()
            if s_key not in context_tasks_map:
                issues.append(
                    ProposalValidationIssue(
                        code="UNKNOWN_SEQUENCING_KEY",
                        severity=ValidationIssueSeverity.ERROR,
                        category=ProposalValidationCategory.SEQUENCING,
                        issue_key=sp.issue_key,
                        field="issue_key",
                        message=f"Sequencing proposal references unknown issue key '{sp.issue_key}'.",
                        evidence=f"Known issue keys: {sorted(list(context_tasks_map.keys()))}",
                    )
                )

        # Check risk signal issue grounding
        for idx, rs in enumerate(planning_proposal.risk_signals):
            if rs.issue_key and rs.issue_key.strip().upper() not in context_tasks_map:
                issues.append(
                    ProposalValidationIssue(
                        code="UNKNOWN_RISK_SIGNAL_KEY",
                        severity=ValidationIssueSeverity.ERROR,
                        category=ProposalValidationCategory.GROUNDING,
                        issue_key=rs.issue_key,
                        field="issue_key",
                        message=f"Risk signal references unknown issue key '{rs.issue_key}'.",
                        evidence=f"Known issue keys: {sorted(list(context_tasks_map.keys()))}",
                    )
                )

        # -------------------------------------------------------------------------
        # Layer 3: Resource Validation (No unauthorized reassignment)
        # -------------------------------------------------------------------------
        for tp in planning_proposal.task_proposals:
            t_key = tp.issue_key.strip().upper()
            if t_key in context_tasks_map:
                ctx_task = context_tasks_map[t_key]
                # If task is assigned in context, verify the proposal evidence doesn't assert a different resource
                for ev in tp.evidence_references:
                    if ev.evidence_type == EvidenceType.CURRENT_QUEUE:
                        source_id = ev.source_identifier.strip().lower()
                        if ctx_task.assigned_resource_id and source_id != ctx_task.assigned_resource_id.strip().lower():
                            if source_id in context_resources_map:
                                issues.append(
                                    ProposalValidationIssue(
                                        code="UNAUTHORIZED_REASSIGNMENT_ATTEMPT",
                                        severity=ValidationIssueSeverity.ERROR,
                                        category=ProposalValidationCategory.RESOURCE,
                                        issue_key=tp.issue_key,
                                        resource_id=source_id,
                                        field="evidence_references",
                                        message=f"Proposal references resource '{source_id}' but task '{tp.issue_key}' is assigned to '{ctx_task.assigned_resource_id}'.",
                                        evidence=f"Context assigned resource: {ctx_task.assigned_resource_id}",
                                    )
                                )

        # -------------------------------------------------------------------------
        # Layer 4: Estimate Validation
        # -------------------------------------------------------------------------
        for tp in planning_proposal.task_proposals:
            t_key = tp.issue_key.strip().upper()
            if t_key in context_tasks_map and tp.proposed_estimate:
                est = tp.proposed_estimate
                ctx_task = context_tasks_map[t_key]
                ctx_est = ctx_task.estimated_remaining_hours

                if est.unit != EstimateUnit.HOURS:
                    issues.append(
                        ProposalValidationIssue(
                            code="INVALID_ESTIMATE_UNIT",
                            severity=ValidationIssueSeverity.ERROR,
                            category=ProposalValidationCategory.ESTIMATE,
                            issue_key=tp.issue_key,
                            field="proposed_estimate.unit",
                            message=f"Estimate unit must be 'hours', got '{est.unit}'.",
                            evidence=f"Proposed unit: {est.unit}",
                        )
                    )

                if est.value < 0.0 or math.isnan(est.value) or math.isinf(est.value):
                    issues.append(
                        ProposalValidationIssue(
                            code="INVALID_ESTIMATE_VALUE",
                            severity=ValidationIssueSeverity.ERROR,
                            category=ProposalValidationCategory.ESTIMATE,
                            issue_key=tp.issue_key,
                            field="proposed_estimate.value",
                            message=f"Estimate value must be a finite non-negative number.",
                            evidence=f"Proposed value: {est.value}",
                        )
                    )
                elif ctx_est > 0.0:
                    variance_ratio = abs(est.value - ctx_est) / ctx_est
                    if variance_ratio > ESTIMATE_LARGE_VARIANCE_RATIO:
                        issues.append(
                            ProposalValidationIssue(
                                code="ESTIMATE_LARGE_VARIANCE",
                                severity=ValidationIssueSeverity.WARNING,
                                category=ProposalValidationCategory.ESTIMATE,
                                issue_key=tp.issue_key,
                                field="proposed_estimate.value",
                                message=(
                                    f"Proposed estimate ({est.value}h) differs significantly from "
                                    f"deterministic estimate ({ctx_est}h) by {round(variance_ratio * 100, 1)}%."
                                ),
                                evidence=f"Context: {ctx_est}h ({ctx_task.duration_evidence_source}), Proposed: {est.value}h",
                            )
                        )
                elif ctx_task.duration_evidence_source in ("unavailable", "UNKNOWN"):
                    issues.append(
                        ProposalValidationIssue(
                            code="MISSING_DURATION_EVIDENCE",
                            severity=ValidationIssueSeverity.WARNING,
                            category=ProposalValidationCategory.ESTIMATE,
                            issue_key=tp.issue_key,
                            field="proposed_estimate",
                            message=f"Deterministic context lacks duration evidence for task '{tp.issue_key}'.",
                            evidence=f"duration_evidence_source: {ctx_task.duration_evidence_source}",
                        )
                    )

        # -------------------------------------------------------------------------
        # Layer 5: Calendar & Date Validation
        # -------------------------------------------------------------------------
        anchor_d = _parse_date_str(planning_context.anchor_date)

        for tp in planning_proposal.task_proposals:
            start_d = _parse_date_str(tp.proposed_start_date)
            due_d = _parse_date_str(tp.proposed_due_date)

            if tp.proposed_start_date and not start_d:
                issues.append(
                    ProposalValidationIssue(
                        code="INVALID_START_DATE_FORMAT",
                        severity=ValidationIssueSeverity.ERROR,
                        category=ProposalValidationCategory.DATE,
                        issue_key=tp.issue_key,
                        field="proposed_start_date",
                        message=f"Invalid start date '{tp.proposed_start_date}', must be YYYY-MM-DD.",
                        evidence=f"Value: {tp.proposed_start_date}",
                    )
                )

            if tp.proposed_due_date and not due_d:
                issues.append(
                    ProposalValidationIssue(
                        code="INVALID_DUE_DATE_FORMAT",
                        severity=ValidationIssueSeverity.ERROR,
                        category=ProposalValidationCategory.DATE,
                        issue_key=tp.issue_key,
                        field="proposed_due_date",
                        message=f"Invalid due date '{tp.proposed_due_date}', must be YYYY-MM-DD.",
                        evidence=f"Value: {tp.proposed_due_date}",
                    )
                )

            if start_d and due_d and start_d > due_d:
                issues.append(
                    ProposalValidationIssue(
                        code="START_DATE_AFTER_DUE_DATE",
                        severity=ValidationIssueSeverity.ERROR,
                        category=ProposalValidationCategory.DATE,
                        issue_key=tp.issue_key,
                        field="proposed_start_date",
                        message=f"Proposed start date ({tp.proposed_start_date}) is after due date ({tp.proposed_due_date}).",
                        evidence=f"Start: {tp.proposed_start_date}, Due: {tp.proposed_due_date}",
                    )
                )

            if start_d and _is_weekend(start_d):
                issues.append(
                    ProposalValidationIssue(
                        code="WEEKEND_DATE_VIOLATION",
                        severity=ValidationIssueSeverity.ERROR,
                        category=ProposalValidationCategory.DATE,
                        issue_key=tp.issue_key,
                        field="proposed_start_date",
                        message=f"Proposed start date {tp.proposed_start_date} falls on a weekend.",
                        evidence=f"Weekday: {start_d.strftime('%A')}",
                    )
                )

            if due_d and _is_weekend(due_d):
                issues.append(
                    ProposalValidationIssue(
                        code="WEEKEND_DATE_VIOLATION",
                        severity=ValidationIssueSeverity.ERROR,
                        category=ProposalValidationCategory.DATE,
                        issue_key=tp.issue_key,
                        field="proposed_due_date",
                        message=f"Proposed due date {tp.proposed_due_date} falls on a weekend.",
                        evidence=f"Weekday: {due_d.strftime('%A')}",
                    )
                )

        # -------------------------------------------------------------------------
        # Layer 6: HARD_BLOCK Dependency Validation
        # -------------------------------------------------------------------------
        # Build map of hard blocker dependencies from context
        hard_predecessors: Dict[str, List[str]] = {}
        for dep in planning_context.dependencies:
            if dep.is_hard_block:
                tgt = dep.target_issue_key.strip().upper()
                src = dep.source_issue_key.strip().upper()
                if tgt not in hard_predecessors:
                    hard_predecessors[tgt] = []
                hard_predecessors[tgt].append(src)

        # Task completion projections from context (or proposal if specified)
        task_completion_dates: Dict[str, date] = {}
        for t in planning_context.tasks:
            t_key = t.issue_key.strip().upper()
            d = _parse_date_str(t.projected_completion_date or t.due_date)
            if d:
                task_completion_dates[t_key] = d

        for tp in planning_proposal.task_proposals:
            t_key = tp.issue_key.strip().upper()
            prop_start_d = _parse_date_str(tp.proposed_start_date)
            
            if t_key in hard_predecessors and prop_start_d:
                for pred_key in hard_predecessors[t_key]:
                    pred_comp_d = task_completion_dates.get(pred_key)
                    if pred_comp_d and prop_start_d < pred_comp_d:
                        issues.append(
                            ProposalValidationIssue(
                                code="HARD_BLOCK_VIOLATION",
                                severity=ValidationIssueSeverity.ERROR,
                                category=ProposalValidationCategory.DEPENDENCY,
                                issue_key=tp.issue_key,
                                field="proposed_start_date",
                                message=(
                                    f"Task '{tp.issue_key}' is proposed to start on {tp.proposed_start_date}, "
                                    f"before HARD_BLOCK predecessor '{pred_key}' completes on {pred_comp_d.strftime('%Y-%m-%d')}."
                                ),
                                evidence=f"Predecessor: {pred_key} (completion: {pred_comp_d}), Proposed start: {tp.proposed_start_date}",
                            )
                        )

        # -------------------------------------------------------------------------
        # Layer 7: Capacity Feasibility Validation
        # -------------------------------------------------------------------------
        # Sum proposed workload per assigned resource
        resource_proposed_hours: Dict[str, float] = {}
        for tp in planning_proposal.task_proposals:
            t_key = tp.issue_key.strip().upper()
            if t_key in context_tasks_map:
                ctx_t = context_tasks_map[t_key]
                r_id = (ctx_t.assigned_resource_id or "unassigned").strip().lower()
                effort = tp.proposed_estimate.value if tp.proposed_estimate else ctx_t.estimated_remaining_hours
                resource_proposed_hours[r_id] = resource_proposed_hours.get(r_id, 0.0) + effort

        for r_id, proposed_hours in resource_proposed_hours.items():
            if r_id in context_resources_map:
                res_ctx = context_resources_map[r_id]
                avail_hours = res_ctx.available_capacity_hours
                if avail_hours > 0:
                    load_ratio = proposed_hours / avail_hours
                    if load_ratio > 1.4:
                        issues.append(
                            ProposalValidationIssue(
                                code="CAPACITY_EXCEEDED",
                                severity=ValidationIssueSeverity.ERROR,
                                category=ProposalValidationCategory.CAPACITY,
                                resource_id=r_id,
                                message=(
                                    f"Proposed workload for resource '{res_ctx.display_name}' ({proposed_hours}h) "
                                    f"exceeds available capacity ({avail_hours}h) by {round((load_ratio - 1.0) * 100, 1)}%."
                                ),
                                evidence=f"Proposed: {proposed_hours}h, Available: {avail_hours}h, Ratio: {round(load_ratio, 2)}x",
                            )
                        )
                    elif load_ratio > 1.0:
                        issues.append(
                            ProposalValidationIssue(
                                code="CAPACITY_PRESSURE",
                                severity=ValidationIssueSeverity.WARNING,
                                category=ProposalValidationCategory.CAPACITY,
                                resource_id=r_id,
                                message=(
                                    f"Resource '{res_ctx.display_name}' is experiencing capacity pressure: "
                                    f"proposed workload ({proposed_hours}h) exceeds standard capacity ({avail_hours}h)."
                                ),
                                evidence=f"Proposed: {proposed_hours}h, Available: {avail_hours}h, Ratio: {round(load_ratio, 2)}x",
                            )
                        )

        # -------------------------------------------------------------------------
        # Layer 8: Schedule Consistency (Comparison with deterministic forecast)
        # -------------------------------------------------------------------------
        for tp in planning_proposal.task_proposals:
            t_key = tp.issue_key.strip().upper()
            if t_key in context_tasks_map and tp.proposed_start_date:
                ctx_t = context_tasks_map[t_key]
                if ctx_t.projected_start_date:
                    ctx_start_d = _parse_date_str(ctx_t.projected_start_date)
                    prop_start_d = _parse_date_str(tp.proposed_start_date)
                    if ctx_start_d and prop_start_d:
                        diff_days = abs((prop_start_d - ctx_start_d).days)
                        if diff_days >= SCHEDULE_SIGNIFICANT_VARIANCE_DAYS:
                            issues.append(
                                ProposalValidationIssue(
                                    code="SCHEDULE_SIGNIFICANT_VARIANCE",
                                    severity=ValidationIssueSeverity.WARNING,
                                    category=ProposalValidationCategory.SCHEDULE,
                                    issue_key=tp.issue_key,
                                    field="proposed_start_date",
                                    message=(
                                        f"Proposed start date ({tp.proposed_start_date}) deviates from "
                                        f"deterministic forecast ({ctx_t.projected_start_date}) by {diff_days} calendar days."
                                    ),
                                    evidence=f"Deterministic: {ctx_t.projected_start_date}, Proposed: {tp.proposed_start_date}",
                                )
                            )

        # -------------------------------------------------------------------------
        # Layer 9: Planning Horizon
        # -------------------------------------------------------------------------
        horizon_end_d = _parse_date_str(planning_context.horizon_end_date)
        for tp in planning_proposal.task_proposals:
            due_d = _parse_date_str(tp.proposed_due_date)
            if due_d and horizon_end_d and due_d > horizon_end_d:
                issues.append(
                    ProposalValidationIssue(
                        code="PROPOSAL_BEYOND_HORIZON",
                        severity=ValidationIssueSeverity.WARNING,
                        category=ProposalValidationCategory.HORIZON,
                        issue_key=tp.issue_key,
                        field="proposed_due_date",
                        message=(
                            f"Proposed task due date {tp.proposed_due_date} extends beyond the "
                            f"{planning_context.planning_horizon_working_days}-day horizon ({planning_context.horizon_end_date})."
                        ),
                        evidence=f"Horizon End: {planning_context.horizon_end_date}, Due Date: {tp.proposed_due_date}",
                    )
                )

        # -------------------------------------------------------------------------
        # Layer 10: Data Quality & Uncertainty
        # -------------------------------------------------------------------------
        if planning_context.truncation and planning_context.truncation.is_truncated:
            reasons = "; ".join(planning_context.truncation.truncation_reasons)
            issues.append(
                ProposalValidationIssue(
                    code="TRUNCATED_CONTEXT_UNCERTAINTY",
                    severity=ValidationIssueSeverity.WARNING,
                    category=ProposalValidationCategory.DATA_QUALITY,
                    message="PlanningContext was bounded/truncated during intake; some tasks or resources were omitted.",
                    evidence=f"Truncation reasons: {reasons}",
                )
            )

        for res in planning_context.resources:
            if res.history_completeness == "NO_HISTORY":
                issues.append(
                    ProposalValidationIssue(
                        code="RESOURCE_NO_HISTORY",
                        severity=ValidationIssueSeverity.WARNING,
                        category=ProposalValidationCategory.DATA_QUALITY,
                        resource_id=res.resource_id,
                        message=f"Resource '{res.display_name}' has no historical pace data in context.",
                        evidence="history_completeness: NO_HISTORY",
                    )
                )
            if res.capacity_quality == "CAPACITY_UNAVAILABLE":
                issues.append(
                    ProposalValidationIssue(
                        code="CAPACITY_UNAVAILABLE",
                        severity=ValidationIssueSeverity.WARNING,
                        category=ProposalValidationCategory.DATA_QUALITY,
                        resource_id=res.resource_id,
                        message=f"Resource '{res.display_name}' has unavailable capacity data in context.",
                        evidence="capacity_quality: CAPACITY_UNAVAILABLE",
                    )
                )

        # -------------------------------------------------------------------------
        # Layer 11: Artifact & Sequencing Checks
        # -------------------------------------------------------------------------
        for art in planning_context.artifacts:
            # If explicit artifact has producer and consumers, verify advisory sequence
            if not art.is_inferred and art.producer_issue_key:
                p_key = art.producer_issue_key.strip().upper()
                p_comp_d = task_completion_dates.get(p_key)
                for c_key in art.consumer_issue_keys:
                    norm_c_key = c_key.strip().upper()
                    if norm_c_key in context_tasks_map:
                        c_tp = next(
                            (tp for tp in planning_proposal.task_proposals if tp.issue_key.strip().upper() == norm_c_key),
                            None,
                        )
                        if c_tp and c_tp.proposed_start_date and p_comp_d:
                            c_start_d = _parse_date_str(c_tp.proposed_start_date)
                            if c_start_d and c_start_d < p_comp_d:
                                issues.append(
                                    ProposalValidationIssue(
                                        code="EXPLICIT_ARTIFACT_HANDOFF_CONFLICT",
                                        severity=ValidationIssueSeverity.WARNING,
                                        category=ProposalValidationCategory.ARTIFACT,
                                        issue_key=c_tp.issue_key,
                                        message=(
                                            f"Task '{c_tp.issue_key}' consumes explicit artifact '{art.artifact_name}' "
                                            f"produced by '{p_key}' on {p_comp_d}, but is scheduled to start on {c_tp.proposed_start_date}."
                                        ),
                                        evidence=f"Artifact: {art.artifact_name}, Producer: {p_key}, Consumer: {c_tp.issue_key}",
                                    )
                                )

        # Sequencing conflict with HARD_BLOCK
        for sp in planning_proposal.sequencing_proposals:
            s_key = sp.issue_key.strip().upper()
            if s_key in hard_predecessors:
                # If a task is sequenced before its hard predecessors on the same resource
                for pred in hard_predecessors[s_key]:
                    pred_sp = next(
                        (p for p in planning_proposal.sequencing_proposals if p.issue_key.strip().upper() == pred),
                        None,
                    )
                    if pred_sp and sp.position < pred_sp.position:
                        issues.append(
                            ProposalValidationIssue(
                                code="SEQUENCING_HARD_BLOCK_CONFLICT",
                                severity=ValidationIssueSeverity.ERROR,
                                category=ProposalValidationCategory.SEQUENCING,
                                issue_key=sp.issue_key,
                                message=(
                                    f"Task '{sp.issue_key}' is sequenced at position {sp.position} "
                                    f"ahead of its HARD_BLOCK predecessor '{pred}' (position {pred_sp.position})."
                                ),
                                evidence=f"Task pos: {sp.position}, Predecessor pos: {pred_sp.position}",
                            )
                        )

        # -------------------------------------------------------------------------
        # Deterministic Ordering of Validation Issues
        # -------------------------------------------------------------------------
        category_order = {
            ProposalValidationCategory.SAFETY: 1,
            ProposalValidationCategory.STRUCTURE: 2,
            ProposalValidationCategory.GROUNDING: 3,
            ProposalValidationCategory.RESOURCE: 4,
            ProposalValidationCategory.DEPENDENCY: 5,
            ProposalValidationCategory.DATE: 6,
            ProposalValidationCategory.ESTIMATE: 7,
            ProposalValidationCategory.CAPACITY: 8,
            ProposalValidationCategory.SEQUENCING: 9,
            ProposalValidationCategory.SCHEDULE: 10,
            ProposalValidationCategory.HORIZON: 11,
            ProposalValidationCategory.ARTIFACT: 12,
            ProposalValidationCategory.DATA_QUALITY: 13,
        }
        severity_order = {
            ValidationIssueSeverity.ERROR: 1,
            ValidationIssueSeverity.WARNING: 2,
            ValidationIssueSeverity.INFO: 3,
        }

        issues.sort(
            key=lambda it: (
                severity_order.get(it.severity, 99),
                category_order.get(it.category, 99),
                it.issue_key or "",
                it.code,
            )
        )

        # -------------------------------------------------------------------------
        # Aggregate Outcome Status & Task Counts
        # -------------------------------------------------------------------------
        error_count = sum(1 for it in issues if it.severity == ValidationIssueSeverity.ERROR)
        warning_count = sum(1 for it in issues if it.severity == ValidationIssueSeverity.WARNING)

        if error_count > 0:
            status = ProposalValidationStatus.INVALID
            summary = f"Proposal is INVALID: found {error_count} hard constraint violation(s)."
            proposal_accepted = False
        elif warning_count > 0:
            status = ProposalValidationStatus.NEEDS_REVIEW
            summary = f"Proposal NEEDS_REVIEW: found {warning_count} advisory warning(s) or data quality limitation(s)."
            proposal_accepted = False
        else:
            status = ProposalValidationStatus.VALID
            summary = "Proposal is VALID: all deterministic checks passed successfully."
            proposal_accepted = True

        task_issue_map: Dict[str, List[ProposalValidationIssue]] = {}
        for it in issues:
            if it.issue_key:
                k = it.issue_key.strip().upper()
                if k not in task_issue_map:
                    task_issue_map[k] = []
                task_issue_map[k].append(it)

        invalid_task_count = 0
        needs_review_task_count = 0
        valid_task_count = 0

        for tp in planning_proposal.task_proposals:
            k = tp.issue_key.strip().upper()
            t_issues = task_issue_map.get(k, [])
            if any(i.severity == ValidationIssueSeverity.ERROR for i in t_issues):
                invalid_task_count += 1
            elif any(i.severity == ValidationIssueSeverity.WARNING for i in t_issues):
                needs_review_task_count += 1
            else:
                valid_task_count += 1

        checks_summary = {
            "total_issues_count": len(issues),
            "errors_count": error_count,
            "warnings_count": warning_count,
            "hard_block_checks": "EVALUATED",
            "capacity_checks": "EVALUATED",
            "calendar_checks": "EVALUATED",
            "grounding_checks": "EVALUATED",
        }

        return ProposalValidationResult(
            status=status,
            proposal_version=planning_proposal.proposal_version,
            context_version=planning_proposal.context_version,
            validated_at=validated_at,
            proposal_accepted=proposal_accepted,
            issues=issues,
            validated_task_count=len(planning_proposal.task_proposals),
            valid_task_count=valid_task_count,
            invalid_task_count=invalid_task_count,
            needs_review_task_count=needs_review_task_count,
            summary=summary,
            deterministic_checks=checks_summary,
        )
