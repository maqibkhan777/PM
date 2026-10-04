"""Planning Data and Cross-Project Capacity Validation Service (Milestone 4A).

Validates:
1. Multi-project scope resolution (`SMTPSUPORT`, `GF`, etc.) and project isolation.
2. Unified resource workload across projects without double-counting issues.
3. Capacity calculation audit and transparency of nominal vs. leave/absence data gaps.
4. Dependency links, blocker feasibility, and cyclic dependency checks.
5. Overall planning feasibility rating (`VALID`, `PARTIAL`, `UNRELIABLE`).

Guarantees:
- Read-only: Zero Jira mutations, zero database writes, zero schedule commits.
- Deterministic and explainable.
"""

from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple
import uuid

from app.config.settings import settings
from app.core.intelligence.effort_recommendation_models import BenchmarkRecommendationStatus
from app.core.intelligence.effort_retrieval_service import (
    HistoricalEffortBenchmarkRetrievalService,
)
from app.core.planning.dag import DependencyGraph
from app.core.planning.validation_models import (
    CapacityDataSource,
    DependencyValidationFinding,
    EstimateType,
    PlanningDataValidationReport,
    ResourceCapacityAudit,
    ValidatedTaskWorkload,
    ValidationOutcome,
)
from app.database.connection import DatabaseManager, db_manager
from app.database.repositories import (
    EmployeeRoleRepository,
    JiraIssueLinkRepository,
    JiraIssueStateRepository,
    JiraWorklogRepository,
)
from app.utils.logger import logger
from app.utils.time import format_iso, utc_now, utc_now_iso


COMPLETED_STATUSES: Set[str] = {
    "done",
    "completed",
    "resolved",
    "closed",
    "finished",
    "cancelled",
    "rejected",
}


