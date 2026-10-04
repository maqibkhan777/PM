#!/usr/bin/env python3
"""CLI Script: Historical Jira Data Quality Diagnostic Probe.

Manually invoked diagnostic tool for evaluating whether historical Jira data in one
or more projects contains sufficient quality to support task estimation and capacity planning.

Usage:
    # Single project (SMTPSUPORT)
    python scripts/probe_jira_historical_data.py --projects SMTPSUPORT --days 90 --cap 200

    # Multiple projects
    python scripts/probe_jira_historical_data.py --projects SMTPSUPORT,TREN --days 90 --cap 200

    # Output to markdown / json report file (optional)
    python scripts/probe_jira_historical_data.py --projects SMTPSUPORT --output-file reports/smtpsuport_quality_probe.md
"""

import argparse
import asyncio
import json
import os
import sys
from typing import List

# Ensure project root is on PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.config.settings import settings
from app.connectors.jira.client import JiraClient
from app.core.intelligence.probe import JiraHistoricalDataQualityProbe
from app.core.intelligence.probe_formatter import JiraProbeReportFormatter
from app.utils.logger import logger


async def run_probe_cli(
    projects: List[str],
    days: int,
    cap: int,
    batch_size: int,
    output_file: str = None,
) -> int:
    """Execute historical Jira data quality probe and output report."""
    if not settings.is_jira_configured():
        print("\n❌ Error: Jira credentials are not configured in settings / environment.")
        print("Please configure JIRA_BASE_URL, JIRA_EMAIL, and JIRA_API_TOKEN before running.\n")
        return 1

    client = JiraClient()
    probe = JiraHistoricalDataQualityProbe(jira_client=client)

    try:
        summary = await probe.run_probe(
            project_keys=projects,
            lookback_days=days,
            max_issues_per_project=cap,
            batch_size=batch_size,
        )

        console_report = JiraProbeReportFormatter.format_console_report(summary)
        print("\n" + console_report + "\n")

        if output_file:
            os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
            if output_file.endswith(".json"):
                with open(output_file, "w", encoding="utf-8") as f:
                    f.write(summary.model_dump_json(indent=2))
                print(f"📁 JSON report written to: {output_file}")
            else:
                md_content = JiraProbeReportFormatter.format_markdown_report(summary)
                with open(output_file, "w", encoding="utf-8") as f:
                    f.write(md_content)
                print(f"📁 Markdown report written to: {output_file}")

        return 0

    except Exception as e:
        logger.error(f"Probe execution error: {e}", exc_info=True)
        print(f"\n❌ Probe encountered an error: {e}\n")
        return 1

    finally:
        await client.close()


def main():
    parser = argparse.ArgumentParser(
        description="Historical Jira Data Quality Diagnostic Probe for PM AI Work Intelligence"
    )
    parser.add_argument(
        "--projects",
        type=str,
        default="SMTPSUPORT",
        help="Comma-separated Jira project keys (e.g. SMTPSUPORT or SMTPSUPORT,TREN). Default: SMTPSUPORT",
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
        "--batch-size",
        type=int,
        default=50,
        help="Batch size per JQL cursor fetch (default: 50)",
    )
    parser.add_argument(
        "--output-file",
        type=str,
        default=None,
        help="Optional path to export markdown (.md) or JSON (.json) report file",
    )

    args = parser.parse_args()
    proj_list = [p.strip() for p in args.projects.split(",") if p.strip()]

    if not proj_list:
        print("❌ Error: At least one project key must be specified.")
        sys.exit(1)

    exit_code = asyncio.run(
        run_probe_cli(
            projects=proj_list,
            days=args.days,
            cap=args.cap,
            batch_size=args.batch_size,
            output_file=args.output_file,
        )
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
