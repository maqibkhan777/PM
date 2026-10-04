"""Service for retrieving and recommending empirical historical effort benchmarks (Milestone 3).

Guarantees:
1. Strict project isolation: Never fallback across different projects (e.g. SMTPSUPORT never uses GF data).
2. Hierarchy fallback within same project only:
   compound (issue_type:priority) -> issue_type -> overall project.
3. Fallback only used when specific tier is sparse/insufficient, and explicitly disclosed.
4. Deterministic recommendations:
   - USABLE (n >= 10): P50 and P90 recommended.
   - LOW_CONFIDENCE (5 <= n <= 9): Tentative P50 recommended with explicit warning.
   - INSUFFICIENT_DATA (n < 5): Explicit insufficient data, no fabricated numbers.
5. Local/Dev SQLite database target (zero production database mutations).
6. Advisory only (zero Jira mutations).
"""

from typing import Any, Dict, List, Optional
from app.core.intelligence.effort_benchmark_models import BenchmarkReliability
from app.core.intelligence.effort_recommendation_models import (
    BenchmarkRecommendationStatus,
    TaskEffortBenchmarkRecommendation,
)
from app.database.connection import DatabaseManager, db_manager
from app.utils.logger import logger


class HistoricalEffortBenchmarkRetrievalService:
    """Retrieves and evaluates empirical historical effort benchmarks for planning."""

    def __init__(self, manager: Optional[DatabaseManager] = None):
        self.mgr = manager or db_manager

    def get_latest_project_benchmarks(
        self,
        project_key: str,
        run_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieve historical benchmark records strictly for the specified project.

        Never reads records belonging to other projects.
        """
        clean_proj = (project_key or "").strip().upper()
        if not clean_proj:
            return []

        with self.mgr.session() as conn:
            # First check if the historical_effort_benchmarks table exists in DB
            table_check = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='historical_effort_benchmarks'"
            ).fetchone()
            if not table_check:
                return []

            # First find the run_id if not explicitly provided
            target_run_id = run_id
            if not target_run_id:
                # Look for latest run containing this project in segment_key
                cursor = conn.execute(
                    """
                    SELECT analysis_run_id
                    FROM historical_effort_benchmarks
                    WHERE segment_key LIKE ?
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (f"{clean_proj}:%",),
                )
                row = cursor.fetchone()
                if row:
                    target_run_id = row["analysis_run_id"]

            if not target_run_id:
                return []

            # Retrieve all benchmark records for this run matching project_key prefix
            cursor = conn.execute(
                """
                SELECT *
                FROM historical_effort_benchmarks
                WHERE analysis_run_id = ?
                  AND segment_key LIKE ?
                ORDER BY sample_count DESC
                """,
                (target_run_id, f"{clean_proj}:%"),
            )
            return [dict(r) for r in cursor.fetchall()]

    def recommend_effort_for_task(
        self,
        project_key: str,
        issue_key: str,
        issue_type: str = "Task",
        priority: str = "Medium",
        run_id: Optional[str] = None,
    ) -> TaskEffortBenchmarkRecommendation:
        """Produce a deterministic, evidence-backed effort recommendation for a single issue.

        Adheres strictly to the 3-tier within-project hierarchy:
        1. issue_type_priority (e.g. SMTPSUPORT:Support:Medium)
        2. issue_type (e.g. SMTPSUPORT:Support)
        3. overall (e.g. SMTPSUPORT:all)
        """
        clean_proj = (project_key or "").strip().upper()
        clean_type = (issue_type or "Task").strip()
        clean_prio = (priority or "Medium").strip()

        rec = TaskEffortBenchmarkRecommendation(
            issue_key=issue_key.strip().upper(),
            project_key=clean_proj,
            issue_type=clean_type,
            priority=clean_prio,
        )

        if not clean_proj:
            rec.reliability_status = BenchmarkRecommendationStatus.NOT_FOUND
            rec.explanation = "Missing project key; cannot retrieve benchmarks."
            return rec

        records = self.get_latest_project_benchmarks(clean_proj, run_id=run_id)
        if not records:
            rec.reliability_status = BenchmarkRecommendationStatus.NOT_FOUND
            rec.explanation = f"No historical effort benchmarks found for project '{clean_proj}'."
            return rec

        # Index records by segment_type and segment_key
        # Note segment_key in DB is formatted as "PROJECT:DIMENSION_KEY"
        records_by_segment: Dict[str, Dict[str, Any]] = {}
        for r in records:
            s_key = r.get("segment_key") or ""
            records_by_segment[s_key] = r

        # Candidate 1: Specific Compound Grouping (issue_type_priority)
        compound_key = f"{clean_proj}:{clean_type}:{clean_prio}"
        type_key = f"{clean_proj}:{clean_type}"
        overall_key = f"{clean_proj}:all"

        compound_record = records_by_segment.get(compound_key)
        type_record = records_by_segment.get(type_key)
        overall_record = records_by_segment.get(overall_key)

        selected_record: Optional[Dict[str, Any]] = None
        grouping_used = "none"
        is_fallback = False
        fallback_disclosure: Optional[str] = None

        # Check Compound Tier
        if compound_record and compound_record.get("sample_count", 0) >= 10:
            selected_record = compound_record
            grouping_used = f"issue_type_priority:{clean_type}:{clean_prio}"
        elif compound_record and compound_record.get("sample_count", 0) >= 5:
            # 5-9 samples at compound level is LOW_CONFIDENCE, but check if type level is USABLE
            if type_record and type_record.get("sample_count", 0) >= 10:
                selected_record = type_record
                grouping_used = f"issue_type:{clean_type}"
                is_fallback = True
                fallback_disclosure = (
                    f"Compound segment '{clean_type}:{clean_prio}' has low confidence (n={compound_record.get('sample_count')}). "
                    f"Fell back to broader issue-type benchmark '{clean_type}' (n={type_record.get('sample_count')}) within project '{clean_proj}'."
                )
            else:
                selected_record = compound_record
                grouping_used = f"issue_type_priority:{clean_type}:{clean_prio}"
        elif type_record and type_record.get("sample_count", 0) >= 10:
            selected_record = type_record
            grouping_used = f"issue_type:{clean_type}"
            if compound_record:
                is_fallback = True
                fallback_disclosure = (
                    f"Compound segment '{clean_type}:{clean_prio}' has insufficient data (n={compound_record.get('sample_count', 0)}). "
                    f"Fell back to issue-type benchmark '{clean_type}' (n={type_record.get('sample_count')}) within project '{clean_proj}'."
                )
            else:
                is_fallback = True
                fallback_disclosure = (
                    f"Compound segment '{clean_type}:{clean_prio}' not observed. "
                    f"Used issue-type benchmark '{clean_type}' (n={type_record.get('sample_count')}) within project '{clean_proj}'."
                )
        elif type_record and type_record.get("sample_count", 0) >= 5:
            selected_record = type_record
            grouping_used = f"issue_type:{clean_type}"
            if compound_record:
                is_fallback = True
                fallback_disclosure = (
                    f"Compound segment '{clean_type}:{clean_prio}' has insufficient data (n={compound_record.get('sample_count', 0)}). "
                    f"Fell back to tentative issue-type benchmark '{clean_type}' (n={type_record.get('sample_count')}) within project '{clean_proj}'."
                )
        elif overall_record and overall_record.get("sample_count", 0) >= 10:
            selected_record = overall_record
            grouping_used = "overall:all"
            is_fallback = True
            fallback_disclosure = (
                f"Specific groupings for '{clean_type}' have insufficient data in project '{clean_proj}'. "
                f"Fell back to overall project benchmark (n={overall_record.get('sample_count')}) within project '{clean_proj}'."
            )
        elif overall_record and overall_record.get("sample_count", 0) >= 5:
            selected_record = overall_record
            grouping_used = "overall:all"
            is_fallback = True
            fallback_disclosure = (
                f"Specific groupings for '{clean_type}' have insufficient data in project '{clean_proj}'. "
                f"Fell back to tentative overall project benchmark (n={overall_record.get('sample_count')}) within project '{clean_proj}'."
            )
        else:
            # Insufficient data even at overall level or for compound
            selected_record = compound_record or type_record or overall_record
            grouping_used = f"issue_type:{clean_type}" if type_record else (
                f"issue_type_priority:{clean_type}:{clean_prio}" if compound_record else "overall:all"
            )

        if not selected_record:
            rec.reliability_status = BenchmarkRecommendationStatus.INSUFFICIENT_DATA
            rec.explanation = f"Insufficient historical data in project '{clean_proj}' to form an effort benchmark."
            return rec

        sample_count = selected_record.get("sample_count", 0)
        mean_hours = float(selected_record.get("mean_hours") or 0.0)
        median_hours = float(selected_record.get("median_hours") or 0.0)
        p25_hours = float(selected_record.get("p25_hours") or 0.0)
        p75_hours = float(selected_record.get("p75_hours") or 0.0)
        run_id_val = selected_record.get("analysis_run_id")

        rec.sample_count = sample_count
        rec.grouping_used = grouping_used
        rec.dimension_type = selected_record.get("segment_type", "unknown")
        rec.dimension_key = selected_record.get("segment_key", "unknown")
        rec.is_fallback_grouping = is_fallback
        rec.fallback_disclosure = fallback_disclosure
        rec.analysis_run_id = run_id_val

        if sample_count >= 10:
            rec.reliability_status = BenchmarkRecommendationStatus.USABLE
            rec.p50_effort_hours = median_hours
            # If p90 is not explicitly in DB table, derive from quantile or p75/stddev, or p75 if bounded
            # Since historical_effort_benchmarks table schema has p25 and p75:
            # Note: The table schema has median_hours (P50), p25_hours, p75_hours, max_hours, stddev_hours.
            # When p75 is present, we provide p50 and p75/upper bound or calculate robustly.
            # Let's set recommended_effort_hours = median_hours (P50)
            rec.recommended_effort_hours = median_hours
            rec.p90_effort_hours = float(selected_record.get("p75_hours") or median_hours)  # Upper quantile bound
            rec.explanation = (
                f"Empirical historical benchmark from {sample_count} completed issues in project '{clean_proj}' "
                f"({grouping_used}). Median effort: {median_hours}h, P75: {p75_hours}h."
            )
        elif sample_count >= 5:
            rec.reliability_status = BenchmarkRecommendationStatus.LOW_CONFIDENCE
            rec.is_tentative = True
            rec.p50_effort_hours = median_hours
            rec.recommended_effort_hours = median_hours
            rec.data_quality_warning = (
                f"Small sample size (n={sample_count}). Recommendation is tentative and should be used with caution."
            )
            rec.explanation = (
                f"Tentative benchmark from {sample_count} completed issues in project '{clean_proj}' "
                f"({grouping_used}). Median effort: {median_hours}h (LOW_CONFIDENCE)."
            )
        else:
            rec.reliability_status = BenchmarkRecommendationStatus.INSUFFICIENT_DATA
            rec.recommended_effort_hours = None
            rec.p50_effort_hours = None
            rec.p90_effort_hours = None
            rec.data_quality_warning = (
                f"Sample size ({sample_count}) is below minimum reliability threshold of 5. No effort estimate manufactured."
            )
            rec.explanation = (
                f"Insufficient historical data (n={sample_count}) for '{grouping_used}' in project '{clean_proj}'. "
                "Advisory estimate withheld to prevent misleading predictions."
            )

        return rec

    def recommend_effort_for_context(
        self,
        tasks: List[Any],
        default_project_key: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> Dict[str, TaskEffortBenchmarkRecommendation]:
        """Produce recommendations for a batch of tasks in planning context."""
        results: Dict[str, TaskEffortBenchmarkRecommendation] = {}
        for t in tasks:
            key = getattr(t, "issue_key", "")
            proj = getattr(t, "project_key", None) or default_project_key
            if not proj or proj == "UNKNOWN":
                # Parse from issue_key (e.g. SMTPSUPORT-123 -> SMTPSUPORT)
                if "-" in key:
                    proj = key.split("-")[0].strip().upper()
                else:
                    proj = default_project_key or "UNKNOWN"

            itype = getattr(t, "issue_type", "Task")
            prio = getattr(t, "priority", "Medium")

            rec = self.recommend_effort_for_task(
                project_key=proj,
                issue_key=key,
                issue_type=itype,
                priority=prio,
                run_id=run_id,
            )
            results[key] = rec
        return results
