"""Deterministic Performance Signals and Evidence Generator for Phase A.

Refinement Rule 5:
Phase A must NOT generate overall performance scores, employee ratings, or PIP/reward recommendations.
Only produce measurable facts, signals, confidence levels, and traceable evidence records.
"""

from typing import Any, Dict, List, Optional
from app.core.models.performance import (
    PerformanceSignal,
    PerformanceEvidence,
    SignalType,
    ConfidenceLevel,
    RiskLevel,
)
from app.utils.time import utc_now_iso


class PerformanceSignalGenerator:
    """Generates traceable signals and evidence records based on calculated metrics."""

    @classmethod
    def generate_signals_and_evidence(
        cls,
        account_id: str,
        analysis_run_id: str,
        metrics: Dict[str, Any]
    ) -> Tuple[List[PerformanceSignal], List[PerformanceEvidence]]:
        """Generate deterministic signals and auditable evidence records."""
        signals: List[PerformanceSignal] = []
        evidence_list: List[PerformanceEvidence] = []
        now_str = utc_now_iso()
        snapshot_date = now_str[:10]

        history_days = metrics.get("history_days", 0)
        completed_tasks = metrics.get("completed_tasks", 0)
        on_time_rate = metrics.get("on_time_rate", 0.0)
        tasks_due = metrics.get("tasks_due", 0)
        tasks_completed_on_time = metrics.get("tasks_completed_on_time", 0)
        tasks_completed_late = metrics.get("tasks_completed_late", 0)
        reopen_rate = metrics.get("reopen_rate", 0.0)
        reopened_tasks = metrics.get("reopened_tasks", 0)
        blocker_count = metrics.get("blocker_count", 0)
        blocked_hours = metrics.get("average_blocker_hours", 0.0) * float(blocker_count)
        est_variance = metrics.get("median_estimation_variance_percent", 0.0)
        estimated_tasks = metrics.get("estimated_tasks", 0)
        capacity_diff = metrics.get("capacity_difference_hours", 0.0)
        queue_remaining = metrics.get("total_remaining_hours", 0.0)
        queue_count = metrics.get("queue_task_count", 0)
        forecast_status = metrics.get("forecast_status", RiskLevel.GREEN)
        active_working_days = metrics.get("active_working_days", 0)

        # 1. Check History Sufficiency
        if history_days < 30 or completed_tasks < 5:
            signals.append(
                PerformanceSignal(
                    signal=SignalType.INSUFFICIENT_HISTORY,
                    value=float(completed_tasks),
                    threshold=5.0,
                    evidence=f"Historical window contains {history_days} days and {completed_tasks} completed tasks (minimum required: 30 days and 5 tasks).",
                    confidence=ConfidenceLevel.HIGH,
                )
            )
            evidence_list.append(
                PerformanceEvidence(
                    evidence_id=f"evi:{analysis_run_id}:{account_id}:history_low",
                    analysis_run_id=analysis_run_id,
                    account_id=account_id,
                    evidence_type="INSUFFICIENT_DATA",
                    observed_value=float(completed_tasks),
                    expected_value=5.0,
                    difference=float(completed_tasks - 5),
                    source="jira_issue_state",
                    confidence=ConfidenceLevel.HIGH,
                    explanation=f"Insufficient historical samples ({completed_tasks} completed tasks).",
                    timestamp=now_str,
                    snapshot_date=snapshot_date,
                )
            )

        # 2. Delivery Signals
        if tasks_due >= 5:
            if on_time_rate >= 0.85 and forecast_status in (RiskLevel.GREEN, RiskLevel.YELLOW):
                signals.append(
                    PerformanceSignal(
                        signal=SignalType.DELIVERY_ON_TRACK,
                        value=round(on_time_rate * 100, 1),
                        threshold=85.0,
                        evidence=f"Historical on-time completion rate is {on_time_rate*100:.1f}% ({tasks_completed_on_time}/{tasks_due} tasks).",
                        confidence=ConfidenceLevel.HIGH if tasks_due >= 15 else ConfidenceLevel.MEDIUM,
                    )
                )
            elif on_time_rate < 0.70 or forecast_status == RiskLevel.RED:
                signals.append(
                    PerformanceSignal(
                        signal=SignalType.DELIVERY_RISK,
                        value=round(on_time_rate * 100, 1),
                        threshold=70.0,
                        evidence=f"On-time rate is {on_time_rate*100:.1f}% with {tasks_completed_late} late tasks; current queue risk is {forecast_status}.",
                        confidence=ConfidenceLevel.HIGH if tasks_due >= 15 else ConfidenceLevel.MEDIUM,
                    )
                )

            evidence_list.append(
                PerformanceEvidence(
                    evidence_id=f"evi:{analysis_run_id}:{account_id}:on_time_rate",
                    analysis_run_id=analysis_run_id,
                    account_id=account_id,
                    evidence_type="ON_TIME_RATE",
                    observed_value=round(on_time_rate * 100, 1),
                    expected_value=85.0,
                    difference=round((on_time_rate - 0.85) * 100, 1),
                    source="jira_issue_state",
                    confidence=ConfidenceLevel.HIGH if tasks_due >= 15 else ConfidenceLevel.MEDIUM,
                    explanation=f"Completed {tasks_completed_on_time} on-time and {tasks_completed_late} late out of {tasks_due} tasks with due dates.",
                    timestamp=now_str,
                    snapshot_date=snapshot_date,
                )
            )

        # 3. Capacity & Workload Signals
        if capacity_diff < -3.0:
            signals.append(
                PerformanceSignal(
                    signal=SignalType.CAPACITY_OVERLOADED,
                    value=round(abs(capacity_diff), 1),
                    threshold=0.0,
                    evidence=f"Current active queue ({queue_remaining}h) exceeds available capacity by {abs(capacity_diff):.1f}h.",
                    confidence=ConfidenceLevel.HIGH,
                )
            )
            evidence_list.append(
                PerformanceEvidence(
                    evidence_id=f"evi:{analysis_run_id}:{account_id}:capacity_deficit",
                    analysis_run_id=analysis_run_id,
                    account_id=account_id,
                    evidence_type="QUEUE_CAPACITY_DEFICIT",
                    observed_value=queue_remaining,
                    expected_value=queue_remaining + capacity_diff,
                    difference=capacity_diff,
                    source="task_delivery_forecasts",
                    confidence=ConfidenceLevel.HIGH,
                    explanation=f"Queue remaining work ({queue_remaining:.1f}h) exceeds available capacity by {abs(capacity_diff):.1f}h.",
                    timestamp=now_str,
                    snapshot_date=snapshot_date,
                )
            )
        elif queue_count <= 1 and queue_remaining < 6.0:
            signals.append(
                PerformanceSignal(
                    signal=SignalType.LOW_WORKLOAD,
                    value=float(queue_count),
                    threshold=2.0,
                    evidence=f"Current queue has only {queue_count} active task ({queue_remaining}h remaining).",
                    confidence=ConfidenceLevel.MEDIUM,
                )
            )

        # 4. Estimation Variance Signal
        if estimated_tasks >= 5:
            if est_variance > 25.0:
                signals.append(
                    PerformanceSignal(
                        signal=SignalType.HIGH_ESTIMATION_VARIANCE,
                        value=round(est_variance, 1),
                        threshold=25.0,
                        evidence=f"Median actual logged effort is {est_variance:.1f}% higher than initial Jira estimates ({estimated_tasks} estimated tasks evaluated).",
                        confidence=ConfidenceLevel.HIGH if estimated_tasks >= 15 else ConfidenceLevel.MEDIUM,
                    )
                )
            evidence_list.append(
                PerformanceEvidence(
                    evidence_id=f"evi:{analysis_run_id}:{account_id}:est_variance",
                    analysis_run_id=analysis_run_id,
                    account_id=account_id,
                    evidence_type="ESTIMATION_VARIANCE",
                    observed_value=round(est_variance, 1),
                    expected_value=0.0,
                    difference=round(est_variance, 1),
                    source="jira_worklogs",
                    confidence=ConfidenceLevel.HIGH if estimated_tasks >= 15 else ConfidenceLevel.MEDIUM,
                    explanation=f"Median actual vs estimated variance is {est_variance:.1f}% across {estimated_tasks} tasks.",
                    timestamp=now_str,
                    snapshot_date=snapshot_date,
                )
            )

        # 5. Reopen Rate Signal
        if completed_tasks >= 10:
            if reopen_rate > 0.15:
                signals.append(
                    PerformanceSignal(
                        signal=SignalType.HIGH_REOPEN_RATE,
                        value=round(reopen_rate * 100, 1),
                        threshold=15.0,
                        evidence=f"Task reopen rate is {reopen_rate*100:.1f}% ({reopened_tasks} reopened tasks observed).",
                        confidence=ConfidenceLevel.HIGH if completed_tasks >= 25 else ConfidenceLevel.MEDIUM,
                    )
                )
            evidence_list.append(
                PerformanceEvidence(
                    evidence_id=f"evi:{analysis_run_id}:{account_id}:reopen_rate",
                    analysis_run_id=analysis_run_id,
                    account_id=account_id,
                    evidence_type="REOPEN_OCCURRENCE",
                    observed_value=round(reopen_rate * 100, 1),
                    expected_value=10.0,
                    difference=round((reopen_rate - 0.10) * 100, 1),
                    source="events",
                    confidence=ConfidenceLevel.HIGH if completed_tasks >= 25 else ConfidenceLevel.MEDIUM,
                    explanation=f"{reopened_tasks} tasks were reopened ({reopen_rate*100:.1f}% reopen rate).",
                    timestamp=now_str,
                    snapshot_date=snapshot_date,
                )
            )

        # 6. Blocker Evidence & Signal
        if blocker_count >= 3 or (completed_tasks >= 5 and (blocker_count / max(1, completed_tasks)) >= 0.25):
            signals.append(
                PerformanceSignal(
                    signal=SignalType.FREQUENT_BLOCKERS,
                    value=float(blocker_count),
                    threshold=3.0,
                    evidence=f"{blocker_count} documented blockers ({blocked_hours:.1f} total blocked hours) encountered in historical analysis window.",
                    confidence=ConfidenceLevel.HIGH,
                )
            )
        if blocker_count > 0:
            evidence_list.append(
                PerformanceEvidence(
                    evidence_id=f"evi:{analysis_run_id}:{account_id}:blockers_summary",
                    analysis_run_id=analysis_run_id,
                    account_id=account_id,
                    evidence_type="DOCUMENTED_BLOCKER",
                    observed_value=round(blocked_hours, 1),
                    expected_value=0.0,
                    difference=round(blocked_hours, 1),
                    source="jira_issue_state",
                    confidence=ConfidenceLevel.HIGH,
                    explanation=f"Encountered {blocker_count} documented external blockers ({blocked_hours:.1f}h total).",
                    timestamp=now_str,
                    snapshot_date=snapshot_date,
                )
            )

        # 7. Worklog Update Activity Signal
        if active_working_days >= 15:
            signals.append(
                PerformanceSignal(
                    signal=SignalType.NORMAL_UPDATE_ACTIVITY,
                    value=float(active_working_days),
                    threshold=10.0,
                    evidence=f"Active worklogs recorded on {active_working_days} working days in analysis window.",
                    confidence=ConfidenceLevel.HIGH,
                )
            )

        return signals, evidence_list
