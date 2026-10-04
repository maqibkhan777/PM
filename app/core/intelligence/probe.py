"""Historical Jira Data Quality Diagnostic Probe Service.

Read-only, configurable diagnostic tool for evaluating whether historical Jira data
contains sufficient quality and volume to support estimation, planning, and capacity modeling.

Strictly non-mutating: No Jira updates, no DB writes, no credentials logged.
Supports multiple configurable projects with strict per-project metric separation.
"""

from datetime import datetime, timezone, timedelta
import statistics
from typing import Any, Dict, List, Optional, Set, Tuple
import uuid

from app.config.settings import settings
from app.connectors.jira.client import JiraClient
from app.core.intelligence.probe_models import (
    FeasibilityRecommendation,
    LifecycleVsEffortSummary,
    MetricCounter,
    MultiProjectProbeSummary,
    ProjectQualityReport,
    SingleIssueQualityRecord,
)
from app.utils.logger import logger
from app.utils.time import parse_iso_datetime, utc_now, utc_now_iso


# Fields requested for historical data quality audit
PROBE_JIRA_FIELDS = [
    "summary",
    "status",
    "issuetype",
    "priority",
    "assignee",
    "reporter",
    "creator",
    "created",
    "resolutiondate",
    "resolution",
    "updated",
    "duedate",
    "components",
    "labels",
    "timetracking",
    "timeoriginalestimate",
    "timespent",
    "worklog",
]


