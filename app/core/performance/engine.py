"""Performance Data Foundation Analysis Engine for Phase A.

Orchestrates deterministic, explainable performance profiling, statistical pace calculation,
conservative blocker detection, capacity evaluation, queue forecasting, signals, and evidence ledger.
"""

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
import time
from typing import Any, Dict, List, Optional, Set, Tuple
import uuid

from app.config.settings import settings
from app.core.models.performance import (
    ConfidenceLevel,
    EffortStatistics,
    PerformanceAnalysisRun,
    PerformanceEvidence,
    PerformanceSignal,
    ResourcePerformanceProfile,
    ResourceRole,
    RiskLevel,
    RoleCategory,
    SignalType,
    TaskComplexity,
    TaskDeliveryForecast,
    TeamPerformanceSummary,
)
from app.core.performance.blockers import BlockerAnalyzer
from app.core.performance.capacity import CapacityCalculator
from app.core.performance.complexity import TaskComplexityCalculator
from app.core.performance.forecaster import DueDateForecaster
from app.core.performance.pace import HistoricalPaceAnalyzer, calculate_percentiles
from app.core.performance.queue import CurrentQueueAnalyzer, COMPLETED_STATUSES
from app.core.performance.roles import (
    get_employee_designation_and_category,
    resolve_resource_role,
    resolve_canonical_account_id,
    get_account_aliases,
)
from app.core.performance.signals import PerformanceSignalGenerator
from app.database.connection import db_manager, DatabaseManager
from app.database.repositories import (
    AuditRepository,
    EmployeeRoleRepository,
    JiraIssueStateRepository,
    JiraWorklogRepository,
    PerformanceRepository,
)
from app.utils.logger import logger
from app.utils.time import format_iso, parse_iso_datetime, utc_now, utc_now_iso


