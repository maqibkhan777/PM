#!/usr/bin/env python3
"""PM Operations Agent - Live QA Execution & Integrity CLI Tool.

Usage:
    python scripts/run_live_qa.py --tier dry_run
    python scripts/run_live_qa.py --tier live --issue TREN-378
    python scripts/run_live_qa.py --tier integration
"""

import argparse
import os
import sys
from datetime import datetime, timezone

# Ensure project root is on PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.config.settings import settings
from app.core.qa.models import TestTier, QAStatus
from app.core.qa.runner import QARunner


def main():
    parser = argparse.ArgumentParser(description="PM Operations Agent Live QA Test Runner & Integrity Gate")
    parser.add_argument(
        "--tier",
        type=str,
        choices=["unit", "integration", "dry_run", "live"],
        default="dry_run",
        help="Test execution tier (default: dry_run)",
    )
    parser.add_argument(
        "--issue",
        type=str,
        default="TREN-378",
        help="Primary live test ticket key (default: TREN-378)",
    )
    parser.add_argument(
        "--log-file",
        type=str,
        default="qa/live/execution-log.md",
        help="Path to execution log markdown file",
    )
    parser.add_argument(
        "--json-output",
        type=str,
        default=None,
        help="Optional path to write raw JSON summary",
    )
    args = parser.parse_args()

    tier_map = {
        "unit": TestTier.UNIT,
        "integration": TestTier.INTEGRATION,
        "dry_run": TestTier.DRY_RUN_E2E,
        "live": TestTier.LIVE_E2E,
    }
    selected_tier = tier_map[args.tier]

    print("=" * 85)
    print(f"PM OPERATIONS AGENT — LIVE QA RUN & INTEGRITY AUDIT")
    print(f"Issue: {args.issue}")
    print(f"Tier: {selected_tier.value}")
    print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 85)

    if selected_tier == TestTier.LIVE_E2E and not settings.LIVE_QA_ENABLED:
        print("[INFO] Setting LIVE_QA_ENABLED=True for this explicit CLI run...")
        settings.LIVE_QA_ENABLED = True

    # Real external operations status
    jira_status = "REAL" if (selected_tier == TestTier.LIVE_E2E and settings.is_jira_configured()) else "NOT_USED"
    discord_status = "REAL" if (selected_tier == TestTier.LIVE_E2E and settings.is_discord_configured()) else "NOT_USED"
    mattermost_status = "REAL" if (selected_tier == TestTier.LIVE_E2E and settings.is_mattermost_configured()) else "NOT_USED"
    db_status = "REAL"

    print("\nREAL EXTERNAL OPERATIONS STATUS:")
    print(f"  Jira:       {jira_status}")
    print(f"  Discord:    {discord_status}")
    print(f"  Mattermost: {mattermost_status}")
    print(f"  Database:   {db_status}\n")

    runner = QARunner(tier=selected_tier, target_issue=args.issue)
    summary = runner.run_all_scenarios()

    print("-" * 85)
    print(f"{'Status':<16} | {'Test ID':<18} | {'Tier':<14} | {'Duration':<10} | Title")
    print("-" * 85)
    for r in summary.results:
        if r.status == QAStatus.PASS:
            status_str = "✅ PASS"
        elif r.status == QAStatus.NOT_EXECUTED:
            status_str = "⚠️ NOT_EXECUTED"
        elif r.status == QAStatus.INVALID_LIVE_TEST:
            status_str = "🛑 INVALID_LIVE"
        elif r.status == QAStatus.FAIL:
            status_str = "❌ FAIL"
        elif r.status == QAStatus.BLOCKED:
            status_str = "🚧 BLOCKED"
        elif r.status == QAStatus.SKIPPED:
            status_str = "⏭️ SKIPPED"
        else:
            status_str = str(r.status.value)

        print(f"{status_str:<16} | {r.test_id:<18} | {r.test_tier.value:<14} | {r.duration_ms:>6.2f}ms | {r.title}")
    print("-" * 85)

    print(f"\nSUMMARY STATISTICS:")
    print(f"  Total Tests:        {summary.total_tests}")
    print(f"  Passed:             {summary.passed}")
    print(f"  Failed:             {summary.failed}")
    print(f"  Not Executed:       {summary.not_executed}")
    print(f"  Invalid Live Tests: {summary.invalid_live_tests}")
    print(f"  Genuinely Live:     {summary.genuinely_live_count}")
    print(f"  Simulated/Internal: {summary.simulated_count}")
    print(f"  Live Integrity:     {summary.live_integrity_valid} Valid | {summary.live_integrity_invalid} Invalid")
    print(f"  Pass Rate (Active): {summary.pass_rate_percent}%\n")

    # Update execution log
    log_entry = f"""
## Run `{summary.run_id}` — {summary.completed_at}
- **Tier**: `{summary.tier.value}`
- **Target Issue**: `{summary.target_issue}`
- **Operations**: `Jira: {jira_status}` | `Discord: {discord_status}` | `Mattermost: {mattermost_status}` | `Database: {db_status}`
- **Total Tests**: `{summary.total_tests}` | **Passed**: `{summary.passed}` | **Failed**: `{summary.failed}` | **Not Executed**: `{summary.not_executed}`
- **Live Integrity**: `{summary.live_integrity_valid} Valid` | `{summary.live_integrity_invalid} Invalid`
- **Pass Rate (Active)**: `{summary.pass_rate_percent}%`

| Status | Test ID | Tier | Category | Title | Execution Mode | Duration | Evidence |
|:---:|:---|:---:|:---|:---|:---|:---:|:---|
"""
    for r in summary.results:
        if r.status == QAStatus.PASS:
            st = "✅ PASS"
        elif r.status == QAStatus.NOT_EXECUTED:
            st = "⚠️ NOT_EXECUTED"
        elif r.status == QAStatus.INVALID_LIVE_TEST:
            st = "🛑 INVALID_LIVE"
        else:
            st = f"❌ {r.status.value}"
        
        exec_mode_str = ", ".join(f"{k}:{v}" for k, v in r.execution_mode.items() if v != "NOT_USED") or "in_memory"
        log_entry += f"| {st} | `{r.test_id}` | `{r.test_tier.value}` | {r.category} | {r.title} | `{exec_mode_str}` | {r.duration_ms:.2f}ms | `{r.evidence_file or 'N/A'}` |\n"

    try:
        log_path = os.path.abspath(args.log_file)
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        if not os.path.exists(log_path):
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("# Live QA Execution Log\n\nChronological audit log of all QA test runs.\n")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(log_entry)
        print(f"Appended run results to: {log_path}")
    except Exception as e:
        print(f"Could not update log file: {e}")

    if summary.failed > 0 or summary.invalid_live_tests > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
