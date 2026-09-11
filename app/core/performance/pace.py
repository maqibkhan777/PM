"""Deterministic Historical Pace and Effort Statistics Analyzer for Phase A Performance Data Foundation.

Refinement Rule 4:
Define historical effort and similarity using structured Jira attributes only
(project, issue_type, complexity, priority) with sufficient sample thresholds (MIN_SEGMENT_SAMPLES=5)
and clear fallback ladders. Never fabricate statistics.
"""

from collections import defaultdict
import math
from typing import Any, Dict, List, Optional, Tuple
from app.config.settings import settings
from app.core.models.performance import EffortStatistics, ConfidenceLevel


def calculate_percentiles(values: List[float]) -> Tuple[float, float, float, float, float, float]:
    """Calculate (mean, median, p25, p75, min_val, max_val) from a list of numeric values."""
    if not values:
        return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0

    sorted_vals = sorted(values)
    n = len(sorted_vals)
    mean_val = sum(sorted_vals) / n
    min_val = sorted_vals[0]
    max_val = sorted_vals[-1]

    def _get_percentile(p: float) -> float:
        if n == 1:
            return sorted_vals[0]
        k = (n - 1) * p
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return sorted_vals[int(k)]
        d0 = sorted_vals[int(f)] * (c - k)
        d1 = sorted_vals[int(c)] * (k - f)
        return d0 + d1

    median_val = _get_percentile(0.50)
    p25_val = _get_percentile(0.25)
    p75_val = _get_percentile(0.75)

    return (
        round(mean_val, 2),
        round(median_val, 2),
        round(p25_val, 2),
        round(p75_val, 2),
        round(min_val, 2),
        round(max_val, 2),
    )


