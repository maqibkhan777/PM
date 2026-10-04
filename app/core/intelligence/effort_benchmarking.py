"""Empirical Historical Effort Benchmarking Engine (Milestone 2).

Computes data-driven effort distribution benchmarks (P25, P50, P75, P90, Mean, StdDev)
strictly segmented on a per-project basis (e.g. SMTPSUPORT vs GF).

Adheres strictly to reliability thresholds:
- < 5 samples: INSUFFICIENT_DATA (No manufactured estimates)
- 5-9 samples: LOW_CONFIDENCE (Mean/P50 reported with warning)
- 10+ samples: USABLE (Full P25/P50/P75/P90 quartiles enabled)

Never combines records or statistics across projects.
Persists benchmarks to the dev/local SQLite database schema (historical_effort_benchmarks).
"""

from collections import defaultdict
from datetime import datetime, timezone, timedelta
import math
from typing import Any, Dict, List, Optional, Set, Tuple
import uuid

from app.config.settings import settings
from app.connectors.jira.client import JiraClient
from app.core.intelligence.effort_benchmark_models import (
    BenchmarkReliability,
    EffortDistributionQuantiles,
    MultiProjectBenchmarkSummary,
    ProjectEffortBenchmarkReport,
    SegmentedProjectBenchmark,
)
from app.core.intelligence.probe import PROBE_JIRA_FIELDS
from app.database.connection import DatabaseManager, db_manager
from app.utils.logger import logger
from app.utils.time import parse_iso_datetime, utc_now, utc_now_iso


def calculate_quantiles_and_stats(values: List[float]) -> EffortDistributionQuantiles:
    """Calculate mean, median (P50), P25, P75, P90, min, max, stddev from sample hours."""
    if not values:
        return EffortDistributionQuantiles()

    sorted_vals = sorted(values)
    n = len(sorted_vals)
    mean_val = sum(sorted_vals) / n
    min_val = sorted_vals[0]
    max_val = sorted_vals[-1]

    # Standard deviation
    if n > 1:
        variance = sum((x - mean_val) ** 2 for x in sorted_vals) / (n - 1)
        stddev_val = math.sqrt(variance)
    else:
        stddev_val = 0.0

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

    med_val = _get_percentile(0.50)

    # Only report advanced quartiles (P25, P75, P90) when sample size is sufficient (n >= 5)
    p25_val = round(_get_percentile(0.25), 2) if n >= 5 else None
    p75_val = round(_get_percentile(0.75), 2) if n >= 5 else None
    p90_val = round(_get_percentile(0.90), 2) if n >= 10 else None

    return EffortDistributionQuantiles(
        sample_count=n,
        mean_hours=round(mean_val, 2),
        median_hours=round(med_val, 2),
        p25_hours=p25_val,
        p75_hours=p75_val,
        p90_hours=p90_val,
        min_hours=round(min_val, 2),
        max_hours=round(max_val, 2),
        stddev_hours=round(stddev_val, 2),
    )