class JiraHistoricalDataQualityProbe:
    """Read-only diagnostic service for Jira historical data quality."""

    def __init__(self, jira_client: Optional[JiraClient] = None):
        self.client = jira_client or JiraClient()

    async def run_probe(
        self,
        project_keys: List[str],
        lookback_days: int = 90,
        max_issues_per_project: int = 200,
        batch_size: int = 50,
        expand_changelog: bool = True,
    ) -> MultiProjectProbeSummary:
        """Run data quality probe across one or more Jira projects.

        Args:
            project_keys: List of uppercase Jira project keys (e.g. ['SMTPSUPORT']).
            lookback_days: Number of days in the past to query resolved issues (default 90).
            max_issues_per_project: Maximum issues to retrieve and process per project.
            batch_size: JQL search batch size per pagination call (max 50).
            expand_changelog: Whether to request changelog expansion in search.

        Returns:
            MultiProjectProbeSummary with independent ProjectQualityReport per project.
        """
        now = utc_now()
        start_date = now - timedelta(days=lookback_days)
        start_date_str = start_date.strftime("%Y-%m-%d")
        now_str = now.strftime("%Y-%m-%d")

        probe_id = f"probe-{uuid.uuid4().hex[:8]}"
        summary = MultiProjectProbeSummary(
            probe_id=probe_id,
            timestamp=utc_now_iso(),
            project_keys=project_keys,
            lookback_days=lookback_days,
            cap_per_project=max_issues_per_project,
            summary_notes=[
                "Jira board IDs are NOT interchangeable with project keys. This probe queries project keys directly via JQL.",
                "Read-only execution: No Jira state mutated; no SQLite persistence written.",
            ],
        )

        for proj in project_keys:
            proj_clean = proj.strip().upper()
            if not proj_clean:
                continue

            report = await self._probe_single_project(
                project_key=proj_clean,
                start_date_str=start_date_str,
                end_date_str=now_str,
                days_evaluated=lookback_days,
                cap_limit=max_issues_per_project,
                batch_size=batch_size,
                expand_changelog=expand_changelog,
            )
            summary.project_reports[proj_clean] = report

        return summary

    async def _probe_single_project(
        self,
        project_key: str,
        start_date_str: str,
        end_date_str: str,
        days_evaluated: int,
        cap_limit: int,
        batch_size: int,
        expand_changelog: bool,
    ) -> ProjectQualityReport:
        """Probe historical issues for a single Jira project using cursor pagination."""
        report = ProjectQualityReport(
            project_key=project_key,
            date_range_start=start_date_str,
            date_range_end=end_date_str,
            days_evaluated=days_evaluated,
            cap_limit=cap_limit,
        )

        # JQL for completed issues in date window
        # JQL query uses resolved or statusCategory = Done
        jql = (
            f'project = "{project_key}" AND statusCategory = Done '
            f'AND resolved >= "{start_date_str}" ORDER BY resolved DESC'
        )

        next_page_token: Optional[str] = None
        seen_tokens: Set[str] = set()
        raw_issues: List[Dict[str, Any]] = []

        try:
            while len(raw_issues) < cap_limit:
                current_batch_limit = min(batch_size, cap_limit - len(raw_issues))
                data = await self.client.search_issues(
                    jql=jql,
                    next_page_token=next_page_token,
                    max_results=current_batch_limit,
                    expand="changelog" if expand_changelog else None,
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
            logger.error(f"Error querying Jira issues for project {project_key}: {e}", exc_info=False)
            report.partial_failure = True
            report.error_message = f"Jira API search failed: {type(e).__name__} - {str(e)}"
            report.api_errors.append(report.error_message)

        report.total_issues_retrieved = len(raw_issues)
        if report.total_issues_retrieved >= cap_limit:
            report.cap_reached = True

        # Process and evaluate retrieved issues
        records: List[SingleIssueQualityRecord] = []
        for raw_issue in raw_issues:
            rec = self._evaluate_issue(raw_issue, project_key)
            records.append(rec)

        self._aggregate_metrics(report, records)
        self._evaluate_feasibility(report)

        return report

    def _evaluate_issue(self, raw_issue: Dict[str, Any], project_key: str) -> SingleIssueQualityRecord:
        """Extract and categorize attributes for a single Jira issue."""
        key = raw_issue.get("key", "UNKNOWN")
        fields = raw_issue.get("fields") or {}

        rec = SingleIssueQualityRecord(issue_key=key, project_key=project_key)

        # 1. Timestamps (Created, Resolved, Updated)
        created_str = fields.get("created")
        resolved_str = fields.get("resolutiondate") or fields.get("resolved")
        updated_str = fields.get("updated")
        created_dt = parse_iso_datetime(created_str)
        resolved_dt = parse_iso_datetime(resolved_str)
        updated_dt = parse_iso_datetime(updated_str)

        if created_dt:
            rec.has_created = True
            rec.created_at = created_str

        if resolved_dt:
            rec.has_resolved = True
            rec.resolved_at = resolved_str

        if updated_dt:
            rec.has_updated = True
            rec.updated_at = updated_str

        if created_dt and resolved_dt:
            lifecycle_secs = (resolved_dt - created_dt).total_seconds()
            if lifecycle_secs >= 0:
                rec.elapsed_lifecycle_seconds = lifecycle_secs

        # 2. Categorization, Ownership & Hierarchy
        issue_type = fields.get("issuetype") or {}
        if issue_type.get("name"):
            rec.has_issue_type = True
            rec.issue_type_name = issue_type.get("name")

        priority = fields.get("priority") or {}
        if priority.get("name"):
            rec.has_priority = True
            rec.priority_name = priority.get("name")

        assignee = fields.get("assignee") or {}
        acc_id = assignee.get("accountId") or assignee.get("name")
        if acc_id:
            rec.has_assignee = True
            rec.assignee_account_id = acc_id

        components = fields.get("components") or []
        if isinstance(components, list) and len(components) > 0:
            rec.has_components = True
            rec.components_count = len(components)

        labels = fields.get("labels") or []
        if isinstance(labels, list) and len(labels) > 0:
            rec.has_labels = True
            rec.labels_count = len(labels)

        parent = fields.get("parent") or {}
        parent_key = parent.get("key") if isinstance(parent, dict) else None
        if not parent_key:
            # Custom epic link field fallback (customfield_10014 or epic)
            parent_key = fields.get("epic") or fields.get("customfield_10014")

        if parent_key:
            rec.has_parent_or_epic = True
            rec.parent_key = str(parent_key)

        # 3. Estimates (timeoriginalestimate / timetracking)
        timetracking = fields.get("timetracking") or {}
        orig_est = fields.get("timeoriginalestimate")
        if orig_est is None:
            orig_est = timetracking.get("originalEstimateSeconds")

        if orig_est is not None:
            try:
                val = int(orig_est)
                if val > 0:
                    rec.has_original_estimate = True
                    rec.original_estimate_seconds = val
                else:
                    rec.is_original_estimate_zero = True
                    rec.original_estimate_seconds = 0
            except (ValueError, TypeError):
                pass

        # 4. Time Spent (timespent / timetracking)
        time_spent = fields.get("timespent")
        if time_spent is None:
            time_spent = timetracking.get("timeSpentSeconds")

        if time_spent is not None:
            try:
                val = int(time_spent)
                if val > 0:
                    rec.has_time_spent = True
                    rec.time_spent_seconds = val
                else:
                    rec.is_time_spent_zero = True
                    rec.time_spent_seconds = 0
            except (ValueError, TypeError):
                pass

        # 5. Worklogs
        worklog_data = fields.get("worklog") or {}
        worklogs = worklog_data.get("worklogs") or []
        if isinstance(worklogs, list) and len(worklogs) > 0:
            rec.has_worklogs = True
            rec.worklog_count = len(worklogs)
            total_sec = sum(int(w.get("timeSpentSeconds", 0)) for w in worklogs if w.get("timeSpentSeconds"))
            rec.total_worklog_logged_seconds = total_sec

        # 6. Changelog Inspection (Status transitions, estimate changes, reopens)
        changelog = raw_issue.get("changelog") or {}
        histories = changelog.get("histories") or []
        if isinstance(histories, list) and len(histories) > 0:
            rec.has_changelog = True
            for history in histories:
                items = history.get("items") or []
                for item in items:
                    field_name = str(item.get("field", "")).lower()
                    if field_name == "status":
                        rec.status_transition_count += 1
                        from_str = str(item.get("fromString", "")).lower()
                        to_str = str(item.get("toString", "")).lower()
                        # Reopen detection
                        if from_str in ("done", "resolved", "closed") and to_str not in ("done", "resolved", "closed"):
                            rec.has_reopen_transitions = True
                    elif field_name in ("timeoriginalestimate", "original estimate", "timeestimate", "remaining estimate"):
                        rec.has_estimate_changes = True

        return rec

    def _aggregate_metrics(self, report: ProjectQualityReport, records: List[SingleIssueQualityRecord]) -> None:
        """Aggregate per-issue records into project-level metric counters."""
        n = report.total_issues_retrieved

        def make_counter(count: int) -> MetricCounter:
            pct = round((count / n) * 100.0, 1) if n > 0 else 0.0
            return MetricCounter(count=count, total=n, percentage=pct)

        created_cnt = sum(1 for r in records if r.has_created)
        resolved_cnt = sum(1 for r in records if r.has_resolved)
        updated_cnt = sum(1 for r in records if r.has_updated)
        lifecycle_cnt = sum(1 for r in records if r.elapsed_lifecycle_seconds is not None)

        report.valid_created_timestamp = make_counter(created_cnt)
        report.valid_resolved_timestamp = make_counter(resolved_cnt)
        report.valid_updated_timestamp = make_counter(updated_cnt)
        report.valid_lifecycle_pair = make_counter(lifecycle_cnt)

        report.has_issue_type = make_counter(sum(1 for r in records if r.has_issue_type))
        report.has_priority = make_counter(sum(1 for r in records if r.has_priority))
        report.has_assignee = make_counter(sum(1 for r in records if r.has_assignee))
        report.has_components = make_counter(sum(1 for r in records if r.has_components))
        report.has_labels = make_counter(sum(1 for r in records if r.has_labels))
        report.has_parent_or_epic = make_counter(sum(1 for r in records if r.has_parent_or_epic))


        orig_est_cnt = sum(1 for r in records if r.has_original_estimate)
        report.has_original_estimate = make_counter(orig_est_cnt)
        report.original_estimate_zero_count = sum(1 for r in records if r.is_original_estimate_zero)
        report.original_estimate_missing_count = n - (orig_est_cnt + report.original_estimate_zero_count)

        time_spent_cnt = sum(1 for r in records if r.has_time_spent)
        report.has_time_spent = make_counter(time_spent_cnt)
        report.time_spent_zero_count = sum(1 for r in records if r.is_time_spent_zero)
        report.time_spent_missing_count = n - (time_spent_cnt + report.time_spent_zero_count)

        has_wl_cnt = sum(1 for r in records if r.has_worklogs)
        report.has_at_least_one_worklog = make_counter(has_wl_cnt)
        total_wl_sec = sum(r.total_worklog_logged_seconds for r in records)
        report.total_worklog_logged_seconds = total_wl_sec
        report.total_worklog_logged_hours = round(total_wl_sec / 3600.0, 2)

        report.changelog_available = make_counter(sum(1 for r in records if r.has_changelog))
        report.has_estimate_changes = make_counter(sum(1 for r in records if r.has_estimate_changes))
        report.has_reopen_transitions = make_counter(sum(1 for r in records if r.has_reopen_transitions))

        # Lifecycle vs Effort Breakdown
        lifecycles = [r.elapsed_lifecycle_seconds / 3600.0 for r in records if r.elapsed_lifecycle_seconds is not None]
        logged_efforts = [
            (r.total_worklog_logged_seconds or r.time_spent_seconds or 0) / 3600.0
            for r in records if (r.total_worklog_logged_seconds > 0 or (r.time_spent_seconds and r.time_spent_seconds > 0))
        ]

        summary_le = LifecycleVsEffortSummary(
            issues_with_lifecycle_time=len(lifecycles),
            total_elapsed_lifecycle_seconds=sum(lifecycles) * 3600.0,
            avg_elapsed_lifecycle_hours=round(statistics.mean(lifecycles), 2) if lifecycles else 0.0,
            p50_elapsed_lifecycle_hours=round(statistics.median(lifecycles), 2) if lifecycles else 0.0,
            issues_with_logged_effort=len(logged_efforts),
            total_logged_effort_seconds=sum(logged_efforts) * 3600.0,
            avg_logged_effort_hours=round(statistics.mean(logged_efforts), 2) if logged_efforts else 0.0,
            p50_logged_effort_hours=round(statistics.median(logged_efforts), 2) if logged_efforts else 0.0,
        )
        report.lifecycle_vs_effort = summary_le

    def _evaluate_feasibility(self, report: ProjectQualityReport) -> None:
        """Formulate recommendation on readiness for an initial estimation prototype."""
        limitations: List[str] = []

        if report.total_issues_retrieved < 10:
            limitations.append(f"Low sample size ({report.total_issues_retrieved} issues retrieved in time window).")

        if report.has_original_estimate.percentage < 30.0:
            limitations.append(
                f"Original estimates are sparse ({report.has_original_estimate.percentage}% coverage). "
                f"Planned vs. actual variance analysis cannot rely on Jira original estimates alone."
            )

        if report.has_at_least_one_worklog.percentage < 30.0 and report.has_time_spent.percentage < 30.0:
            limitations.append(
                f"Logged worklogs and timeSpent are sparse ({report.has_at_least_one_worklog.percentage}% worklog coverage). "
                f"Effort estimation models must rely on active cycle time or deterministic complexity heuristics."
            )

        if report.has_components.percentage < 20.0:
            limitations.append(
                f"Component tags are sparsely populated ({report.has_components.percentage}%). "
                f"Domain/skill breakdown will rely primarily on issueType or summary NLP classification."
            )

        if report.changelog_available.percentage == 0.0:
            limitations.append(
                "Changelog expansion was not returned or permitted by Jira API for these issues."
            )

        report.data_quality_limitations = limitations

        # Determine Recommendation
        if report.total_issues_retrieved == 0:
            report.recommendation = FeasibilityRecommendation.INSUFFICIENT_DATA
            report.recommendation_rationale = (
                f"Zero completed issues found in project {report.project_key} for the last {report.days_evaluated} days."
            )
        elif (
            report.valid_lifecycle_pair.percentage >= 80.0
            and (report.has_at_least_one_worklog.percentage >= 40.0 or report.has_time_spent.percentage >= 40.0)
            and report.total_issues_retrieved >= 20
        ):
            report.recommendation = FeasibilityRecommendation.READY
            report.recommendation_rationale = (
                f"Strong historical worklog and timestamp coverage in project {report.project_key}. "
                f"Supports evidence-based effort and cycle time estimation prototypes."
            )
        elif report.valid_lifecycle_pair.percentage >= 60.0 and report.total_issues_retrieved >= 10:
            report.recommendation = FeasibilityRecommendation.READY_WITH_RESERVATIONS
            report.recommendation_rationale = (
                f"Adequate lifecycle timeline timestamps ({report.valid_lifecycle_pair.percentage}%). "
                f"Because explicit logged effort/estimates are partial ({report.has_at_least_one_worklog.percentage}%), "
                f"the prototype should use historical elapsed cycle-time benchmarks combined with deterministic fallbacks."
            )
        else:
            report.recommendation = FeasibilityRecommendation.NOT_RECOMMENDED
            report.recommendation_rationale = (
                f"Data quality or volume is too low for reliable statistical estimation modeling in project {report.project_key}."
            )
