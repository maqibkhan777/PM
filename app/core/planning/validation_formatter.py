"""Markdown report formatter for Planning Data and Cross-Project Capacity Validation (Milestone 4A)."""

from typing import List
from app.core.planning.validation_models import PlanningDataValidationReport


class PlanningDataValidationFormatter:
    """Formats PlanningDataValidationReport into a clear, explainable Markdown document."""

    @classmethod
    def format_markdown_report(cls, report: PlanningDataValidationReport) -> str:
        """Format the validation report into structured markdown."""
        lines: List[str] = [
            f"# Planning Data & Cross-Project Capacity Validation Report",
            "",
            f"- **Report ID:** `{report.report_id}`",
            f"- **Generated At:** `{report.generated_at}`",
            f"- **Anchor Date:** `{report.anchor_date}` | **Horizon:** {report.planning_horizon_working_days} working days",
            f"- **Overall Validation Outcome:** **`{report.overall_status.value}`**",
            "",
            f"## 1. Executive Summary",
            f"{report.executive_summary}",
            "",
            f"## 2. Project Scope & Resolution",
            f"- **Configured Projects:** {', '.join(report.configured_projects)}",
            f"- **Successfully Resolved ({len(report.resolved_projects)}):** {', '.join(report.resolved_projects) if report.resolved_projects else 'None'}",
        ]

        if report.unresolved_projects:
            lines.append(f"- **Unresolved Projects ({len(report.unresolved_projects)}):** ⚠️ {', '.join(report.unresolved_projects)} (Not found in Jira database)")

        lines.extend([
            "",
            f"## 3. Workload & Estimate Breakdown",
            f"| Metric | Count |",
            f"| :--- | :---: |",
            f"| **Total Active Tasks Evaluated** | {report.total_active_tasks} |",
            f"| **Tasks with Explicit Jira Remaining Estimates** | {report.tasks_with_explicit_remaining} |",
            f"| **Tasks with Empirical Benchmark Proxies** | {report.tasks_with_benchmark_estimates} |",
            f"| **Tasks with Missing/Unestimated Effort** | {report.tasks_with_missing_estimates} |",
            f"| **Tasks Missing Assignee** | {report.tasks_missing_assignee} |",
            f"| **Blocked Tasks (Unresolved Predecessors)** | {report.blocked_tasks_count} |",
            "",
            f"## 4. Resource Cross-Project Capacity Audit",
            f"| Resource | Role | Projects | Active Tasks | Remaining Effort | Available (10d) | Allocation % | Contention |",
            f"| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: |",
        ])

        for r in report.resources_audited:
            contention_badge = "⚠️ Cross-Project" if r.cross_project_conflict_detected else "Single Project"
            alloc_pct = f"{r.workload_pressure_ratio * 100:.1f}%"
            if r.is_overallocated:
                alloc_pct += " 🚨"
            projects_str = ", ".join(r.projects_involved)
            lines.append(
                f"| **{r.display_name}** | {r.role or 'Unknown'} | {projects_str} | {r.assigned_tasks_count} | {r.total_assigned_remaining_hours:.1f}h | {r.available_capacity_hours:.1f}h | {alloc_pct} | {contention_badge} |"
            )

        lines.extend([
            "",
            f"## 5. Capacity Calculation & Data Quality Disclosures",
            f"- **Formula Used:** `Available Capacity = forecast_daily_hours (6.5h) * horizon_working_days (10d) = 65.0h`",
            f"- **Working Hours:** Nominal default (6.5h - 6.75h) per standard working day.",
            f"- **Absence & Calendar Status:**",
            f"  - Working Days/Holidays: `UNKNOWN` (No authoritative local holiday calendar repository).",
            f"  - Vacation/Sick Leave: `UNKNOWN` (No authoritative leave tracking system integration).",
            f"  - Non-Project Focus Factor: `UNKNOWN` (Assumed 100% project allocation).",
            "",
            f"## 6. Dependency & Blocker Analysis",
        ])

        if report.dependency_findings:
            lines.append(f"| Source Issue | Target Issue | Relationship | Target Status | Cross-Project? | Notes |")
            lines.append(f"| :--- | :--- | :--- | :--- | :---: | :--- |")
            for df in report.dependency_findings[:15]:
                cross_str = "Yes" if df.is_cross_project else "No"
                res_str = "Resolved" if df.is_target_resolved else "UNRESOLVED"
                lines.append(f"| {df.source_issue_key} | {df.target_issue_key} | {df.link_type} | {res_str} | {cross_str} | {df.notes} |")
        else:
            lines.append("No active hard blocker relationships detected in validated projects.")

        lines.extend([
            "",
            f"## 7. Limitations & Recommendations",
        ])
        for lim in report.capacity_limitations_summary:
            lines.append(f"- ⚠️ {lim}")

        lines.extend([
            "",
            f"---",
            f"*Advisory Report only: Zero Jira mutations or schedule modifications executed.*",
        ])
        return "\n".join(lines)