class PlanningDataValidationService:
    """Read-only validator for planning context, multi-project workloads, and capacity."""

    def __init__(
        self,
        manager: Optional[DatabaseManager] = None,
        issue_repo: Optional[JiraIssueStateRepository] = None,
        role_repo: Optional[EmployeeRoleRepository] = None,
        link_repo: Optional[JiraIssueLinkRepository] = None,
        worklog_repo: Optional[JiraWorklogRepository] = None,
        benchmark_svc: Optional[HistoricalEffortBenchmarkRetrievalService] = None,
    ):
        self.mgr = manager or db_manager
        self.issue_repo = issue_repo or JiraIssueStateRepository(self.mgr)
        self.role_repo = role_repo or EmployeeRoleRepository(self.mgr)
        self.link_repo = link_repo or JiraIssueLinkRepository(self.mgr)
        self.worklog_repo = worklog_repo or JiraWorklogRepository(self.mgr)
        self.benchmark_svc = benchmark_svc or HistoricalEffortBenchmarkRetrievalService(self.mgr)

    def validate_planning_data(
        self,
        project_keys: List[str],
        horizon_working_days: int = 10,
        now: Optional[datetime] = None,
    ) -> PlanningDataValidationReport:
        """Run complete planning data and capacity validation across specified projects."""
        now_dt = now or utc_now()
        now_iso = format_iso(now_dt)
        anchor_date = now_iso[:10]
        report_id = f"val-{uuid.uuid4().hex[:8]}"

        # 1. Scope Resolution
        clean_keys = [k.strip().upper() for k in project_keys if k and k.strip()]
        distinct_clean_keys = list(dict.fromkeys(clean_keys))

        known_db_projects = set(self.issue_repo.get_distinct_project_keys())
        resolved_projects = [k for k in distinct_clean_keys if k in known_db_projects]
        unresolved_projects = [k for k in distinct_clean_keys if k not in known_db_projects]

        exclusions: Dict[str, str] = {}
        for unk in unresolved_projects:
            exclusions[unk] = "Project key not found in Jira issue state database"

        # 2. Collect and Deduplicate Active Issues Across Resolved Projects
        seen_keys: Set[str] = set()
        active_tasks: List[ValidatedTaskWorkload] = []
        tasks_missing_assignee = 0
        tasks_with_explicit_remaining = 0
        tasks_with_benchmark_estimates = 0
        tasks_with_missing_estimates = 0

        # Load all links for blocker analysis
        all_links = self.link_repo.list_all_links(active_only=True)
        dep_graph = DependencyGraph(include_only_hard_blocks=True)
        hard_blocker_map: Dict[str, List[str]] = defaultdict(list)
        
        for l in all_links:
            src = l.get("source_issue_key", "").strip().upper()
            tgt = l.get("target_issue_key", "").strip().upper()
            class_str = l.get("classification", "HARD_BLOCK")
            if src and tgt and class_str == "HARD_BLOCK":
                hard_blocker_map[tgt].append(src)
                try:
                    dep_graph.add_edge(
                        source_key=src,
                        target_key=tgt,
                        link_type="Blocks",
                        classification=DependencyClassification.HARD_BLOCK,
                    )
                except Exception:
                    pass

        # Cycle check
        cycle_res = dep_graph.detect_cycles()
        if cycle_res.has_cycle:
            findings.append(DependencyValidationFinding(
                finding_type="CYCLE_DETECTED",
                severity="CRITICAL",
                source_issue_key=",".join(cycle_res.cycle_nodes),
                message=f"Dependency cycle detected among active issues: {cycle_res.cycle_path_str}",
            ))

        for p_key in resolved_projects:
            # Query all active issues for this project
            with self.mgr.session() as conn:
                cursor = conn.execute(
                    """
                    SELECT * FROM jira_issue_state
                    WHERE UPPER(project_key) = ?
                    ORDER BY jira_issue_key ASC
                    """,
                    (p_key,),
                )
                rows = [dict(r) for r in cursor.fetchall()]

            for r in rows:
                t_key = (r.get("jira_issue_key") or "").strip().upper()
                if not t_key:
                    continue

                status = (r.get("status") or "To Do").strip()
                if status.lower() in COMPLETED_STATUSES:
                    continue

                if t_key in seen_keys:
                    exclusions[t_key] = "Duplicate issue key ignored in multi-path retrieval"
                    continue
                seen_keys.add(t_key)

                # Process Assignee
                assignee_name = r.get("assignee")
                if not assignee_name or str(assignee_name).strip().lower() == "unassigned":
                    assignee_name = None
                    tasks_missing_assignee += 1

                # Parse estimates
                orig_sec = r.get("original_estimate_seconds")
                orig_h = round(float(orig_sec) / 3600.0, 2) if orig_sec is not None and orig_sec > 0 else None
                spent_sec = r.get("time_spent_seconds")
                spent_h = round(float(spent_sec) / 3600.0, 2) if spent_sec is not None and spent_sec > 0 else None

                # Remaining estimate resolution
                raw_ref = r.get("raw_reference") or {}
                if isinstance(raw_ref, str):
                    import json
                    try:
                        raw_ref = json.loads(raw_ref)
                    except Exception:
                        raw_ref = {}
                fields = raw_ref.get("fields", {}) if isinstance(raw_ref, dict) else {}
                rem_sec = fields.get("timetracking", {}).get("remainingEstimateSeconds")
                if rem_sec is None:
                    rem_sec = fields.get("timeestimate")

                rem_h: Optional[float] = None
                est_type: EstimateType = EstimateType.MISSING_UNESTIMATED
                is_proxy = False
                rationale = ""

                if rem_sec is not None:
                    if rem_sec == 0:
                        rem_h = 0.0
                        est_type = EstimateType.EXPLICIT_ZERO
                        rationale = "Explicit 0h remaining in Jira timetracking"
                    else:
                        rem_h = round(float(rem_sec) / 3600.0, 2)
                        est_type = EstimateType.EXPLICIT_REMAINING
                        tasks_with_explicit_remaining += 1
                        rationale = f"Authoritative Jira remaining estimate: {rem_h}h"
                elif orig_h is not None and orig_h > 0:
                    # Fallback to remaining from original if time spent
                    spent_val = spent_h or 0.0
                    calc_rem = max(0.0, orig_h - spent_val)
                    rem_h = round(calc_rem, 2)
                    est_type = EstimateType.EXPLICIT_REMAINING
                    tasks_with_explicit_remaining += 1
                    rationale = f"Calculated from Jira original estimate ({orig_h}h) minus time spent ({spent_val}h)"
                else:
                    # Check empirical historical benchmarks
                    rec = self.benchmark_svc.recommend_effort_for_task(
                        project_key=p_key,
                        issue_key=t_key,
                        issue_type=r.get("issue_type") or "Task",
                        priority=r.get("priority") or "Medium",
                    )
                    if rec.reliability_status in (
                        BenchmarkRecommendationStatus.USABLE,
                        BenchmarkRecommendationStatus.LOW_CONFIDENCE,
                    ) and rec.recommended_effort_hours is not None:
                        rem_h = rec.recommended_effort_hours
                        est_type = EstimateType.HISTORICAL_BENCHMARK
                        is_proxy = True
                        tasks_with_benchmark_estimates += 1
                        rationale = f"Empirical historical benchmark proxy: {rec.explanation}"
                    else:
                        est_type = EstimateType.MISSING_UNESTIMATED
                        tasks_with_missing_estimates += 1
                        rationale = "No Jira estimate and insufficient empirical benchmark data"

                # Dependencies & Blockers
                blockers = hard_blocker_map.get(t_key, [])
                is_blocked = len(blockers) > 0

                task_obj = ValidatedTaskWorkload(
                    issue_key=t_key,
                    project_key=p_key,
                    summary=r.get("summary") or "Untitled",
                    status=status,
                    status_category="In Progress" if status.lower() in ("in progress", "in development", "doing") else "To Do",
                    assignee_name=assignee_name,
                    priority=r.get("priority") or "Medium",
                    issue_type=r.get("issue_type") or "Task",
                    due_date=r.get("due_date"),
                    original_estimate_hours=orig_h,
                    time_spent_hours=spent_h,
                    remaining_estimate_hours=rem_h,
                    estimate_type=est_type,
                    is_proxy_estimate=is_proxy,
                    estimate_rationale=rationale,
                    is_blocked=is_blocked,
                    hard_blocker_keys=blockers,
                    has_unresolved_predecessors=is_blocked,
                    data_source="jira_issue_state",
                    retrieval_timestamp=now_iso,
                )
                active_tasks.append(task_obj)

        # 3. Cross-Project Resource Capacity and Workload Auditing
        role_assignments = self.role_repo.list_assignments()
        name_to_account_id = {a.get("display_name"): a.get("account_id") for a in role_assignments if a.get("display_name")}
        
        # Group tasks by assigned resource
        tasks_by_resource: Dict[str, List[ValidatedTaskWorkload]] = defaultdict(list)
        for t in active_tasks:
            res_identifier = t.assignee_name or "UNASSIGNED"
            tasks_by_resource[res_identifier].append(t)

        resource_audits: List[ResourceCapacityAudit] = []
        overallocated_resources: List[str] = []
        contention_resources: List[str] = []

        # Nominal baseline
        nom_daily = float(getattr(settings, "PERFORMANCE_WORKDAY_HOURS", 6.75))
        fore_daily = float(getattr(settings, "PERFORMANCE_WORKDAY_MIN_HOURS", 6.50))
        available_hours_std = round(fore_daily * float(horizon_working_days), 2)

        for res_name, res_tasks in tasks_by_resource.items():
            if res_name == "UNASSIGNED":
                continue

            acc_id = name_to_account_id.get(res_name) or res_name
            role_rec = self.role_repo.get_by_account_id(acc_id) if acc_id else None
            role_name = role_rec.get("designation") if isinstance(role_rec, dict) else (getattr(role_rec, "designation", None) if role_rec else None)
            team = role_rec.get("team_group") if isinstance(role_rec, dict) else (getattr(role_rec, "team_group", None) if role_rec else None)

            # Project workload breakdown
            proj_workload: Dict[str, float] = defaultdict(float)
            total_rem = 0.0
            for rt in res_tasks:
                effort = rt.remaining_estimate_hours or 0.0
                proj_workload[rt.project_key] += effort
                total_rem += effort

            projects_inv = sorted(list(proj_workload.keys()))
            is_cross = len(projects_inv) > 1
            if is_cross:
                contention_resources.append(res_name)

            pressure_ratio = round(total_rem / available_hours_std, 3) if available_hours_std > 0 else 0.0
            is_over = pressure_ratio > 1.0
            if is_over:
                overallocated_resources.append(res_name)

            unreliable_reasons: List[str] = []
            disclosures: List[str] = [
                "Available capacity assumes nominal 6.5h/day over 10 working days without individual leave calendar verification."
            ]

            # Check capacity reliability gaps
            if not role_rec:
                unreliable_reasons.append("Resource has no authoritative role assignment in employee registry.")
            
            disclosures.append("Holidays, sick leave, part-time schedule, and non-project focus factor are UNKNOWN.")

            res_status = ValidationOutcome.PARTIAL
            if len(unreliable_reasons) > 0:
                res_status = ValidationOutcome.PARTIAL

            audit = ResourceCapacityAudit(
                account_id=acc_id,
                display_name=res_name,
                role=role_name,
                team_group=team,
                planning_horizon_working_days=horizon_working_days,
                nominal_daily_hours=nom_daily,
                forecast_daily_hours=fore_daily,
                capacity_formula="forecast_daily_hours (6.5h) * horizon_working_days (10d)",
                available_capacity_hours=available_hours_std,
                working_hours_source=CapacityDataSource.NOMINAL_BASELINE,
                holidays_calendar_source=CapacityDataSource.UNKNOWN,
                leave_absence_source=CapacityDataSource.UNKNOWN,
                part_time_schedule_source=CapacityDataSource.UNKNOWN,
                focus_factor_source=CapacityDataSource.UNKNOWN,
                assigned_tasks_count=len(res_tasks),
                projects_involved=projects_inv,
                project_workload_hours=dict(proj_workload),
                total_assigned_remaining_hours=round(total_rem, 2),
                workload_pressure_ratio=pressure_ratio,
                is_overallocated=is_over,
                cross_project_conflict_detected=is_cross,
                capacity_reliability=res_status,
                unreliable_reasons=unreliable_reasons,
                data_quality_disclosures=disclosures,
            )
            resource_audits.append(audit)

        # 4. Dependency Findings
        dep_findings: List[DependencyValidationFinding] = []
        for tgt_key, src_keys in hard_blocker_map.items():
            tgt_proj = tgt_key.split("-")[0] if "-" in tgt_key else "UNKNOWN"
            for src_key in src_keys:
                src_proj = src_key.split("-")[0] if "-" in src_key else "UNKNOWN"
                src_task = self.issue_repo.get(src_key)
                src_status = src_task.get("status", "Unknown") if src_task else "Missing"
                is_resolved = src_status.lower() in COMPLETED_STATUSES
                is_cross = src_proj != tgt_proj

                dep_findings.append(
                    DependencyValidationFinding(
                        source_issue_key=src_key,
                        target_issue_key=tgt_key,
                        link_type="Blocks",
                        is_hard_block=True,
                        is_target_resolved=is_resolved,
                        is_cycle_detected=cycle_res.has_cycle,
                        is_cross_project=is_cross,
                        source_project=src_proj,
                        target_project=tgt_proj,
                        notes="Cross-project dependency" if is_cross else "Intra-project dependency",
                    )
                )

        # 5. Overall Validation Assessment
        limitations: List[str] = [
            "Authoritative employee leave, holiday calendars, and part-time schedules are UNKNOWN; nominal 6.5h/day baseline applied.",
        ]
        if tasks_with_missing_estimates > 0:
            limitations.append(f"{tasks_with_missing_estimates} active task(s) lack explicit estimates or historical benchmark proxies.")
        if tasks_missing_assignee > 0:
            limitations.append(f"{tasks_missing_assignee} active task(s) are unassigned.")
        if len(unresolved_projects) > 0:
            limitations.append(f"Configured project(s) {', '.join(unresolved_projects)} could not be resolved.")

        overall_status = ValidationOutcome.VALID
        if len(unresolved_projects) > 0 or tasks_with_missing_estimates > 0 or len(overallocated_resources) > 0:
            overall_status = ValidationOutcome.PARTIAL
        if len(resolved_projects) == 0:
            overall_status = ValidationOutcome.UNRELIABLE

        summary_text = (
            f"Validated planning data across {len(resolved_projects)} project(s) ({', '.join(resolved_projects)}): "
            f"{len(active_tasks)} active tasks, {len(resource_audits)} assigned resources, "
            f"{len(overallocated_resources)} over-allocated, {len(contention_resources)} cross-project allocations. "
            f"Status: {overall_status.value}."
        )

        return PlanningDataValidationReport(
            report_id=report_id,
            generated_at=now_iso,
            anchor_date=anchor_date,
            planning_horizon_working_days=horizon_working_days,
            configured_projects=distinct_clean_keys,
            resolved_projects=resolved_projects,
            unresolved_projects=unresolved_projects,
            total_active_tasks=len(active_tasks),
            tasks_with_explicit_remaining=tasks_with_explicit_remaining,
            tasks_with_benchmark_estimates=tasks_with_benchmark_estimates,
            tasks_with_missing_estimates=tasks_with_missing_estimates,
            tasks_missing_assignee=tasks_missing_assignee,
            resources_audited=resource_audits,
            overallocated_resources=overallocated_resources,
            cross_project_contention_resources=contention_resources,
            blocked_tasks_count=len([t for t in active_tasks if t.is_blocked]),
            dependency_findings=dep_findings,
            validated_tasks=active_tasks,
            deduplicated_issue_keys=list(seen_keys),
            exclusions_and_reasons=exclusions,
            overall_status=overall_status,
            executive_summary=summary_text,
            capacity_limitations_summary=limitations,
        )
