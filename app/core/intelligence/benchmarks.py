"""Historical Effort Benchmark Engine for Phase B Historical Intelligence.

Calculates statistical effort benchmarks across 11 standard segmentation hierarchies
with percentile metrics, standard deviation, and sample-size confidence thresholds.
"""

from collections import defaultdict
import math
from typing import Any, Dict, List, Optional, Tuple

from app.core.intelligence.models import HistoricalEffortBenchmark, TaskNature
from app.core.models.performance import ConfidenceLevel
from app.core.performance.pace import calculate_percentiles


def calculate_stddev(values: List[float], mean_val: Optional[float] = None) -> float:
    """Calculate sample standard deviation for a list of values."""
    if not values or len(values) < 2:
        return 0.0
    m = mean_val if mean_val is not None else sum(values) / len(values)
    variance = sum((x - m) ** 2 for x in values) / (len(values) - 1)
    return round(math.sqrt(variance), 2)


def get_sample_confidence(sample_count: int) -> ConfidenceLevel:
    """Determine statistical confidence rating from sample size thresholds:
    
    < 3: INSUFFICIENT
    3-4: LOW
    5-9: MEDIUM
    10+: HIGH
    """
    if sample_count < 3:
        return ConfidenceLevel.INSUFFICIENT
    elif sample_count < 5:
        return ConfidenceLevel.LOW
    elif sample_count < 10:
        return ConfidenceLevel.MEDIUM
    else:
        return ConfidenceLevel.HIGH


