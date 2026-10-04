"""Markdown report and Discord summary formatter for Advisory Schedule Proposals (Milestone 4B)."""

from typing import List
from app.core.planning.schedule_models import AdvisoryScheduleProposal


class AdvisoryScheduleFormatter:
    """Formats AdvisoryScheduleProposal into Markdown reports and concise Discord-friendly summaries."""

    @classmethod
    def format_markdown_report(cls, proposal: AdvisoryScheduleProposal) -> str:
        """Format the complete schedule proposal into an explainable Markdown report."""
        lines: List[str] = [
            "# Advisory Schedule Proposal Report",
            "",
            f"- **Proposal ID:** `{proposal.proposal_id}`",
            f"- **Generated At:** `{proposal.generated_at}`",
            f"- **Anchor Date:** `{proposal.anchor_date}` | **Planning Horizon:** {proposal.planning_horizon_working_days} working days (to `{proposal.horizon_end_date}`)",
            f"- **Validation Status:** `{proposal.validation_status.value}` | **Feasibility Status:** **`{proposal.feasibility_status.value}`**",
            f"- **Human Review Required:** `YES (Advisory Only)`",
            "",
            "## 1. Executive Summary & Scope",
            f"- **Configured Projects:** {', '.join(proposal.configured_projects)}",
            f"- **Resolved Projects ({len(proposal.resolved_projects)}):** {', '.join(proposal.resolved_projects) if proposal.resolved_projects else 'None'}",
        ]

        if proposal.unresolved_projects:
            lines.append(f"- **Unresolved Projects ({len(proposal.unresolved_projects)}):** ⚠️ {', '.join(proposal.unresolved_projects)} (Not found in Jira database)")

        lines.extend([
            "",
            "## 2. Workload & Estimation Breakdown",
            "| Metric | Count | Note |",
            "| :--- | :---: | :--- |",
            f"| **Total Tasks Considered** | {proposal.total_tasks_considered} | Across all resolved projects |",
            f"| **Tasks with Explicit Jira Estimates** | {proposal.tasks_explicit_estimates} | Authoritative timetracking / original estimate |",
            f"| **Tasks with Empirical Benchmark Proxies** | {proposal.tasks_benchmark_proxies} | Historical P50 proxy from logged effort |",
            f"| **Tasks with Fallback Estimates** | {proposal.tasks_fallback_estimates} | Complexity heuristic fallback |",
            f"| **Tasks with Missing Estimates** | {proposal.tasks_missing_estimates} | Date projection unavailable |",
            f"| **Blocked Tasks (Unresolved Predecessors)** | {proposal.blocked_tasks_count} | Cannot start immediately |",
            f"| **Unassigned Tasks** | {proposal.unassigned_tasks_count} | Cannot sequence without resource |",
            f"| **Tasks Extending Beyond Horizon** | {proposal.tasks_beyond_horizon_count} | Complete after {proposal.horizon_end_date} |",
            "",
            "## 3. Resource Workload & Capacity Audit",
            "| Resource | Role | Projects | Active Tasks | Remaining Effort | Available (10d) | Allocation % | Status |",
            "| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :--- |",
        ])

        for r in proposal.resource_schedules:
            status_badge = "🚨 Overallocated" if r.is_overallocated else ("⚠️ Cross-Project" if r.is_cross_project else "Normal")
            projects_str = ", ".join(r.projects_involved)
            lines.append(
                f"| **{r.display_name}** | {r.role or 'Unknown'} | {projects_str} | {r.tasks_count} | {r.total_assigned_remaining_hours:.1f}h | {r.available_capacity_hours:.1f}h | {r.allocation_percentage:.1f}% | {status_badge} |"
            )

        lines.extend([
            "",
            "## 4. Proposed Task Sequence & Tentative Dates",
        ])

        for r in proposal.resource_schedules:
            lines.extend([
                f"### {r.display_name} ({r.role or 'Team Member'}) — {r.total_assigned_remaining_hours:.1f}h / {r.available_capacity_hours:.1f}h",
                "",
                "| # | Issue | Summary | Priority | Effort (Range) | Source | Tentative Start | Tentative Done | Status / Blockers |",
                "| :-: | :--- | :--- | :---: | :---: | :--- | :---: | :---: | :--- |",
            ])

            for t in r.scheduled_tasks:
                effort_display = f"{t.estimated_effort_hours:.1f}h" if t.estimated_effort_hours is not None else "Missing"
                if t.p50_effort_hours is not None and t.p90_effort_hours is not None and t.p50_effort_hours != t.p90_effort_hours:
                    effort_display = f"{t.estimated_effort_hours:.1f}h ({t.p50_effort_hours:.1f}h–{t.p90_effort_hours:.1f}h)"

                source_label = "Jira Remaining" if t.estimate_type.value == "EXPLICIT_REMAINING" else (
                    f"Benchmark (n={t.benchmark_sample_count or 0})" if t.is_proxy_estimate else t.estimate_type.value
                )

                start_str = t.tentative_start_date or "N/A"
                done_str = t.tentative_completion_date or "N/A"
                if t.is_beyond_horizon:
                    done_str += " ⚠️"

                status_notes = []
                if t.is_blocked:
                    status_notes.append(f"⛔ Blocked by {', '.join(t.unresolved_predecessor_keys)} (Conditional on predecessor completion)")
                
                if not t.dates_available:
                    status_notes.append("No dates (Missing effort)")
                elif t.is_beyond_horizon:
                    status_notes.append("Beyond 10d horizon")
                elif not t.is_blocked:
                    status_notes.append("Executable")

                note_text = "; ".join(status_notes)

                # Clean summary text to avoid breaking Markdown tables
                clean_summary = t.summary.replace("|", "-").replace("\n", " ")[:40]
                if len(t.summary) > 40:
                    clean_summary += "..."

                lines.append(
                    f"| {t.queue_sequence} | `{t.issue_key}` | {clean_summary} | {t.priority} | {effort_display} | {source_label} | {start_str} | {done_str} | {note_text} |"
                )
            lines.append("")

        if proposal.unassigned_tasks:
            lines.extend([
                "### Unassigned Tasks (Requires Resource Assignment)",
                "",
                "| Issue | Project | Summary | Priority | Effort | Notes |",
                "| :--- | :--- | :--- | :---: | :---: | :--- |",
            ])
            for ut in proposal.unassigned_tasks:
                clean_summary = ut.summary.replace("|", "-").replace("\n", " ")[:45]
                lines.append(
                    f"| `{ut.issue_key}` | {ut.project_key} | {clean_summary} | {ut.priority} | {ut.estimated_effort_hours or 'N/A'}h | Unassigned |"
                )
            lines.append("")

        lines.extend([
            "## 5. Capacity Assumptions & Data-Quality Limitations",
            f"- **Capacity Formula:** `{proposal.capacity_formula_disclosure}`",
            "- **Working Calendar Source:** Nominal Monday-Friday business days (6.5h daily forecast).",
            "- **Absence & Calendar Status:**",
        ])

        for disc in proposal.unknown_availability_disclosures:
            lines.append(f"  - ⚠️ {disc}")

        lines.extend([
            "",
            "## 6. Key Risks, Blockers & Assumptions",
        ])
        if proposal.risks_and_assumptions:
            for risk in proposal.risks_and_assumptions:
                lines.append(f"- ⚠️ {risk}")
        else:
            lines.append("- None identified.")

        lines.extend([
            "",
            "## 7. Mandatory Governance & Disclaimer",
            f"> [!IMPORTANT]",
            f"> **Advisory Proposal Only:** {proposal.human_review_disclaimer}",
            "> Zero Jira mutations, assignment changes, or due-date writes have been executed.",
        ])

        return "\n".join(lines)

    @classmethod
    def format_discord_summary(cls, proposal: AdvisoryScheduleProposal) -> str:
        """Format a concise, Discord-friendly summary of the advisory schedule proposal."""
        lines = [
            f"📅 **Advisory Schedule Proposal** (`{proposal.proposal_id}`)",
            f"**Projects:** {', '.join(proposal.resolved_projects)} | **Status:** `{proposal.feasibility_status.value}` | **Horizon:** {proposal.anchor_date} to {proposal.horizon_end_date} (10 working days)",
            "",
            f"**Workload Summary:** {proposal.total_tasks_considered} active tasks ({proposal.tasks_explicit_estimates} explicit estimates, {proposal.tasks_benchmark_proxies} benchmark proxies, {proposal.blocked_tasks_count} blocked).",
            "",
            "**Key Resource Timelines:**",
        ]

        for r in proposal.resource_schedules:
            if r.tasks_count == 0:
                continue
            badge = " 🚨 (Overallocated)" if r.is_overallocated else (" ⚠️ (Cross-Project)" if r.is_cross_project else "")
            lines.append(
                f"• **{r.display_name}** ({r.role or 'Member'}): {r.tasks_count} tasks, {r.total_assigned_remaining_hours:.1f}h / {r.available_capacity_hours:.1f}h ({r.allocation_percentage:.1f}%){badge}"
            )
            for t in r.scheduled_tasks[:3]:  # Top 3 tasks for brevity in Discord
                dates_text = f"[{t.tentative_start_date} → {t.tentative_completion_date}]" if t.dates_available else "[No dates - Missing estimate]"
                block_badge = " ⛔ [BLOCKED]" if t.is_blocked else ""
                lines.append(f"   `{t.issue_key}` ({t.estimated_effort_hours:.1f}h): {dates_text}{block_badge}")
            if len(r.scheduled_tasks) > 3:
                lines.append(f"   *... and {len(r.scheduled_tasks) - 3} more task(s)*")

        lines.extend([
            "",
            "⚠️ *Advisory proposal only. Dates are analytical projections based on nominal 6.5h/day baseline without verified leave calendars. Human review required.*",
        ])
        return "\n".join(lines)
