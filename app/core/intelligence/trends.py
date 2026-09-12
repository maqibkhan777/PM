"""Rolling Trend Analysis Engine for Phase B Historical Intelligence.

Computes multi-window metrics across 30d, 90d, 180d, and 365d rolling periods
and deterministically derives trend directions:
INCREASING, DECREASING, STABLE, INSUFFICIENT_DATA.
"""

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
from app.core.intelligence.models import (
    HistoricalTrendsProfile,
    RollingTrendMetric,
    TrendDirection,
)


class HistoricalTrendAnalyzer:
    """Evaluates multi-window rolling trends across operational metrics."""

    @classmethod
    def analyze_trends(
        cls,
        account_id: str,
        metrics_by_window: Optional[Dict[str, Dict[str, float]]] = None,
        issues: Optional[List[Any]] = None,
        worklogs: Optional[List[Any]] = None,
        min_history_days: int = 30,
        now: Optional[datetime] = None,
    ) -> HistoricalTrendsProfile:
        """Compute rolling trends across 30d, 90d, 180d, 365d windows."""
        if metrics_by_window is None:
            if now is None:
                from app.utils.time import utc_now
                now_dt = utc_now()
            elif now.tzinfo is None:
                now_dt = now.replace(tzinfo=timezone.utc)
            else:
                now_dt = now

            raw_issues = issues or []
            raw_worklogs = worklogs or []

            windows = {"30d": 30, "90d": 90, "180d": 180, "365d": 365}
            metrics_by_window = {w: {} for w in windows}

            for w_name, w_days in windows.items():
                w_start = now_dt - timedelta(days=w_days)

                # Logged hours
                w_logs = []
                for wl in raw_worklogs:
                    started = getattr(wl, "started_at", "") or ""
                    try:
                        from app.utils.time import parse_iso_datetime
                        dt = parse_iso_datetime(started)
                        if dt >= w_start:
                            w_logs.append(wl)
                    except Exception:
                        pass
                total_h = sum(float(getattr(l, "time_spent_seconds", 0) or 0) / 3600.0 for l in w_logs)
                metrics_by_window[w_name]["logged_hours"] = total_h

                # Completed tasks
                w_comp = 0
                w_reopen = 0
                w_blocker = 0.0
                for iss in raw_issues:
                    res_at = getattr(iss, "resolved_at", None) or getattr(iss, "updated_at", None)
                    is_done = (getattr(iss, "status_category", "") or getattr(iss, "status", "")).lower() in [
                        "done", "resolved", "closed", "complete"
                    ]
                    if is_done and res_at:
                        try:
                            from app.utils.time import parse_iso_datetime
                            dt = parse_iso_datetime(res_at)
                            if dt >= w_start:
                                w_comp += 1
                                if (getattr(iss, "reopen_count", 0) or 0) > 0:
                                    w_reopen += 1
                        except Exception:
                            pass
                    if getattr(iss, "blocker_hours", 0):
                        w_blocker += float(getattr(iss, "blocker_hours", 0) or 0)

                metrics_by_window[w_name]["completed_tasks"] = float(w_comp)
                metrics_by_window[w_name]["reopen_count"] = float(w_reopen)
                metrics_by_window[w_name]["blocker_hours"] = float(w_blocker)
                metrics_by_window[w_name]["active_queue_size"] = float(
                    len([
                        i for i in raw_issues
                        if (getattr(i, "status_category", "") or getattr(i, "status", "")).lower()
                        not in ["done", "resolved", "closed", "complete"]
                    ])
                )
        w30 = metrics_by_window.get("30d", {})
        w90 = metrics_by_window.get("90d", {})
        w180 = metrics_by_window.get("180d", {})
        w365 = metrics_by_window.get("365d", {})

        logged_trend = cls._compute_trend_metric(
            name="logged_hours",
            v30=w30.get("logged_hours", 0.0),
            v90=w90.get("logged_hours", 0.0),
            v180=w180.get("logged_hours", 0.0),
            v365=w365.get("logged_hours", 0.0),
            min_history_days=min_history_days,
            normalize_by_days=True,
            unit="hours",
        )

        completed_trend = cls._compute_trend_metric(
            name="completed_tasks",
            v30=w30.get("completed_tasks", 0.0),
            v90=w90.get("completed_tasks", 0.0),
            v180=w180.get("completed_tasks", 0.0),
            v365=w365.get("completed_tasks", 0.0),
            min_history_days=min_history_days,
            normalize_by_days=True,
            unit="tasks",
        )

        queue_trend = cls._compute_trend_metric(
            name="active_queue",
            v30=w30.get("active_queue", 0.0),
            v90=w90.get("active_queue", 0.0),
            v180=w180.get("active_queue", 0.0),
            v365=w365.get("active_queue", 0.0),
            min_history_days=min_history_days,
            normalize_by_days=False,
            unit="tasks",
        )

        workload_trend = cls._compute_trend_metric(
            name="expected_workload",
            v30=w30.get("expected_workload", 0.0),
            v90=w90.get("expected_workload", 0.0),
            v180=w180.get("expected_workload", 0.0),
            v365=w365.get("expected_workload", 0.0),
            min_history_days=min_history_days,
            normalize_by_days=False,
            unit="hours",
        )

        complexity_trend = cls._compute_trend_metric(
            name="task_complexity",
            v30=w30.get("complexity", 3.0),
            v90=w90.get("complexity", 3.0),
            v180=w180.get("complexity", 3.0),
            v365=w365.get("complexity", 3.0),
            min_history_days=min_history_days,
            normalize_by_days=False,
            unit="score",
            tolerance_percent=10.0,
        )

        reopen_trend = cls._compute_trend_metric(
            name="reopened_rate",
            v30=w30.get("reopen_rate", 0.0),
            v90=w90.get("reopen_rate", 0.0),
            v180=w180.get("reopen_rate", 0.0),
            v365=w365.get("reopen_rate", 0.0),
            min_history_days=min_history_days,
            normalize_by_days=False,
            unit="ratio",
            tolerance_percent=15.0,
        )

        overdue_trend = cls._compute_trend_metric(
            name="overdue_rate",
            v30=w30.get("overdue_rate", 0.0),
            v90=w90.get("overdue_rate", 0.0),
            v180=w180.get("overdue_rate", 0.0),
            v365=w365.get("overdue_rate", 0.0),
            min_history_days=min_history_days,
            normalize_by_days=False,
            unit="ratio",
            tolerance_percent=15.0,
        )

        blocker_trend = cls._compute_trend_metric(
            name="blocker_hours",
            v30=w30.get("blocker_hours", 0.0),
            v90=w90.get("blocker_hours", 0.0),
            v180=w180.get("blocker_hours", 0.0),
            v365=w365.get("blocker_hours", 0.0),
            min_history_days=min_history_days,
            normalize_by_days=True,
            unit="hours",
        )

        capacity_trend = cls._compute_trend_metric(
            name="capacity_pressure",
            v30=w30.get("capacity_pressure", 1.0),
            v90=w90.get("capacity_pressure", 1.0),
            v180=w180.get("capacity_pressure", 1.0),
            v365=w365.get("capacity_pressure", 1.0),
            min_history_days=min_history_days,
            normalize_by_days=False,
            unit="ratio",
            tolerance_percent=15.0,
        )

        return HistoricalTrendsProfile(
            account_id=account_id,
            logged_hours_trend=logged_trend,
            completed_tasks_trend=completed_trend,
            active_queue_trend=queue_trend,
            expected_workload_trend=workload_trend,
            complexity_trend=complexity_trend,
            reopen_rate_trend=reopen_trend,
            overdue_rate_trend=overdue_trend,
            blocker_hours_trend=blocker_trend,
            capacity_pressure_trend=capacity_trend,
        )

    @classmethod
    def _compute_trend_metric(
        cls,
        name: str,
        v30: float,
        v90: float,
        v180: float,
        v365: float,
        min_history_days: int,
        normalize_by_days: bool = False,
        unit: str = "",
        tolerance_percent: float = 15.0,
    ) -> RollingTrendMetric:
        """Evaluate directional trend between recent 30d/90d and longer term baseline."""
        if min_history_days < 30:
            return RollingTrendMetric(
                metric_name=name,
                value_30d=round(v30, 2),
                value_90d=round(v90, 2),
                value_180d=round(v180, 2),
                value_365d=round(v365, 2),
                direction=TrendDirection.INSUFFICIENT_DATA,
                explanation=f"Insufficient history ({min_history_days}d available) to calculate {name} trend.",
            )

        # Normalize totals to 30-day equivalent rates if requested (e.g. cumulative logged hours)
        recent_rate = v30 if not normalize_by_days else v30
        baseline_rate = (
            v90 / 3.0 if (normalize_by_days and v90 > 0)
            else (v180 / 6.0 if (normalize_by_days and v180 > 0)
            else (v365 / 12.0 if (normalize_by_days and v365 > 0) else v90))
        )

        if not normalize_by_days:
            baseline_rate = v90 if v90 > 0 else (v180 if v180 > 0 else v365)

        if baseline_rate <= 0 and recent_rate <= 0:
            direction = TrendDirection.STABLE
            explanation = f"{name.replace('_', ' ').title()} is stable at zero across historical windows."
        elif baseline_rate <= 0 and recent_rate > 0:
            direction = TrendDirection.INCREASING
            explanation = f"{name.replace('_', ' ').title()} increased recently ({recent_rate:.1f} {unit}) from zero baseline."
        else:
            diff_pct = ((recent_rate - baseline_rate) / baseline_rate) * 100.0
            if diff_pct > tolerance_percent:
                direction = TrendDirection.INCREASING
                explanation = f"{name.replace('_', ' ').title()} is increasing (+{diff_pct:.1f}% vs baseline rate)."
            elif diff_pct < -tolerance_percent:
                direction = TrendDirection.DECREASING
                explanation = f"{name.replace('_', ' ').title()} is decreasing ({diff_pct:.1f}% vs baseline rate)."
            else:
                direction = TrendDirection.STABLE
                explanation = f"{name.replace('_', ' ').title()} is stable ({diff_pct:+.1f}% within ±{tolerance_percent}% baseline window)."

        return RollingTrendMetric(
            metric_name=name,
            value_30d=round(v30, 2),
            value_90d=round(v90, 2),
            value_180d=round(v180, 2),
            value_365d=round(v365, 2),
            direction=direction,
            explanation=explanation,
        )
