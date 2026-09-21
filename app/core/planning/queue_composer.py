"""Deterministic Resource Queue & Capacity Intelligence Composer for Phase 3C.

Composes existing Phase A/B intelligence engines and Phase 3A/3B dependency models
into a consistent, explainable ResourceQueueSnapshot.

CRITICAL ARCHITECTURAL RULES:
1. Deterministic composition of existing intelligence only.
2. Contains ZERO scheduling decisions (no start/finish dates, no schedule mutation).
3. Contains ZERO employee rankings, ratings, or leaderboard metrics.
4. Performs ZERO Jira mutations and ZERO additional Jira API calls (reads local SQLite projections).
5. Never calls DeepSeek or Action Engine.
"""

from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from app.config.settings import settings
from app.core.intelligence.classifier import TaskNatureClassifier
from app.core.intelligence.models import WorkloadPressureLevel
from app.core.intelligence.workload import WorkloadPressureAnalyzer
from app.core.models.performance import (
    ConfidenceLevel,
    EffortStatistics,
    JiraIssueState,
    RoleCategory,
)
from app.core.models.planning import (
    CapacityState,
    DependencyClassification,
    QueueTaskDetail,
    ResourceArtifactContext,
    ResourceCapacitySummary,
    ResourceDependencyContext,
    ResourcePaceSummary,
    ResourceQueueSnapshot,
    TeamWorkloadSnapshot,
)
from app.core.performance.blockers import BlockerAnalyzer
from app.core.performance.capacity import CapacityCalculator
from app.core.performance.complexity import TaskComplexityCalculator
from app.core.performance.pace import HistoricalPaceAnalyzer, calculate_percentiles
from app.core.performance.queue import COMPLETED_STATUSES, CurrentQueueAnalyzer
from app.core.performance.roles import (
    get_account_aliases,
    get_employee_designation_and_category,
    resolve_canonical_account_id,
)
from app.core.planning.artifacts import ArtifactEngine
from app.database.connection import DatabaseManager, db_manager
from app.database.repositories import (
    ArtifactRepository,
    EmployeeRoleRepository,
    JiraIssueLinkRepository,
    JiraIssueStateRepository,
    JiraWorklogRepository,
    PerformanceRepository,
)
from app.utils.logger import logger
from app.utils.time import format_iso, parse_iso_datetime, utc_now, utc_now_iso


