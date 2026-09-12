"""Historical Intelligence & Evidence Layer Engine for Phase B v1.1.

Strictly deterministic, explainable, and factual.
Synthesizes Phase A analytics, task classifications, benchmarks, baselines, trends,
workload pressure, delivery context, and blocker history into AI-ready profiles
and an immutable evidence ledger.

NO LLM, NO AI models, NO productivity scores, NO employee rankings.
"""

from collections import defaultdict
from datetime import datetime, timezone, timedelta
import math
from typing import Any, Dict, List, Optional, Set, Tuple
import uuid

from app.config.settings import settings
from app.core.intelligence.baselines import PersonalBaselineEngine
from app.core.intelligence.benchmarks import HistoricalEffortBenchmarkEngine
from app.core.intelligence.classifier import TaskNatureClassifier
from app.core.intelligence.delivery import BlockerHistoryAnalyzer, DeliveryContextAnalyzer
from app.core.intelligence.models import (
    AIReadinessStatus,
    BlockerHistoryProfile,
    DataCompletenessRating,
    DataQualityProfile,
    DeliveryContextProfile,
    HistoricalEffortBenchmark,
    HistoricalEvidenceRecord,
    HistoricalIntelligenceProfile,
    HistoricalTrendsProfile,
    PersonalBaselineProfile,
    ReviewReworkProfile,
    TaskMixDistributionItem,
    TaskMixProfile,
    TaskNature,
    WorkloadPressureAssessment,
)
from app.core.intelligence.rework import ReviewReworkAnalyzer
from app.core.intelligence.trends import HistoricalTrendAnalyzer
from app.core.intelligence.workload import WorkloadPressureAnalyzer
from app.core.models.performance import (
    ConfidenceLevel,
    JiraIssueState,
    JiraWorklog,
    ResourceRole,
    RoleCategory,
)
from app.core.performance.capacity import CapacityCalculator
from app.core.performance.complexity import TaskComplexityCalculator
from app.core.performance.forecaster import DueDateForecaster
from app.core.performance.pace import HistoricalPaceAnalyzer
from app.core.performance.queue import COMPLETED_STATUSES, CurrentQueueAnalyzer
from app.core.performance.roles import (
    get_account_aliases,
    get_employee_designation_and_category,
    resolve_canonical_account_id,
    resolve_resource_role,
)
from app.database.connection import DatabaseManager, db_manager
from app.database.repositories import (
    AuditRepository,
    EmployeeRoleRepository,
    HistoricalIntelligenceRepository,
    JiraIssueStateRepository,
    JiraWorklogRepository,
    PerformanceRepository,
)
from app.utils.logger import logger
from app.utils.time import format_iso, parse_iso_datetime, utc_now, utc_now_iso


