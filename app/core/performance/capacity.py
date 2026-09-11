"""Deterministic Capacity Model for Phase A Performance Data Foundation.

Refinement Rule 2:
Keep nominal_capacity_hours, observed_logged_capacity_hours, and forecast_capacity_hours separate.
The nominal 6.75h/day remains the default forecasting baseline.
Observed logged hours are an observational metric, not a productivity target.
Excessive logged hours (e.g. 10-12h) are never rewarded or expected as daily productive capacity.
"""

from datetime import datetime, timedelta
import zoneinfo
from typing import Any, Dict, List, Optional, Tuple
from app.config.settings import settings
from app.core.models.performance import ConfidenceLevel
from app.utils.time import parse_iso_datetime, utc_now


class CapacityCalculator:
    """Calculates nominal, observed, and bounded forecast capacity and working days."""

    @classmethod
    def calculate_capacity_metrics(
        cls,
        total_logged_seconds: int,
        active_working_days: int,
        history_days: int = 180
    ) -> Dict[str, Any]:
        """Compute the three separate capacity metrics and confidence.
        
        Returns:
            Dict containing:
            - nominal_capacity_hours (6.75h default)
            - observed_logged_capacity_hours (actual historical observation)
            - forecast_capacity_hours (bounded forecasting capacity)
            - capacity_method
            - confidence
        """
        nominal_hours = float(settings.PERFORMANCE_WORKDAY_HOURS)  # 6.75h
        min_hours = float(settings.PERFORMANCE_WORKDAY_MIN_HOURS)   # 6.5h
        max_hours = float(settings.PERFORMANCE_WORKDAY_MAX_HOURS)   # 7.0h

        if active_working_days >= 5 and total_logged_seconds > 0:
            raw_observed = (total_logged_seconds / 3600.0) / float(active_working_days)
            observed_logged_hours = round(raw_observed, 2)
        else:
            observed_logged_hours = nominal_hours

        # Forecast capacity is calibrated conservatively and bounded strictly to nominal workday range
        if active_working_days >= 15:
            # If observed is within reasonable bounds, use observed capped between 6.5 and 7.0
            bounded = max(min_hours, min(max_hours, observed_logged_hours))
            forecast_hours = round(bounded, 2)
            method = "calibrated_bounded_worklog"
            confidence = ConfidenceLevel.HIGH if active_working_days >= 30 else ConfidenceLevel.MEDIUM
        else:
            forecast_hours = nominal_hours
            method = "nominal_baseline_default"
            confidence = ConfidenceLevel.LOW

        return {
            "nominal_capacity_hours": nominal_hours,
            "observed_logged_capacity_hours": observed_logged_hours,
            "forecast_capacity_hours": forecast_hours,
            "capacity_method": method,
            "confidence": confidence,
        }

    @classmethod
    def count_working_days_between(
        cls,
        start_date: str,
        end_date: str,
        timezone_str: Optional[str] = None
    ) -> int:
        """Count standard working days (Mon-Fri) between two ISO/YYYY-MM-DD dates (inclusive)."""
        tz_name = timezone_str or settings.PERFORMANCE_TIMEZONE or "Asia/Karachi"
        try:
            tz = zoneinfo.ZoneInfo(tz_name)
        except Exception:
            tz = None

        d_start = parse_iso_datetime(start_date)
        d_end = parse_iso_datetime(end_date)

        if not d_start or not d_end:
            try:
                d_start = datetime.strptime(start_date[:10], "%Y-%m-%d")
                d_end = datetime.strptime(end_date[:10], "%Y-%m-%d")
            except Exception:
                return 0

        if d_start > d_end:
            return 0

        curr = d_start.date() if hasattr(d_start, "date") else d_start
        target = d_end.date() if hasattr(d_end, "date") else d_end

        working_days = 0
        while curr <= target:
            # Monday is 0 and Sunday is 6 -> weekday < 5 is Mon-Fri
            if curr.weekday() < 5:
                working_days += 1
            curr += timedelta(days=1)

        return working_days

    @classmethod
    def project_completion_date(
        cls,
        start_dt: datetime,
        required_hours: float,
        daily_capacity_hours: float = 6.75,
        timezone_str: Optional[str] = None
    ) -> Tuple[str, float]:
        """Project the completion date sequentially across standard working days.
        
        Returns:
            Tuple of (projected_date_iso_YYYY_MM_DD, total_working_days_needed)
        """
        if required_hours <= 0.0:
            return start_dt.strftime("%Y-%m-%d"), 0.0

        cap = max(daily_capacity_hours, 1.0)
        days_needed = required_hours / cap

        curr = start_dt.date() if hasattr(start_dt, "date") else start_dt
        full_days = int(days_needed)
        fractional = days_needed - full_days

        accumulated_days = 0
        while accumulated_days < full_days:
            curr += timedelta(days=1)
            if curr.weekday() < 5:  # Mon-Fri
                accumulated_days += 1

        # If there is remaining fractional work on a non-working day, advance to next working day
        if fractional > 0:
            if curr.weekday() >= 5:
                while curr.weekday() >= 5:
                    curr += timedelta(days=1)

        return curr.strftime("%Y-%m-%d"), round(days_needed, 2)
