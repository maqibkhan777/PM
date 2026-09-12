"""Personal Historical Baseline Engine for Phase B Historical Intelligence.

Calculates individual historical baselines and compares current operational states
against personal historical patterns using neutral statistical state classifications:
ABOVE_PERSONAL_BASELINE, NEAR_PERSONAL_BASELINE, BELOW_PERSONAL_BASELINE, INSUFFICIENT_HISTORY.

Strictly non-judgmental. Deviations represent operational context, NOT performance scores.
"""

from typing import Any, Dict, List, Optional
from app.core.intelligence.models import (
    BaselineComparisonState,
    MetricBaselineItem,
    PersonalBaselineProfile,
)


class PersonalBaselineEngine:
    """Evaluates individual historical baselines and current deviations."""

    @classmethod
    def evaluate_personal_baseline(
        cls,
        account_id: str,
        historical_stats: Dict[str, Any],
        current_stats: Dict[str, Any],
        sample_count: int,
    ) -> PersonalBaselineProfile:
        """Compute personal baseline profile comparing current workload with historical patterns.
        
        Args:
            account_id: Atlassian account ID
            historical_stats: Typical historical metrics (median_queue, avg_logged_hours, avg_complexity,
                              median_expected_effort, reopen_rate, overdue_rate)
            current_stats: Current active metrics (active_queue, current_logged_hours, avg_complexity,
                           current_expected_effort, reopen_rate, overdue_rate)
            sample_count: Number of completed historical tasks
        """
        has_sufficient = sample_count >= 3

        queue_item = cls._compare_metric(
            metric_name="active_queue_size",
            typical_val=float(historical_stats.get("typical_queue_size", 0.0)),
            current_val=float(current_stats.get("active_queue_size", 0.0)),
            has_sufficient_history=has_sufficient,
            unit="tasks",
        )

        logged_item = cls._compare_metric(
            metric_name="logged_hours_per_active_day",
            typical_val=float(historical_stats.get("typical_logged_hours", 0.0)),
            current_val=float(current_stats.get("current_logged_hours", 0.0)),
            has_sufficient_history=has_sufficient,
            unit="hours/day",
        )

        complexity_item = cls._compare_metric(
            metric_name="task_complexity",
            typical_val=float(historical_stats.get("typical_complexity", 3.0)),
            current_val=float(current_stats.get("current_complexity", 3.0)),
            has_sufficient_history=has_sufficient,
            unit="score",
            tolerance_percent=15.0,
        )

        effort_item = cls._compare_metric(
            metric_name="expected_effort_per_task",
            typical_val=float(historical_stats.get("typical_expected_effort", 0.0)),
            current_val=float(current_stats.get("current_expected_effort", 0.0)),
            has_sufficient_history=has_sufficient,
            unit="hours",
        )

        reopen_item = cls._compare_metric(
            metric_name="reopened_task_rate",
            typical_val=float(historical_stats.get("typical_reopen_rate", 0.0)),
            current_val=float(current_stats.get("current_reopen_rate", 0.0)),
            has_sufficient_history=has_sufficient,
            unit="ratio",
            tolerance_percent=20.0,
        )

        overdue_item = cls._compare_metric(
            metric_name="overdue_task_volume",
            typical_val=float(historical_stats.get("typical_overdue_count", 0.0)),
            current_val=float(current_stats.get("current_overdue_count", 0.0)),
            has_sufficient_history=has_sufficient,
            unit="tasks",
        )

        return PersonalBaselineProfile(
            account_id=account_id,
            has_sufficient_history=has_sufficient,
            active_queue_baseline=queue_item,
            logged_hours_baseline=logged_item,
            complexity_baseline=complexity_item,
            expected_effort_baseline=effort_item,
            reopen_rate_baseline=reopen_item,
            overdue_rate_baseline=overdue_item,
        )

    @classmethod
    def _compare_metric(
        cls,
        metric_name: str,
        typical_val: float,
        current_val: float,
        has_sufficient_history: bool,
        unit: str = "",
        tolerance_percent: float = 25.0,
    ) -> MetricBaselineItem:
        """Compare current vs typical metric value with explainability."""
        diff = round(current_val - typical_val, 2)
        pct_diff = round(((diff / typical_val) * 100.0), 1) if typical_val > 0 else 0.0

        if not has_sufficient_history:
            return MetricBaselineItem(
                metric_name=metric_name,
                typical_historical_value=typical_val,
                current_value=current_val,
                difference=diff,
                percent_difference=pct_diff,
                comparison_state=BaselineComparisonState.INSUFFICIENT_HISTORY,
                explanation=f"Insufficient historical data to establish personal baseline for {metric_name}.",
            )

        if typical_val <= 0 and current_val <= 0:
            state = BaselineComparisonState.NEAR_PERSONAL_BASELINE
            explanation = f"Current {metric_name} ({current_val} {unit}) aligns with historical zero baseline."
        elif typical_val <= 0 and current_val > 0:
            state = BaselineComparisonState.ABOVE_PERSONAL_BASELINE
            explanation = f"Current {metric_name} ({current_val} {unit}) is above historical baseline of 0 {unit}."
        elif pct_diff > tolerance_percent:
            state = BaselineComparisonState.ABOVE_PERSONAL_BASELINE
            explanation = f"Current {metric_name} ({current_val} {unit}) is +{pct_diff}% above personal baseline ({typical_val} {unit})."
        elif pct_diff < -tolerance_percent:
            state = BaselineComparisonState.BELOW_PERSONAL_BASELINE
            explanation = f"Current {metric_name} ({current_val} {unit}) is {pct_diff}% below personal baseline ({typical_val} {unit})."
        else:
            state = BaselineComparisonState.NEAR_PERSONAL_BASELINE
            explanation = f"Current {metric_name} ({current_val} {unit}) is within expected baseline variance ({typical_val} {unit})."

        return MetricBaselineItem(
            metric_name=metric_name,
            typical_historical_value=round(typical_val, 2),
            current_value=round(current_val, 2),
            difference=diff,
            percent_difference=pct_diff,
            comparison_state=state,
            explanation=explanation,
        )

    @classmethod
    def compute_personal_baseline(
        cls,
        account_id: str,
        issues: Optional[List[Any]] = None,
        worklogs: Optional[List[Any]] = None,
        current_active_queue_count: int = 0,
        current_inferred_workload_hours: float = 0.0,
        historical_stats: Optional[Dict[str, Any]] = None,
        current_stats: Optional[Dict[str, Any]] = None,
    ) -> PersonalBaselineProfile:
        """Compute personal baseline from issues and worklogs or provided stat dicts."""
        if historical_stats is not None and current_stats is not None:
            sample_count = int(historical_stats.get("sample_count", 0))
            return cls.evaluate_personal_baseline(account_id, historical_stats, current_stats, sample_count)

        raw_issues = issues or []
        raw_worklogs = worklogs or []

        # Completed tasks and historical metrics
        completed_issues = [
            i for i in raw_issues
            if (getattr(i, "status_category", "") or getattr(i, "status", "")).lower() in ["done", "resolved", "closed", "complete"]
        ]
        sample_count = len(completed_issues)

        # Worklogs per active day
        daily_hours: Dict[str, float] = {}
        for w in raw_worklogs:
            started = getattr(w, "started_at", "") or ""
            day_str = started[:10] if len(started) >= 10 else "day"
            secs = float(getattr(w, "time_spent_seconds", 0) or 0)
            daily_hours[day_str] = daily_hours.get(day_str, 0.0) + (secs / 3600.0)

        active_days = len(daily_hours)
        total_hours = sum(daily_hours.values())
        avg_logged_hours = round(total_hours / active_days, 2) if active_days > 0 else 0.0

        # Complexity
        complexities = [float(getattr(i, "complexity_score", 3) or 3) for i in raw_issues]
        avg_complexity = round(sum(complexities) / len(complexities), 2) if complexities else 3.0

        # Reopen rate & overdue rate
        reopened_count = sum(1 for i in completed_issues if (getattr(i, "reopen_count", 0) or 0) > 0)
        reopen_rate = round(reopened_count / len(completed_issues), 2) if completed_issues else 0.0

        hist_dict = {
            "typical_queue_size": max(1.0, round(len(raw_issues) / 10.0, 1)),
            "typical_logged_hours": avg_logged_hours or 6.75,
            "typical_complexity": avg_complexity,
            "typical_expected_effort": 8.0,
            "typical_reopen_rate": reopen_rate,
            "typical_overdue_count": 0.0,
            "sample_count": max(sample_count, len(raw_worklogs)),
        }

        curr_dict = {
            "active_queue_size": float(current_active_queue_count),
            "current_logged_hours": avg_logged_hours or 6.75,
            "current_complexity": avg_complexity,
            "current_expected_effort": current_inferred_workload_hours / max(1, current_active_queue_count) if current_active_queue_count > 0 else 8.0,
            "current_reopen_rate": reopen_rate,
            "current_overdue_count": 0.0,
        }

        return cls.evaluate_personal_baseline(account_id, hist_dict, curr_dict, hist_dict["sample_count"])