class HistoricalIntelligenceEngine:
    """Orchestrator for Phase B v1.1 Historical Intelligence and Evidence Layer."""

    def __init__(
        self,
        manager: Optional[DatabaseManager] = None,
        perf_repo: Optional[PerformanceRepository] = None,
        issue_repo: Optional[JiraIssueStateRepository] = None,
        worklog_repo: Optional[JiraWorklogRepository] = None,
        audit_repo: Optional[AuditRepository] = None,
        role_repo: Optional[EmployeeRoleRepository] = None,
        intelligence_repo: Optional[HistoricalIntelligenceRepository] = None,
    ):
        self.mgr = manager or db_manager
        self.perf_repo = perf_repo or PerformanceRepository(self.mgr)
        self.issue_repo = issue_repo or JiraIssueStateRepository(self.mgr)
        self.worklog_repo = worklog_repo or JiraWorklogRepository(self.mgr)
        self.audit_repo = audit_repo or AuditRepository(self.mgr)
        self.role_repo = role_repo or EmployeeRoleRepository(self.mgr)
        self.intelligence_repo = intelligence_repo or HistoricalIntelligenceRepository(self.mgr)

    def _get_dynamic_historical_bounds(self, now_dt: datetime) -> Tuple[int, Optional[str], Optional[str]]:
        """Dynamically compute actual available history range from SQLite data."""
        earliest_dates: List[str] = []
        latest_dates: List[str] = []

        with self.mgr.session() as conn:
            c1 = conn.execute(
                "SELECT MIN(started_at), MAX(started_at) FROM jira_worklogs "
                "WHERE started_at IS NOT NULL AND trim(started_at) != ''"
            )
            r1 = c1.fetchone()
            if r1:
                if r1[0]:
                    earliest_dates.append(r1[0])
                if r1[1]:
                    latest_dates.append(r1[1])

            c2 = conn.execute(
                "SELECT MIN(updated_at), MAX(updated_at) FROM jira_issue_state "
                "WHERE updated_at IS NOT NULL AND trim(updated_at) != ''"
            )
            r2 = c2.fetchone()
            if r2:
                if r2[0]:
                    earliest_dates.append(r2[0])
                if r2[1]:
                    latest_dates.append(r2[1])

        if not earliest_dates:
            return 0, None, None

        earliest_dt = now_dt
        for d_str in earliest_dates:
            try:
                dt = parse_iso_datetime(d_str)
                if dt < earliest_dt:
                    earliest_dt = dt
            except Exception:
                pass

        latest_dt = earliest_dt
        for d_str in latest_dates:
            try:
                dt = parse_iso_datetime(d_str)
                if dt > latest_dt:
                    latest_dt = dt
            except Exception:
                pass

        diff_days = max(0, (now_dt - earliest_dt).days)
        return diff_days, format_iso(earliest_dt), format_iso(latest_dt)

    def run_analysis(
        self,
        run_id: Optional[str] = None,
        history_days: int = 365,
        team_group: Optional[str] = None,
        account_id_filter: Optional[str] = None,
        now: Optional[datetime] = None,
        persist: bool = True,
    ) -> Dict[str, Any]:
        """Execute complete Phase B v1.1 Historical Intelligence analysis."""
        if now is None:
            now_dt = utc_now()
        elif now.tzinfo is None:
            now_dt = now.replace(tzinfo=timezone.utc)
        else:
            now_dt = now

        analysis_run_id = run_id or f"intel_run_{uuid.uuid4().hex[:12]}"
        calc_timestamp = format_iso(now_dt)

        actual_history_days, earliest_date, latest_date = self._get_dynamic_historical_bounds(now_dt)
        effective_history_days = min(history_days, actual_history_days) if actual_history_days > 0 else history_days
        history_start_dt = now_dt - timedelta(days=effective_history_days)

        # 1. Authoritative seed assignments and non-excluded employee discovery
        authoritative_assignments = self.role_repo.list_assignments()
        authoritative_by_account: Dict[str, Dict[str, Any]] = {
            a["account_id"]: a for a in authoritative_assignments
            if a["account_id"] not in settings.CANONICAL_EXCLUDED_ACCOUNT_IDS
        }

        # 2. Load Jira issues and worklogs from local SQLite projection
        with self.mgr.session() as conn:
            c_issues = conn.execute("SELECT * FROM jira_issue_state")
            all_raw_issues = [dict(r) for r in c_issues.fetchall()]
            c_worklogs = conn.execute("SELECT * FROM jira_worklogs")
            all_raw_worklogs = [dict(r) for r in c_worklogs.fetchall()]

        # Aggregate worklog hours by issue key and author
        issue_logged_hours = defaultdict(float)
        issue_author_logged_hours = defaultdict(lambda: defaultdict(float))
        for wrow in all_raw_worklogs:
            k = wrow.get("jira_issue_key")
            auth_raw = wrow.get("author_account_id")
            resolved_author = resolve_canonical_account_id(
                auth_raw,
                display_name=wrow.get("author_display_name"),
                role_repo=self.role_repo,
            )
            whours = float(wrow.get("time_spent_seconds") or 0) / 3600.0
            if k and whours > 0:
                issue_logged_hours[k] += whours
                if resolved_author:
                    issue_author_logged_hours[k][resolved_author] += whours

        # Filter out canonically excluded accounts and normalize legacy aliases
        issues_by_account: Dict[str, List[JiraIssueState]] = defaultdict(list)
        all_classified_issues: List[Dict[str, Any]] = []

        for row in all_raw_issues:
            import json
            raw_ref = json.loads(row["raw_reference"]) if row.get("raw_reference") else {}
            fields = raw_ref.get("fields", {}) if isinstance(raw_ref, dict) else {}
            assignee_field = fields.get("assignee") if isinstance(fields, dict) else {}
            raw_acc = (assignee_field.get("accountId") if isinstance(assignee_field, dict) else None) or row.get("assignee_account_id") or row.get("assignee")
            d_name = (assignee_field.get("displayName") if isinstance(assignee_field, dict) else None) or row.get("assignee_display_name") or row.get("assignee")

            resolved_assignee = resolve_canonical_account_id(
                raw_acc,
                display_name=d_name,
                role_repo=self.role_repo,
            )

            if resolved_assignee in settings.CANONICAL_EXCLUDED_ACCOUNT_IDS:
                continue

            issue_data = dict(row)
            issue_data["issue_key"] = row.get("jira_issue_key") or row.get("issue_key") or "UNKNOWN"
            issue_data["assignee_account_id"] = resolved_assignee
            issue_data["assignee_display_name"] = d_name

            # Assign logged hours from worklogs or issue fields
            spent_h = (
                issue_author_logged_hours[issue_data["issue_key"]].get(resolved_assignee)
                or issue_logged_hours.get(issue_data["issue_key"])
                or (float(fields.get("timespent") or 0) / 3600.0)
                or (float(row.get("time_spent_seconds") or 0) / 3600.0)
                or float(row.get("original_estimate_hours") or 0.0)
            )
            issue_data["hours"] = spent_h
            issue_data["logged_hours"] = spent_h

            # Components & Labels
            raw_comps = fields.get("components", []) if isinstance(fields, dict) else []
            comps = [c.get("name") if isinstance(c, dict) else str(c) for c in raw_comps]
            labels = fields.get("labels", []) if isinstance(fields, dict) else []
            issue_data["components"] = comps
            issue_data["labels"] = labels

            # Issue type
            itype = (fields.get("issuetype", {}).get("name") if isinstance(fields, dict) and isinstance(fields.get("issuetype"), dict) else None) or row.get("issue_type") or "Task"
            issue_data["issue_type"] = itype

            # Derive complexity score if not present
            if not issue_data.get("complexity_score"):
                calc = TaskComplexityCalculator.calculate_complexity(
                    issue_type=issue_data.get("issue_type", "Task"),
                    summary=issue_data.get("summary", ""),
                    components=comps,
                    labels=labels,
                    original_estimate_seconds=int(float(issue_data.get("original_estimate_hours") or 0) * 3600) if issue_data.get("original_estimate_hours") else None,
                )
                issue_data["complexity_score"] = calc.complexity_score
                issue_data["complexity_band"] = f"Band_{calc.complexity_score}"

            # Classify task nature
            classification = TaskNatureClassifier.classify_issue(
                issue_key=issue_data["issue_key"],
                issue_type=itype,
                summary=issue_data.get("summary", ""),
                description=issue_data.get("description", ""),
                components=comps,
                labels=labels,
            )
            issue_data["task_nature"] = classification.task_nature.value
            all_classified_issues.append(issue_data)

            issue_state = JiraIssueState(**issue_data)
            if resolved_assignee:
                issues_by_account[resolved_assignee].append(issue_state)

        # Worklogs filtering & normalization
        worklogs_by_account: Dict[str, List[JiraWorklog]] = defaultdict(list)
        for row in all_raw_worklogs:
            wdata = dict(row)
            author_raw = wdata.get("author_account_id")
            resolved_author = resolve_canonical_account_id(
                author_raw,
                display_name=wdata.get("author_display_name"),
                role_repo=self.role_repo,
            )

            if resolved_author in settings.CANONICAL_EXCLUDED_ACCOUNT_IDS:
                continue

            wdata["author_account_id"] = resolved_author
            worklog = JiraWorklog(**wdata)
            if resolved_author:
                worklogs_by_account[resolved_author].append(worklog)

        # 3. Compute Global / Role / Team Benchmarks across the full dataset
        team_members_by_role: Dict[str, Set[str]] = defaultdict(set)
        for acc_id, a_data in authoritative_by_account.items():
            role_cat = a_data.get("role_category") or "Unknown"
            team_members_by_role[role_cat].add(acc_id)

        # 4. Generate Profiles for Authoritative Employees
        profiles: Dict[str, HistoricalIntelligenceProfile] = {}
        all_evidence_records: List[HistoricalEvidenceRecord] = []

        target_account_ids = (
            [account_id_filter]
            if account_id_filter
            else list(authoritative_by_account.keys())
        )

        for account_id in target_account_ids:
            auth_info = authoritative_by_account.get(account_id)
            if not auth_info:
                continue

            if team_group and auth_info.get("team_group") != team_group:
                continue

            emp_name = auth_info.get("display_name", "Unknown Employee")
            emp_designation = auth_info.get("designation", "Unknown")
            emp_role_cat = auth_info.get("role_category", "Unknown")
            emp_team = auth_info.get("team_group")
            known_aliases = get_account_aliases(account_id)

            emp_issues = issues_by_account.get(account_id, [])
            emp_worklogs = worklogs_by_account.get(account_id, [])

            # --- A. Work Profile & Task Mix ---
            task_mix = self._build_task_mix_profile(emp_issues, emp_worklogs)

            # --- B. Effort Benchmarks (11 segmentations) ---
            effort_benchmarks = HistoricalEffortBenchmarkEngine.compute_benchmarks(
                account_id=account_id,
                all_issues=all_classified_issues,
                role_category=emp_role_cat,
                team_group=emp_team,
            )

            # Aggregate logged hours on active days
            active_days_set = set()
            total_logged_hours = 0.0
            daily_hours_map: Dict[str, float] = defaultdict(float)
            for w in emp_worklogs:
                started_str = getattr(w, "started_at", "")
                if started_str:
                    try:
                        w_dt = parse_iso_datetime(started_str)
                        d_key = w_dt.date().isoformat()
                        active_days_set.add(d_key)
                        hours = float(getattr(w, "time_spent_seconds", 0) or 0) / 3600.0
                        daily_hours_map[d_key] += hours
                        total_logged_hours += hours
                    except Exception:
                        pass

            active_days_count = len(active_days_set)
            avg_logged_per_day = (
                round(total_logged_hours / active_days_count, 2) if active_days_count > 0 else 0.0
            )
            daily_values = sorted(daily_hours_map.values())
            median_logged_per_day = (
                round(daily_values[len(daily_values) // 2], 2) if daily_values else 0.0
            )

            # --- C. Capacity & Queue ---
            active_queue_issues = [
                i for i in emp_issues
                if (getattr(i, "status_category", "") or getattr(i, "status", "")).lower() not in COMPLETED_STATUSES
            ]
            inferred_remaining_workload = 0.0
            for i in active_queue_issues:
                est = getattr(i, "original_estimate_hours", None)
                if est is not None and est > 0:
                    time_spent = float(getattr(i, "time_spent_seconds", 0) or 0) / 3600.0
                    inferred_remaining_workload += max(0.5, est - time_spent)
                else:
                    score = getattr(i, "complexity_score", 3) or 3
                    # Conservative baseline expectation by complexity
                    complexity_defaults = {1: 2.0, 2: 4.0, 3: 8.0, 4: 16.0, 5: 32.0}
                    inferred_remaining_workload += complexity_defaults.get(score, 8.0)

            # 14-day standard forecast horizon
            nominal_daily_cap = 6.75
            forecast_capacity = round(nominal_daily_cap * 10, 2)  # ~10 working days in 14d
            cap_difference = round(forecast_capacity - inferred_remaining_workload, 2)

            # --- D. Workload Pressure Assessment ---
            blocker_count = sum(1 for i in active_queue_issues if (getattr(i, "blocker_hours", 0) or 0) > 0)
            workload_pressure = WorkloadPressureAnalyzer.assess_workload_pressure(
                account_id=account_id,
                active_issues=active_queue_issues,
                inferred_remaining_workload_hours=inferred_remaining_workload,
                forecast_capacity_hours=forecast_capacity,
                active_blockers_count=blocker_count,
                now=now_dt,
            )

            # --- E. Personal Baselines ---
            personal_baseline = PersonalBaselineEngine.compute_personal_baseline(
                account_id=account_id,
                issues=emp_issues,
                worklogs=emp_worklogs,
                current_active_queue_count=len(active_queue_issues),
                current_inferred_workload_hours=inferred_remaining_workload,
            )

            # --- F. Rolling Trends ---
            trends = HistoricalTrendAnalyzer.analyze_trends(
                account_id=account_id,
                issues=emp_issues,
                worklogs=emp_worklogs,
                now=now_dt,
            )

            # --- G. Delivery Context & Blocker History ---
            delivery_context = DeliveryContextAnalyzer.analyze_delivery_context(
                account_id=account_id,
                issues=emp_issues,
                now=now_dt,
            )
            blocker_history = BlockerHistoryAnalyzer.analyze_blocker_history(
                account_id=account_id,
                issues=emp_issues,
                total_logged_hours=total_logged_hours,
            )
            review_rework = ReviewReworkAnalyzer.analyze_employee_rework(
                account_id=account_id,
                issues=emp_issues,
            )

            # --- H. Data Quality & Investigation Signals ---
            data_quality = self._assess_employee_data_quality(
                account_id=account_id,
                auth_info=auth_info,
                issues=emp_issues,
                worklogs=emp_worklogs,
                actual_history_days=actual_history_days,
            )

            investigation_signals: List[str] = []
            if delivery_context.currently_overdue > 2:
                investigation_signals.append(
                    f"{delivery_context.currently_overdue} active tasks currently overdue; "
                    f"{delivery_context.correlated_blocker_count} correlated with historical blockers."
                )
            if workload_pressure.pressure_level.value in ["HIGH", "ELEVATED"]:
                investigation_signals.append(
                    f"{workload_pressure.pressure_level.value} workload pressure: {workload_pressure.explanation}"
                )
            if review_rework.reopened_tasks_count > 3:
                investigation_signals.append(
                    f"{review_rework.reopened_tasks_count} tasks reopened across history "
                    f"({review_rework.rework_reasons_breakdown})."
                )

            # --- I. Build Evidence Records ---
            emp_evidence = self._generate_evidence_ledger(
                analysis_run_id=analysis_run_id,
                account_id=account_id,
                calc_timestamp=calc_timestamp,
                task_mix=task_mix,
                workload_pressure=workload_pressure,
                personal_baseline=personal_baseline,
                trends=trends,
                delivery_context=delivery_context,
                blocker_history=blocker_history,
                review_rework=review_rework,
                issues=emp_issues,
            )
            all_evidence_records.extend(emp_evidence)

            # --- J. Assemble Complete Historical Intelligence Profile ---
            profile = HistoricalIntelligenceProfile(
                analysis_run_id=analysis_run_id,
                calculated_at=calc_timestamp,
                algorithm_version="1.1.0",
                account_id=account_id,
                display_name=emp_name,
                designation=emp_designation,
                role_category=emp_role_cat,
                team_group=emp_team,
                identity_resolution_status="CANONICAL_RESOLVED",
                known_aliases=known_aliases,
                requested_history_days=history_days,
                actual_available_history_days=actual_history_days,
                earliest_record_date=earliest_date,
                latest_record_date=latest_date,
                task_mix=task_mix,
                total_logged_hours=round(total_logged_hours, 2),
                active_working_days=active_days_count,
                average_logged_hours_per_active_day=avg_logged_per_day,
                median_logged_hours_per_active_day=median_logged_per_day,
                effort_benchmarks=effort_benchmarks,
                nominal_daily_capacity_hours=nominal_daily_cap,
                observed_daily_capacity_hours=avg_logged_per_day or nominal_daily_cap,
                forecast_daily_capacity_hours=nominal_daily_cap,
                current_active_tasks_count=len(active_queue_issues),
                current_queue_inferred_remaining_hours=round(inferred_remaining_workload, 2),
                capacity_difference_hours=cap_difference,
                workload_pressure=workload_pressure,
                personal_baseline=personal_baseline,
                trends=trends,
                delivery_context=delivery_context,
                blocker_history=blocker_history,
                review_rework=review_rework,
                data_quality=data_quality,
                investigation_signals=investigation_signals,
                evidence_records_count=len(emp_evidence),
                evidence_sample=emp_evidence[:10],
            )
            profiles[account_id] = profile

        # Overall AI Readiness Evaluation
        ai_readiness = self._evaluate_overall_readiness(profiles, actual_history_days)

        # Persistence
        if persist and self.intelligence_repo:
            self._persist_intelligence_run(
                analysis_run_id=analysis_run_id,
                calculated_at=calc_timestamp,
                profiles=profiles,
                evidence_records=all_evidence_records,
                ai_readiness=ai_readiness,
                history_days=history_days,
                actual_history_days=actual_history_days,
            )

        return {
            "analysis_run_id": analysis_run_id,
            "calculated_at": calc_timestamp,
            "requested_history_days": history_days,
            "actual_available_history_days": actual_history_days,
            "earliest_record_date": earliest_date,
            "latest_record_date": latest_date,
            "authoritative_count": len(profiles),
            "excluded_count": len(settings.CANONICAL_EXCLUDED_ACCOUNT_IDS),
            "evidence_count": len(all_evidence_records),
            "ai_readiness_status": ai_readiness.value,
            "profiles": profiles,
        }

    def _build_task_mix_profile(
        self,
        issues: List[JiraIssueState],
        worklogs: List[JiraWorklog],
    ) -> TaskMixProfile:
        """Deterministically calculate task mix distribution across dimensions."""
        total_tasks = len(issues)
        subtask_count = sum(1 for i in issues if (getattr(i, "issue_type", "") or "").lower() in ["sub-task", "subtask"])

        type_counts: Dict[str, int] = defaultdict(int)
        nature_counts: Dict[str, int] = defaultdict(int)
        complexity_counts: Dict[str, int] = defaultdict(int)
        priority_counts: Dict[str, int] = defaultdict(int)
        project_counts: Dict[str, int] = defaultdict(int)

        for i in issues:
            itype = getattr(i, "issue_type", "Unknown") or "Unknown"
            tnature = getattr(i, "task_nature", TaskNature.UNKNOWN.value) or TaskNature.UNKNOWN.value
            complexity = str(getattr(i, "complexity_score", 3) or 3)
            priority = getattr(i, "priority", "Medium") or "Medium"
            project = getattr(i, "project_key", "UNKNOWN") or "UNKNOWN"

            type_counts[itype] += 1
            nature_counts[tnature] += 1
            complexity_counts[complexity] += 1
            priority_counts[priority] += 1
            project_counts[project] += 1

        def _to_dist(counts: Dict[str, int]) -> List[TaskMixDistributionItem]:
            items = []
            for k, cnt in sorted(counts.items(), key=lambda x: x[1], reverse=True):
                pct = round((cnt / total_tasks * 100.0), 2) if total_tasks > 0 else 0.0
                items.append(TaskMixDistributionItem(key=k, count=cnt, percentage=pct))
            return items

        nature_dist = _to_dist(nature_counts)
        type_dist = _to_dist(type_counts)

        primary_nature = TaskNature(nature_dist[0].key) if nature_dist and nature_dist[0].key in TaskNature.__members__ else TaskNature.UNKNOWN
        primary_type = type_dist[0].key if type_dist else "Unknown"

        return TaskMixProfile(
            total_tasks=total_tasks,
            issue_type_distribution=type_dist,
            task_nature_distribution=nature_dist,
            complexity_distribution=_to_dist(complexity_counts),
            priority_distribution=_to_dist(priority_counts),
            project_distribution=_to_dist(project_counts),
            subtask_count=subtask_count,
            primary_task_nature=primary_nature,
            primary_issue_type=primary_type,
        )

    def _assess_employee_data_quality(
        self,
        account_id: str,
        auth_info: Dict[str, Any],
        issues: List[JiraIssueState],
        worklogs: List[JiraWorklog],
        actual_history_days: int,
    ) -> DataQualityProfile:
        """Evaluate evidence completeness ratings for an individual employee profile."""
        limitations: List[str] = []

        identity_rating = (
            DataCompletenessRating.GOOD
            if auth_info.get("account_id") and auth_info.get("display_name")
            else DataCompletenessRating.PARTIAL
        )

        history_rating = (
            DataCompletenessRating.GOOD
            if actual_history_days >= 180
            else (DataCompletenessRating.PARTIAL if actual_history_days >= 30 else DataCompletenessRating.INSUFFICIENT)
        )

        worklog_rating = (
            DataCompletenessRating.GOOD
            if len(worklogs) >= 10
            else (DataCompletenessRating.PARTIAL if len(worklogs) >= 1 else DataCompletenessRating.INSUFFICIENT)
        )
        if len(worklogs) < 10:
            limitations.append(f"Low worklog sample size: {len(worklogs)} logs recorded.")

        unclassified_count = sum(
            1 for i in issues if getattr(i, "task_nature", TaskNature.UNKNOWN.value) == TaskNature.UNKNOWN.value
        )
        task_class_rating = (
            DataCompletenessRating.GOOD
            if (len(issues) == 0 or (unclassified_count / len(issues)) < 0.20)
            else DataCompletenessRating.PARTIAL
        )
        if unclassified_count > 0:
            limitations.append(f"{unclassified_count} issues classified with fallback UNKNOWN task nature.")

        estimated_count = sum(
            1 for i in issues if getattr(i, "original_estimate_hours", None) is not None
        )
        expected_effort_rating = (
            DataCompletenessRating.GOOD
            if (len(issues) == 0 or (estimated_count / len(issues)) >= 0.50)
            else DataCompletenessRating.PARTIAL
        )

        role_rating = (
            DataCompletenessRating.GOOD
            if auth_info.get("role_category") and auth_info.get("designation")
            else DataCompletenessRating.PARTIAL
        )

        return DataQualityProfile(
            identity_completeness=identity_rating,
            historical_completeness=history_rating,
            worklog_completeness=worklog_rating,
            task_classification_completeness=task_class_rating,
            expected_effort_completeness=expected_effort_rating,
            role_completeness=role_rating,
            confidence_limitations=limitations,
        )

    def _generate_evidence_ledger(
        self,
        analysis_run_id: str,
        account_id: str,
        calc_timestamp: str,
        task_mix: TaskMixProfile,
        workload_pressure: WorkloadPressureAssessment,
        personal_baseline: PersonalBaselineProfile,
        trends: HistoricalTrendsProfile,
        delivery_context: DeliveryContextProfile,
        blocker_history: BlockerHistoryProfile,
        review_rework: ReviewReworkProfile,
        issues: List[JiraIssueState],
    ) -> List[HistoricalEvidenceRecord]:
        """Generate structured, immutable evidence records traceable to sources."""
        records: List[HistoricalEvidenceRecord] = []

        # 1. Workload Pressure Evidence
        records.append(
            HistoricalEvidenceRecord(
                evidence_id=f"ev_wp_{uuid.uuid4().hex[:10]}",
                analysis_run_id=analysis_run_id,
                account_id=account_id,
                evidence_type="WORKLOAD_PRESSURE",
                metric="workload_pressure_level",
                value=float(workload_pressure.active_tasks_count),
                comparison_baseline=workload_pressure.pressure_level.value,
                source="jira_issue_state,workload_pressure_analyzer",
                confidence=ConfidenceLevel.HIGH,
                timestamp=calc_timestamp,
                explanation=workload_pressure.explanation,
            )
        )

        # 2. Personal Baseline Evidences
        baselines = [
            personal_baseline.active_queue_baseline,
            personal_baseline.logged_hours_baseline,
            personal_baseline.complexity_baseline,
            personal_baseline.expected_effort_baseline,
            personal_baseline.reopen_rate_baseline,
            personal_baseline.overdue_rate_baseline,
        ]
        for b in baselines:
            records.append(
                HistoricalEvidenceRecord(
                    evidence_id=f"ev_base_{uuid.uuid4().hex[:10]}",
                    analysis_run_id=analysis_run_id,
                    account_id=account_id,
                    evidence_type="BASELINE_COMPARISON",
                    metric=b.metric_name,
                    value=b.current_value,
                    comparison_baseline=f"{b.typical_historical_value:.2f} ({b.comparison_state.value})",
                    source="jira_issue_state,jira_worklogs,personal_baseline_engine",
                    confidence=ConfidenceLevel.HIGH if personal_baseline.has_sufficient_history else ConfidenceLevel.LOW,
                    timestamp=calc_timestamp,
                    explanation=b.explanation,
                )
            )

        # 3. Rolling Trend Evidences
        rolling_metrics = [
            trends.logged_hours_trend,
            trends.completed_tasks_trend,
            trends.active_queue_trend,
            trends.expected_workload_trend,
            trends.complexity_trend,
            trends.reopen_rate_trend,
            trends.overdue_rate_trend,
            trends.blocker_hours_trend,
            trends.capacity_pressure_trend,
        ]
        for t in rolling_metrics:
            records.append(
                HistoricalEvidenceRecord(
                    evidence_id=f"ev_trend_{uuid.uuid4().hex[:10]}",
                    analysis_run_id=analysis_run_id,
                    account_id=account_id,
                    evidence_type="TREND",
                    metric=t.metric_name,
                    value=t.value_30d,
                    comparison_baseline=f"30d:{t.value_30d} vs 365d:{t.value_365d} ({t.direction.value})",
                    source="jira_worklogs,jira_issue_state,historical_trend_analyzer",
                    confidence=ConfidenceLevel.HIGH,
                    timestamp=calc_timestamp,
                    explanation=t.explanation,
                )
            )

        # 4. Delivery & Rework Evidences
        records.append(
            HistoricalEvidenceRecord(
                evidence_id=f"ev_del_{uuid.uuid4().hex[:10]}",
                analysis_run_id=analysis_run_id,
                account_id=account_id,
                evidence_type="DELIVERY_CONTEXT",
                metric="due_date_delivery",
                value=float(delivery_context.completed_on_due_date + delivery_context.completed_before_due_date),
                comparison_baseline=f"Coverage: {delivery_context.due_date_coverage_percent:.1f}%",
                source="jira_issue_state,delivery_context_analyzer",
                confidence=ConfidenceLevel.HIGH,
                timestamp=calc_timestamp,
                explanation=(
                    f"{delivery_context.completed_before_due_date} completed early, "
                    f"{delivery_context.completed_on_due_date} on-date, "
                    f"{delivery_context.completed_after_due_date} late. "
                    f"{delivery_context.currently_overdue} currently overdue."
                ),
            )
        )

        for event in review_rework.rework_events[:15]:
            records.append(
                HistoricalEvidenceRecord(
                    evidence_id=f"ev_rw_{uuid.uuid4().hex[:10]}",
                    analysis_run_id=analysis_run_id,
                    account_id=account_id,
                    issue_key=event.issue_key,
                    evidence_type="REOPEN_REWORK",
                    metric="rework_reason",
                    value=1.0,
                    comparison_baseline=event.reason.value,
                    source="jira_issue_state,review_rework_analyzer",
                    confidence=ConfidenceLevel.MEDIUM if event.reason.value != "UNKNOWN" else ConfidenceLevel.LOW,
                    timestamp=event.timestamp or calc_timestamp,
                    explanation=event.evidence_text,
                )
            )

        return records

    def _evaluate_overall_readiness(
        self,
        profiles: Dict[str, HistoricalIntelligenceProfile],
        actual_history_days: int,
    ) -> AIReadinessStatus:
        """Assess whether the historical intelligence dataset is ready for future AI consumption."""
        if not profiles:
            return AIReadinessStatus.NOT_READY_FOR_AI_LAYER

        good_profiles_count = sum(
            1 for p in profiles.values()
            if p.data_quality.identity_completeness == DataCompletenessRating.GOOD
            and p.data_quality.role_completeness == DataCompletenessRating.GOOD
        )

        if actual_history_days >= 180 and good_profiles_count >= (len(profiles) * 0.85):
            return AIReadinessStatus.READY_FOR_AI_LAYER
        elif actual_history_days >= 30:
            return AIReadinessStatus.READY_WITH_LIMITATIONS
        else:
            return AIReadinessStatus.NOT_READY_FOR_AI_LAYER

    def _persist_intelligence_run(
        self,
        analysis_run_id: str,
        calculated_at: str,
        profiles: Dict[str, HistoricalIntelligenceProfile],
        evidence_records: List[HistoricalEvidenceRecord],
        ai_readiness: AIReadinessStatus,
        history_days: int,
        actual_history_days: int,
    ) -> None:
        """Persist intelligence profiles and evidence to SQLite tables."""
        try:
            self.intelligence_repo.save_profiles(list(profiles.values()))
            self.intelligence_repo.save_evidence_records(evidence_records)
            logger.info(
                f"Successfully persisted Historical Intelligence run {analysis_run_id}: "
                f"{len(profiles)} profiles, {len(evidence_records)} evidence records."
            )
        except Exception as e:
            logger.error(f"Failed to persist historical intelligence run {analysis_run_id}: {e}", exc_info=True)
