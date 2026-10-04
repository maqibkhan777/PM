#!/usr/bin/env python3
"""CLI Script: Empirical Historical Effort Benchmarking Engine (Milestone 2).

Computes empirical effort distributions (P25, P50, P75, P90, Mean, StdDev)
from live Jira historical data, strictly separated per project (e.g. SMTPSUPORT, GF).

Usage:
    # Single project
    python scripts/generate_effort_benchmarks.py --projects SMTPSUPORT --days 90

    # Multi-project (SMTPSUPORT and GF)
    python scripts/generate_effort_benchmarks.py --projects SMTPSUPORT,GF --days 90

    # Output to Markdown report file
    python scripts/generate_effort_benchmarks.py --projects SMTPSUPORT,GF --days 90 --output-file reports/historical_effort_benchmarks.md
"""

import argparse
import asyncio
import os
import sys
from typing import List

# Ensure project root is on PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.config.settings import settings
from app.connectors.jira.client import JiraClient
from app.core.intelligence.effort_benchmarking import EmpiricalHistoricalEffortBenchmarkingEngine
from app.core.intelligence.effort_benchmark_formatter import EffortBenchmarkReportFormatter
from app.database.schema import init_db
from app.utils.logger import logger


async def run_benchmarks_cli(
    projects: List[str],
    days: int,
    cap: int,
    output_file: str = None,
    no_persist: bool = False,
) -> int:
    """Execute empirical effort benchmarking across projects."""
    if not settings.is_jira_configured():
        print("\n❌ Error: Jira credentials are not configured in settings / environment.")
        print("Please configure JIRA_BASE_URL, JIRA_EMAIL, and JIRA_API_TOKEN before running.\n")
        return 1

    # Ensure local DB tables exist
    init_db()

    client = JiraClient()
    engine = EmpiricalHistoricalEffortBenchmarkingEngine(jira_client=client)

    try:
        summary = await engine.compute_multi_project_benchmarks(
            project_keys=projects,
            lookback_days=days,
            max_issues_per_project=cap,
            persist=not no_persist,
        )

        console_report = EffortBenchmarkReportFormatter.format_console_report(summary)
        print("\n" + console_report + "\n")

        if output_file:
            os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
            if output_file.endswith(".json"):
                with open(output_file, "w", encoding="utf-8") as f:
                    f.write(summary.model_dump_json(indent=2))
                print(f"📁 JSON report written to: {output_file}")
            else:
                md_content = EffortBenchmarkReportFormatter.format_markdown_report(summary)
                with open(output_file, "w", encoding="utf-8") as f:
                    f.write(md_content)
                print(f"📁 Markdown report written to: {output_file}")

        return 0

    except Exception as e:
        logger.error(f"Effort benchmarking execution error: {e}", exc_info=True)
        print(f"\n❌ Effort benchmarking encountered an error: {e}\n")
        return 1

    finally:
        await client.close()


def main():
    parser = argparse.ArgumentParser(
        description="Empirical Historical Effort Benchmarking Engine for PM AI Work Intelligence"
    )
    parser.add_argument(
        "--projects",
        type=str,
        default="SMTPSUPORT,GF",
        help="Comma-separated Jira project keys (e.g. SMTPSUPORT,GF). Default: SMTPSUPORT,GF",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=90,
        help="Lookback period in days for completed issues (default: 90)",
    )
    parser.add_argument(
        "--cap",
        type=int,
        default=200,
        help="Maximum issue count cap per project (default: 200)",
    )
    parser.add_argument(
        "--output-file",
        type=str,
        default=None,
        help="Optional path to export markdown (.md) or JSON (.json) report file",
    )
    parser.add_argument(
        "--no-persist",
        action="store_true",
        help="Do not persist computed benchmarks to local SQLite database",
    )

    args = parser.parse_args()
    proj_list = [p.strip() for p in args.projects.split(",") if p.strip()]

    if not proj_list:
        print("❌ Error: At least one project key must be specified.")
        sys.exit(1)

    exit_code = asyncio.run(
        run_benchmarks_cli(
            projects=proj_list,
            days=args.days,
            cap=args.cap,
            output_file=args.output_file,
            no_persist=args.no_persist,
        )
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