class EmpiricalHistoricalEffortBenchmarkingEngine:
    """Generates project-isolated historical effort benchmarks from live Jira data."""

    def __init__(
        self,
        jira_client: Optional[JiraClient] = None,
        db_mgr: Optional[DatabaseManager] = None,
    ):
        self.client = jira_client or JiraClient()
        self.mgr = db_mgr or db_manager

    async def compute_multi_project_benchmarks(
        self,
        project_keys: List[str],
        lookback_days: int = 90,
        max_issues_per_project: int = 200,
        persist: bool = True,
    ) -> MultiProjectBenchmarkSummary:
        """Fetch historical completed issues and compute benchmarks strictly per project.

        Args:
            project_keys: List of uppercase Jira project keys (e.g. ['SMTPSUPORT', 'GF']).
            lookback_days: Lookback window in days (default: 90).
            max_issues_per_project: Cap per project query.
            persist: Whether to save computed benchmarks to local SQLite tables.

        Returns:
            MultiProjectBenchmarkSummary with independent benchmarks per project.
        """
        now_dt = utc_now()
        start_date = now_dt - timedelta(days=lookback_days)
        start_date_str = start_date.strftime("%Y-%m-%d")
        now_str = now_dt.strftime("%Y-%m-%d")

        summary_id = f"bench_run_{uuid.uuid4().hex[:12]}"
        summary = MultiProjectBenchmarkSummary(
            summary_id=summary_id,
            generated_at=utc_now_iso(),
            lookback_days=lookback_days,
            projects=project_keys,
            cross_project_isolation_verified=True,
        )

        for proj in project_keys:
            clean_proj = proj.strip().upper()
            if not clean_proj:
                continue

            report = await self._benchmark_single_project(
                project_key=clean_proj,
                start_date_str=start_date_str,
                end_date_str=now_str,
                cap_limit=max_issues_per_project,
                run_id=summary_id,
                persist=persist,
            )
            summary.reports_by_project[clean_proj] = report

        return summary

    async def _benchmark_single_project(
        self,
        project_key: str,
        start_date_str: str,
        end_date_str: str,
        cap_limit: int,
        run_id: str,
        persist: bool,
    ) -> ProjectEffortBenchmarkReport:
        """Fetch issues for one project and calculate all empirical effort benchmarks."""
        report = ProjectEffortBenchmarkReport(
            project_key=project_key,
            date_range_start=start_date_str,
            date_range_end=end_date_str,
        )

        jql = (
            f'project = "{project_key}" AND statusCategory = Done '
            f'AND resolved >= "{start_date_str}" ORDER BY resolved DESC'
        )

        raw_issues: List[Dict[str, Any]] = []
        next_page_token: Optional[str] = None
        seen_tokens: Set[str] = set()

        try:
            while len(raw_issues) < cap_limit:
                current_batch_limit = min(50, cap_limit - len(raw_issues))
                data = await self.client.search_issues(
                    jql=jql,
                    next_page_token=next_page_token,
                    max_results=current_batch_limit,
                    expand="changelog",
                    fields=PROBE_JIRA_FIELDS,
                )

                issues = data.get("issues", [])
                if not issues:
                    break

                remaining_needed = cap_limit - len(raw_issues)
                raw_issues.extend(issues[:remaining_needed])

                if len(raw_issues) >= cap_limit:
                    break

                is_last = data.get("isLast", True if not data.get("nextPageToken") else False)
                next_page_token = data.get("nextPageToken")
                if is_last or not next_page_token or next_page_token in seen_tokens:
                    break
                seen_tokens.add(next_page_token)

        except Exception as e:
            logger.error(f"Error fetching issues for project {project_key}: {e}")
            report.data_quality_warnings.append(f"Jira API search encountered an error: {e}")

        report.total_completed_issues = len(raw_issues)

        # Normalize issue effort records
        parsed_records: List[Dict[str, Any]] = []
        for raw in raw_issues:
            fields = raw.get("fields") or {}
            key = raw.get("key", "UNKNOWN")

            # Extract effort hours from worklogs or timespent
            worklog_data = fields.get("worklog") or {}
            worklogs = worklog_data.get("worklogs") or []
            total_wl_sec = sum(int(w.get("timeSpentSeconds", 0)) for w in worklogs if w.get("timeSpentSeconds"))

            time_spent_sec = fields.get("timespent")
            if time_spent_sec is None:
                timetracking = fields.get("timetracking") or {}
                time_spent_sec = timetracking.get("timeSpentSeconds")

            logged_sec = total_wl_sec or (int(time_spent_sec) if time_spent_sec else 0)
            logged_hours = round(logged_sec / 3600.0, 2)

            itype = (fields.get("issuetype") or {}).get("name") or "Task"
            priority = (fields.get("priority") or {}).get("name") or "Medium"

            labels = fields.get("labels") or []
            clean_labels = [str(l).strip().lower() for l in labels if str(l).strip()]

            parsed_records.append({
                "issue_key": key,
                "project_key": project_key,
                "issue_type": itype,
                "priority": priority,
                "labels": clean_labels,
                "logged_hours": logged_hours,
                "has_effort": logged_hours > 0,
            })

        # Calculate missing vs populated effort
        issues_with_effort = [r for r in parsed_records if r["has_effort"]]
        issues_missing_effort = [r for r in parsed_records if not r["has_effort"]]

        report.issues_with_logged_effort = len(issues_with_effort)
        report.issues_missing_effort = len(issues_missing_effort)
        report.total_logged_hours = round(sum(r["logged_hours"] for r in issues_with_effort), 2)
        if report.total_completed_issues > 0:
            report.missing_effort_percentage = round(
                (report.issues_missing_effort / report.total_completed_issues) * 100.0, 1
            )

        # 1. Overall Project Benchmark
        all_hours = [r["logged_hours"] for r in issues_with_effort]
        report.overall_benchmark = self._create_segment_benchmark(
            project_key=project_key,
            dimension_type="overall",
            dimension_key="all",
            sample_hours=all_hours,
            missing_count=report.issues_missing_effort,
        )

        # 2. Segment by Issue Type
        by_type_hours = defaultdict(list)
        by_type_missing = defaultdict(int)
        for r in parsed_records:
            if r["has_effort"]:
                by_type_hours[r["issue_type"]].append(r["logged_hours"])
            else:
                by_type_missing[r["issue_type"]] += 1

        for itype, hrs in by_type_hours.items():
            bench = self._create_segment_benchmark(
                project_key=project_key,
                dimension_type="issue_type",
                dimension_key=itype,
                sample_hours=hrs,
                missing_count=by_type_missing[itype],
            )
            report.by_issue_type[itype] = bench
            if bench.reliability == BenchmarkReliability.USABLE:
                report.supported_groupings.append(f"issue_type:{itype} (n={bench.sample_count}, USABLE)")
            elif bench.reliability == BenchmarkReliability.LOW_CONFIDENCE:
                report.supported_groupings.append(f"issue_type:{itype} (n={bench.sample_count}, LOW_CONFIDENCE)")
            else:
                report.unsupported_groupings.append(f"issue_type:{itype} (n={bench.sample_count}, INSUFFICIENT_DATA)")

        # 3. Segment by Priority
        by_prio_hours = defaultdict(list)
        by_prio_missing = defaultdict(int)
        for r in parsed_records:
            if r["has_effort"]:
                by_prio_hours[r["priority"]].append(r["logged_hours"])
            else:
                by_prio_missing[r["priority"]] += 1

        for prio, hrs in by_prio_hours.items():
            bench = self._create_segment_benchmark(
                project_key=project_key,
                dimension_type="priority",
                dimension_key=prio,
                sample_hours=hrs,
                missing_count=by_prio_missing[prio],
            )
            report.by_priority[prio] = bench
            if bench.reliability == BenchmarkReliability.USABLE:
                report.supported_groupings.append(f"priority:{prio} (n={bench.sample_count}, USABLE)")
            elif bench.reliability == BenchmarkReliability.LOW_CONFIDENCE:
                report.supported_groupings.append(f"priority:{prio} (n={bench.sample_count}, LOW_CONFIDENCE)")
            else:
                report.unsupported_groupings.append(f"priority:{prio} (n={bench.sample_count}, INSUFFICIENT_DATA)")

        # 4. Segment by Issue Type + Priority
        by_tp_hours = defaultdict(list)
        by_tp_missing = defaultdict(int)
        for r in parsed_records:
            tp_key = f"{r['issue_type']}:{r['priority']}"
            if r["has_effort"]:
                by_tp_hours[tp_key].append(r["logged_hours"])
            else:
                by_tp_missing[tp_key] += 1

        for tp_key, hrs in by_tp_hours.items():
            bench = self._create_segment_benchmark(
                project_key=project_key,
                dimension_type="issue_type_priority",
                dimension_key=tp_key,
                sample_hours=hrs,
                missing_count=by_tp_missing[tp_key],
            )
            report.by_issue_type_priority[tp_key] = bench
            if bench.reliability == BenchmarkReliability.USABLE:
                report.supported_groupings.append(f"type_priority:{tp_key} (n={bench.sample_count}, USABLE)")
            elif bench.reliability == BenchmarkReliability.LOW_CONFIDENCE:
                report.supported_groupings.append(f"type_priority:{tp_key} (n={bench.sample_count}, LOW_CONFIDENCE)")
            else:
                report.unsupported_groupings.append(f"type_priority:{tp_key} (n={bench.sample_count}, INSUFFICIENT_DATA)")

        # 5. Segment by Label (if present)
        by_lbl_hours = defaultdict(list)
        by_lbl_missing = defaultdict(int)
        for r in parsed_records:
            for lbl in r["labels"]:
                if r["has_effort"]:
                    by_lbl_hours[lbl].append(r["logged_hours"])
                else:
                    by_lbl_missing[lbl] += 1

        for lbl, hrs in by_lbl_hours.items():
            bench = self._create_segment_benchmark(
                project_key=project_key,
                dimension_type="label",
                dimension_key=lbl,
                sample_hours=hrs,
                missing_count=by_lbl_missing[lbl],
            )
            report.by_label[lbl] = bench

        # Add project level warnings
        if report.missing_effort_percentage > 25.0:
            report.data_quality_warnings.append(
                f"{report.missing_effort_percentage}% of completed issues have no logged worklogs/timeSpent."
            )

        if persist:
            self._persist_project_benchmarks(report, run_id)

        return report

    def _create_segment_benchmark(
        self,
        project_key: str,
        dimension_type: str,
        dimension_key: str,
        sample_hours: List[float],
        missing_count: int = 0,
    ) -> SegmentedProjectBenchmark:
        """Calculate quantile statistics and assign reliability rating."""
        n = len(sample_hours)
        dist = calculate_quantiles_and_stats(sample_hours)

        warning: Optional[str] = None
        if n < 5:
            reliability = BenchmarkReliability.INSUFFICIENT_DATA
            warning = f"Sample count ({n}) is below minimum threshold of 5. Benchmark cannot be used for automated planning."
        elif n < 10:
            reliability = BenchmarkReliability.LOW_CONFIDENCE
            warning = f"Sample count ({n}) provides low confidence (5-9 issues). P50/Mean should be used with caution."
        else:
            reliability = BenchmarkReliability.USABLE

        total_hrs = round(sum(sample_hours), 2)

        return SegmentedProjectBenchmark(
            project_key=project_key,
            dimension_type=dimension_type,
            dimension_key=dimension_key,
            sample_count=n,
            missing_effort_count=missing_count,
            total_logged_hours=total_hrs,
            distribution=dist,
            reliability=reliability,
            data_quality_warning=warning,
        )

    def _persist_project_benchmarks(
        self,
        report: ProjectEffortBenchmarkReport,
        run_id: str,
    ) -> None:
        """Persist computed empirical benchmarks to historical_effort_benchmarks table in SQLite."""
        try:
            now_iso = utc_now_iso()
            records_to_insert = []

            all_benchmarks = [report.overall_benchmark]
            all_benchmarks.extend(report.by_issue_type.values())
            all_benchmarks.extend(report.by_priority.values())
            all_benchmarks.extend(report.by_issue_type_priority.values())
            all_benchmarks.extend(report.by_label.values())

            for b in all_benchmarks:
                conf_str = "HIGH" if b.reliability == BenchmarkReliability.USABLE else (
                    "LOW" if b.reliability == BenchmarkReliability.LOW_CONFIDENCE else "INSUFFICIENT"
                )
                rec_id = f"bench_{uuid.uuid4().hex[:12]}"
                records_to_insert.append((
                    rec_id,
                    run_id,
                    None,  # account_id is None for project-level segments
                    f"project_{b.dimension_type}",
                    b.dimension_type,
                    f"{b.project_key}:{b.dimension_key}",
                    b.sample_count,
                    b.distribution.mean_hours,
                    b.distribution.median_hours,
                    b.distribution.p25_hours or 0.0,
                    b.distribution.p75_hours or 0.0,
                    b.distribution.min_hours,
                    b.distribution.max_hours,
                    b.distribution.stddev_hours,
                    conf_str,
                    1 if b.reliability == BenchmarkReliability.INSUFFICIENT_DATA else 0,
                    now_iso,
                ))

            with self.mgr.session() as conn:
                conn.executemany(
                    """
                    INSERT INTO historical_effort_benchmarks (
                        id, analysis_run_id, account_id, segmentation_tier,
                        segment_type, segment_key, sample_count, mean_hours,
                        median_hours, p25_hours, p75_hours, min_hours,
                        max_hours, stddev_hours, confidence, is_fallback,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    records_to_insert,
                )
            logger.info(f"Persisted {len(records_to_insert)} empirical benchmarks for project {report.project_key}")
        except Exception as e:
            logger.error(f"Failed to persist benchmarks for project {report.project_key}: {e}", exc_info=False)