class HistoricalEffortBenchmarkEngine:
    """Computes statistical effort benchmarks across 11 segmentation hierarchies."""

    @classmethod
    def compute_all_benchmarks(
        cls,
        task_records: List[Dict[str, Any]],
        account_id: Optional[str] = None,
        role_category: Optional[str] = None,
        team_group: Optional[str] = None,
    ) -> List[HistoricalEffortBenchmark]:
        """Compute segmented benchmarks from a list of historical completed task records.
        
        Args:
            task_records: List of dicts with:
                - hours (logged effort in hours > 0)
                - account_id
                - role_category
                - task_nature
                - issue_type
                - complexity_score
                - team_group
        """
        benchmarks: List[HistoricalEffortBenchmark] = []
        raw_list = task_records or []

        # Filter to completed tasks with positive logged hours
        valid_records = [r for r in raw_list if float(r.get("hours") or 0.0) > 0]

        # Grouping buckets for each of the 11 hierarchies
        emp_records = [r for r in valid_records if r.get("account_id") == account_id] if account_id else valid_records
        role_records = [r for r in valid_records if r.get("role_category") == role_category] if role_category else valid_records
        role_label = role_category or "all_roles"
        team_label = team_group or "all_team"
        acc_label = account_id or "all_employees"

        # 1. Tier 1: Employee Overall
        benchmarks.append(cls._build_benchmark(
            tier="1_employee_overall",
            segment_type="employee",
            segment_key=acc_label,
            hours=[r["hours"] for r in emp_records],
        ))

        # 2. Tier 2: Employee + Task Nature
        by_nature = defaultdict(list)
        for r in emp_records:
            by_nature[r.get("task_nature", TaskNature.UNKNOWN.value)].append(r["hours"])
        if not by_nature:
            by_nature[TaskNature.DEVELOPMENT.value] = []
        for nat, hrs in by_nature.items():
            benchmarks.append(cls._build_benchmark(
                tier="2_employee_task_nature",
                segment_type="employee_task_nature",
                segment_key=f"{acc_label}:{nat}",
                hours=hrs,
            ))

        # 3. Tier 3: Employee + Issue Type
        by_type = defaultdict(list)
        for r in emp_records:
            by_type[r.get("issue_type", "Task")].append(r["hours"])
        if not by_type:
            by_type["Task"] = []
        for itype, hrs in by_type.items():
            benchmarks.append(cls._build_benchmark(
                tier="3_employee_issue_type",
                segment_type="employee_issue_type",
                segment_key=f"{acc_label}:{itype}",
                hours=hrs,
            ))

        # 4. Tier 4: Employee + Complexity
        by_comp = defaultdict(list)
        for r in emp_records:
            by_comp[str(r.get("complexity_score", 3))].append(r["hours"])
        if not by_comp:
            by_comp["3"] = []
        for comp, hrs in by_comp.items():
            benchmarks.append(cls._build_benchmark(
                tier="4_employee_complexity",
                segment_type="employee_complexity",
                segment_key=f"{acc_label}:{comp}",
                hours=hrs,
            ))

        # 5. Tier 5: Employee + Issue Type + Complexity
        by_type_comp = defaultdict(list)
        for r in emp_records:
            by_type_comp[f"{r.get('issue_type', 'Task')}:{r.get('complexity_score', 3)}"].append(r["hours"])
        if not by_type_comp:
            by_type_comp["Task:3"] = []
        for key, hrs in by_type_comp.items():
            benchmarks.append(cls._build_benchmark(
                tier="5_employee_issue_type_complexity",
                segment_type="employee_issue_type_complexity",
                segment_key=f"{acc_label}:{key}",
                hours=hrs,
            ))

        # 6. Tier 6: Role + Task Nature
        by_role_nature = defaultdict(list)
        for r in role_records:
            by_role_nature[r.get("task_nature", TaskNature.UNKNOWN.value)].append(r["hours"])
        if not by_role_nature:
            by_role_nature[TaskNature.DEVELOPMENT.value] = []
        for nat, hrs in by_role_nature.items():
            benchmarks.append(cls._build_benchmark(
                tier="6_role_task_nature",
                segment_type="role_task_nature",
                segment_key=f"{role_label}:{nat}",
                hours=hrs,
            ))

        # 7. Tier 7: Role + Issue Type
        by_role_type = defaultdict(list)
        for r in role_records:
            by_role_type[r.get("issue_type", "Task")].append(r["hours"])
        if not by_role_type:
            by_role_type["Task"] = []
        for itype, hrs in by_role_type.items():
            benchmarks.append(cls._build_benchmark(
                tier="7_role_issue_type",
                segment_type="role_issue_type",
                segment_key=f"{role_label}:{itype}",
                hours=hrs,
            ))

        # 8. Tier 8: Role + Complexity
        by_role_comp = defaultdict(list)
        for r in role_records:
            by_role_comp[str(r.get("complexity_score", 3))].append(r["hours"])
        if not by_role_comp:
            by_role_comp["3"] = []
        for comp, hrs in by_role_comp.items():
            benchmarks.append(cls._build_benchmark(
                tier="8_role_complexity",
                segment_type="role_complexity",
                segment_key=f"{role_label}:{comp}",
                hours=hrs,
            ))

        # 9. Tier 9: Team + Task Nature
        by_team_nature = defaultdict(list)
        for r in valid_records:
            by_team_nature[r.get("task_nature", TaskNature.UNKNOWN.value)].append(r["hours"])
        if not by_team_nature:
            by_team_nature[TaskNature.DEVELOPMENT.value] = []
        for nat, hrs in by_team_nature.items():
            benchmarks.append(cls._build_benchmark(
                tier="9_team_task_nature",
                segment_type="team_task_nature",
                segment_key=f"{team_label}:{nat}",
                hours=hrs,
            ))

        # 10. Tier 10: Team + Issue Type
        by_team_type = defaultdict(list)
        for r in valid_records:
            by_team_type[r.get("issue_type", "Task")].append(r["hours"])
        if not by_team_type:
            by_team_type["Task"] = []
        for itype, hrs in by_team_type.items():
            benchmarks.append(cls._build_benchmark(
                tier="10_team_issue_type",
                segment_type="team_issue_type",
                segment_key=f"{team_label}:{itype}",
                hours=hrs,
            ))

        # 11. Tier 11: Team + Complexity
        by_team_comp = defaultdict(list)
        for r in valid_records:
            by_team_comp[str(r.get("complexity_score", 3))].append(r["hours"])
        if not by_team_comp:
            by_team_comp["3"] = []
        for comp, hrs in by_team_comp.items():
            benchmarks.append(cls._build_benchmark(
                tier="11_team_complexity",
                segment_type="team_complexity",
                segment_key=f"{team_label}:{comp}",
                hours=hrs,
            ))

        return benchmarks

    @classmethod
    def _build_benchmark(
        cls,
        tier: str,
        segment_type: str,
        segment_key: str,
        hours: List[float],
    ) -> HistoricalEffortBenchmark:
        """Construct a single HistoricalEffortBenchmark object."""
        count = len(hours)
        if count == 0:
            return HistoricalEffortBenchmark(
                segmentation_tier=tier,
                segment_type=segment_type,
                segment_key=segment_key,
                sample_count=0,
                confidence=ConfidenceLevel.INSUFFICIENT,
                is_fallback=True,
            )

        mean_h, med_h, p25_h, p75_h, min_h, max_h = calculate_percentiles(hours)
        std_h = calculate_stddev(hours, mean_h)
        conf = get_sample_confidence(count)

        return HistoricalEffortBenchmark(
            segmentation_tier=tier,
            segment_type=segment_type,
            segment_key=segment_key,
            sample_count=count,
            mean_hours=mean_h,
            median_hours=med_h,
            p25_hours=p25_h,
            p75_hours=p75_h,
            min_hours=min_h,
            max_hours=max_h,
            stddev_hours=std_h,
            confidence=conf,
            is_fallback=False,
        )

    @classmethod
    def _calculate_stats(
        cls,
        tier: str,
        segment_type: str,
        segment_key: str,
        hours: List[float],
    ) -> HistoricalEffortBenchmark:
        """Alias for _build_benchmark."""
        return cls._build_benchmark(tier, segment_type, segment_key, hours)

    @classmethod
    def compute_benchmarks(
        cls,
        account_id: Optional[str] = None,
        all_issues: Optional[List[Dict[str, Any]]] = None,
        task_records: Optional[List[Dict[str, Any]]] = None,
        role_category: Optional[str] = None,
        team_group: Optional[str] = None,
    ) -> List[HistoricalEffortBenchmark]:
        """Unified entrypoint for benchmark computation."""
        raw_list = task_records if task_records is not None else (all_issues or [])
        normalized_records: List[Dict[str, Any]] = []
        for r in raw_list:
            d = dict(r) if not isinstance(r, dict) else r
            hrs = float(d.get("hours") or d.get("logged_hours") or (float(d.get("time_spent_seconds") or 0) / 3600.0) or 0.0)
            if hrs > 0:
                normalized_records.append({
                    "hours": hrs,
                    "account_id": d.get("account_id") or d.get("assignee_account_id"),
                    "role_category": d.get("role_category"),
                    "task_nature": d.get("task_nature", TaskNature.UNKNOWN.value),
                    "issue_type": d.get("issue_type", "Task"),
                    "complexity_score": d.get("complexity_score", 3),
                    "team_group": d.get("team_group"),
                })
        return cls.compute_all_benchmarks(
            task_records=normalized_records,
            account_id=account_id,
            role_category=role_category,
            team_group=team_group,
        )
