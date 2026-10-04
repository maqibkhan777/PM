"""Deterministic, explainable Advisory Schedule Generation Service for Milestone 4B.

CRITICAL ARCHITECTURAL BOUNDARIES:
- ZERO AI or DeepSeek calls.
- ZERO Jira mutations, assignments, status changes, or due-date writes.
- ZERO Action Engine executions.
- ZERO automatic schedule commits.
- Uses PlanningDataValidationReport as strictly validated input.
- Clear distinction between explicit estimates, empirical benchmark proxies, and missing data.
- Discloses unknown capacity dimensions (leave, holidays, focus factor).
- Preserves blocker relationships and distinguishes effort from calendar duration.
"""

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple
import uuid

from app.config.settings import settings
from app.core.intelligence.effort_recommendation_models import BenchmarkRecommendationStatus
from app.core.intelligence.effort_retrieval_service import HistoricalEffortBenchmarkRetrievalService
from app.core.models.planning import DependencyClassification
from app.core.planning.dag import DependencyGraph
from app.core.planning.schedule_models import (
    AdvisoryScheduleProposal,
    ResourceScheduleAudit,
    ScheduleFeasibilityStatus,
    ScheduledTaskProposal,
)
from app.core.planning.validation_models import (
    CapacityDataSource,
    EstimateType,
    PlanningDataValidationReport,
    ValidatedTaskWorkload,
    ValidationOutcome,
)
from app.database.connection import DatabaseManager, db_manager
from app.database.repositories import EmployeeRoleRepository
from app.utils.logger import logger
from app.utils.time import parse_iso_datetime, utc_now, utc_now_iso


# Deterministic priority ranking for tie-breaking
PRIORITY_ORDER: Dict[str, int] = {
    "highest": 1,
    "high": 2,
    "medium": 3,
    "low": 4,
    "lowest": 5,
}


def _get_priority_rank(priority_str: Optional[str]) -> int:
    if not priority_str:
        return 99
    return PRIORITY_ORDER.get(priority_str.strip().lower(), 99)


def _to_date(dt_val: Any) -> date:
    if isinstance(dt_val, datetime):
        return dt_val.date()
    if isinstance(dt_val, date):
        return dt_val
    if isinstance(dt_val, str):
        parsed = parse_iso_datetime(dt_val)
        if parsed:
            return parsed.date()
        try:
            return datetime.strptime(dt_val[:10], "%Y-%m-%d").date()
        except Exception:
            pass
    return utc_now().date()


def _advance_to_working_day(d: date) -> date:
    """Advance date to nearest working day (Mon-Fri) if it falls on a weekend."""
    cur = d
    while cur.weekday() >= 5:  # 5=Saturday, 6=Sunday
        cur += timedelta(days=1)
    return cur


def _add_working_days(start_d: date, working_days: float) -> date:
    """Add working days (Mon-Fri) to start_d."""
    cur = _advance_to_working_day(start_d)
    full_days = int(working_days)
    fraction = working_days - full_days
    
    # If fractional part > 0, round up or account for partial day span
    days_to_add = full_days if fraction == 0 else full_days + 1
    
    added = 0
    while added < max(1, days_to_add) - 1:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            added += 1
    return cur


