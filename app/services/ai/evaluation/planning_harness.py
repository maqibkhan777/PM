"""Evaluation metrics, failure classification, and deterministic harness for Phase 4D: AI Planning Evaluation.

Evaluates the complete planning pipeline:
PlanningContext -> PlanningPromptBuilder -> AIProvider -> PlanningProposal -> PlanningProposalValidator -> EvaluationResult

CRITICAL ARCHITECTURAL CONSTRAINTS:
1. ZERO arbitrary overall scores or pass/fail model rankings.
2. Factual, scenario-level measurements and structured failure categorization.
3. Completely deterministic execution.
4. Safe redaction of all secrets.
"""

from dataclasses import dataclass, field
from enum import Enum
import math
import time
from typing import Any, Dict, List, Optional, Set

from app.core.models.planning import (
    PlanningContext,
    PlanningProposal,
    ProposalValidationCategory,
    ProposalValidationIssue,
    ProposalValidationResult,
    ProposalValidationStatus,
    ValidationIssueSeverity,
)
from app.core.planning.validator import PlanningProposalValidator
from app.services.ai.evaluation.planning_dataset import (
    PHASE_4D_EVALUATION_DATASET,
    PlanningEvaluationScenario,
)
from app.services.ai.planning_prompt import PLANNING_PROMPT_VERSION
from app.services.ai.provider import AIProvider


class PlanningFailureCategory(str, Enum):
    """Fine-grained failure taxonomy for planning evaluations."""
    STRUCTURAL_FAILURE = "STRUCTURAL_FAILURE"
    GROUNDING_FAILURE = "GROUNDING_FAILURE"
    HARD_CONSTRAINT_FAILURE = "HARD_CONSTRAINT_FAILURE"
    ESTIMATION_FAILURE = "ESTIMATION_FAILURE"
    SCHEDULE_FAILURE = "SCHEDULE_FAILURE"
    CAPACITY_FAILURE = "CAPACITY_FAILURE"
    DEPENDENCY_FAILURE = "DEPENDENCY_FAILURE"
    ARTIFACT_FAILURE = "ARTIFACT_FAILURE"
    UNCERTAINTY_FAILURE = "UNCERTAINTY_FAILURE"
    COMPLETENESS_FAILURE = "COMPLETENESS_FAILURE"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    OTHER = "OTHER"


@dataclass
class ScenarioPlanningEvaluationResult:
    """Detailed evaluation measurements for a single synthetic scenario."""
    scenario_id: str
    scenario_name: str
    category: str
    success: bool
    latency_seconds: float
    
    # Token usage
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    
    # Structural validity
    parse_success: bool = False
    schema_valid: bool = False
    
    # Grounding metrics
    is_grounded: bool = False
    invented_entities: List[str] = field(default_factory=list)
    
    # Deterministic validation outcome
    validation_result: Optional[ProposalValidationResult] = None
    validation_status: Optional[ProposalValidationStatus] = None
    
    # Detailed metrics
    hard_constraint_violations: int = 0
    hard_block_violations: int = 0
    resource_violations: int = 0
    invalid_dates: int = 0
    capacity_exceeded: int = 0
    capacity_pressure: int = 0
    large_estimate_variances: int = 0
    missing_duration_evidence: int = 0
    schedule_significant_variances: int = 0
    beyond_horizon: int = 0
    uncertainty_warnings: int = 0
    
    # Failure classification
    failure_category: Optional[PlanningFailureCategory] = None
    error_message: Optional[str] = None
    proposal: Optional[PlanningProposal] = None


