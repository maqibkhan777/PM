"""Workload pressure analyzer for Phase B v1.1 Historical Intelligence Layer.

Deterministic and explainable evaluation of workload vs capacity.
Treats workload pressure strictly as operational context, NOT an employee performance rating.
"""

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from app.core.intelligence.models import (
    WorkloadPressureAssessment,
    WorkloadPressureLevel,
)
from app.core.models.performance import JiraIssueState


class WorkloadPressureAnalyzer:
    """Evaluates contextual workload pressure against capacity and deadlines."""

    @staticmethod
    def assess_workload_pressure(
        account_id: str,
        active_issues: List[JiraIssueState],
        inferred_remaining_workload_hours: float,
        forecast_capacity_hours: float,
        active_blockers_count: int = 0,
        effort_confidence: str = "medium",
        now: Optional[datetime] = None,
    ) -> WorkloadPressureAssessment:
        """Deterministically assess workload pressure level with explainability."""
        if now is None:
            now = datetime.now(timezone.utc)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        active_tasks_count = len(active_issues)
        due_within_7_days_count = 0
        high_complexity_tasks_count = 0

        seven_days_future = now + timedelta(days=7)

        for issue in active_issues:
            # Check complexity
            complexity = getattr(issue, "complexity_score", None) or 3
            if complexity >= 4:
                high_complexity_tasks_count += 1

            # Check due date
            due_date_str = getattr(issue, "due_date", None)
            if due_date_str:
                try:
                    due_date = datetime.fromisoformat(due_date_str.replace("Z", "+00:00"))
                    if due_date.tzinfo is None:
                        due_date = due_date.replace(tzinfo=timezone.utc)
                    if due_date <= seven_days_future:
                        due_within_7_days_count += 1
                except Exception:
                    pass

        capacity_diff = forecast_capacity_hours - inferred_remaining_workload_hours

        # Determine level
        if active_tasks_count == 0 and inferred_remaining_workload_hours <= 0:
            pressure_level = WorkloadPressureLevel.LOW
        elif forecast_capacity_hours <= 0 and inferred_remaining_workload_hours > 0:
            pressure_level = WorkloadPressureLevel.HIGH
        elif forecast_capacity_hours > 0:
            ratio = inferred_remaining_workload_hours / forecast_capacity_hours
            if ratio > 1.6 or (high_complexity_tasks_count >= 3 and due_within_7_days_count >= 2):
                pressure_level = WorkloadPressureLevel.HIGH
            elif ratio > 1.2 or (due_within_7_days_count >= 3) or (high_complexity_tasks_count >= 2 and ratio > 1.0):
                pressure_level = WorkloadPressureLevel.ELEVATED
            elif ratio < 0.6 and due_within_7_days_count == 0:
                pressure_level = WorkloadPressureLevel.LOW
            else:
                pressure_level = WorkloadPressureLevel.NORMAL
        else:
            pressure_level = WorkloadPressureLevel.UNKNOWN

        # Build explainability text
        explanation = (
            f"{pressure_level.value} workload pressure because: "
            f"{active_tasks_count} active tasks, "
            f"{inferred_remaining_workload_hours:.1f}h inferred remaining workload vs "
            f"{forecast_capacity_hours:.1f}h forecast capacity "
            f"(difference: {capacity_diff:+.1f}h), "
            f"{due_within_7_days_count} tasks due within 7 days, "
            f"{high_complexity_tasks_count} high-complexity tasks (score >= 4), "
            f"{active_blockers_count} active blockers."
        )

        return WorkloadPressureAssessment(
            account_id=account_id,
            pressure_level=pressure_level,
            active_tasks_count=active_tasks_count,
            inferred_remaining_workload_hours=round(inferred_remaining_workload_hours, 2),
            forecast_capacity_hours=round(forecast_capacity_hours, 2),
            capacity_difference_hours=round(capacity_diff, 2),
            tasks_due_within_7_days=due_within_7_days_count,
            high_complexity_tasks_count=high_complexity_tasks_count,
            effort_confidence=effort_confidence,
            active_blockers_count=active_blockers_count,
            explanation=explanation,
        )
