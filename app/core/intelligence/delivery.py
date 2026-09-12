"""Delivery context and blocker history analyzer for Phase B v1.1.

Deterministic and explainable evaluation of due dates, blockers, and timelines.
Treats due-date context as operational reality, not employee penalty or fault.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.core.intelligence.models import (
    BlockerHistoryProfile,
    DeliveryContextProfile,
)
from app.core.models.performance import JiraIssueState


class DeliveryContextAnalyzer:
    """Analyzes historical delivery timeliness and due-date coverage."""

    @staticmethod
    def analyze_delivery_context(
        account_id: str,
        issues: List[JiraIssueState],
        now: Optional[datetime] = None,
    ) -> DeliveryContextProfile:
        """Calculate historical due-date delivery metrics."""
        if now is None:
            now = datetime.now(timezone.utc)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        total_completed = 0
        completed_before_due = 0
        completed_on_due = 0
        completed_after_due = 0
        currently_overdue = 0
        tasks_without_due_date = 0
        total_tasks_count = len(issues)

        lateness_days_list: List[float] = []
        correlated_blocker_count = 0
        correlated_missing_estimates_count = 0

        for issue in issues:
            status = (getattr(issue, "status_category", "") or getattr(issue, "status", "")).lower()
            is_resolved = status in ["done", "resolved", "closed", "complete"]
            due_date_str = getattr(issue, "due_date", None)
            resolved_at_str = getattr(issue, "resolved_at", None) or getattr(issue, "updated_at", None)
            blocker_hours = getattr(issue, "blocker_hours", 0.0) or 0.0
            has_estimate = (getattr(issue, "original_estimate_hours", None) is not None) or (
                getattr(issue, "expected_effort_hours", None) is not None
            )

            if not due_date_str:
                tasks_without_due_date += 1
                if is_resolved:
                    total_completed += 1
                continue

            try:
                due_date = datetime.fromisoformat(due_date_str.replace("Z", "+00:00"))
                if due_date.tzinfo is None:
                    due_date = due_date.replace(tzinfo=timezone.utc)
            except Exception:
                tasks_without_due_date += 1
                if is_resolved:
                    total_completed += 1
                continue

            if is_resolved:
                total_completed += 1
                resolved_at = None
                if resolved_at_str:
                    try:
                        resolved_at = datetime.fromisoformat(resolved_at_str.replace("Z", "+00:00"))
                        if resolved_at.tzinfo is None:
                            resolved_at = resolved_at.replace(tzinfo=timezone.utc)
                    except Exception:
                        pass

                if resolved_at:
                    diff_days = (resolved_at.date() - due_date.date()).days
                    if diff_days < 0:
                        completed_before_due += 1
                    elif diff_days == 0:
                        completed_on_due += 1
                    else:
                        completed_after_due += 1
                        lateness_days_list.append(float(diff_days))
                        if blocker_hours > 0:
                            correlated_blocker_count += 1
                        if not has_estimate:
                            correlated_missing_estimates_count += 1
                else:
                    completed_on_due += 1
            else:
                # Active issue
                if due_date < now:
                    currently_overdue += 1
                    diff_days = max(1.0, (now.date() - due_date.date()).days)
                    lateness_days_list.append(float(diff_days))
                    if blocker_hours > 0:
                        correlated_blocker_count += 1
                    if not has_estimate:
                        correlated_missing_estimates_count += 1

        due_date_coverage = (
            round(((total_tasks_count - tasks_without_due_date) / total_tasks_count) * 100.0, 2)
            if total_tasks_count > 0
            else 0.0
        )
        avg_days_late = (
            round(sum(lateness_days_list) / len(lateness_days_list), 2)
            if lateness_days_list
            else 0.0
        )

        return DeliveryContextProfile(
            account_id=account_id,
            total_completed_tasks=total_completed,
            completed_before_due_date=completed_before_due,
            completed_on_due_date=completed_on_due,
            completed_after_due_date=completed_after_due,
            currently_overdue=currently_overdue,
            tasks_without_due_date=tasks_without_due_date,
            due_date_coverage_percent=due_date_coverage,
            average_days_late=avg_days_late,
            correlated_blocker_count=correlated_blocker_count,
            correlated_missing_estimates_count=correlated_missing_estimates_count,
        )


class BlockerHistoryAnalyzer:
    """Analyzes historical blocker volume and duration."""

    @staticmethod
    def analyze_blocker_history(
        account_id: str,
        issues: List[JiraIssueState],
        total_logged_hours: float = 0.0,
    ) -> BlockerHistoryProfile:
        """Compute aggregate blocker metrics across issues."""
        total_blocker_events = 0
        total_blocked_seconds = 0
        affected_tasks = set()

        for issue in issues:
            blocker_events = getattr(issue, "blocker_events_count", 0) or 0
            blocked_seconds = getattr(issue, "blocked_seconds", 0) or 0
            blocker_hours = getattr(issue, "blocker_hours", 0.0) or 0.0

            if blocked_seconds == 0 and blocker_hours > 0:
                blocked_seconds = int(blocker_hours * 3600)

            if blocker_events > 0 or blocked_seconds > 0:
                total_blocker_events += max(1, blocker_events)
                total_blocked_seconds += blocked_seconds
                affected_tasks.add(issue.issue_key)

        total_blocked_hours = round(total_blocked_seconds / 3600.0, 2)
        avg_hours = (
            round(total_blocked_hours / total_blocker_events, 2)
            if total_blocker_events > 0
            else 0.0
        )

        total_tracked_hours = total_logged_hours + total_blocked_hours
        blocker_pct = (
            round((total_blocked_hours / total_tracked_hours) * 100.0, 2)
            if total_tracked_hours > 0
            else 0.0
        )

        return BlockerHistoryProfile(
            account_id=account_id,
            total_blocker_events=total_blocker_events,
            total_blocked_seconds=total_blocked_seconds,
            total_blocked_hours=total_blocked_hours,
            average_blocker_hours=avg_hours,
            affected_tasks_count=len(affected_tasks),
            affected_tasks_keys=sorted(list(affected_tasks)),
            active_period_blocker_percentage=blocker_pct,
        )