class PerformanceAnalysisEngine:
    """Core engine for running deterministic Phase A performance data foundation analyses."""

    def __init__(
        self,
        manager: Optional[DatabaseManager] = None,
        perf_repo: Optional[PerformanceRepository] = None,
        issue_repo: Optional[JiraIssueStateRepository] = None,
        worklog_repo: Optional[JiraWorklogRepository] = None,
        audit_repo: Optional[AuditRepository] = None,
        role_repo: Optional[EmployeeRoleRepository] = None,
    ):
        self.mgr = manager or db_manager
        self.perf_repo = perf_repo or PerformanceRepository(self.mgr)
        self.issue_repo = issue_repo or JiraIssueStateRepository(self.mgr)
        self.worklog_repo = worklog_repo or JiraWorklogRepository(self.mgr)
        self.audit_repo = audit_repo or AuditRepository(self.mgr)
        self.role_repo = role_repo or EmployeeRoleRepository(self.mgr)

    def _get_actual_available_history_days(self, now_dt: datetime) -> int:
        """Determine actual days of historical data available in local SQLite projection."""
        earliest_dates = []
        with self.mgr.session() as conn:
            c1 = conn.execute("SELECT MIN(started_at) FROM jira_worklogs WHERE started_at IS NOT NULL AND trim(started_at) != ''")
            r1 = c1.fetchone()
            if r1 and r1[0]:
                earliest_dates.append(r1[0])

            c2 = conn.execute("SELECT MIN(updated_at) FROM jira_issue_state WHERE updated_at IS NOT NULL AND trim(updated_at) != ''")
            r2 = c2.fetchone()
            if r2 and r2[0]:
                earliest_dates.append(r2[0])

        if not earliest_dates:
            return 0

        earliest_dt = now_dt
        for d_str in earliest_dates:
            try:
                dt = parse_iso_datetime(d_str)
                if dt < earliest_dt:
                    earliest_dt = dt
            except Exception:
                pass

        diff_days = (now_dt - earliest_dt).days
        return max(1, diff_days)

    def run_analysis(
        self,
        team_group: Optional[str] = None,
        history_days: Optional[int] = None,
        as_of_date: Optional[str] = None,
    ) -> PerformanceAnalysisRun:
        """Execute a full performance analysis run across all relevant resources."""
        start_time_mono = time.monotonic()
        now_dt = parse_iso_datetime(as_of_date) if as_of_date else utc_now()
        now_iso = format_iso(now_dt)
        hist_days = history_days or settings.PERFORMANCE_HISTORY_DAYS

        window_end_dt = now_dt
        window_start_dt = window_end_dt - timedelta(days=hist_days)
        window_start_iso = format_iso(window_start_dt)
        window_end_iso = format_iso(window_end_dt)

        actual_avail_days = self._get_actual_available_history_days(now_dt)

        run_id = f"run_{now_dt.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        logger.info(f"Starting performance analysis run (run_id={run_id}, team_group={team_group}, requested_history_days={hist_days}, actual_available_history_days={actual_avail_days})")

        run_meta = {
            "analysis_run_id": run_id,
            "calculated_at": now_iso,
            "analysis_window_start": window_start_iso,
            "analysis_window_end": window_end_iso,
            "requested_history_days": hist_days,
            "actual_available_history_days": actual_avail_days,
            "algorithm_version": "1.0.0",
            "team_group": team_group,
            "resources_analyzed": 0,
            "tasks_analyzed": 0,
            "unresolved_employees_count": 0,
            "duration_ms": 0,
            "status": "IN_PROGRESS",
            "error_message": None,
        }
        self.perf_repo.record_analysis_run(run_meta)

        try:
            # 1. Discover target resources (canonical exclusions applied)
            resources_map = self._discover_resources(team_group=team_group, start_iso=window_start_iso, end_iso=window_end_iso)
            logger.info(f"Discovered {len(resources_map)} non-excluded resources for performance analysis")

            # Check unresolved employees
            unresolved = self.role_repo.get_unresolved_employees(list(resources_map.values()))
            unresolved_count = len(unresolved)

            # 2. Build team-wide benchmark statistics for fallbacks (canonical exclusions applied)
            team_benchmark_stats = self._build_team_benchmarks(
                team_group=team_group,
                start_iso=window_start_iso,
                end_iso=window_end_iso,
            )

            profiles_created = 0
            tasks_analyzed_total = 0

            # 3. Analyze each non-excluded resource
            for account_id, res_info in resources_map.items():
                display_name = res_info.get("display_name") or account_id
                res_team = res_info.get("team_group") or team_group

                profile = self.analyze_resource(
                    account_id=account_id,
                    display_name=display_name,
                    team_group=res_team,
                    history_days=hist_days,
                    analysis_run_id=run_id,
                    as_of_date=now_iso,
                    team_benchmark_stats=team_benchmark_stats,
                    actual_available_history_days=actual_avail_days,
                )
                profiles_created += 1
                tasks_analyzed_total += (
                    profile.history.get("completed_tasks", 0) + profile.current_queue.get("task_count", 0)
                )

            duration_ms = int((time.monotonic() - start_time_mono) * 1000)

            final_run = PerformanceAnalysisRun(
                analysis_run_id=run_id,
                calculated_at=now_iso,
                analysis_window_start=window_start_iso,
                analysis_window_end=window_end_iso,
                requested_history_days=hist_days,
                actual_available_history_days=actual_avail_days,
                algorithm_version="1.0.0",
                team_group=team_group,
                resources_analyzed=profiles_created,
                tasks_analyzed=tasks_analyzed_total,
                unresolved_employees_count=unresolved_count,
                duration_ms=duration_ms,
                status="COMPLETED",
                error_message=None,
            )
            self.perf_repo.record_analysis_run(final_run.model_dump())

            self.audit_repo.insert(
                actor="PerformanceAnalysisEngine",
                action="PERFORMANCE_ANALYSIS_RUN",
                target=team_group or "ALL",
                result="SUCCESS",
                details={
                    "analysis_run_id": run_id,
                    "resources_analyzed": profiles_created,
                    "tasks_analyzed": tasks_analyzed_total,
                    "unresolved_employees": unresolved_count,
                    "duration_ms": duration_ms,
                },
            )
            logger.info(f"Performance analysis run completed successfully (run_id={run_id}, duration_ms={duration_ms})")
            return final_run

        except Exception as e:
            duration_ms = int((time.monotonic() - start_time_mono) * 1000)
            err_msg = str(e)
            logger.error(f"Performance analysis run failed (run_id={run_id}): {err_msg}", exc_info=True)

            failed_run = PerformanceAnalysisRun(
                analysis_run_id=run_id,
                calculated_at=now_iso,
                analysis_window_start=window_start_iso,
                analysis_window_end=window_end_iso,
                requested_history_days=hist_days,
                actual_available_history_days=actual_avail_days,
                algorithm_version="1.0.0",
                team_group=team_group,
                resources_analyzed=0,
                tasks_analyzed=0,
                unresolved_employees_count=0,
                duration_ms=duration_ms,
                status="FAILED",
                error_message=err_msg,
            )
            self.perf_repo.record_analysis_run(failed_run.model_dump())
            return failed_run

    def analyze_resource(
        self,
        account_id: str,
        display_name: str,
        team_group: Optional[str] = None,
        history_days: Optional[int] = None,
        analysis_run_id: Optional[str] = None,
        as_of_date: Optional[str] = None,
        team_benchmark_stats: Optional[Dict[str, EffortStatistics]] = None,
        actual_available_history_days: Optional[int] = None,
    ) -> ResourcePerformanceProfile:
        """Generate a complete, deterministic performance profile for an individual resource."""
        now_dt = parse_iso_datetime(as_of_date) if as_of_date else utc_now()
        now_iso = format_iso(now_dt)
        hist_days = history_days or settings.PERFORMANCE_HISTORY_DAYS
        run_id = analysis_run_id or f"run_{now_dt.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        actual_avail_days = actual_available_history_days or self._get_actual_available_history_days(now_dt)

        window_end_dt = now_dt
        window_start_dt = window_end_dt - timedelta(days=hist_days)
        window_start_iso = format_iso(window_start_dt)
        window_end_iso = format_iso(window_end_dt)

        # 1. Resolve Designation and Role Category from Authoritative SQLite table
        designation, role_category, is_resolved = get_employee_designation_and_category(
            account_id=account_id,
            display_name=display_name,
            role_repo=self.role_repo,
        )
        legacy_role = resolve_resource_role(
            account_id=account_id,
            display_name=display_name,
            role_repo=self.role_repo,
        )

        # 2. Query Worklogs in Window for this resource
        worklogs = self._get_resource_worklogs(
            account_id=account_id,
            display_name=display_name,
            start_date=window_start_iso,
            end_date=window_end_iso,
        )

        total_logged_seconds = sum(w.get("time_spent_seconds", 0) for w in worklogs)
        active_dates = {w.get("started_at")[:10] for w in worklogs if w.get("started_at")}
        active_working_days = len(active_dates)

        # Daily hours distribution
        daily_hours_map: Dict[str, float] = defaultdict(float)
        for w in worklogs:
            d_str = w.get("started_at")[:10]
            daily_hours_map[d_str] += w.get("time_spent_seconds", 0) / 3600.0
        daily_hours_list = list(daily_hours_map.values())
        mean_daily_h, med_daily_h, _, _, min_daily_h, max_daily_h = calculate_percentiles(daily_hours_list)

        # Compute Rolling Windows (30d, 90d, 180d, 365d)
        rolling_windows: Dict[str, Dict[str, Any]] = {}
        for w_days in [30, 90, 180, 365]:
            w_start_dt = window_end_dt - timedelta(days=w_days)
            w_start_iso_sub = format_iso(w_start_dt)
            sub_worklogs = [w for w in worklogs if w.get("started_at") and w.get("started_at") >= w_start_iso_sub[:10]]
            sub_logged_sec = sum(w.get("time_spent_seconds", 0) for w in sub_worklogs)
            sub_active_days = len({w.get("started_at")[:10] for w in sub_worklogs if w.get("started_at")})
            rolling_windows[f"{w_days}d"] = {
                "window_days": w_days,
                "active_working_days": sub_active_days,
                "total_logged_hours": round(sub_logged_sec / 3600.0, 2),
                "average_logged_hours_per_active_day": round((sub_logged_sec / 3600.0) / max(1, sub_active_days), 2) if sub_active_days > 0 else 0.0,
            }

        # 3. Query Historical Completed Tasks for this resource
        completed_issues = self._get_resource_completed_issues(
            account_id=account_id,
            display_name=display_name,
            start_date=window_start_iso,
            end_date=window_end_iso,
        )

        task_efforts: List[Dict[str, Any]] = []
        task_classifications: List[Dict[str, Any]] = []
        delivery_records: List[Dict[str, Any]] = []
        estimation_variances: List[float] = []
        reopened_count = 0
        total_blocked_seconds = 0
        blocker_count = 0

        # Type, Priority, Complexity distributions
        dist_issue_type: Dict[str, int] = defaultdict(int)
        dist_priority: Dict[str, int] = defaultdict(int)
        dist_complexity: Dict[str, int] = defaultdict(int)
        dist_project: Dict[str, int] = defaultdict(int)

        for issue in completed_issues:
            tkey = issue.get("jira_issue_key")
            raw_ref = issue.get("raw_reference") or {}
            fields = raw_ref.get("fields", {}) if isinstance(raw_ref, dict) else {}

            itype = issue.get("issue_type") or fields.get("issuetype", {}).get("name") or "Task"
            priority = issue.get("priority") or fields.get("priority", {}).get("name") or "Medium"
            project_key = issue.get("project_key") or (tkey.split("-")[0] if tkey and "-" in tkey else "PROJ")
            due_date = issue.get("due_date") or fields.get("duedate")
            summary = issue.get("summary") or fields.get("summary") or "Untitled"

            components = [c.get("name") for c in fields.get("components", []) if isinstance(c, dict)]
            labels = fields.get("labels", [])
            subtasks = fields.get("subtasks", [])
            orig_est_secs = fields.get("timeoriginalestimate")

            # Determine task complexity using characteristics
            complexity = TaskComplexityCalculator.calculate_complexity(
                issue_type=itype,
                priority=priority,
                project_key=project_key,
                components=components,
                labels=labels,
                subtask_count=len(subtasks) if isinstance(subtasks, list) else 0,
                original_estimate_seconds=orig_est_secs,
                summary=summary,
            )

            # Task logged hours
            task_logged_secs = issue.get("logged_seconds", 0)
            if not task_logged_secs and fields.get("timespent"):
                task_logged_secs = int(fields.get("timespent", 0))
            task_hours = round(task_logged_secs / 3600.0, 2)

            # Analyze blockers conservatively
            blocker_res = BlockerAnalyzer.analyze_issue_blockers(raw_ref)
            if blocker_res["blocker_detected"]:
                blocker_count += blocker_res["blocker_count"]
                total_blocked_seconds += blocker_res["blocked_seconds"]

            # Check reopen occurrences
            task_reopens = self._get_issue_reopen_count(tkey)
            if task_reopens > 0 or issue.get("status", "").lower() in ("reopened", "re-opened"):
                reopened_count += 1

            # Check delivery on-time status
            is_on_time = True
            days_late = 0.0
            if due_date and str(due_date).strip():
                due_clean = str(due_date).strip()[:10]
                completed_at = issue.get("last_activity_at") or issue.get("updated_at") or now_iso
                comp_clean = completed_at[:10]
                if comp_clean > due_clean:
                    is_on_time = False
                    days_late = float(CapacityCalculator.count_working_days_between(due_clean, comp_clean))
                delivery_records.append({
                    "issue_key": tkey,
                    "due_date": due_clean,
                    "completed_date": comp_clean,
                    "is_on_time": is_on_time,
                    "days_late": days_late,
                })

            # Check estimation variance
            if orig_est_secs and orig_est_secs > 0 and task_logged_secs > 0:
                est_h = orig_est_secs / 3600.0
                act_h = task_logged_secs / 3600.0
                var_pct = ((act_h - est_h) / est_h) * 100.0
                estimation_variances.append(var_pct)

            # Update distributions
            dist_issue_type[itype] += 1
            dist_priority[priority] += 1
            dist_complexity[str(complexity.complexity_score)] += 1
            dist_project[project_key] += 1

            task_efforts.append({
                "issue_key": tkey,
                "hours": task_hours,
                "issue_type": itype,
                "complexity_score": complexity.complexity_score,
                "priority": priority,
                "project_key": project_key,
                "role_category": role_category,
            })

            task_classifications.append({
                "analysis_run_id": run_id,
                "issue_key": tkey,
                "issue_type": itype,
                "priority": priority,
                "project_key": project_key,
                "components": components,
                "labels": labels,
                "complexity_score": complexity.complexity_score,
                "complexity_factors": complexity.factors,
                "complexity_confidence": complexity.confidence.value,
                "estimated_seconds": orig_est_secs,
                "actual_logged_seconds": task_logged_secs,
                "status": issue.get("status"),
                "is_completed": True,
                "reopen_count": task_reopens,
                "blocker_detected": blocker_res["blocker_detected"],
                "blocker_hours": blocker_res["blocked_hours"],
                "updated_at": now_iso,
            })

        # 4. Compute Effort Statistics and Pace
        effort_stats = HistoricalPaceAnalyzer.compute_effort_statistics(
            account_id=account_id,
            task_efforts=task_efforts,
            team_benchmark_stats=team_benchmark_stats,
        )

        overall_effort_stat = next((s for s in effort_stats if s.segment_type == "overall"), None)
        avg_task_h = overall_effort_stat.mean_hours if overall_effort_stat else 0.0
        med_task_h = overall_effort_stat.median_hours if overall_effort_stat else 0.0
        p25_task_h = overall_effort_stat.p25_hours if overall_effort_stat else 0.0
        p75_task_h = overall_effort_stat.p75_hours if overall_effort_stat else 0.0

        team_overall_stat = team_benchmark_stats.get("overall:all") if team_benchmark_stats else None
        team_median = team_overall_stat.median_hours if team_overall_stat else 5.0
        pace_factor_info = HistoricalPaceAnalyzer.calculate_pace_factor(
            resource_median_hours=med_task_h,
            team_benchmark_median_hours=team_median,
            sample_count=len(task_efforts),
        )

        # 5. Delivery Performance Metrics
        tasks_due = len(delivery_records)
        tasks_on_time = sum(1 for d in delivery_records if d["is_on_time"])
        tasks_late = tasks_due - tasks_on_time
        on_time_rate = round(tasks_on_time / max(1, tasks_due), 3) if tasks_due > 0 else 1.0
        late_days_list = [d["days_late"] for d in delivery_records if not d["is_on_time"]]
        mean_days_late, med_days_late, _, _, _, _ = calculate_percentiles(late_days_list)

        # 6. Quality & Reopen Metrics
        completed_count = len(completed_issues)
        reopen_rate = round(reopened_count / max(1, completed_count), 3) if completed_count > 0 else 0.0

        # 7. Estimation Metrics
        est_count = len(estimation_variances)
        mean_est_var, med_est_var, _, _, _, _ = calculate_percentiles(estimation_variances)

        # 8. Capacity Metrics
        capacity_info = CapacityCalculator.calculate_capacity_metrics(
            total_logged_seconds=total_logged_seconds,
            active_working_days=active_working_days,
            history_days=hist_days,
        )

        # 9. Active Queue Analysis (Uses 7-tier expected effort hierarchy & Jira remaining separation)
        active_issues = self._get_resource_active_issues(
            account_id=account_id,
            display_name=display_name,
            team_group=team_group,
        )

        queue_result = CurrentQueueAnalyzer.analyze_active_queue(
            account_id=account_id,
            active_issues=active_issues,
            resource_effort_stats=effort_stats,
            team_effort_stats=list(team_benchmark_stats.values()) if team_benchmark_stats else None,
            designation=designation,
            role_category=role_category,
        )

        # 10. Due Date & Completion Forecasting
        forecast_cap_hours = capacity_info["forecast_capacity_hours"]
        forecast_result = DueDateForecaster.forecast_queue_and_tasks(
            task_forecasts=queue_result["task_forecasts"],
            daily_capacity_hours=forecast_cap_hours,
            horizon_working_days=5,
            start_date=now_dt,
        )

        # 11. Generate Signals and Evidence Ledger
        metrics_for_signals = {
            "history_days": hist_days,
            "completed_tasks": completed_count,
            "on_time_rate": on_time_rate,
            "tasks_due": tasks_due,
            "tasks_completed_on_time": tasks_on_time,
            "tasks_completed_late": tasks_late,
            "reopen_rate": reopen_rate,
            "reopened_tasks": reopened_count,
            "blocker_count": blocker_count,
            "average_blocker_hours": round((total_blocked_seconds / 3600.0) / max(1, blocker_count), 2) if blocker_count > 0 else 0.0,
            "median_estimation_variance_percent": med_est_var,
            "estimated_tasks": est_count,
            "capacity_difference_hours": forecast_result["capacity_difference_hours"],
            "total_remaining_hours": forecast_result["total_remaining_hours"],
            "queue_task_count": queue_result["task_count"],
            "forecast_status": forecast_result["forecast_status"],
            "active_working_days": active_working_days,
        }

        signals, evidence_list = PerformanceSignalGenerator.generate_signals_and_evidence(
            account_id=account_id,
            analysis_run_id=run_id,
            metrics=metrics_for_signals,
        )

        # Determine overall confidence
        if completed_count >= 20 and active_working_days >= 30:
            overall_confidence = ConfidenceLevel.HIGH
        elif completed_count >= 5 and active_working_days >= 10:
            overall_confidence = ConfidenceLevel.MEDIUM
        elif completed_count > 0:
            overall_confidence = ConfidenceLevel.LOW
        else:
            overall_confidence = ConfidenceLevel.INSUFFICIENT

        # 12. Assemble ResourcePerformanceProfile
        profile = ResourcePerformanceProfile(
            analysis_run_id=run_id,
            algorithm_version="1.0.0",
            resource={
                "account_id": account_id,
                "display_name": display_name,
                "role": legacy_role.value,
                "designation": designation,
                "role_category": role_category,
                "is_role_resolved": is_resolved,
                "team_group": team_group,
            },
            history={
                "start_date": window_start_iso,
                "end_date": window_end_iso,
                "history_days": hist_days,
                "requested_history_days": hist_days,
                "actual_available_history_days": actual_avail_days,
                "completed_tasks": completed_count,
                "active_working_days": active_working_days,
                "total_logged_seconds": total_logged_seconds,
                "rolling_windows": rolling_windows,
            },
            workload={
                "average_daily_hours": mean_daily_h,
                "median_daily_hours": med_daily_h,
            },
            pace={
                "average_task_hours": avg_task_h,
                "median_task_hours": med_task_h,
                "p25_task_hours": p25_task_h,
                "p75_task_hours": p75_task_h,
                "pace_factor": pace_factor_info["pace_factor"],
                "pace_confidence": pace_factor_info["confidence"].value,
            },
            delivery={
                "tasks_due": tasks_due,
                "tasks_completed": completed_count,
                "tasks_completed_on_time": tasks_on_time,
                "tasks_completed_late": tasks_late,
                "on_time_rate": on_time_rate,
                "average_days_late": mean_days_late,
                "median_days_late": med_days_late,
            },
            estimation={
                "estimated_tasks": est_count,
                "average_actual_hours": avg_task_h,
                "estimation_variance_percent": mean_est_var,
                "median_estimation_variance_percent": med_est_var,
            },
            quality={
                "reopened_tasks": reopened_count,
                "reopen_rate": reopen_rate,
            },
            blockers={
                "blocker_count": blocker_count,
                "blocked_seconds": total_blocked_seconds,
                "average_blocker_hours": round((total_blocked_seconds / 3600.0) / max(1, blocker_count), 2) if blocker_count > 0 else 0.0,
            },
            distributions={
                "issue_type": dict(dist_issue_type),
                "priority": dict(dist_priority),
                "complexity": dict(dist_complexity),
                "project": dict(dist_project),
            },
            capacity={
                "nominal_capacity_hours": capacity_info["nominal_capacity_hours"],
                "observed_logged_capacity_hours": capacity_info["observed_logged_capacity_hours"],
                "forecast_capacity_hours": capacity_info["forecast_capacity_hours"],
                "available_capacity_hours": forecast_result["available_capacity_hours"],
            },
            current_queue={
                "task_count": queue_result["task_count"],
                "expected_base_hours": queue_result["expected_base_hours"],
                "review_buffer_hours": queue_result["review_buffer_hours"],
                "total_expected_hours": queue_result["total_expected_hours"],
                "remaining_hours": forecast_result["total_remaining_hours"],
                "capacity_difference_hours": forecast_result["capacity_difference_hours"],
            },
            forecast={
                "status": forecast_result["forecast_status"].value,
                "projected_completion": forecast_result["projected_queue_completion_date"],
                "reason": forecast_result["forecast_reason"],
                "risk_counts": forecast_result["risk_counts"],
            },
            signals=signals,
            evidence=evidence_list,
            task_forecasts=forecast_result["task_forecasts"],
            effort_statistics=effort_stats,
            confidence_level=overall_confidence,
        )

        # 13. Persist everything to database
        self._persist_profile_and_components(
            profile=profile,
            task_classifications=task_classifications,
            designation=designation,
            role_category=role_category,
            requested_history_days=hist_days,
            actual_available_history_days=actual_avail_days,
        )

        return profile

    def get_team_summary(
        self,
        team_group: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> TeamPerformanceSummary:
        """Aggregate team-level summary from individual resource profiles."""
        profiles_raw = self.perf_repo.list_profiles(team_group=team_group, run_id=run_id)
        tg_name = team_group or (profiles_raw[0].get("team_group") if profiles_raw else "All Teams") or "General"
        run_id_val = run_id or (profiles_raw[0].get("analysis_run_id") if profiles_raw else "N/A")

        total_completed = sum(p.get("completed_tasks", 0) for p in profiles_raw)
        total_active_queue_tasks = sum(p.get("current_queue_task_count", 0) for p in profiles_raw)
        total_remaining_hours = sum(
            p.get("profile", {}).get("current_queue", {}).get("remaining_hours", 0.0) if p.get("profile") else 0.0
            for p in profiles_raw
        )
        total_avail_cap = sum(p.get("available_capacity_hours", 0.0) for p in profiles_raw)
        team_cap_diff = round(total_avail_cap - total_remaining_hours, 2)

        risk_counts = {"GREEN": 0, "YELLOW": 0, "ORANGE": 0, "RED": 0}
        resources_summary = []

        for p in profiles_raw:
            f_status = p.get("forecast_status", "GREEN")
            risk_counts[f_status] = risk_counts.get(f_status, 0) + 1

            resources_summary.append({
                "account_id": p.get("account_id"),
                "display_name": p.get("display_name"),
                "role": p.get("role"),
                "designation": p.get("designation"),
                "role_category": p.get("role_category"),
                "completed_tasks": p.get("completed_tasks", 0),
                "active_queue_tasks": p.get("current_queue_task_count", 0),
                "remaining_hours": p.get("profile", {}).get("current_queue", {}).get("remaining_hours", 0.0) if p.get("profile") else 0.0,
                "forecast_status": f_status,
                "confidence_level": p.get("confidence_level", "LOW"),
            })

        at_risk = risk_counts.get("ORANGE", 0) + risk_counts.get("RED", 0)

        # Query unresolved employees across active team
        active_res = [{"account_id": p.get("account_id"), "display_name": p.get("display_name"), "team_group": p.get("team_group")} for p in profiles_raw]
        unresolved_list = self.role_repo.get_unresolved_employees(active_res)

        # Get history windows from latest profile
        req_hist = profiles_raw[0].get("requested_history_days", 365) if profiles_raw else 365
        act_hist = profiles_raw[0].get("actual_available_history_days", 0) if profiles_raw else 0

        return TeamPerformanceSummary(
            analysis_run_id=run_id_val,
            calculated_at=utc_now_iso(),
            team_group=tg_name,
            requested_history_days=req_hist,
            actual_available_history_days=act_hist,
            algorithm_version="1.0.0",
            resources_count=len(profiles_raw),
            active_resources_count=len([p for p in profiles_raw if p.get("completed_tasks", 0) > 0 or p.get("current_queue_task_count", 0) > 0]),
            total_completed_tasks_history=total_completed,
            total_active_queue_tasks=total_active_queue_tasks,
            total_active_queue_remaining_hours=round(total_remaining_hours, 2),
            total_available_capacity_hours=round(total_avail_cap, 2),
            team_capacity_difference_hours=team_cap_diff,
            resources_at_risk_count=at_risk,
            risk_breakdown=risk_counts,
            resources=resources_summary,
            unresolved_employees=unresolved_list,
        )

    # -------------------------------------------------------------------------
    # Internal Helpers & Queries
    # -------------------------------------------------------------------------

    def _discover_resources(
        self,
        team_group: Optional[str] = None,
        start_iso: Optional[str] = None,
        end_iso: Optional[str] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """Find all active non-excluded resources assigned tasks or logging work within the target scope."""
        resources: Dict[str, Dict[str, Any]] = {}

        with self.mgr.session() as conn:
            # 1. Discover from Jira worklogs
            if team_group:
                cursor = conn.execute(
                    """
                    SELECT DISTINCT author_account_id, author_display_name, team_group
                    FROM jira_worklogs
                    WHERE team_group = ? AND author_account_id IS NOT NULL AND trim(author_account_id) != ''
                    """,
                    (team_group,),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT DISTINCT author_account_id, author_display_name, team_group
                    FROM jira_worklogs
                    WHERE author_account_id IS NOT NULL AND trim(author_account_id) != ''
                    """
                )
            for row in cursor.fetchall():
                raw_acc = row["author_account_id"]
                d_name = row["author_display_name"] or raw_acc
                acc_id = resolve_canonical_account_id(raw_acc, display_name=d_name, role_repo=self.role_repo) or raw_acc

                # Enforce Canonical Global Exclusion
                if settings.is_canonical_excluded(acc_id, d_name):
                    continue

                if acc_id not in resources:
                    resources[acc_id] = {
                        "account_id": acc_id,
                        "display_name": d_name,
                        "team_group": row["team_group"] or team_group,
                    }

            # 2. Discover from Jira issue assignments
            if team_group:
                cursor = conn.execute(
                    """
                    SELECT DISTINCT assignee, team_group, raw_reference
                    FROM jira_issue_state
                    WHERE team_group = ? AND assignee IS NOT NULL AND trim(assignee) != '' AND lower(assignee) != 'unassigned'
                    """,
                    (team_group,),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT DISTINCT assignee, team_group, raw_reference
                    FROM jira_issue_state
                    WHERE assignee IS NOT NULL AND trim(assignee) != '' AND lower(assignee) != 'unassigned'
                    """
                )
            for row in cursor.fetchall():
                r_dict = dict(row)
                assignee_name = r_dict.get("assignee")
                raw_ref = json.loads(r_dict["raw_reference"]) if r_dict.get("raw_reference") else {}
                assignee_field = raw_ref.get("fields", {}).get("assignee") if isinstance(raw_ref, dict) else {}
                raw_acc = (assignee_field.get("accountId") if isinstance(assignee_field, dict) else None) or assignee_name
                d_name = (assignee_field.get("displayName") if isinstance(assignee_field, dict) else None) or assignee_name

                acc_id = resolve_canonical_account_id(raw_acc, display_name=d_name, role_repo=self.role_repo) or raw_acc

                # Enforce Canonical Global Exclusion
                if settings.is_canonical_excluded(acc_id, d_name):
                    continue

                if acc_id and acc_id not in resources:
                    resources[acc_id] = {
                        "account_id": acc_id,
                        "display_name": d_name or acc_id,
                        "team_group": r_dict.get("team_group") or team_group,
                    }

        return resources

    def _build_team_benchmarks(
        self,
        team_group: Optional[str] = None,
        start_iso: Optional[str] = None,
        end_iso: Optional[str] = None,
    ) -> Dict[str, EffortStatistics]:
        """Compute aggregated team-wide effort benchmarks across all completed tasks (excluding excluded resources)."""
        benchmarks: Dict[str, EffortStatistics] = {}
        all_completed = self._get_all_completed_issues(team_group=team_group, start_date=start_iso, end_date=end_iso)

        all_hours: List[float] = []
        by_type: Dict[str, List[float]] = defaultdict(list)
        by_comp: Dict[str, List[float]] = defaultdict(list)
        by_comparable: Dict[str, List[float]] = defaultdict(list)
        by_prio: Dict[str, List[float]] = defaultdict(list)
        by_role: Dict[str, List[float]] = defaultdict(list)

        for issue in all_completed:
            raw_ref = issue.get("raw_reference") or {}
            fields = raw_ref.get("fields", {}) if isinstance(raw_ref, dict) else {}

            itype = str(issue.get("issue_type") or fields.get("issuetype", {}).get("name") or "Task").strip().lower()
            priority = str(issue.get("priority") or fields.get("priority", {}).get("name") or "Medium").strip().lower()
            components = [c.get("name") for c in fields.get("components", []) if isinstance(c, dict)]
            labels = fields.get("labels", [])
            subtasks = fields.get("subtasks", [])
            orig_est_secs = fields.get("timeoriginalestimate")

            complexity = TaskComplexityCalculator.calculate_complexity(
                issue_type=itype,
                priority=priority,
                project_key=issue.get("project_key"),
                components=components,
                labels=labels,
                subtask_count=len(subtasks) if isinstance(subtasks, list) else 0,
                original_estimate_seconds=orig_est_secs,
            )

            logged_secs = issue.get("logged_seconds", 0)
            if not logged_secs and fields.get("timespent"):
                logged_secs = int(fields.get("timespent", 0))

            if logged_secs > 0:
                h = round(logged_secs / 3600.0, 2)
                all_hours.append(h)
                by_type[itype].append(h)
                by_comp[str(complexity.complexity_score)].append(h)
                by_comparable[f"{itype}:{complexity.complexity_score}"].append(h)
                by_prio[priority].append(h)

                # Look up role category for task assignee
                assignee_acc = (fields.get("assignee", {}).get("accountId") if isinstance(fields.get("assignee"), dict) else None) or issue.get("assignee")
                if assignee_acc:
                    _, r_cat, r_res = get_employee_designation_and_category(assignee_acc, role_repo=self.role_repo)
                    if r_res and r_cat:
                        by_role[r_cat.lower()].append(h)

        # Overall Team Benchmark
        mean, med, p25, p75, min_h, max_h = calculate_percentiles(all_hours)
        benchmarks["overall:all"] = EffortStatistics(
            segment_type="overall",
            segment_key="all",
            sample_count=len(all_hours),
            mean_hours=mean,
            median_hours=med,
            p25_hours=p25,
            p75_hours=p75,
            min_hours=min_h,
            max_hours=max_h,
            confidence=ConfidenceLevel.HIGH if len(all_hours) >= 15 else (ConfidenceLevel.MEDIUM if len(all_hours) >= 5 else ConfidenceLevel.LOW),
            is_fallback=False,
        )

        def _add_bench(stype: str, grouped: Dict[str, List[float]]):
            for k, h_list in grouped.items():
                if len(h_list) >= 3:
                    s_mean, s_med, s_p25, s_p75, s_min, s_max = calculate_percentiles(h_list)
                    benchmarks[f"{stype}:{k}"] = EffortStatistics(
                        segment_type=stype,
                        segment_key=k,
                        sample_count=len(h_list),
                        mean_hours=s_mean,
                        median_hours=s_med,
                        p25_hours=s_p25,
                        p75_hours=s_p75,
                        min_hours=s_min,
                        max_hours=s_max,
                        confidence=ConfidenceLevel.HIGH if len(h_list) >= 10 else (ConfidenceLevel.MEDIUM if len(h_list) >= 5 else ConfidenceLevel.LOW),
                        is_fallback=False,
                    )

        _add_bench("comparable", by_comparable)
        _add_bench("issue_type", by_type)
        _add_bench("complexity", by_comp)
        _add_bench("role_category", by_role)
        _add_bench("priority", by_prio)

        return benchmarks

    def _get_resource_worklogs(
        self,
        account_id: str,
        display_name: str,
        start_date: str,
        end_date: str,
    ) -> List[Dict[str, Any]]:
        """Retrieve worklogs authored by this resource within the date range."""
        s_pref = start_date[:10]
        e_pref = end_date[:10]
        aliases = get_account_aliases(account_id)
        placeholders = ", ".join("?" for _ in aliases)

        with self.mgr.session() as conn:
            params = list(aliases) + [display_name, s_pref, e_pref]
            cursor = conn.execute(
                f"""
                SELECT * FROM jira_worklogs
                WHERE (author_account_id IN ({placeholders}) OR author_display_name = ?)
                  AND substr(started_at, 1, 10) >= ?
                  AND substr(started_at, 1, 10) <= ?
                ORDER BY started_at ASC
                """,
                params,
            )
            return [dict(r) for r in cursor.fetchall()]

    def _get_resource_completed_issues(
        self,
        account_id: str,
        display_name: str,
        start_date: str,
        end_date: str,
    ) -> List[Dict[str, Any]]:
        """Retrieve completed issues associated with this resource."""
        completed_clauses = "lower(status) IN ('done', 'completed', 'resolved', 'closed', 'finished')"
        aliases = get_account_aliases(account_id)
        placeholders = ", ".join("?" for _ in aliases)

        with self.mgr.session() as conn:
            params = list(aliases) + [display_name] + list(aliases) + [display_name] + list(aliases) + [display_name]
            cursor = conn.execute(
                f"""
                SELECT s.*, COALESCE(SUM(w.time_spent_seconds), 0) as logged_seconds
                FROM jira_issue_state s
                LEFT JOIN jira_worklogs w ON s.jira_issue_key = w.jira_issue_key AND (w.author_account_id IN ({placeholders}) OR w.author_display_name = ?)
                WHERE {completed_clauses}
                  AND (
                      s.assignee IN ({placeholders}) OR s.assignee = ?
                      OR s.jira_issue_key IN (SELECT jira_issue_key FROM jira_worklogs WHERE author_account_id IN ({placeholders}) OR author_display_name = ?)
                  )
                GROUP BY s.jira_issue_key
                ORDER BY s.last_activity_at DESC
                """,
                params,
            )
            rows = cursor.fetchall()
            out = []
            for r in rows:
                d = dict(r)
                if d.get("raw_reference"):
                    try:
                        d["raw_reference"] = json.loads(d["raw_reference"])
                    except Exception:
                        pass
                out.append(d)
            return out

    def _get_all_completed_issues(
        self,
        team_group: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieve all completed issues across the scope (excluding canonical excluded resources)."""
        completed_clauses = "lower(status) IN ('done', 'completed', 'resolved', 'closed', 'finished')"
        with self.mgr.session() as conn:
            if team_group:
                cursor = conn.execute(
                    f"""
                    SELECT s.*, COALESCE(SUM(w.time_spent_seconds), 0) as logged_seconds
                    FROM jira_issue_state s
                    LEFT JOIN jira_worklogs w ON s.jira_issue_key = w.jira_issue_key
                    WHERE {completed_clauses} AND s.team_group = ?
                    GROUP BY s.jira_issue_key
                    ORDER BY s.last_activity_at DESC
                    """,
                    (team_group,),
                )
            else:
                cursor = conn.execute(
                    f"""
                    SELECT s.*, COALESCE(SUM(w.time_spent_seconds), 0) as logged_seconds
                    FROM jira_issue_state s
                    LEFT JOIN jira_worklogs w ON s.jira_issue_key = w.jira_issue_key
                    WHERE {completed_clauses}
                    GROUP BY s.jira_issue_key
                    ORDER BY s.last_activity_at DESC
                    """
                )
            rows = cursor.fetchall()
            out = []
            for r in rows:
                d = dict(r)
                assignee_val = d.get("assignee")
                can_assignee = resolve_canonical_account_id(assignee_val, display_name=assignee_val, role_repo=self.role_repo) or assignee_val
                if settings.is_canonical_excluded(can_assignee, assignee_val):
                    continue

                if d.get("raw_reference"):
                    try:
                        d["raw_reference"] = json.loads(d["raw_reference"])
                        raw_acc = d["raw_reference"].get("fields", {}).get("assignee", {}).get("accountId")
                        raw_name = d["raw_reference"].get("fields", {}).get("assignee", {}).get("displayName")
                        can_raw = resolve_canonical_account_id(raw_acc, display_name=raw_name, role_repo=self.role_repo) or raw_acc
                        if settings.is_canonical_excluded(can_raw, raw_name):
                            continue
                    except Exception:
                        pass
                out.append(d)
            return out

    def _get_resource_active_issues(
        self,
        account_id: str,
        display_name: str,
        team_group: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieve active assigned issues for a resource."""
        aliases = get_account_aliases(account_id)
        placeholders = ", ".join("?" for _ in aliases)

        with self.mgr.session() as conn:
            params = list(aliases) + [display_name] + list(aliases) + [display_name]
            cursor = conn.execute(
                f"""
                SELECT s.*, COALESCE(SUM(w.time_spent_seconds), 0) as logged_seconds
                FROM jira_issue_state s
                LEFT JOIN jira_worklogs w ON s.jira_issue_key = w.jira_issue_key AND (w.author_account_id IN ({placeholders}) OR w.author_display_name = ?)
                WHERE lower(s.status) NOT IN ('done', 'completed', 'resolved', 'closed', 'finished', 'cancelled', 'rejected')
                  AND (s.assignee IN ({placeholders}) OR s.assignee = ?)
                GROUP BY s.jira_issue_key
                ORDER BY s.due_date ASC, s.last_activity_at DESC
                """,
                params,
            )
            rows = cursor.fetchall()
            out = []
            for r in rows:
                d = dict(r)
                if d.get("raw_reference"):
                    try:
                        d["raw_reference"] = json.loads(d["raw_reference"])
                    except Exception:
                        pass
                out.append(d)
            return out

    def _get_issue_reopen_count(self, issue_key: str) -> int:
        """Count reopening events recorded for this issue."""
        with self.mgr.session() as conn:
            cursor = conn.execute(
                """
                SELECT COUNT(*) FROM events
                WHERE task_id = ? AND event_type IN ('TaskReopened', 'IssueReopened')
                """,
                (issue_key,),
            )
            row = cursor.fetchone()
            return int(row[0]) if row else 0

    def _persist_profile_and_components(
        self,
        profile: ResourcePerformanceProfile,
        task_classifications: List[Dict[str, Any]],
        designation: Optional[str] = None,
        role_category: Optional[str] = None,
        requested_history_days: int = 365,
        actual_available_history_days: int = 0,
    ) -> None:
        """Batch persist the profile, statistics, forecasts, signals, evidence, and classifications."""
        run_id = profile.analysis_run_id
        acc_id = profile.resource["account_id"]
        now_str = utc_now_iso()

        # 1. Profile DB Record
        profile_dict = {
            "analysis_run_id": run_id,
            "account_id": acc_id,
            "display_name": profile.resource.get("display_name") or acc_id,
            "role": profile.resource.get("role", "Unknown"),
            "designation": designation or profile.resource.get("designation"),
            "role_category": role_category or profile.resource.get("role_category"),
            "team_group": profile.resource.get("team_group"),
            "analysis_start": profile.history.get("start_date", ""),
            "analysis_end": profile.history.get("end_date", ""),
            "history_days": profile.history.get("history_days", requested_history_days),
            "requested_history_days": requested_history_days,
            "actual_available_history_days": actual_available_history_days,
            "completed_tasks": profile.history.get("completed_tasks", 0),
            "active_working_days": profile.history.get("active_working_days", 0),
            "total_logged_seconds": profile.history.get("total_logged_seconds", 0),
            "average_logged_hours_per_active_day": profile.workload.get("average_daily_hours", 0.0),
            "median_logged_hours_per_active_day": profile.workload.get("median_daily_hours", 0.0),
            "tasks_due": profile.delivery.get("tasks_due", 0),
            "tasks_completed_on_time": profile.delivery.get("tasks_completed_on_time", 0),
            "tasks_completed_late": profile.delivery.get("tasks_completed_late", 0),
            "on_time_rate": profile.delivery.get("on_time_rate", 0.0),
            "average_days_late": profile.delivery.get("average_days_late", 0.0),
            "median_days_late": profile.delivery.get("median_days_late", 0.0),
            "average_task_hours": profile.pace.get("average_task_hours", 0.0),
            "median_task_hours": profile.pace.get("median_task_hours", 0.0),
            "p25_task_hours": profile.pace.get("p25_task_hours", 0.0),
            "p75_task_hours": profile.pace.get("p75_task_hours", 0.0),
            "estimated_tasks": profile.estimation.get("estimated_tasks", 0),
            "average_estimated_hours": 0.0,
            "average_actual_hours": profile.estimation.get("average_actual_hours", 0.0),
            "estimation_variance_percent": profile.estimation.get("estimation_variance_percent", 0.0),
            "median_estimation_variance_percent": profile.estimation.get("median_estimation_variance_percent", 0.0),
            "reopened_tasks": profile.quality.get("reopened_tasks", 0),
            "reopen_rate": profile.quality.get("reopen_rate", 0.0),
            "blocker_count": profile.blockers.get("blocker_count", 0),
            "blocked_seconds": profile.blockers.get("blocked_seconds", 0),
            "average_blocker_hours": profile.blockers.get("average_blocker_hours", 0.0),
            "nominal_daily_capacity_hours": profile.capacity.get("nominal_capacity_hours", 6.75),
            "observed_daily_capacity_hours": profile.capacity.get("observed_logged_capacity_hours", 6.75),
            "forecast_daily_capacity_hours": profile.capacity.get("forecast_capacity_hours", 6.75),
            "current_queue_task_count": profile.current_queue.get("task_count", 0),
            "current_queue_expected_hours": profile.current_queue.get("expected_base_hours", 0.0),
            "current_queue_review_buffer_hours": profile.current_queue.get("review_buffer_hours", 0.0),
            "current_queue_total_expected_hours": profile.current_queue.get("total_expected_hours", 0.0),
            "available_capacity_hours": profile.capacity.get("available_capacity_hours", 0.0),
            "capacity_difference_hours": profile.current_queue.get("capacity_difference_hours", 0.0),
            "forecast_status": profile.forecast.get("status", "GREEN"),
            "forecast_reason": profile.forecast.get("reason"),
            "projected_queue_completion_date": profile.forecast.get("projected_completion"),
            "confidence_level": profile.confidence_level.value,
            "raw_profile_json": profile.model_dump_json(),
            "created_at": now_str,
            "updated_at": now_str,
        }
        self.perf_repo.upsert_profile(profile_dict)

        # 2. Effort Statistics
        stats_dicts = []
        for s in profile.effort_statistics:
            stats_dicts.append({
                "analysis_run_id": run_id,
                "account_id": acc_id,
                "segment_type": s.segment_type,
                "segment_key": s.segment_key,
                "sample_count": s.sample_count,
                "mean_hours": s.mean_hours,
                "median_hours": s.median_hours,
                "p25_hours": s.p25_hours,
                "p75_hours": s.p75_hours,
                "min_hours": s.min_hours,
                "max_hours": s.max_hours,
                "confidence": s.confidence.value,
                "is_fallback": s.is_fallback,
                "updated_at": now_str,
            })
        self.perf_repo.upsert_effort_statistics_batch(stats_dicts)

        # 3. Task Classifications
        self.perf_repo.upsert_task_classifications_batch(task_classifications)

        # 4. Task Forecasts (Includes separate Jira remaining & inferred analytics)
        fcst_dicts = []
        for f in profile.task_forecasts:
            fcst_dicts.append({
                "analysis_run_id": run_id,
                "issue_key": f.issue_key,
                "account_id": acc_id,
                "due_date": f.due_date,
                "jira_remaining_hours": f.jira_remaining_hours,
                "inferred_expected_hours": f.inferred_expected_hours,
                "inferred_remaining_hours": f.inferred_remaining_hours,
                "expected_base_hours": f.expected_base_hours,
                "review_buffer_hours": f.review_buffer_hours,
                "total_expected_hours": f.total_expected_hours,
                "logged_hours": f.logged_hours,
                "remaining_hours": f.remaining_hours,
                "expected_effort_source": f.expected_effort_source,
                "expected_effort_confidence": f.expected_effort_confidence,
                "sample_size": f.sample_size,
                "designation": f.designation or designation,
                "role_category": f.role_category or role_category,
                "projected_completion_date": f.projected_completion_date,
                "slack_hours": f.slack_hours,
                "risk_level": f.risk_level.value,
                "risk_reason": f.risk_reason,
                "updated_at": now_str,
            })
        self.perf_repo.upsert_task_forecasts_batch(fcst_dicts)

        # 5. Signals
        sig_dicts = []
        for s in profile.signals:
            sig_dicts.append({
                "analysis_run_id": run_id,
                "account_id": acc_id,
                "signal_type": s.signal.value,
                "signal_value": s.value,
                "threshold_value": s.threshold,
                "evidence_text": s.evidence,
                "confidence": s.confidence.value,
                "created_at": now_str,
            })
        self.perf_repo.upsert_signals_batch(sig_dicts)

        # 6. Evidence
        evi_dicts = []
        for e in profile.evidence:
            evi_dicts.append({
                "evidence_id": e.evidence_id,
                "analysis_run_id": run_id,
                "account_id": acc_id,
                "issue_key": e.issue_key,
                "evidence_type": e.evidence_type,
                "observed_value": e.observed_value,
                "expected_value": e.expected_value,
                "difference": e.difference,
                "source": e.source,
                "confidence": e.confidence.value,
                "explanation": e.explanation,
                "timestamp": e.timestamp,
                "snapshot_date": e.snapshot_date,
            })
        self.perf_repo.insert_evidence_batch(evi_dicts)