class HistoricalPaceAnalyzer:
    """Computes explainable effort percentiles, segmentations, and pace factors."""

    @classmethod
    def compute_effort_statistics(
        cls,
        account_id: str,
        task_efforts: List[Dict[str, Any]],
        team_benchmark_stats: Optional[Dict[str, EffortStatistics]] = None
    ) -> List[EffortStatistics]:
        """Generate overall and segmented effort statistics for a resource.
        
        Args:
            account_id: Atlassian account ID
            task_efforts: List of dicts with keys: hours, issue_type, complexity_score, priority, project_key
            team_benchmark_stats: Optional team-wide fallback statistics
        """
        min_samples = settings.PERFORMANCE_MIN_SEGMENT_SAMPLES
        results: List[EffortStatistics] = []

        if not task_efforts:
            # Zero historical tasks: return fallback overall
            results.append(
                EffortStatistics(
                    segment_type="overall",
                    segment_key="all",
                    sample_count=0,
                    mean_hours=0.0,
                    median_hours=0.0,
                    p25_hours=0.0,
                    p75_hours=0.0,
                    confidence=ConfidenceLevel.INSUFFICIENT,
                    is_fallback=True,
                )
            )
            return results

        # 1. Overall Statistics
        all_hours = [t["hours"] for t in task_efforts if t.get("hours", 0) > 0]
        mean, med, p25, p75, min_h, max_h = calculate_percentiles(all_hours)
        overall_conf = (
            ConfidenceLevel.HIGH if len(all_hours) >= 30
            else (ConfidenceLevel.MEDIUM if len(all_hours) >= 15
            else (ConfidenceLevel.LOW if len(all_hours) >= 5 else ConfidenceLevel.INSUFFICIENT))
        )
        overall_stat = EffortStatistics(
            segment_type="overall",
            segment_key="all",
            sample_count=len(all_hours),
            mean_hours=mean,
            median_hours=med,
            p25_hours=p25,
            p75_hours=p75,
            min_hours=min_h,
            max_hours=max_h,
            confidence=overall_conf,
            is_fallback=False,
        )
        results.append(overall_stat)

        # 2. Segment by Issue Type, Complexity, Comparable, Priority, Project
        by_type: Dict[str, List[float]] = defaultdict(list)
        by_complexity: Dict[str, List[float]] = defaultdict(list)
        by_comparable: Dict[str, List[float]] = defaultdict(list)
        by_priority: Dict[str, List[float]] = defaultdict(list)
        by_project: Dict[str, List[float]] = defaultdict(list)
        by_role: Dict[str, List[float]] = defaultdict(list)

        for t in task_efforts:
            h = t.get("hours", 0)
            if h <= 0:
                continue
            itype = str(t.get("issue_type", "")).strip().lower()
            cscore = str(t.get("complexity_score", "")).strip()
            if itype:
                by_type[itype].append(h)
            if cscore:
                by_complexity[cscore].append(h)
            if itype and cscore:
                by_comparable[f"{itype}:{cscore}"].append(h)
            if t.get("priority"):
                by_priority[str(t["priority"]).strip().lower()].append(h)
            if t.get("project_key"):
                by_project[str(t["project_key"]).strip().upper()].append(h)
            if t.get("role_category"):
                by_role[str(t["role_category"]).strip()].append(h)

        # Helper to generate stats with fallback
        def _add_segment_stats(segment_type: str, grouped: Dict[str, List[float]]) -> None:
            for k, hours_list in grouped.items():
                if len(hours_list) >= 3:
                    s_mean, s_med, s_p25, s_p75, s_min, s_max = calculate_percentiles(hours_list)
                    s_conf = ConfidenceLevel.HIGH if len(hours_list) >= 15 else (ConfidenceLevel.MEDIUM if len(hours_list) >= 5 else ConfidenceLevel.LOW)
                    results.append(
                        EffortStatistics(
                            segment_type=segment_type,
                            segment_key=k,
                            sample_count=len(hours_list),
                            mean_hours=s_mean,
                            median_hours=s_med,
                            p25_hours=s_p25,
                            p75_hours=s_p75,
                            min_hours=s_min,
                            max_hours=s_max,
                            confidence=s_conf,
                            is_fallback=False,
                        )
                    )
                else:
                    # Fallback to team benchmark if available or overall resource stats
                    fallback_source = team_benchmark_stats.get(f"{segment_type}:{k}") if team_benchmark_stats else None
                    if fallback_source and fallback_source.sample_count >= 3:
                        results.append(
                            EffortStatistics(
                                segment_type=segment_type,
                                segment_key=k,
                                sample_count=len(hours_list),
                                mean_hours=fallback_source.mean_hours,
                                median_hours=fallback_source.median_hours,
                                p25_hours=fallback_source.p25_hours,
                                p75_hours=fallback_source.p75_hours,
                                min_hours=fallback_source.min_hours,
                                max_hours=fallback_source.max_hours,
                                confidence=ConfidenceLevel.LOW,
                                is_fallback=True,
                            )
                        )
                    else:
                        # Fallback to overall resource median
                        results.append(
                            EffortStatistics(
                                segment_type=segment_type,
                                segment_key=k,
                                sample_count=len(hours_list),
                                mean_hours=overall_stat.mean_hours,
                                median_hours=overall_stat.median_hours,
                                p25_hours=overall_stat.p25_hours,
                                p75_hours=overall_stat.p75_hours,
                                min_hours=overall_stat.min_hours,
                                max_hours=overall_stat.max_hours,
                                confidence=ConfidenceLevel.LOW,
                                is_fallback=True,
                            )
                        )

        _add_segment_stats("comparable", by_comparable)
        _add_segment_stats("complexity", by_complexity)
        _add_segment_stats("issue_type", by_type)
        _add_segment_stats("priority", by_priority)
        _add_segment_stats("project", by_project)
        _add_segment_stats("role_category", by_role)

        return results

    @classmethod
    def calculate_pace_factor(
        cls,
        resource_median_hours: float,
        team_benchmark_median_hours: float,
        sample_count: int
    ) -> Dict[str, Any]:
        """Compute an explainable pace factor as a forecasting calibration input (NOT a verdict)."""
        if team_benchmark_median_hours <= 0 or resource_median_hours <= 0 or sample_count < settings.PERFORMANCE_MIN_SEGMENT_SAMPLES:
            return {
                "pace_factor": 1.0,
                "benchmark_used": "neutral_default",
                "sample_size": sample_count,
                "confidence": ConfidenceLevel.INSUFFICIENT if sample_count < 5 else ConfidenceLevel.LOW,
            }

        # Bounded between 0.5x and 2.5x to prevent wild distortions in forecasting
        raw_factor = resource_median_hours / team_benchmark_median_hours
        bounded_factor = max(0.5, min(2.5, round(raw_factor, 2)))
        confidence = ConfidenceLevel.HIGH if sample_count >= 30 else (ConfidenceLevel.MEDIUM if sample_count >= 15 else ConfidenceLevel.LOW)

        return {
            "pace_factor": bounded_factor,
            "benchmark_used": f"team_median_{team_benchmark_median_hours:.1f}h",
            "sample_size": sample_count,
            "confidence": confidence,
        }