class ResourceQueueComposer:
    """Orchestrates the deterministic assembly of ResourceQueueSnapshots."""

    def __init__(
        self,
        manager: Optional[DatabaseManager] = None,
        issue_repo: Optional[JiraIssueStateRepository] = None,
        worklog_repo: Optional[JiraWorklogRepository] = None,
        role_repo: Optional[EmployeeRoleRepository] = None,
        perf_repo: Optional[PerformanceRepository] = None,
        link_repo: Optional[JiraIssueLinkRepository] = None,
        artifact_repo: Optional[ArtifactRepository] = None,
    ):
        self.mgr = manager or db_manager
        self.issue_repo = issue_repo or JiraIssueStateRepository(self.mgr)
        self.worklog_repo = worklog_repo or JiraWorklogRepository(self.mgr)
        self.role_repo = role_repo or EmployeeRoleRepository(self.mgr)
        self.perf_repo = perf_repo or PerformanceRepository(self.mgr)
        self.link_repo = link_repo or JiraIssueLinkRepository(self.mgr)
        self.artifact_repo = artifact_repo or ArtifactRepository(self.mgr)
        self.artifact_engine = ArtifactEngine(self.mgr)

    def compose_snapshot(
        self,
        account_id: str,
        display_name: Optional[str] = None,
        team_group: Optional[str] = None,
        horizon_working_days: int = 10,
        now: Optional[datetime] = None,
    ) -> ResourceQueueSnapshot:
        """Deterministically compose a ResourceQueueSnapshot for a single resource.

        Args:
            account_id: Raw or canonical Atlassian account ID.
            display_name: Optional human display name.
            team_group: Optional team scoping.
            horizon_working_days: Operational planning horizon in business days (default: 10).
            now: Optional anchor datetime (defaults to UTC now).
        """
        now_dt = now or utc_now()
        now_iso = format_iso(now_dt)
        today_date_str = now_iso[:10]

        # 1. Canonical Identity Resolution
        canonical_id = resolve_canonical_account_id(
            account_id,
            display_name=display_name,
            role_repo=self.role_repo,
        ) or account_id

        # Lookup employee role / designation
        assignment = self.role_repo.get_by_account_id(canonical_id)
        if assignment:
            resolved_name = assignment["display_name"] if isinstance(assignment, dict) else assignment.display_name
            designation = assignment["designation"] if isinstance(assignment, dict) else assignment.designation
            role_cat = assignment["role_category"] if isinstance(assignment, dict) else assignment.role_category
            team = team_group or (assignment.get("team_group") if isinstance(assignment, dict) else getattr(assignment, "team_group", None))
        else:
            designation_val, role_cat_val, is_res = get_employee_designation_and_category(
                canonical_id,
                display_name=display_name,
                role_repo=self.role_repo,
            )
            resolved_name = display_name or canonical_id
            designation = designation_val
            role_cat = role_cat_val
            team = team_group

        data_quality_notes: List[str] = []

        # 2. Retrieve Active Assigned Issues
        active_issues_raw = self.issue_repo.get_active_issues_for_resource(
            account_id=canonical_id,
            display_name=resolved_name,
            team_group=team,
        )

        # 3. Retrieve Historical Worklogs & Completed Tasks
        worklogs_raw = self._get_resource_worklogs(canonical_id, resolved_name)
        completed_issues_raw = self._get_resource_completed_issues(canonical_id, resolved_name)

        # Compute active working days and total logged seconds
        active_days_set = {
            w.get("started_at")[:10]
            for w in worklogs_raw
            if w.get("started_at") and len(w.get("started_at")) >= 10
        }
        active_working_days = len(active_days_set)
        total_logged_seconds = sum(int(w.get("time_spent_seconds") or 0) for w in worklogs_raw)

        # 4. Historical Pace & Effort Statistics
        task_efforts: List[Dict[str, Any]] = []
        for c_iss in completed_issues_raw:
            raw_ref = c_iss.get("raw_reference") or {}
            fields = raw_ref.get("fields", {}) if isinstance(raw_ref, dict) else {}
            spent_sec = c_iss.get("time_spent_seconds") or fields.get("timespent") or 0
            task_h = round(float(spent_sec) / 3600.0, 2)
            itype = c_iss.get("issue_type") or "Task"
            c_score = c_iss.get("complexity_score") or 3
            prio = c_iss.get("priority") or "Medium"
            pkey = c_iss.get("project_key") or "UNKNOWN"
            task_efforts.append({
                "hours": task_h,
                "issue_type": itype,
                "complexity_score": c_score,
                "priority": prio,
                "project_key": pkey,
            })

        effort_stats = HistoricalPaceAnalyzer.compute_effort_statistics(
            account_id=canonical_id,
            task_efforts=task_efforts,
        )
        overall_stat = next((s for s in effort_stats if s.segment_type == "overall"), None)

        completed_count = len(completed_issues_raw)
        if overall_stat and completed_count > 0:
            pace_summary = ResourcePaceSummary(
                completed_task_count=completed_count,
                mean_hours=overall_stat.mean_hours,
                median_hours=overall_stat.median_hours,
                p25_hours=overall_stat.p25_hours,
                p75_hours=overall_stat.p75_hours,
                min_hours=overall_stat.min_hours,
                max_hours=overall_stat.max_hours,
                pace_factor=1.0,
                confidence=overall_stat.confidence.value,
                is_fallback=overall_stat.is_fallback,
            )
        else:
            pace_summary = ResourcePaceSummary(
                completed_task_count=0,
                mean_hours=0.0,
                median_hours=0.0,
                p25_hours=0.0,
                p75_hours=0.0,
                min_hours=0.0,
                max_hours=0.0,
                pace_factor=1.0,
                confidence="INSUFFICIENT",
                is_fallback=True,
            )

        # 5. Capacity Calculation
        capacity_metrics = CapacityCalculator.calculate_capacity_metrics(
            total_logged_seconds=total_logged_seconds,
            active_working_days=active_working_days,
            history_days=180,
        )
        nom_daily = capacity_metrics["nominal_capacity_hours"]
        obs_daily = capacity_metrics["observed_logged_capacity_hours"]
        fore_daily = capacity_metrics["forecast_capacity_hours"]
        cap_method = capacity_metrics["capacity_method"]
        cap_conf = capacity_metrics["confidence"].value

        avail_cap_hours = round(fore_daily * float(horizon_working_days), 2)

        # 6. Current Queue Detailed Processing
        queue_res = CurrentQueueAnalyzer.analyze_active_queue(
            account_id=canonical_id,
            active_issues=active_issues_raw,
            resource_effort_stats=effort_stats,
            designation=designation,
            role_category=role_cat,
        )

        forecasts_by_key = {tf.issue_key: tf for tf in queue_res.get("task_forecasts", [])}

        active_tasks_details: List[QueueTaskDetail] = []
        overdue_keys: List[str] = []
        stale_keys: List[str] = []
        blocked_keys: List[str] = []
        reopened_keys: List[str] = []
        unestimated_keys: List[str] = []

        priority_summary: Dict[str, int] = defaultdict(int)
        task_type_summary: Dict[str, int] = defaultdict(int)
        task_nature_summary: Dict[str, int] = defaultdict(int)

        stale_cutoff = now_dt - timedelta(hours=48)
        stale_cutoff_iso = format_iso(stale_cutoff)

        # Pre-query all links and artifacts for active tasks to avoid N+1 scans
        active_issue_keys = [
            (iss.get("jira_issue_key") or iss.get("key") or "").strip().upper()
            for iss in active_issues_raw
            if iss.get("jira_issue_key") or iss.get("key")
        ]

        # Fetch dependency predecessors for all active issues
        hard_blocker_map: Dict[str, List[str]] = defaultdict(list)
        all_dependency_links_count = 0
        downstream_map: Dict[str, List[str]] = defaultdict(list)

        for tkey in active_issue_keys:
            target_links = self.link_repo.list_links_for_target(tkey, active_only=True)
            for link in target_links:
                all_dependency_links_count += 1
                if link.get("classification") == DependencyClassification.HARD_BLOCK.value:
                    hard_blocker_map[tkey].append(link["source_issue_key"])

            source_links = self.link_repo.list_links_for_source(tkey, active_only=True)
            for link in source_links:
                all_dependency_links_count += 1
                downstream_map[tkey].append(link["target_issue_key"])

        # Fetch artifact relationships for active tasks
        task_produced_artifacts: Dict[str, List[str]] = defaultdict(list)
        task_consumed_artifacts: Dict[str, List[str]] = defaultdict(list)
        all_produced_art_ids: Set[str] = set()
        all_consumed_art_ids: Set[str] = set()

        for tkey in active_issue_keys:
            art_info = self.artifact_engine.list_artifacts_for_issue(tkey)
            for p in art_info.get("produced", []):
                task_produced_artifacts[tkey].append(p["name"])
                all_produced_art_ids.add(p["id"])
            for c in art_info.get("consumed", []):
                task_consumed_artifacts[tkey].append(c["name"])
                all_consumed_art_ids.add(c["id"])

        for issue in active_issues_raw:
            tkey = (issue.get("jira_issue_key") or issue.get("key") or "").strip().upper()
            if not tkey:
                continue

            status_str = (issue.get("status") or "").strip()
            if status_str.lower() in COMPLETED_STATUSES:
                continue

            raw_ref = issue.get("raw_reference") or {}
            fields = raw_ref.get("fields", {}) if isinstance(raw_ref, dict) else {}

            itype = issue.get("issue_type") or fields.get("issuetype", {}).get("name") or "Task"
            priority = issue.get("priority") or fields.get("priority", {}).get("name") or "Medium"
            due_date = issue.get("due_date") or fields.get("duedate")
            summary = issue.get("summary") or fields.get("summary") or "Untitled"
            pkey = issue.get("project_key") or (tkey.split("-")[0] if "-" in tkey else "PROJ")

            # Components & Labels
            comps = issue.get("components") or []
            if isinstance(comps, str):
                import json
                try:
                    comps = json.loads(comps)
                except Exception:
                    comps = [comps]
            labels = issue.get("labels") or []
            if isinstance(labels, str):
                import json
                try:
                    labels = json.loads(labels)
                except Exception:
                    labels = [labels]

            # Intrinsic Complexity
            subtask_count = int(issue.get("subtask_count") or 0)
            orig_est_secs = issue.get("original_estimate_seconds") or fields.get("timeoriginalestimate")
            complexity = TaskComplexityCalculator.calculate_complexity(
                issue_type=itype,
                priority=priority,
                project_key=pkey,
                components=comps,
                labels=labels,
                subtask_count=subtask_count,
                original_estimate_seconds=orig_est_secs,
                summary=summary,
            )

            # Task Nature Classification
            nature_res = TaskNatureClassifier.classify_issue(
                issue_key=tkey,
                issue_type=itype,
                summary=summary,
                description=issue.get("description") or "",
                components=comps,
                labels=labels,
            )

            # Check Overdue Status
            is_overdue = False
            if due_date and str(due_date).strip():
                due_clean = str(due_date).strip()[:10]
                if due_clean < today_date_str:
                    is_overdue = True
                    overdue_keys.append(tkey)

            # Check Stale Status
            last_act = issue.get("last_activity_at") or issue.get("updated_at")
            is_stale = False
            if last_act and str(last_act) <= stale_cutoff_iso:
                is_stale = True
                stale_keys.append(tkey)

            # Check Blocker Status (Evidence-based changelog or JiraIssueLink HARD_BLOCK)
            blocker_res = BlockerAnalyzer.analyze_issue_blockers(raw_ref)
            has_hard_blocks = len(hard_blocker_map[tkey]) > 0
            is_blocked = blocker_res["blocker_detected"] or has_hard_blocks
            if is_blocked:
                blocked_keys.append(tkey)

            # Check Reopen Status
            task_reopens = int(issue.get("reopen_count") or 0)
            is_reopened = task_reopens > 0 or status_str.lower() in ("reopened", "re-opened")
            if is_reopened:
                reopened_keys.append(tkey)

            # Check Estimate Availability
            raw_orig_h = (
                round(float(orig_est_secs) / 3600.0, 2)
                if orig_est_secs is not None and float(orig_est_secs) > 0
                else None
            )
            if raw_orig_h is None:
                unestimated_keys.append(tkey)

            # Get forecast from queue analyzer
            t_forecast = forecasts_by_key.get(tkey)
            time_spent_h = (
                round(float(issue.get("time_spent_seconds") or 0) / 3600.0, 2)
                if issue.get("time_spent_seconds")
                else (t_forecast.logged_hours if t_forecast else 0.0)
            )
            rem_h = t_forecast.remaining_hours if t_forecast else (raw_orig_h or 8.0)
            exp_h = t_forecast.inferred_expected_hours if t_forecast else (raw_orig_h or 8.0)
            effort_src = t_forecast.expected_effort_source if t_forecast else "unavailable"
            effort_conf = t_forecast.expected_effort_confidence if t_forecast else "unavailable"

            # Summaries
            priority_summary[priority] += 1
            task_type_summary[itype] += 1
            task_nature_summary[nature_res.task_nature.value] += 1

            detail = QueueTaskDetail(
                issue_key=tkey,
                summary=summary,
                status=status_str,
                priority=priority,
                issue_type=itype,
                task_nature=nature_res.task_nature.value,
                project_key=pkey,
                due_date=due_date,
                complexity_score=complexity.complexity_score,
                complexity_confidence=complexity.confidence.value,
                original_estimate_hours=raw_orig_h,
                time_spent_hours=time_spent_h,
                remaining_hours=rem_h,
                expected_effort_hours=exp_h,
                expected_effort_source=effort_src,
                expected_effort_confidence=effort_conf,
                is_overdue=is_overdue,
                is_stale=is_stale,
                is_blocked=is_blocked,
                is_reopened=is_reopened,
                hard_blocker_keys=hard_blocker_map[tkey],
                produced_artifact_names=task_produced_artifacts[tkey],
                consumed_artifact_names=task_consumed_artifacts[tkey],
            )
            active_tasks_details.append(detail)

        # Queue Totals
        tot_remaining_workload = round(float(queue_res.get("total_remaining_hours", 0.0)), 2)
        tot_logged = round(float(queue_res.get("total_logged_hours", 0.0)), 2)
        tot_review_buffer = round(float(queue_res.get("total_review_buffer_hours", 0.0)), 2)

        # 7. Capacity State Assessment
        rem_cap_hours = round(avail_cap_hours - tot_remaining_workload, 2)
        if avail_cap_hours <= 0:
            cap_state = CapacityState.UNKNOWN
        elif tot_remaining_workload < 0.6 * avail_cap_hours:
            cap_state = CapacityState.UNDER_UTILIZED
        elif tot_remaining_workload <= 1.0 * avail_cap_hours:
            cap_state = CapacityState.BALANCED
        elif tot_remaining_workload <= 1.4 * avail_cap_hours:
            cap_state = CapacityState.OVERLOADED
        else:
            cap_state = CapacityState.SATURATED

        capacity_summary = ResourceCapacitySummary(
            nominal_daily_capacity_hours=nom_daily,
            observed_daily_capacity_hours=obs_daily,
            forecast_daily_capacity_hours=fore_daily,
            horizon_working_days=horizon_working_days,
            available_capacity_hours=avail_cap_hours,
            committed_workload_hours=tot_remaining_workload,
            remaining_capacity_hours=rem_cap_hours,
            capacity_state=cap_state,
            capacity_method=cap_method,
            confidence=cap_conf,
        )

        # 8. Workload Pressure Composition (Reused from WorkloadPressureAnalyzer)
        active_issue_states = [
            JiraIssueState(
                issue_key=t.issue_key,
                summary=t.summary,
                status=t.status,
                priority=t.priority,
                issue_type=t.issue_type,
                due_date=t.due_date,
                complexity_score=t.complexity_score,
                original_estimate_hours=t.original_estimate_hours,
                time_spent_seconds=int(t.time_spent_hours * 3600),
            )
            for t in active_tasks_details
        ]

        pressure_eval = WorkloadPressureAnalyzer.assess_workload_pressure(
            account_id=canonical_id,
            active_issues=active_issue_states,
            inferred_remaining_workload_hours=tot_remaining_workload,
            forecast_capacity_hours=avail_cap_hours,
            active_blockers_count=len(blocked_keys),
            now=now_dt,
        )

        # 9. Dependency & Artifact Contexts
        downstream_flat = sorted(list({k for keys in downstream_map.values() for k in keys}))
        dep_context = ResourceDependencyContext(
            total_dependencies=all_dependency_links_count,
            hard_blocker_count=sum(len(v) for v in hard_blocker_map.values()),
            blocked_issue_keys=sorted(list(set(blocked_keys))),
            downstream_dependent_keys=downstream_flat,
        )

        art_context = ResourceArtifactContext(
            total_artifacts=len(all_produced_art_ids) + len(all_consumed_art_ids),
            produced_artifact_ids=sorted(list(all_produced_art_ids)),
            consumed_artifact_ids=sorted(list(all_consumed_art_ids)),
        )

        # 10. Data Quality Classification
        # History
        if completed_count >= 15 and active_working_days >= 30:
            history_comp = "SUFFICIENT_HISTORY"
        elif completed_count >= 1 or active_working_days >= 5:
            history_comp = "LIMITED_HISTORY"
            data_quality_notes.append(
                f"Limited historical tasks ({completed_count} completed) or active days ({active_working_days} days)."
            )
        else:
            history_comp = "NO_HISTORY"
            data_quality_notes.append("No historical completed tasks or worklogs found for resource.")

        # Capacity
        if active_working_days >= 15:
            capacity_quality = "CAPACITY_KNOWN"
        elif active_working_days >= 5:
            capacity_quality = "CAPACITY_PARTIAL"
            data_quality_notes.append(
                f"Capacity partially calibrated ({active_working_days} active working days)."
            )
        else:
            capacity_quality = "CAPACITY_UNAVAILABLE"
            data_quality_notes.append(
                "Capacity based purely on default nominal workday (insufficient logged activity)."
            )

        # Queue
        if len(active_tasks_details) == 0:
            queue_comp = "QUEUE_EMPTY"
        elif len(unestimated_keys) == 0:
            queue_comp = "QUEUE_COMPLETE"
        else:
            queue_comp = "QUEUE_PARTIAL"
            data_quality_notes.append(
                f"{len(unestimated_keys)} of {len(active_tasks_details)} tasks lack explicit Jira estimates (relying on empirical complexity inference)."
            )

        if len(blocked_keys) > 0:
            data_quality_notes.append(
                f"{len(blocked_keys)} active tasks currently blocked by external dependencies or hard blockers."
            )

        if len(overdue_keys) > 0:
            data_quality_notes.append(
                f"{len(overdue_keys)} active tasks have passed their stated due date."
            )

        return ResourceQueueSnapshot(
            resource_id=canonical_id,
            display_name=resolved_name,
            designation=designation,
            role_category=role_cat,
            team_group=team,
            snapshot_timestamp=now_iso,
            active_tasks=active_tasks_details,
            active_task_count=len(active_tasks_details),
            overdue_task_count=len(overdue_keys),
            stale_task_count=len(stale_keys),
            blocked_task_count=len(blocked_keys),
            reopened_task_count=len(reopened_keys),
            unestimated_task_count=len(unestimated_keys),
            priority_summary=dict(priority_summary),
            task_type_summary=dict(task_type_summary),
            task_nature_summary=dict(task_nature_summary),
            total_inferred_remaining_hours=tot_remaining_workload,
            total_logged_hours=tot_logged,
            total_review_buffer_hours=tot_review_buffer,
            historical_completed_tasks=completed_count,
            historical_active_working_days=active_working_days,
            historical_pace=pace_summary,
            capacity=capacity_summary,
            workload_pressure_level=pressure_eval.pressure_level.value,
            workload_pressure_explanation=pressure_eval.explanation,
            tasks_due_within_7_days=pressure_eval.tasks_due_within_7_days,
            high_complexity_tasks_count=pressure_eval.high_complexity_tasks_count,
            dependency_context=dep_context,
            artifact_context=art_context,
            history_completeness=history_comp,
            capacity_quality=capacity_quality,
            queue_completeness=queue_comp,
            data_quality_notes=data_quality_notes,
        )

    def compose_team_snapshots(
        self,
        account_ids: Optional[List[str]] = None,
        team_group: Optional[str] = None,
        horizon_working_days: int = 10,
        now: Optional[datetime] = None,
    ) -> TeamWorkloadSnapshot:
        """Deterministically compose snapshots for multiple resources without employee ranking.

        Args:
            account_ids: Explicit list of canonical account IDs to analyze.
            team_group: Scoping filter by team group.
            horizon_working_days: Operational planning horizon in business days (default: 10).
            now: Optional anchor datetime.
        """
        now_dt = now or utc_now()
        now_iso = format_iso(now_dt)

        # 1. Discover target account IDs
        target_accounts: List[str] = []
        if account_ids:
            target_accounts = [
                acc for acc in account_ids
                if acc not in settings.CANONICAL_EXCLUDED_ACCOUNT_IDS
            ]
        else:
            assignments = self.role_repo.list_assignments()
            for a in assignments:
                acc = a.get("account_id")
                if acc and acc not in settings.CANONICAL_EXCLUDED_ACCOUNT_IDS:
                    if team_group and a.get("team_group") != team_group:
                        continue
                    target_accounts.append(acc)

        # Sort deterministically by account_id to guarantee reproducible order
        target_accounts = sorted(list(set(target_accounts)))

        snapshots: List[ResourceQueueSnapshot] = []
        tot_active_tasks = 0
        tot_remaining_workload = 0.0
        tot_avail_capacity = 0.0
        tot_overdue = 0
        tot_blocked = 0

        for acc in target_accounts:
            snap = self.compose_snapshot(
                account_id=acc,
                team_group=team_group,
                horizon_working_days=horizon_working_days,
                now=now_dt,
            )
            snapshots.append(snap)
            tot_active_tasks += snap.active_task_count
            tot_remaining_workload += snap.total_inferred_remaining_hours
            tot_avail_capacity += snap.capacity.available_capacity_hours
            tot_overdue += snap.overdue_task_count
            tot_blocked += snap.blocked_task_count

        return TeamWorkloadSnapshot(
            snapshot_timestamp=now_iso,
            team_group=team_group,
            resources_count=len(snapshots),
            total_active_tasks=tot_active_tasks,
            total_remaining_workload_hours=round(tot_remaining_workload, 2),
            total_available_capacity_hours=round(tot_avail_capacity, 2),
            total_overdue_tasks=tot_overdue,
            total_blocked_tasks=tot_blocked,
            resource_snapshots=snapshots,
        )

    # -------------------------------------------------------------------------
    # Internal Helpers (Read-only projections from SQLite)
    # -------------------------------------------------------------------------

    def _get_resource_worklogs(self, account_id: str, display_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retrieve historical worklogs for an employee using alias matching."""
        aliases = get_account_aliases(account_id) or [account_id]
        with self.mgr.session() as conn:
            placeholders = ", ".join("?" for _ in aliases)
            clauses = [f"author_account_id IN ({placeholders})"]
            params: List[Any] = list(aliases)
            if display_name and display_name not in aliases:
                clauses.append("author_display_name = ?")
                params.append(display_name)
            query = f"""
                SELECT * FROM jira_worklogs
                WHERE ({' OR '.join(clauses)})
                ORDER BY started_at ASC
            """
            cursor = conn.execute(query, tuple(params))
            return [dict(r) for r in cursor.fetchall()]

    def _get_resource_completed_issues(self, account_id: str, display_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retrieve historical completed tasks for an employee using alias matching."""
        aliases = get_account_aliases(account_id) or [account_id]
        done_clause = "lower(status) IN ('done', 'completed', 'resolved', 'closed', 'finished')"
        with self.mgr.session() as conn:
            placeholders = ", ".join("?" for _ in aliases)
            clauses = [f"assignee IN ({placeholders})"]
            params: List[Any] = list(aliases)
            if display_name and display_name not in aliases:
                clauses.append("assignee = ?")
                params.append(display_name)
            query = f"""
                SELECT * FROM jira_issue_state
                WHERE {done_clause}
                  AND ({' OR '.join(clauses)})
                ORDER BY updated_at DESC
            """
            cursor = conn.execute(query, tuple(params))
            rows = cursor.fetchall()
            return [JiraIssueStateRepository._format_row(dict(r)) for r in rows]
