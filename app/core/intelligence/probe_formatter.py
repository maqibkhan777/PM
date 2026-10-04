"""Terminal and text report formatter for Historical Jira Data Quality Probe.

Produces clean, human-readable terminal reports and markdown export summaries.
Strictly redacts/excludes credentials, API tokens, and PII.
"""

from typing import Dict, List, Optional
from app.core.intelligence.probe_models import (
    FeasibilityRecommendation,
    MultiProjectProbeSummary,
    ProjectQualityReport,
)


class JiraProbeReportFormatter:
    """Format probe results for CLI stdout and Markdown export."""

    @classmethod
    def format_console_report(cls, summary: MultiProjectProbeSummary) -> str:
        """Generate formatted CLI console text report."""
        lines: List[str] = []
        width = 88
        lines.append("=" * width)
        lines.append(f"  HISTORICAL JIRA DATA QUALITY PROBE REPORT  (Probe ID: {summary.probe_id})")
        lines.append("=" * width)
        lines.append(f"• Evaluation Timestamp: {summary.timestamp}")
        lines.append(f"• Projects Evaluated:   {', '.join(summary.project_keys)}")
        lines.append(f"• Lookback Period:      {summary.lookback_days} days")
        lines.append(f"• Max Cap Per Project:  {summary.cap_per_project} issues")
        lines.append("-" * width)

        for proj_key, report in summary.project_reports.items():
            lines.append("")
            lines.append(f"▶ PROJECT: {proj_key} (Scope: {report.date_range_start} to {report.date_range_end})")
            lines.append("-" * width)

            if report.partial_failure:
                lines.append(f"⚠️  PARTIAL FAILURE / API ERROR: {report.error_message}")
                if report.total_issues_retrieved == 0:
                    lines.append("   (No issues could be retrieved for this project.)")
                    lines.append("-" * width)
                    continue

            lines.append(
                f"• Total Issues Sampled: {report.total_issues_retrieved} "
                f"({'⚠️ Cap Reached' if report.cap_reached else 'All in range retrieved'})"
            )
            lines.append("")
            lines.append("  DATA COVERAGE BREAKDOWN:")
            lines.append(
                f"  {'Metric':<38} | {'Count':>7} | {'Coverage %':>10} | {'Status/Notes':<20}"
            )
            lines.append("  " + "-" * 82)

            # Table rows
            rows = [
                ("Valid Created Timestamp", report.valid_created_timestamp),
                ("Valid Resolved Timestamp", report.valid_resolved_timestamp),
                ("Valid Updated Timestamp", report.valid_updated_timestamp),
                ("Valid Lifecycle Pair (Start->End)", report.valid_lifecycle_pair),
                ("Issue Type Populated", report.has_issue_type),
                ("Priority Populated", report.has_priority),
                ("Assignee Populated", report.has_assignee),
                ("Components Populated", report.has_components),
                ("Labels Populated", report.has_labels),
                ("Parent / Epic Linked", report.has_parent_or_epic),
                ("Original Estimate Available (>0)", report.has_original_estimate),
                ("Time Spent Available (>0)", report.has_time_spent),
                ("Issues With >=1 Worklog", report.has_at_least_one_worklog),
                ("Changelog / Transitions Available", report.changelog_available),
                ("Changelog Estimate Changes", report.has_estimate_changes),
                ("Changelog Reopen Transitions", report.has_reopen_transitions),
            ]


            for label, counter in rows:
                status_note = "OK" if counter.percentage >= 70.0 else ("Moderate" if counter.percentage >= 30.0 else "Sparse")
                lines.append(
                    f"  {label:<38} | {counter.count:>7} | {counter.percentage:>9.1f}% | {status_note:<20}"
                )

            lines.append("  " + "-" * 82)
            lines.append(
                f"  • Original Estimates Zero vs Missing: {report.original_estimate_zero_count} zero, "
                f"{report.original_estimate_missing_count} missing"
            )
            lines.append(
                f"  • Time Spent Zero vs Missing:         {report.time_spent_zero_count} zero, "
                f"{report.time_spent_missing_count} missing"
            )
            lines.append(
                f"  • Total Worklogs Logged Time:         {report.total_worklog_logged_hours:.2f} hours "
                f"({report.total_worklog_logged_seconds} seconds)"
            )

            lines.append("")
            lines.append("  LIFECYCLE DURATION VS. LOGGED EFFORT (CRITICAL DISTINCTION):")
            le = report.lifecycle_vs_effort
            lines.append(
                f"  • Elapsed Wall-Clock Lifecycle Time (Created->Resolved): "
                f"Avg = {le.avg_elapsed_lifecycle_hours:.1f}h, Median (P50) = {le.p50_elapsed_lifecycle_hours:.1f}h "
                f"({le.issues_with_lifecycle_time} issues)"
            )
            lines.append(
                f"  • Actual Logged Developer Effort (Worklogs/TimeSpent):  "
                f"Avg = {le.avg_logged_effort_hours:.1f}h, Median (P50) = {le.p50_logged_effort_hours:.1f}h "
                f"({le.issues_with_logged_effort} issues)"
            )
            lines.append(f"  ℹ️  {le.lifecycle_effort_discrepancy_note}")

            if report.data_quality_limitations:
                lines.append("")
                lines.append("  DATA QUALITY LIMITATIONS:")
                for lim in report.data_quality_limitations:
                    lines.append(f"  - {lim}")

            lines.append("")
            rec_icon = {
                FeasibilityRecommendation.READY: "✅",
                FeasibilityRecommendation.READY_WITH_RESERVATIONS: "⚠️",
                FeasibilityRecommendation.NOT_RECOMMENDED: "❌",
                FeasibilityRecommendation.INSUFFICIENT_DATA: "❓",
            }.get(report.recommendation, "•")

            lines.append(f"  FEASIBILITY RECOMMENDATION: {rec_icon} {report.recommendation.value}")
            lines.append(f"  Rationale: {report.recommendation_rationale}")
            lines.append("-" * width)

        lines.append("")
        lines.append("=" * width)
        lines.append("  PROBE EXECUTION COMMANDS:")
        lines.append("  • Single Project (e.g. SMTPSUPORT):")
        lines.append("      python scripts/probe_jira_historical_data.py --projects SMTPSUPORT --days 90 --cap 200")
        lines.append("  • Multiple Projects (e.g. SMTPSUPORT and TREN):")
        lines.append("      python scripts/probe_jira_historical_data.py --projects SMTPSUPORT,TREN --days 90 --cap 200")
        lines.append("  • Optional JSON/Markdown Export:")
        lines.append("      python scripts/probe_jira_historical_data.py --projects SMTPSUPORT --output-file reports/smtpsuport_quality_probe.json")
        lines.append("=" * width)

        return "\n".join(lines)

    @classmethod
    def format_markdown_report(cls, summary: MultiProjectProbeSummary) -> str:
        """Generate clean markdown document for local archiving."""
        md: List[str] = []
        md.append(f"# Historical Jira Data Quality Diagnostic Report")
        md.append("")
        md.append(f"- **Probe ID**: `{summary.probe_id}`")
        md.append(f"- **Timestamp**: `{summary.timestamp}`")
        md.append(f"- **Evaluated Projects**: `{', '.join(summary.project_keys)}`")
        md.append(f"- **Lookback Days**: `{summary.lookback_days}`")
        md.append(f"- **Max Cap Per Project**: `{summary.cap_per_project}`")
        md.append("")

        for proj_key, report in summary.project_reports.items():
            md.append(f"## Project: `{proj_key}`")
            md.append("")
            if report.partial_failure:
                md.append(f"> [!WARNING]\n> **Partial Failure / Error**: {report.error_message}\n")
                if report.total_issues_retrieved == 0:
                    continue

            md.append(
                f"**Total Sampled Issues**: {report.total_issues_retrieved} "
                f"({'*(Cap reached)*' if report.cap_reached else '*(Complete set in range)*'})"
            )
            md.append("")
            md.append("| Metric | Count | Coverage % | Status |")
            md.append("| :--- | :---: | :---: | :--- |")

            rows = [
                ("Valid Created Timestamp", report.valid_created_timestamp),
                ("Valid Resolved Timestamp", report.valid_resolved_timestamp),
                ("Valid Updated Timestamp", report.valid_updated_timestamp),
                ("Valid Lifecycle Pair (Start & End)", report.valid_lifecycle_pair),
                ("Issue Type Populated", report.has_issue_type),
                ("Priority Populated", report.has_priority),
                ("Assignee Populated", report.has_assignee),
                ("Components Populated", report.has_components),
                ("Labels Populated", report.has_labels),
                ("Parent / Epic Linked", report.has_parent_or_epic),
                ("Original Estimate Available (>0)", report.has_original_estimate),
                ("Time Spent Available (>0)", report.has_time_spent),
                ("Issues With >=1 Worklog", report.has_at_least_one_worklog),
                ("Changelog / Transitions Available", report.changelog_available),
                ("Changelog Estimate Changes", report.has_estimate_changes),
                ("Changelog Reopen Transitions", report.has_reopen_transitions),
            ]


            for label, counter in rows:
                status_note = "Good" if counter.percentage >= 70.0 else ("Moderate" if counter.percentage >= 30.0 else "Sparse")
                md.append(f"| {label} | {counter.count} | {counter.percentage:.1f}% | {status_note} |")

            md.append("")
            md.append("### Lifecycle Duration vs. Actual Logged Effort")
            le = report.lifecycle_vs_effort
            md.append(f"- **Elapsed Wall-Clock Lifecycle (Created to Resolved)**: Avg `{le.avg_elapsed_lifecycle_hours:.1f}h`, Median `{le.p50_elapsed_lifecycle_hours:.1f}h` ({le.issues_with_lifecycle_time} issues)")
            md.append(f"- **Actual Logged Worklog Effort**: Avg `{le.avg_logged_effort_hours:.1f}h`, Median `{le.p50_logged_effort_hours:.1f}h` ({le.issues_with_logged_effort} issues)")
            md.append(f"- **Total Worklog Logged Time**: `{report.total_worklog_logged_hours:.2f} hours`")
            md.append(f"> [!NOTE]\n> {le.lifecycle_effort_discrepancy_note}\n")

            if report.data_quality_limitations:
                md.append("### Identified Limitations")
                for lim in report.data_quality_limitations:
                    md.append(f"- {lim}")
                md.append("")

            md.append(f"### Feasibility Recommendation: **{report.recommendation.value}**")
            md.append(f"_{report.recommendation_rationale}_")
            md.append("")

        return "\n".join(md)
