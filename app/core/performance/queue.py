"""Current Queue and Expected Effort Analysis for Phase A Performance Data Foundation.

Implements the corrected expected-effort inference hierarchy:
1. jira_estimate
2. resource_historical_comparable (issue_type + complexity_score, n >= 3)
3. resource_complexity_history (complexity_score, n >= 3)
4. resource_issue_type_history (issue_type, n >= 3)
5. role_team_benchmark (role/team benchmark, n >= 3)
6. deterministic_fallback (Settings: 1=1.5h, 2=3.0h, 3=5.0h, 4=8.0h, 5=14.0h)
7. unavailable

Keeps Jira's raw remaining estimate completely separate from inferred analytics:
- jira_remaining_hours
- inferred_expected_hours
- inferred_remaining_hours
- expected_effort_source
- expected_effort_confidence
- sample_size
"""

from typing import Any, Dict, List, Optional, Tuple
from app.config.settings import settings
from app.core.models.performance import TaskDeliveryForecast, RiskLevel, EffortStatistics
from app.core.performance.complexity import TaskComplexityCalculator


COMPLETED_STATUSES = {"done", "completed", "resolved", "closed", "finished", "cancelled", "rejected"}


class CurrentQueueAnalyzer:
    """Analyzes active assigned Jira issues and computes expected delivery effort and review buffers."""

    @classmethod
    def resolve_expected_task_hours(
        cls,
        task: Dict[str, Any],
        resource_effort_stats: List[EffortStatistics],
        team_effort_stats: Optional[List[EffortStatistics]] = None,
        role_category: Optional[str] = None,
    ) -> Tuple[float, str, str, int]:
        """Determine base expected effort (in hours), source, confidence, and sample size.

        Returns:
            (base_expected_hours, source, confidence, sample_size)
        """
        issue_type = str(task.get("issue_type") or "Task").strip().lower()
        complexity_score = int(task.get("complexity_score") or 3)
        orig_est_secs = task.get("original_estimate_seconds")

        # 1. Jira explicit estimate (Highest priority in corrected hierarchy)
        if orig_est_secs is not None and orig_est_secs > 0:
            est_hours = round(orig_est_secs / 3600.0, 2)
            return est_hours, "jira_estimate", "high", 1

        # Index resource stats (filter out fallback stats)
        res_by_comp = {
            s.segment_key: s
            for s in resource_effort_stats
            if s.segment_type == "complexity" and not s.is_fallback and s.sample_count >= 3
        }
        res_by_type = {
            s.segment_key.lower(): s
            for s in resource_effort_stats
            if s.segment_type == "issue_type" and not s.is_fallback and s.sample_count >= 3
        }
        res_by_comparable = {
            s.segment_key.lower(): s
            for s in resource_effort_stats
            if s.segment_type == "comparable" and not s.is_fallback and s.sample_count >= 3
        }

        # Index team / benchmark stats
        team_stats = team_effort_stats or []
        team_by_comparable = {
            s.segment_key.lower(): s
            for s in team_stats
            if s.segment_type == "comparable" and s.sample_count >= 3
        }
        team_by_role = {
            s.segment_key.lower(): s
            for s in team_stats
            if s.segment_type == "role_category" and s.sample_count >= 3
        }
        team_by_comp = {
            s.segment_key: s
            for s in team_stats
            if s.segment_type == "complexity" and s.sample_count >= 3
        }
        team_by_type = {
            s.segment_key.lower(): s
            for s in team_stats
            if s.segment_type == "issue_type" and s.sample_count >= 3
        }
        team_overall = next(
            (s for s in team_stats if s.segment_type == "overall" and s.sample_count >= 3),
            None,
        )

        comparable_key = f"{issue_type}:{complexity_score}"

        # 2. Resource historical comparable (issue_type + complexity_score, sample >= 3)
        if comparable_key in res_by_comparable and res_by_comparable[comparable_key].median_hours > 0:
            stat = res_by_comparable[comparable_key]
            conf = "high" if stat.sample_count >= 10 else "medium"
            return stat.median_hours, "resource_historical_comparable", conf, stat.sample_count

        # 3. Resource complexity history (complexity_score, sample >= 3)
        if str(complexity_score) in res_by_comp and res_by_comp[str(complexity_score)].median_hours > 0:
            stat = res_by_comp[str(complexity_score)]
            conf = "medium" if stat.sample_count >= 5 else "low"
            return stat.median_hours, "resource_complexity_history", conf, stat.sample_count

        # 4. Resource issue type history (issue_type, sample >= 3)
        if issue_type in res_by_type and res_by_type[issue_type].median_hours > 0:
            stat = res_by_type[issue_type]
            conf = "medium" if stat.sample_count >= 5 else "low"
            return stat.median_hours, "resource_issue_type_history", conf, stat.sample_count

        # 5. Role / team benchmark (sample >= 3)
        if comparable_key in team_by_comparable and team_by_comparable[comparable_key].median_hours > 0:
            stat = team_by_comparable[comparable_key]
            conf = "medium" if stat.sample_count >= 10 else "low"
            return stat.median_hours, "role_team_benchmark", conf, stat.sample_count

        if role_category and role_category.lower() in team_by_role and team_by_role[role_category.lower()].median_hours > 0:
            stat = team_by_role[role_category.lower()]
            conf = "medium" if stat.sample_count >= 10 else "low"
            return stat.median_hours, "role_team_benchmark", conf, stat.sample_count

        if str(complexity_score) in team_by_comp and team_by_comp[str(complexity_score)].median_hours > 0:
            stat = team_by_comp[str(complexity_score)]
            conf = "medium" if stat.sample_count >= 10 else "low"
            return stat.median_hours, "role_team_benchmark", conf, stat.sample_count

        if issue_type in team_by_type and team_by_type[issue_type].median_hours > 0:
            stat = team_by_type[issue_type]
            conf = "medium" if stat.sample_count >= 10 else "low"
            return stat.median_hours, "role_team_benchmark", conf, stat.sample_count

        if team_overall and team_overall.median_hours > 0:
            conf = "medium" if team_overall.sample_count >= 10 else "low"
            return team_overall.median_hours, "role_team_benchmark", conf, team_overall.sample_count

        # 6. Deterministic fallback based on complexity from Settings
        fb_hours = settings.get_fallback_hours_for_complexity(complexity_score)
        if fb_hours > 0:
            return fb_hours, "deterministic_fallback", "low", 0

        # 7. Unavailable
        return 0.0, "unavailable", "unavailable", 0

    @classmethod
    def analyze_active_queue(
        cls,
        account_id: str,
        active_issues: List[Dict[str, Any]],
        resource_effort_stats: List[EffortStatistics],
        team_effort_stats: Optional[List[EffortStatistics]] = None,
        designation: Optional[str] = None,
        role_category: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Analyze all active assigned issues for a resource and compute task-level and queue-level totals."""
        buffer_percent = float(settings.PERFORMANCE_REVIEW_BUFFER_PERCENT) / 100.0

        task_forecasts: List[TaskDeliveryForecast] = []
        total_base_expected_hours = 0.0
        total_review_buffer_hours = 0.0
        total_expected_hours = 0.0
        total_logged_hours = 0.0
        total_remaining_hours = 0.0

        for issue in active_issues:
            tkey = issue.get("jira_issue_key") or issue.get("key")
            if not tkey:
                continue

            status_str = (issue.get("status") or "").strip().lower()
            if status_str in COMPLETED_STATUSES:
                continue

            raw_ref = issue.get("raw_reference") or {}
            fields = raw_ref.get("fields", {}) if isinstance(raw_ref, dict) else {}

            itype = issue.get("issue_type") or fields.get("issuetype", {}).get("name") or "Task"
            priority = issue.get("priority") or fields.get("priority", {}).get("name") or "Medium"
            due_date = issue.get("due_date") or fields.get("duedate")
            summary = issue.get("summary") or fields.get("summary") or "Untitled"

            # Parse complexity
            complexity = TaskComplexityCalculator.calculate_complexity(
                issue_type=itype,
                priority=priority,
                project_key=issue.get("project_key"),
                components=[c.get("name") for c in fields.get("components", []) if isinstance(c, dict)],
                labels=fields.get("labels"),
                original_estimate_seconds=fields.get("timeoriginalestimate"),
                summary=summary,
            )

            # Jira raw estimates (Preserve un-mutated)
            raw_orig_est_secs = fields.get("timeoriginalestimate")
            raw_rem_est_secs = fields.get("timeestimate")
            jira_remaining_hours = (
                round(float(raw_rem_est_secs) / 3600.0, 2)
                if raw_rem_est_secs is not None and float(raw_rem_est_secs) >= 0
                else None
            )

            task_input = {
                "issue_key": tkey,
                "issue_type": itype,
                "complexity_score": complexity.complexity_score,
                "original_estimate_seconds": raw_orig_est_secs,
            }

            base_expected, effort_source, effort_conf, sample_sz = cls.resolve_expected_task_hours(
                task=task_input,
                resource_effort_stats=resource_effort_stats,
                team_effort_stats=team_effort_stats,
                role_category=role_category,
            )

            # Explicit review buffer calculation (15%)
            review_buffer = round(base_expected * buffer_percent, 2)
            task_total_expected = round(base_expected + review_buffer, 2)

            # Logged time on this task
            logged_secs = issue.get("logged_seconds", 0)
            if not logged_secs and fields.get("timespent"):
                logged_secs = int(fields.get("timespent", 0))
            task_logged_hours = round(logged_secs / 3600.0, 2)

            # Inferred remaining hours
            inferred_remaining = max(0.0, round(task_total_expected - task_logged_hours, 2))
            task_remaining = inferred_remaining

            total_base_expected_hours += base_expected
            total_review_buffer_hours += review_buffer
            total_expected_hours += task_total_expected
            total_logged_hours += task_logged_hours
            total_remaining_hours += task_remaining

            task_forecasts.append(
                TaskDeliveryForecast(
                    issue_key=tkey,
                    account_id=account_id,
                    summary=summary,
                    due_date=due_date,
                    jira_remaining_hours=jira_remaining_hours,
                    inferred_expected_hours=round(base_expected, 2),
                    inferred_remaining_hours=round(inferred_remaining, 2),
                    expected_base_hours=round(base_expected, 2),
                    review_buffer_hours=round(review_buffer, 2),
                    total_expected_hours=round(task_total_expected, 2),
                    logged_hours=round(task_logged_hours, 2),
                    remaining_hours=round(task_remaining, 2),
                    expected_effort_source=effort_source,
                    expected_effort_confidence=effort_conf,
                    sample_size=sample_sz,
                    designation=designation,
                    role_category=role_category,
                    risk_level=RiskLevel.GREEN,  # Evaluated by forecaster module
                )
            )

        return {
            "task_count": len(task_forecasts),
            "expected_base_hours": round(total_base_expected_hours, 2),
            "review_buffer_percent": float(settings.PERFORMANCE_REVIEW_BUFFER_PERCENT),
            "review_buffer_hours": round(total_review_buffer_hours, 2),
            "total_expected_hours": round(total_expected_hours, 2),
            "total_logged_hours": round(total_logged_hours, 2),
            "total_remaining_hours": round(total_remaining_hours, 2),
            "task_forecasts": task_forecasts,
        }
