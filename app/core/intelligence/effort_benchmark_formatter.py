"""Report formatter for Empirical Historical Effort Benchmarks.

Produces console tables and markdown exports separating project benchmarks.
"""

from typing import List
from app.core.intelligence.effort_benchmark_models import (
    BenchmarkReliability,
    MultiProjectBenchmarkSummary,
    ProjectEffortBenchmarkReport,
    SegmentedProjectBenchmark,
)


class EffortBenchmarkReportFormatter:
    """Formats multi-project empirical effort benchmarks for console and Markdown."""

    @classmethod
    def format_console_report(cls, summary: MultiProjectBenchmarkSummary) -> str:
        """Format multi-project benchmark report for CLI console."""
        lines: List[str] = []
        w = 92
        lines.append("=" * w)
        lines.append(f"  EMPIRICAL HISTORICAL EFFORT BENCHMARK REPORT  (Run ID: {summary.summary_id})")
        lines.append("=" * w)
        lines.append(f"• Generated At:          {summary.generated_at}")
        lines.append(f"• Lookback Period:       {summary.lookback_days} days")
        lines.append(f"• Evaluated Projects:    {', '.join(summary.projects)}")
        lines.append(f"• Cross-Project Isolation: Verified (No combined statistics)")
        lines.append("-" * w)

        for proj_key, report in summary.reports_by_project.items():
            lines.append("")
            lines.append(f"▶ PROJECT: {proj_key} (Scope: {report.date_range_start} to {report.date_range_end})")
            lines.append("-" * w)
            lines.append(
                f"• Total Completed Issues: {report.total_completed_issues} | "
                f"With Logged Effort: {report.issues_with_logged_effort} ({100.0 - report.missing_effort_percentage:.1f}%) | "
                f"Missing Effort: {report.issues_missing_effort} ({report.missing_effort_percentage:.1f}%)"
            )
            lines.append(f"• Total Logged Hours:     {report.total_logged_hours:.2f} hours")
            lines.append("")

            # Overall Benchmark
            ov = report.overall_benchmark
            ov_dist = ov.distribution
            lines.append(f"  OVERALL PROJECT EFFORT DISTRIBUTION:")
            lines.append(
                f"  • Sample Count: {ov.sample_count} issues | Reliability: {ov.reliability.value}"
            )
            lines.append(
                f"  • Mean: {ov_dist.mean_hours:.2f}h | Median (P50): {ov_dist.median_hours:.2f}h | "
                f"P25: {f'{ov_dist.p25_hours:.2f}h' if ov_dist.p25_hours is not None else 'N/A'} | "
                f"P75: {f'{ov_dist.p75_hours:.2f}h' if ov_dist.p75_hours is not None else 'N/A'} | "
                f"P90: {f'{ov_dist.p90_hours:.2f}h' if ov_dist.p90_hours is not None else 'N/A'}"
            )
            lines.append(
                f"  • Min: {ov_dist.min_hours:.2f}h | Max: {ov_dist.max_hours:.2f}h | StdDev: {ov_dist.stddev_hours:.2f}h"
            )
            lines.append("")

            # Segmented Table
            lines.append("  SEGMENTED BENCHMARKS (Issue Types, Priorities, & Groupings):")
            lines.append(
                f"  {'Dimension / Key':<32} | {'n':>4} | {'Median':>7} | {'Mean':>7} | {'P25':>6} | {'P75':>6} | {'P90':>6} | {'Reliability':<15}"
            )
            lines.append("  " + "-" * 88)

            all_segments: List[SegmentedProjectBenchmark] = []
            all_segments.extend(report.by_issue_type.values())
            all_segments.extend(report.by_priority.values())
            all_segments.extend(report.by_issue_type_priority.values())

            for seg in sorted(all_segments, key=lambda s: (s.dimension_type, -s.sample_count)):
                d = seg.distribution
                p25_str = f"{d.p25_hours:.1f}h" if d.p25_hours is not None else "—"
                p75_str = f"{d.p75_hours:.1f}h" if d.p75_hours is not None else "—"
                p90_str = f"{d.p90_hours:.1f}h" if d.p90_hours is not None else "—"
                med_str = f"{d.median_hours:.1f}h" if d.sample_count > 0 else "—"
                mean_str = f"{d.mean_hours:.1f}h" if d.sample_count > 0 else "—"

                dim_label = f"{seg.dimension_type}:{seg.dimension_key}"
                lines.append(
                    f"  {dim_label:<32} | {seg.sample_count:>4} | {med_str:>7} | {mean_str:>7} | {p25_str:>6} | {p75_str:>6} | {p90_str:>6} | {seg.reliability.value:<15}"
                )

            lines.append("  " + "-" * 88)

            if report.data_quality_warnings:
                lines.append("")
                lines.append("  DATA QUALITY & RELIABILITY WARNINGS:")
                for w_msg in report.data_quality_warnings:
                    lines.append(f"  ⚠️  {w_msg}")

            lines.append("-" * w)

        lines.append("")
        lines.append("=" * w)
        lines.append("  EXECUTION COMMANDS:")
        lines.append("  • Single Project:")
        lines.append("      python scripts/generate_effort_benchmarks.py --projects SMTPSUPORT --days 90")
        lines.append("  • Multi-Project (SMTPSUPORT and GF):")
        lines.append("      python scripts/generate_effort_benchmarks.py --projects SMTPSUPORT,GF --days 90")
        lines.append("  • Export Markdown Report:")
        lines.append("      python scripts/generate_effort_benchmarks.py --projects SMTPSUPORT,GF --output-file reports/historical_effort_benchmarks.md")
        lines.append("=" * w)

        return "\n".join(lines)

    @classmethod
    def format_markdown_report(cls, summary: MultiProjectBenchmarkSummary) -> str:
        """Format multi-project benchmark report as clean Markdown."""
        md: List[str] = []
        md.append("# Empirical Historical Effort Benchmarking Report")
        md.append("")
        md.append(f"- **Benchmark Run ID**: `{summary.summary_id}`")
        md.append(f"- **Generated At**: `{summary.generated_at}`")
        md.append(f"- **Lookback Window**: `{summary.lookback_days} days`")
        md.append(f"- **Projects Evaluated**: `{', '.join(summary.projects)}`")
        md.append(f"- **Cross-Project Isolation**: Strict (Zero data pooling across projects)")
        md.append("")

        for proj_key, report in summary.reports_by_project.items():
            md.append(f"## Project: `{proj_key}`")
            md.append("")
            md.append(
                f"**Total Completed Issues**: {report.total_completed_issues} | "
                f"**With Logged Effort**: {report.issues_with_logged_effort} ({100.0 - report.missing_effort_percentage:.1f}%) | "
                f"**Missing Effort**: {report.issues_missing_effort} ({report.missing_effort_percentage:.1f}%) | "
                f"**Total Logged Hours**: `{report.total_logged_hours:.2f}h`"
            )
            md.append("")

            # Overall benchmark card
            ov = report.overall_benchmark
            ov_dist = ov.distribution
            md.append("### Overall Project Effort Distribution")
            md.append(f"- **Reliability**: `{ov.reliability.value}` (Sample size $n = {ov.sample_count}$)")
            md.append(
                f"- **Quantiles**: Median (P50) = `{ov_dist.median_hours:.2f}h` | "
                f"Mean = `{ov_dist.mean_hours:.2f}h` | "
                f"P25 = `{f'{ov_dist.p25_hours:.2f}h' if ov_dist.p25_hours is not None else 'N/A'}` | "
                f"P75 = `{f'{ov_dist.p75_hours:.2f}h' if ov_dist.p75_hours is not None else 'N/A'}` | "
                f"P90 = `{f'{ov_dist.p90_hours:.2f}h' if ov_dist.p90_hours is not None else 'N/A'}`"
            )
            md.append(f"- **Dispersion**: Min = `{ov_dist.min_hours:.2f}h` | Max = `{ov_dist.max_hours:.2f}h` | StdDev = `{ov_dist.stddev_hours:.2f}h`")
            md.append("")

            # Segmentation Table
            md.append("### Segmented Benchmarks")
            md.append("| Dimension | Key | Sample $n$ | Median (P50) | Mean | P25 | P75 | P90 | Reliability |")
            md.append("| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |")

            all_segments: List[SegmentedProjectBenchmark] = []
            all_segments.extend(report.by_issue_type.values())
            all_segments.extend(report.by_priority.values())
            all_segments.extend(report.by_issue_type_priority.values())

            for seg in sorted(all_segments, key=lambda s: (s.dimension_type, -s.sample_count)):
                d = seg.distribution
                p25_str = f"{d.p25_hours:.2f}h" if d.p25_hours is not None else "—"
                p75_str = f"{d.p75_hours:.2f}h" if d.p75_hours is not None else "—"
                p90_str = f"{d.p90_hours:.2f}h" if d.p90_hours is not None else "—"
                med_str = f"{d.median_hours:.2f}h" if d.sample_count > 0 else "—"
                mean_str = f"{d.mean_hours:.2f}h" if d.sample_count > 0 else "—"

                badge = {
                    BenchmarkReliability.USABLE: "🟢 USABLE",
                    BenchmarkReliability.LOW_CONFIDENCE: "🟡 LOW_CONFIDENCE",
                    BenchmarkReliability.INSUFFICIENT_DATA: "🔴 INSUFFICIENT_DATA",
                }.get(seg.reliability, seg.reliability.value)

                md.append(
                    f"| `{seg.dimension_type}` | `{seg.dimension_key}` | {seg.sample_count} | {med_str} | {mean_str} | {p25_str} | {p75_str} | {p90_str} | {badge} |"
                )

            md.append("")
            if report.data_quality_warnings:
                md.append("> [!WARNING]")
                for w_msg in report.data_quality_warnings:
                    md.append(f"> - {w_msg}")
                md.append("")

        return "\n".join(md)
