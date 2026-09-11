"""Due Date and Queue Capacity Forecasting for Phase A Performance Data Foundation.

Evaluates remaining task effort sequentially against available working capacity (Mon-Fri)
and computes slack and risk bands (GREEN, YELLOW, ORANGE, RED).
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from app.config.settings import settings
from app.core.models.performance import TaskDeliveryForecast, RiskLevel
from app.core.performance.capacity import CapacityCalculator
from app.utils.time import utc_now, parse_iso_datetime


class DueDateForecaster:
    """Projects sequential completion dates, slack hours, and delivery risk levels."""

    @classmethod
    def forecast_queue_and_tasks(
        cls,
        task_forecasts: List[TaskDeliveryForecast],
        daily_capacity_hours: float = 6.75,
        horizon_working_days: int = 5,
        start_date: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """Sequentially project task completion dates along standard working days and evaluate risk.
        
        Args:
            task_forecasts: List of TaskDeliveryForecast items from CurrentQueueAnalyzer
            daily_capacity_hours: Realistic daily working capacity (default 6.75h)
            horizon_working_days: Available planning window in business days (default 5 days = 1 week)
            start_date: Starting anchor datetime (defaults to current time)
        """
        now_dt = start_date or utc_now()
        cap = max(daily_capacity_hours, 1.0)
        available_capacity = round(cap * float(horizon_working_days), 2)

        green_thresh = float(settings.PERFORMANCE_RISK_GREEN_BUFFER_DAYS)
        yellow_thresh = float(settings.PERFORMANCE_RISK_YELLOW_BUFFER_DAYS)

        accumulated_hours = 0.0
        updated_tasks: List[TaskDeliveryForecast] = []

        red_count = 0
        orange_count = 0
        yellow_count = 0

        for t in task_forecasts:
            rem_h = t.remaining_hours
            accumulated_hours += rem_h

            # Project completion date for this task based on cumulative queue hours
            proj_date_str, _ = CapacityCalculator.project_completion_date(
                start_dt=now_dt,
                required_hours=accumulated_hours,
                daily_capacity_hours=cap
            )
            t.projected_completion_date = proj_date_str

            # Evaluate due date risk if due date is present
            if t.due_date and str(t.due_date).strip():
                due_clean = str(t.due_date).strip()[:10]
                try:
                    # Calculate working days slack = due_date - projected_date
                    proj_dt = datetime.strptime(proj_date_str[:10], "%Y-%m-%d")
                    due_dt = datetime.strptime(due_clean, "%Y-%m-%d")

                    if proj_dt <= due_dt:
                        slack_days = CapacityCalculator.count_working_days_between(proj_date_str, due_clean)
                    else:
                        slack_days = -CapacityCalculator.count_working_days_between(due_clean, proj_date_str)

                    slack_hours = round(slack_days * cap, 2)
                    t.slack_hours = slack_hours

                    if slack_days < 0:
                        t.risk_level = RiskLevel.RED
                        t.risk_reason = f"Projected completion ({proj_date_str}) is {abs(slack_days)} working days after due date ({due_clean})"
                        red_count += 1
                    elif slack_days < yellow_thresh:
                        t.risk_level = RiskLevel.ORANGE
                        t.risk_reason = f"Tight delivery buffer ({slack_days} working days before due date)"
                        orange_count += 1
                    elif slack_days < green_thresh:
                        t.risk_level = RiskLevel.YELLOW
                        t.risk_reason = f"Moderate delivery buffer ({slack_days} working days before due date)"
                        yellow_count += 1
                    else:
                        t.risk_level = RiskLevel.GREEN
                        t.risk_reason = f"Comfortable buffer ({slack_days} working days before due date)"
                except Exception as e:
                    t.risk_level = RiskLevel.GREEN
                    t.risk_reason = f"Unable to parse due date: {e}"
            else:
                t.risk_level = RiskLevel.GREEN
                t.risk_reason = "No due date specified"

            updated_tasks.append(t)

        total_remaining = round(accumulated_hours, 2)
        capacity_diff = round(available_capacity - total_remaining, 2)

        # Final queue completion date
        queue_proj_date, _ = CapacityCalculator.project_completion_date(
            start_dt=now_dt,
            required_hours=total_remaining,
            daily_capacity_hours=cap
        )

        # Overall Queue Forecast Status
        if capacity_diff < -10.0 or red_count >= 2:
            queue_status = RiskLevel.RED
            queue_reason = f"Current queue ({total_remaining}h) significantly exceeds {horizon_working_days}-day capacity ({available_capacity}h) with {red_count} overdue/at-risk tasks."
        elif capacity_diff < 0.0 or red_count >= 1 or orange_count >= 2:
            queue_status = RiskLevel.ORANGE
            queue_reason = f"Current queue ({total_remaining}h) exceeds available capacity by {abs(capacity_diff)}h."
        elif capacity_diff <= 4.0 or yellow_count >= 2:
            queue_status = RiskLevel.YELLOW
            queue_reason = f"Current queue is near available capacity (surplus: {capacity_diff}h)."
        else:
            queue_status = RiskLevel.GREEN
            queue_reason = f"Queue is comfortably within capacity (surplus: {capacity_diff}h)."

        return {
            "total_remaining_hours": total_remaining,
            "available_capacity_hours": available_capacity,
            "capacity_difference_hours": capacity_diff,
            "projected_queue_completion_date": queue_proj_date,
            "forecast_status": queue_status,
            "forecast_reason": queue_reason,
            "task_forecasts": updated_tasks,
            "risk_counts": {
                "red": red_count,
                "orange": orange_count,
                "yellow": yellow_count,
                "green": len(updated_tasks) - (red_count + orange_count + yellow_count),
            },
        }