@dataclass
class PlanningEvaluationReport:
    """Comprehensive, factual evaluation report aggregating metrics across all scenarios."""
    total_scenarios: int
    successful_calls: int
    failed_calls: int
    
    # Structural validity rates
    parse_success_count: int
    schema_valid_count: int
    
    # Grounding metrics
    grounded_count: int
    grounding_failure_count: int
    total_invented_entities: int
    
    # Safety & Hard Constraints
    valid_proposals_count: int
    needs_review_proposals_count: int
    invalid_proposals_count: int
    total_hard_constraint_violations: int
    total_hard_block_violations: int
    total_resource_violations: int
    total_invalid_dates: int
    total_capacity_exceeded: int
    
    # Estimation measurements
    total_estimates_evaluated: int
    supported_estimates_count: int
    reasonable_variance_count: int
    large_variance_count: int
    missing_evidence_count: int
    avg_absolute_variance: float
    median_absolute_variance: float
    
    # Scheduling measurements
    total_dates_evaluated: int
    schedule_aligned_count: int
    schedule_minor_variance_count: int
    schedule_significant_variance_count: int
    beyond_horizon_count: int
    
    # Performance & Tokens
    min_latency: float
    max_latency: float
    median_latency: float
    avg_latency: float
    total_prompt_tokens: int
    total_completion_tokens: int
    total_tokens: int
    
    # Metadata
    prompt_version: str
    model: str
    
    # Scenario level results
    scenario_results: List[ScenarioPlanningEvaluationResult] = field(default_factory=list)
    failure_breakdown: Dict[str, int] = field(default_factory=dict)