class AdvisoryScheduleService:
    """Generates explainable, deterministic advisory schedule proposals from validated planning data."""

    def __init__(
        self,
        manager: Optional[DatabaseManager] = None,
        benchmark_service: Optional[HistoricalEffortBenchmarkRetrievalService] = None,
    ):
        self.mgr = manager or db_manager
        self.role_repo = EmployeeRoleRepository(self.mgr)
        self.benchmark_svc = benchmark_service or HistoricalEffortBenchmarkRetrievalService(self.mgr)

    def generate_advisory_schedule(
        self,
        validation_report: PlanningDataValidationReport,
        anchor_date: Optional[str] = None,
        planning_horizon_working_days: Optional[int] = None,
    ) -> AdvisoryScheduleProposal:
        """Generate a deterministic advisory schedule proposal based on a validated planning report."""
        now_dt = utc_now()
        now_iso = utc_now_iso()
        proposal_id = f"sch-{uuid.uuid4().hex[:8]}"

        horizon_working_days = planning_horizon_working_days or validation_report.planning_horizon_working_days
        base_anchor_str = anchor_date or validation_report.anchor_date or now_dt.strftime("%Y-%m-%d")
        anchor_d = _advance_to_working_day(_to_date(base_anchor_str))
        horizon_end_d = _add_working_days(anchor_d, horizon_working_days)
        horizon_end_str = horizon_end_d.strftime("%Y-%m-%d")

        # 1. Inspect Scope & Validation Outcome
        configured_projs = validation_report.configured_projects
        resolved_projs = validation_report.resolved_projects
        unresolved_projs = validation_report.unresolved_projects
        
        # Build DAG for dependency & cycle check
        dep_graph = DependencyGraph(include_only_hard_blocks=True)
        blocker_map: Dict[str, List[str]] = defaultdict(list)
        predecessor_to_successors: Dict[str, List[str]] = defaultdict(list)

        for finding in validation_report.dependency_findings:
            if finding.is_hard_block:
                blocker_map[finding.target_issue_key].append(finding.source_issue_key)
                predecessor_to_successors[finding.source_issue_key].append(finding.target_issue_key)
                try:
                    dep_graph.add_edge(
                        source_key=finding.source_issue_key,
                        target_key=finding.target_issue_key,
                        link_type=finding.link_type,
                        classification=DependencyClassification.HARD_BLOCK,
                    )
                except Exception:
                    pass

        cycle_res = dep_graph.detect_cycles()
        has_cycle = cycle_res.has_cycle

        # Determine Feasibility Status
        if validation_report.overall_status == ValidationOutcome.UNRELIABLE or has_cycle:
            feasibility_status = ScheduleFeasibilityStatus.UNRELIABLE
        elif (
            validation_report.overall_status == ValidationOutcome.PARTIAL
            or len(unresolved_projs) > 0
            or validation_report.blocked_tasks_count > 0
            or validation_report.tasks_with_missing_estimates > 0
            or len(validation_report.overallocated_resources) > 0
        ):
            feasibility_status = ScheduleFeasibilityStatus.PARTIALLY_CONSTRAINED
        else:
            feasibility_status = ScheduleFeasibilityStatus.FEASIBLE

        # Disclosures & Assumptions
        unknown_disclosures = list(validation_report.capacity_limitations_summary)
        sequencing_rules = [
            "1. Respects Jira hard-blocker relationships: blocked tasks cannot begin before predecessors complete.",
            "2. Within each resource's queue, tasks are ordered deterministically by: (a) In-progress vs. To Do, (b) Priority ranking (Highest -> Lowest), (c) Issue key tie-breaker.",
            "3. Effort hours are converted to business calendar duration using nominal 6.5h/day forecast capacity (excluding weekends).",
            "4. Missing estimates are highlighted and not scheduled with tentative completion dates.",
        ]
        risks_and_assumptions: List[str] = []

        if has_cycle:
            cycle_desc = cycle_res.error_message or " -> ".join(cycle_res.cycle_nodes)
            risks_and_assumptions.append(
                f"CRITICAL: Dependency cycle detected among active tasks ({cycle_desc}). Deterministic scheduling disabled."
            )
        if len(unresolved_projs) > 0:
            risks_and_assumptions.append(
                f"Partial project scope: Configured project(s) {', '.join(unresolved_projs)} could not be resolved from Jira data."
            )
        if len(validation_report.overallocated_resources) > 0:
            risks_and_assumptions.append(
                f"Resource overload: Resource(s) {', '.join(validation_report.overallocated_resources)} have assigned workload exceeding 65h nominal capacity."
            )
        if len(validation_report.cross_project_contention_resources) > 0:
            risks_and_assumptions.append(
                f"Cross-project contention: Resource(s) {', '.join(validation_report.cross_project_contention_resources)} hold active assignments across multiple Jira projects."
            )
        if validation_report.blocked_tasks_count > 0:
            risks_and_assumptions.append(
                f"Blockers active: {validation_report.blocked_tasks_count} active task(s) depend on unresolved predecessor tasks."
            )

        # 2. Process Validated Tasks
        tasks_by_resource: Dict[str, List[ValidatedTaskWorkload]] = defaultdict(list)
        unassigned_tasks_raw: List[ValidatedTaskWorkload] = []

        for task in validation_report.validated_tasks:
            if not task.assignee_name or task.assignee_name.strip().lower() == "unassigned":
                unassigned_tasks_raw.append(task)
            else:
                tasks_by_resource[task.assignee_name].append(task)

        # 3. Schedule Each Resource Queue
        resource_audits_map = {a.display_name: a for a in validation_report.resources_audited}
        resource_schedules: List[ResourceScheduleAudit] = []
        
        tasks_scheduled_count = 0
        tasks_beyond_horizon_count = 0
        tasks_explicit = 0
        tasks_benchmark = 0
        tasks_fallback = 0
        tasks_missing = 0

        # Deterministic resource ordering
        for res_name in sorted(tasks_by_resource.keys()):
            raw_queue = tasks_by_resource[res_name]
            audit_info = resource_audits_map.get(res_name)

            # Sort tasks in resource queue deterministically
            # Rule: In-progress tasks first, then by priority rank (1=Highest to 5=Lowest), then by issue key
            def task_sort_key(t: ValidatedTaskWorkload) -> Tuple[int, int, str]:
                is_in_prog = 0 if t.status_category.lower() in ("in progress", "in development", "doing") else 1
                prio_rank = _get_priority_rank(t.priority)
                return (is_in_prog, prio_rank, t.issue_key)

            sorted_tasks = sorted(raw_queue, key=task_sort_key)
            
            res_scheduled_tasks: List[ScheduledTaskProposal] = []
            res_warnings: List[str] = []
            
            nominal_daily_h = audit_info.forecast_daily_hours if audit_info else 6.50
            total_rem_h = audit_info.total_assigned_remaining_hours if audit_info else 0.0
            avail_h = audit_info.available_capacity_hours if audit_info else 65.0
            alloc_pct = round((total_rem_h / avail_h) * 100.0, 1) if avail_h > 0 else 0.0

            if audit_info and audit_info.is_overallocated:
                res_warnings.append(
                    f"Overallocated: Total workload ({total_rem_h:.1f}h) exceeds nominal 10-day capacity ({avail_h:.1f}h) by {alloc_pct}%."
                )
            if audit_info and audit_info.cross_project_conflict_detected:
                res_warnings.append(
                    f"Cross-project contention: Assigned to active tasks across {', '.join(audit_info.projects_involved)}."
                )

            # Calendar tracking for this resource
            current_cursor_d = anchor_d
            res_tasks_beyond_horizon = 0

            for idx, t in enumerate(sorted_tasks, start=1):
                tasks_scheduled_count += 1
                t_warnings: List[str] = []
                
                # Fetch detailed benchmark metadata if proxy
                p50_val: Optional[float] = None
                p90_val: Optional[float] = None
                sample_count: Optional[int] = None
                conf_status: Optional[str] = None
                
                if t.estimate_type == EstimateType.EXPLICIT_REMAINING:
                    tasks_explicit += 1
                    p50_val = t.remaining_estimate_hours
                    p90_val = t.remaining_estimate_hours
                elif t.estimate_type == EstimateType.EXPLICIT_ZERO:
                    tasks_explicit += 1
                    p50_val = 0.0
                    p90_val = 0.0
                elif t.estimate_type == EstimateType.HISTORICAL_BENCHMARK:
                    tasks_benchmark += 1
                    # Look up benchmark quantiles for display
                    bm_rec = self.benchmark_svc.recommend_effort_for_task(
                        project_key=t.project_key,
                        issue_key=t.issue_key,
                        issue_type=getattr(t, "issue_type", "Task") or "Task",
                        priority=t.priority or "Medium",
                    )
                    p50_val = bm_rec.p50_effort_hours
                    p90_val = bm_rec.p90_effort_hours
                    sample_count = bm_rec.sample_count
                    conf_status = bm_rec.reliability_status.value
                    if bm_rec.reliability_status == BenchmarkRecommendationStatus.LOW_CONFIDENCE:
                        t_warnings.append(f"Low confidence benchmark proxy (n={sample_count}); planning range uncertain.")
                elif t.estimate_type == EstimateType.COMPLEXITY_FALLBACK:
                    tasks_fallback += 1
                    t_warnings.append("Complexity fallback estimate used; no empirical benchmark available.")
                else:
                    tasks_missing += 1
                    t_warnings.append("Missing estimate: Cannot compute defensible completion date without effort duration.")

                # Blocker checks
                unresolved_preds = blocker_map.get(t.issue_key, [])
                succs = predecessor_to_successors.get(t.issue_key, [])
                is_blocked = len(unresolved_preds) > 0

                if is_blocked:
                    t_warnings.append(
                        f"Blocked by unresolved predecessor(s): {', '.join(unresolved_preds)}. Cannot execute immediately."
                    )

                # Date projection
                tentative_start_str: Optional[str] = None
                tentative_comp_str: Optional[str] = None
                working_days_needed = 0.0
                is_beyond = False
                dates_avail = True

                if has_cycle or t.remaining_estimate_hours is None:
                    # Dates cannot be reliably computed
                    dates_avail = False
                else:
                    effort_h = t.remaining_estimate_hours
                    if effort_h <= 0.0:
                        working_days_needed = 0.0
                        tentative_start_str = current_cursor_d.strftime("%Y-%m-%d")
                        tentative_comp_str = current_cursor_d.strftime("%Y-%m-%d")
                    else:
                        working_days_needed = round(effort_h / nominal_daily_h, 2)
                        tentative_start_str = current_cursor_d.strftime("%Y-%m-%d")
                        comp_d = _add_working_days(current_cursor_d, working_days_needed)
                        tentative_comp_str = comp_d.strftime("%Y-%m-%d")
                        
                        # Advance resource cursor for subsequent tasks
                        current_cursor_d = comp_d
                        
                        if comp_d > horizon_end_d:
                            is_beyond = True
                            res_tasks_beyond_horizon += 1
                            tasks_beyond_horizon_count += 1
                            t_warnings.append(
                                f"Projected completion ({tentative_comp_str}) extends beyond 10-day planning horizon ({horizon_end_str})."
                            )

                act_id = f"act-{proposal_id}-{t.issue_key}-duedate"
                task_proposal = ScheduledTaskProposal(
                    action_id=act_id,
                    issue_key=t.issue_key,
                    project_key=t.project_key,
                    summary=t.summary,
                    assignee_name=t.assignee_name,
                    assignee_role=audit_info.role if audit_info else None,
                    priority=t.priority,
                    queue_sequence=idx,
                    estimated_effort_hours=t.remaining_estimate_hours,
                    p50_effort_hours=p50_val,
                    p90_effort_hours=p90_val,
                    estimate_type=t.estimate_type,
                    is_proxy_estimate=t.is_proxy_estimate,
                    estimate_source_description=t.estimate_rationale,
                    benchmark_sample_count=sample_count,
                    benchmark_confidence_status=conf_status,
                    multiplier_applied=1.0,
                    multiplier_reason=None,
                    dates_available=dates_avail,
                    tentative_start_date=tentative_start_str,
                    tentative_completion_date=tentative_comp_str,
                    working_days_needed=working_days_needed,
                    is_beyond_horizon=is_beyond,
                    is_blocked=is_blocked,
                    unresolved_predecessor_keys=unresolved_preds,
                    successor_keys=succs,
                    blocker_rationale=f"Waiting for {', '.join(unresolved_preds)}" if is_blocked else None,
                    warnings=t_warnings,
                )
                res_scheduled_tasks.append(task_proposal)

            res_sched_audit = ResourceScheduleAudit(
                account_id=audit_info.account_id if audit_info else res_name,
                display_name=res_name,
                role=audit_info.role if audit_info else None,
                team_group=audit_info.team_group if audit_info else None,
                capacity_authority=audit_info.working_hours_source if audit_info else CapacityDataSource.NOMINAL_BASELINE,
                available_capacity_hours=avail_h,
                nominal_daily_hours=nominal_daily_h,
                total_assigned_remaining_hours=total_rem_h,
                allocation_percentage=alloc_pct,
                is_overallocated=audit_info.is_overallocated if audit_info else False,
                is_cross_project=audit_info.cross_project_conflict_detected if audit_info else False,
                projects_involved=audit_info.projects_involved if audit_info else [],
                tasks_count=len(res_scheduled_tasks),
                scheduled_tasks=res_scheduled_tasks,
                tasks_beyond_horizon=res_tasks_beyond_horizon,
                resource_warnings=res_warnings,
            )
            resource_schedules.append(res_sched_audit)

        # 4. Process Unassigned Tasks
        unassigned_proposals: List[ScheduledTaskProposal] = []
        for idx, ut in enumerate(unassigned_tasks_raw, start=1):
            tasks_scheduled_count += 1
            tasks_missing += 1
            unassigned_proposals.append(
                ScheduledTaskProposal(
                    issue_key=ut.issue_key,
                    project_key=ut.project_key,
                    summary=ut.summary,
                    assignee_name=None,
                    assignee_role=None,
                    priority=ut.priority,
                    queue_sequence=idx,
                    estimated_effort_hours=ut.remaining_estimate_hours,
                    estimate_type=ut.estimate_type,
                    is_proxy_estimate=ut.is_proxy_estimate,
                    estimate_source_description=ut.estimate_rationale,
                    dates_available=False,
                    tentative_start_date=None,
                    tentative_completion_date=None,
                    working_days_needed=0.0,
                    is_beyond_horizon=False,
                    is_blocked=ut.is_blocked,
                    unresolved_predecessor_keys=ut.hard_blocker_keys,
                    warnings=["Unassigned task: Cannot schedule without an assigned resource."],
                )
            )

        return AdvisoryScheduleProposal(
            proposal_id=proposal_id,
            proposal_version=1,
            generated_at=now_iso,
            anchor_date=anchor_d.strftime("%Y-%m-%d"),
            planning_horizon_working_days=horizon_working_days,
            horizon_end_date=horizon_end_str,
            validation_report_id=validation_report.report_id,
            validation_status=validation_report.overall_status,
            feasibility_status=feasibility_status,
            configured_projects=configured_projs,
            resolved_projects=resolved_projs,
            unresolved_projects=unresolved_projs,
            total_tasks_considered=len(validation_report.validated_tasks),
            tasks_scheduled_count=tasks_scheduled_count,
            tasks_beyond_horizon_count=tasks_beyond_horizon_count,
            blocked_tasks_count=validation_report.blocked_tasks_count,
            unassigned_tasks_count=len(unassigned_proposals),
            tasks_explicit_estimates=tasks_explicit,
            tasks_benchmark_proxies=tasks_benchmark,
            tasks_fallback_estimates=tasks_fallback,
            tasks_missing_estimates=tasks_missing,
            resource_schedules=resource_schedules,
            unassigned_tasks=unassigned_proposals,
            longest_dependency_chain=[],
            cross_project_contention_resources=validation_report.cross_project_contention_resources,
            capacity_formula_disclosure="forecast_daily_hours (6.5h) * horizon_working_days (10d) = 65.0h nominal baseline",
            unknown_availability_disclosures=unknown_disclosures,
            sequencing_rules_explanation=sequencing_rules,
            risks_and_assumptions=risks_and_assumptions,
            requires_human_review=True,
            is_advisory_only=True,
        )