class PlanningEvaluationHarness:
    """Deterministic harness executing planning evaluation across synthetic datasets."""

    def __init__(self, provider: AIProvider, prompt_version: str = PLANNING_PROMPT_VERSION):
        self.provider = provider
        self.prompt_version = prompt_version

    async def evaluate_scenario(
        self,
        scenario: PlanningEvaluationScenario,
    ) -> ScenarioPlanningEvaluationResult:
        """Evaluate a single scenario deterministically through provider analysis and Phase 4C validator."""
        t0 = time.monotonic()
        context = scenario.context

        try:
            proposal = await self.provider.analyze_planning(context)
            latency = round(time.monotonic() - t0, 3)

            # Token usage if tracked on provider
            usage = getattr(self.provider, "last_usage", {}) or {}
            pt = usage.get("prompt_tokens")
            ct = usage.get("completion_tokens")
            tt = usage.get("total_tokens") or ((pt or 0) + (ct or 0) if pt or ct else None)

            # Structural & Grounding Check
            known_issue_keys = {t.issue_key.strip().upper() for t in context.tasks}
            known_resource_ids = {r.resource_id.strip().lower() for r in context.resources}
            invented: List[str] = []

            for tp in proposal.task_proposals:
                k = tp.issue_key.strip().upper()
                if k not in known_issue_keys:
                    invented.append(f"task:{tp.issue_key}")
                for p in tp.proposed_predecessors:
                    if p.strip().upper() not in known_issue_keys:
                        invented.append(f"predecessor:{p}")
                for s in tp.proposed_successors:
                    if s.strip().upper() not in known_issue_keys:
                        invented.append(f"successor:{s}")

            for sp in proposal.sequencing_proposals:
                sk = sp.issue_key.strip().upper()
                if sk not in known_issue_keys:
                    invented.append(f"sequencing:{sp.issue_key}")

            for rs in proposal.risk_signals:
                if rs.issue_key and rs.issue_key.strip().upper() not in known_issue_keys:
                    invented.append(f"risk_issue:{rs.issue_key}")

            is_grounded = (len(invented) == 0)

            # Phase 4C Deterministic Validator Execution
            val_result = PlanningProposalValidator.validate_proposal(context, proposal)

            # Extract metric counts from validator findings
            hard_violations = sum(1 for i in val_result.issues if i.severity == ValidationIssueSeverity.ERROR)
            hb_violations = sum(1 for i in val_result.issues if i.code == "HARD_BLOCK_VIOLATION")
            res_violations = sum(1 for i in val_result.issues if i.code == "UNAUTHORIZED_REASSIGNMENT_ATTEMPT")
            inv_dates = sum(1 for i in val_result.issues if i.code in ("INVALID_START_DATE_FORMAT", "INVALID_DUE_DATE_FORMAT", "WEEKEND_DATE_VIOLATION", "START_DATE_AFTER_DUE_DATE"))
            cap_exceeded = sum(1 for i in val_result.issues if i.code == "CAPACITY_EXCEEDED")
            cap_pressure = sum(1 for i in val_result.issues if i.code == "CAPACITY_PRESSURE")
            large_est_vars = sum(1 for i in val_result.issues if i.code == "ESTIMATE_LARGE_VARIANCE")
            missing_ev = sum(1 for i in val_result.issues if i.code == "MISSING_DURATION_EVIDENCE")
            sched_sig_vars = sum(1 for i in val_result.issues if i.code == "SCHEDULE_SIGNIFICANT_VARIANCE")
            beyond_hz = sum(1 for i in val_result.issues if i.code == "PROPOSAL_BEYOND_HORIZON")
            uncertainty_warns = sum(1 for i in val_result.issues if i.code in ("RESOURCE_NO_HISTORY", "CAPACITY_UNAVAILABLE", "TRUNCATED_CONTEXT_UNCERTAINTY"))

            # Determine failure categorization if proposal is invalid
            failure_cat = None
            if not is_grounded:
                failure_cat = PlanningFailureCategory.GROUNDING_FAILURE
            elif hb_violations > 0:
                failure_cat = PlanningFailureCategory.DEPENDENCY_FAILURE
            elif cap_exceeded > 0:
                failure_cat = PlanningFailureCategory.CAPACITY_FAILURE
            elif inv_dates > 0:
                failure_cat = PlanningFailureCategory.HARD_CONSTRAINT_FAILURE
            elif res_violations > 0:
                failure_cat = PlanningFailureCategory.HARD_CONSTRAINT_FAILURE
            elif hard_violations > 0:
                failure_cat = PlanningFailureCategory.HARD_CONSTRAINT_FAILURE

            return ScenarioPlanningEvaluationResult(
                scenario_id=scenario.scenario_id,
                scenario_name=scenario.scenario_name,
                category=scenario.category,
                success=True,
                latency_seconds=latency,
                prompt_tokens=pt,
                completion_tokens=ct,
                total_tokens=tt,
                parse_success=True,
                schema_valid=True,
                is_grounded=is_grounded,
                invented_entities=invented,
                validation_result=val_result,
                validation_status=val_result.status,
                hard_constraint_violations=hard_violations,
                hard_block_violations=hb_violations,
                resource_violations=res_violations,
                invalid_dates=inv_dates,
                capacity_exceeded=cap_exceeded,
                capacity_pressure=cap_pressure,
                large_estimate_variances=large_est_vars,
                missing_duration_evidence=missing_ev,
                schedule_significant_variances=sched_sig_vars,
                beyond_horizon=beyond_hz,
                uncertainty_warnings=uncertainty_warns,
                failure_category=failure_cat,
                proposal=proposal,
            )

        except Exception as e:
            latency = round(time.monotonic() - t0, 3)
            return ScenarioPlanningEvaluationResult(
                scenario_id=scenario.scenario_id,
                scenario_name=scenario.scenario_name,
                category=scenario.category,
                success=False,
                latency_seconds=latency,
                parse_success=False,
                schema_valid=False,
                is_grounded=False,
                failure_category=PlanningFailureCategory.PROVIDER_FAILURE,
                error_message=str(e),
            )

    async def run_evaluation(
        self,
        dataset: Optional[List[PlanningEvaluationScenario]] = None,
    ) -> PlanningEvaluationReport:
        """Execute evaluation across the full scenario suite deterministically."""
        scenarios = dataset or PHASE_4D_EVALUATION_DATASET
        results: List[ScenarioPlanningEvaluationResult] = []

        for scen in scenarios:
            res = await self.evaluate_scenario(scen)
            results.append(res)

        # Aggregate metrics
        successful = [r for r in results if r.success]
        latencies = [r.latency_seconds for r in results]
        sorted_lat = sorted(latencies) if latencies else [0.0]
        n_lat = len(sorted_lat)
        median_lat = sorted_lat[n_lat // 2] if n_lat % 2 == 1 else round((sorted_lat[n_lat // 2 - 1] + sorted_lat[n_lat // 2]) / 2.0, 3)
        avg_lat = round(sum(latencies) / max(len(latencies), 1), 3)

        # Estimates metrics across all scenarios
        total_estimates = 0
        supported_est = 0
        reasonable_est = 0
        large_est = 0
        missing_est = 0
        variances: List[float] = []

        for r in successful:
            if r.proposal and r.validation_result:
                scen_obj = next((s for s in scenarios if s.scenario_id == r.scenario_id), None)
                if scen_obj:
                    ctx_tasks = {t.issue_key.strip().upper(): t for t in scen_obj.context.tasks}
                    for tp in r.proposal.task_proposals:
                        if tp.proposed_estimate:
                            total_estimates += 1
                            t_key = tp.issue_key.strip().upper()
                            ctx_t = ctx_tasks.get(t_key)
                            if ctx_t and ctx_t.estimated_remaining_hours > 0:
                                diff = abs(tp.proposed_estimate.value - ctx_t.estimated_remaining_hours)
                                variances.append(diff)
                                v_ratio = diff / ctx_t.estimated_remaining_hours
                                if v_ratio <= 0.05:
                                    supported_est += 1
                                elif v_ratio <= 0.50:
                                    reasonable_est += 1
                                else:
                                    large_est += 1
                            else:
                                missing_est += 1

        avg_var = round(sum(variances) / max(len(variances), 1), 2) if variances else 0.0
        sorted_var = sorted(variances) if variances else [0.0]
        n_var = len(sorted_var)
        med_var = sorted_var[n_var // 2] if n_var % 2 == 1 else round((sorted_var[n_var // 2 - 1] + sorted_var[n_var // 2]) / 2.0, 2)

        # Failure breakdown
        breakdown: Dict[str, int] = {}
        for r in results:
            if r.failure_category:
                cat_name = r.failure_category.value
                breakdown[cat_name] = breakdown.get(cat_name, 0) + 1

        model_name = getattr(self.provider, "model", "mock-provider")

        return PlanningEvaluationReport(
            total_scenarios=len(scenarios),
            successful_calls=len(successful),
            failed_calls=len(results) - len(successful),
            parse_success_count=sum(1 for r in results if r.parse_success),
            schema_valid_count=sum(1 for r in results if r.schema_valid),
            grounded_count=sum(1 for r in results if r.is_grounded),
            grounding_failure_count=sum(1 for r in results if not r.is_grounded),
            total_invented_entities=sum(len(r.invented_entities) for r in results),
            valid_proposals_count=sum(1 for r in results if r.validation_status == ProposalValidationStatus.VALID),
            needs_review_proposals_count=sum(1 for r in results if r.validation_status == ProposalValidationStatus.NEEDS_REVIEW),
            invalid_proposals_count=sum(1 for r in results if r.validation_status == ProposalValidationStatus.INVALID),
            total_hard_constraint_violations=sum(r.hard_constraint_violations for r in results),
            total_hard_block_violations=sum(r.hard_block_violations for r in results),
            total_resource_violations=sum(r.resource_violations for r in results),
            total_invalid_dates=sum(r.invalid_dates for r in results),
            total_capacity_exceeded=sum(r.capacity_exceeded for r in results),
            total_estimates_evaluated=total_estimates,
            supported_estimates_count=supported_est,
            reasonable_variance_count=reasonable_est,
            large_variance_count=large_est,
            missing_evidence_count=missing_est,
            avg_absolute_variance=avg_var,
            median_absolute_variance=med_var,
            total_dates_evaluated=sum(len(r.proposal.task_proposals) for r in successful if r.proposal),
            schedule_aligned_count=sum(len(r.proposal.task_proposals) - r.schedule_significant_variances for r in successful if r.proposal),
            schedule_minor_variance_count=0,
            schedule_significant_variance_count=sum(r.schedule_significant_variances for r in results),
            beyond_horizon_count=sum(r.beyond_horizon for r in results),
            min_latency=min(latencies) if latencies else 0.0,
            max_latency=max(latencies) if latencies else 0.0,
            median_latency=median_lat,
            avg_latency=avg_lat,
            total_prompt_tokens=sum(r.prompt_tokens or 0 for r in results),
            total_completion_tokens=sum(r.completion_tokens or 0 for r in results),
            total_tokens=sum(r.total_tokens or 0 for r in results),
            prompt_version=self.prompt_version,
            model=model_name,
            scenario_results=results,
            failure_breakdown=breakdown,
        )
