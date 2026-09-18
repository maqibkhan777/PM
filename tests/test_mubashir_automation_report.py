"""Behavior-based tests for Mubashir Automation Report Generator and Query Layer."""

import datetime
import json
import uuid
import zoneinfo
from typing import Any, Dict, Optional
from unittest.mock import patch, MagicMock, AsyncMock
import pytest

from app.config.settings import Settings, settings
from app.connectors.discord.formatter import DiscordFormatter, COLOR_GREEN
from app.core.reports.mubashir_report import (
    MubashirAutomationReportGenerator,
    format_date_human,
    resolve_calendar_day_window_utc,
)
from app.database.repositories import ActionRepository, DailyReportHistoryRepository
from app.services.scheduler import PeriodicScheduler


def _insert_action(
    temp_db,
    action_id: Optional[str] = None,
    action_type: str = "AddComment",
    target_system: str = "jira",
    target_id: str = "TREN-381",
    requested_by: str = "MubashirStaleSupport",
    status: str = "COMPLETED",
    dry_run: int = 0,
    created_at: str = "2026-09-18T05:00:00+00:00",
    executed_at: Optional[str] = "2026-09-18T05:00:01+00:00",
    parameters: Optional[Dict[str, Any]] = None,
    result_data: Optional[Dict[str, Any]] = None,
) -> str:
    aid = action_id or str(uuid.uuid4())
    params = parameters or {"title": "Test Issue Summary", "comment": "Automated reminder text"}
    res = result_data or {"id": "10050", "body": "comment response"}
    with temp_db.session() as conn:
        conn.execute(
            """
            INSERT INTO actions (
                id, action_id, idempotency_key, action_type, target_system,
                target_id, parameters, status, attempt_count, dry_run,
                requested_by, created_at, executed_at, result_data
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
            """,
            (
                aid, aid, f"key-{aid}", action_type, target_system,
                target_id, json.dumps(params), status, dry_run,
                requested_by, created_at, executed_at, json.dumps(res)
            )
        )
    return aid


# ==============================================================================
# 1. EMPTY STATE
# ==============================================================================

def test_report_empty_state(temp_db):
    """When no matching actions exist, return a structured report with total_comments=0 and empty groups."""
    gen = MubashirAutomationReportGenerator(manager=temp_db)
    report = gen.generate_report(target_date="2026-09-18")

    assert report["total_comments"] == 0
    assert report["date"] == "2026-09-18"
    assert report["formatted_date"] == "18 Sep 2026"
    assert report["by_automation"]["MubashirStaleSupport"] == []
    assert report["by_automation"]["MubashirSupportRule"] == []
    assert report["comments"] == []
    assert "window_start_utc" in report
    assert "window_end_utc" in report


# ==============================================================================
# 2. STALE SUPPORT COMPLETED ACTION
# ==============================================================================

def test_report_includes_stale_support_completed_action(temp_db):
    """Verify MubashirStaleSupport AddComment action is correctly queried and mapped."""
    _insert_action(
        temp_db,
        action_id="act-stale-1",
        requested_by="MubashirStaleSupport",
        action_type="AddComment",
        target_system="jira",
        target_id="TREN-381",
        status="COMPLETED",
        dry_run=0,
        executed_at="2026-09-18T04:30:00+00:00",
        parameters={"title": "Fix Broken Checkout", "comment": "3 days stale reminder"}
    )

    gen = MubashirAutomationReportGenerator(manager=temp_db)
    report = gen.generate_report(target_date="2026-09-18")

    assert report["total_comments"] == 1
    assert len(report["by_automation"]["MubashirStaleSupport"]) == 1
    assert len(report["by_automation"]["MubashirSupportRule"]) == 0

    item = report["by_automation"]["MubashirStaleSupport"][0]
    assert item["issue_key"] == "TREN-381"
    assert item["automation_source"] == "MubashirStaleSupport"
    assert item["summary"] == "Fix Broken Checkout"
    assert item["executed_at"] == "2026-09-18T04:30:00+00:00"
    assert "TREN-381" in item["jira_url"]


# ==============================================================================
# 3. SUPPORT RULE COMPLETED ACTION
# ==============================================================================

def test_report_includes_support_rule_completed_action(temp_db):
    """Verify MubashirSupportRule AddComment action is correctly queried and mapped."""
    _insert_action(
        temp_db,
        action_id="act-rule-1",
        requested_by="MubashirSupportRule",
        action_type="AddComment",
        target_system="jira",
        target_id="TREN-402",
        status="COMPLETED",
        dry_run=0,
        executed_at="2026-09-18T06:15:00+00:00",
        parameters={"title": "Missing Sprint Label Support", "comment": "Please assign sprint"}
    )

    gen = MubashirAutomationReportGenerator(manager=temp_db)
    report = gen.generate_report(target_date="2026-09-18")

    assert report["total_comments"] == 1
    assert len(report["by_automation"]["MubashirSupportRule"]) == 1
    assert len(report["by_automation"]["MubashirStaleSupport"]) == 0

    item = report["by_automation"]["MubashirSupportRule"][0]
    assert item["issue_key"] == "TREN-402"
    assert item["automation_source"] == "MubashirSupportRule"
    assert item["summary"] == "Missing Sprint Label Support"
    assert item["executed_at"] == "2026-09-18T06:15:00+00:00"


# ==============================================================================
# 4. MIXED AUTOMATION SOURCES
# ==============================================================================

def test_report_mixed_automation_sources(temp_db):
    """Verify report handles actions from both automations simultaneously."""
    _insert_action(
        temp_db,
        action_id="act-stale-1",
        requested_by="MubashirStaleSupport",
        target_id="TREN-381",
        executed_at="2026-09-18T02:00:00+00:00"
    )
    _insert_action(
        temp_db,
        action_id="act-stale-2",
        requested_by="MubashirStaleSupport",
        target_id="TREN-394",
        executed_at="2026-09-18T03:00:00+00:00"
    )
    _insert_action(
        temp_db,
        action_id="act-rule-1",
        requested_by="MubashirSupportRule",
        target_id="TREN-402",
        executed_at="2026-09-18T05:00:00+00:00"
    )

    gen = MubashirAutomationReportGenerator(manager=temp_db)
    report = gen.generate_report(target_date="2026-09-18")

    assert report["total_comments"] == 3
    assert len(report["by_automation"]["MubashirStaleSupport"]) == 2
    assert len(report["by_automation"]["MubashirSupportRule"]) == 1
    assert [x["issue_key"] for x in report["by_automation"]["MubashirStaleSupport"]] == ["TREN-381", "TREN-394"]
    assert [x["issue_key"] for x in report["by_automation"]["MubashirSupportRule"]] == ["TREN-402"]


# ==============================================================================
# 5. EXCLUDES DRY RUN
# ==============================================================================

def test_report_excludes_dry_run(temp_db):
    """Verify DRY_RUN_SIMULATED and dry_run=1 actions are strictly excluded."""
    _insert_action(
        temp_db,
        action_id="act-dry-1",
        requested_by="MubashirStaleSupport",
        target_id="TREN-381",
        status="DRY_RUN_SIMULATED",
        dry_run=1,
        executed_at="2026-09-18T04:00:00+00:00"
    )

    gen = MubashirAutomationReportGenerator(manager=temp_db)
    report = gen.generate_report(target_date="2026-09-18")
    assert report["total_comments"] == 0
    assert report["comments"] == []


# ==============================================================================
# 6. EXCLUDES FAILED
# ==============================================================================

def test_report_excludes_failed(temp_db):
    """Verify FAILED actions are strictly excluded."""
    _insert_action(
        temp_db,
        action_id="act-fail-1",
        requested_by="MubashirSupportRule",
        target_id="TREN-402",
        status="FAILED",
        dry_run=0,
        executed_at="2026-09-18T04:00:00+00:00"
    )

    gen = MubashirAutomationReportGenerator(manager=temp_db)
    report = gen.generate_report(target_date="2026-09-18")
    assert report["total_comments"] == 0


# ==============================================================================
# 7. EXCLUDES UNRELATED ADD_COMMENT
# ==============================================================================

def test_report_excludes_unrelated_add_comment(temp_db):
    """Verify AddComment actions from unrelated callers (e.g. PM, ActiveEpicReview) are excluded."""
    _insert_action(
        temp_db,
        action_id="act-pm-1",
        requested_by="PM",
        action_type="AddComment",
        target_system="jira",
        target_id="TREN-100",
        status="COMPLETED",
        dry_run=0,
        executed_at="2026-09-18T04:00:00+00:00"
    )
    _insert_action(
        temp_db,
        action_id="act-epic-1",
        requested_by="ActiveEpicReview",
        action_type="AddComment",
        target_system="jira",
        target_id="TREN-200",
        status="COMPLETED",
        dry_run=0,
        executed_at="2026-09-18T04:30:00+00:00"
    )

    gen = MubashirAutomationReportGenerator(manager=temp_db)
    report = gen.generate_report(target_date="2026-09-18")
    assert report["total_comments"] == 0


# ==============================================================================
# 8. EXCLUDES NON-JIRA ACTIONS
# ==============================================================================

def test_report_excludes_non_jira_action(temp_db):
    """Verify actions targeting discord or other systems are excluded."""
    _insert_action(
        temp_db,
        action_id="act-disc-1",
        requested_by="MubashirSupportRule",
        action_type="AddComment",
        target_system="discord",
        target_id="channel-123",
        status="COMPLETED",
        dry_run=0,
        executed_at="2026-09-18T04:00:00+00:00"
    )

    gen = MubashirAutomationReportGenerator(manager=temp_db)
    report = gen.generate_report(target_date="2026-09-18")
    assert report["total_comments"] == 0


# ==============================================================================
# 9. TIMEZONE BOUNDARIES & UTC CONVERSION
# ==============================================================================

def test_report_timezone_boundaries(temp_db):
    """Verify boundary handling against Asia/Karachi (UTC+5):
    - 2026-09-18 00:00:00 PKT = 2026-09-17 19:00:00 UTC (INCLUDED)
    - 2026-09-18 23:59:59 PKT = 2026-09-18 18:59:59 UTC (INCLUDED)
    - 2026-09-17 18:59:59 UTC = 2026-09-17 23:59:59 PKT (EXCLUDED)
    - 2026-09-18 19:00:00 UTC = 2026-09-19 00:00:00 PKT (EXCLUDED)
    """
    # 1. Just inside lower boundary: 00:00:00 PKT -> 19:00:00 UTC prev day
    _insert_action(
        temp_db,
        action_id="act-bound-start",
        target_id="TREN-START",
        executed_at="2026-09-17T19:00:00+00:00"
    )
    # 2. Inside upper boundary: 23:59:59 PKT -> 18:59:59 UTC
    _insert_action(
        temp_db,
        action_id="act-bound-end",
        target_id="TREN-END",
        executed_at="2026-09-18T18:59:59+00:00"
    )
    # 3. Just outside lower boundary: 1 second before start
    _insert_action(
        temp_db,
        action_id="act-bound-before",
        target_id="TREN-BEFORE",
        executed_at="2026-09-17T18:59:59+00:00"
    )
    # 4. Outside upper boundary: exactly next day 00:00:00 PKT -> 19:00:00 UTC
    _insert_action(
        temp_db,
        action_id="act-bound-after",
        target_id="TREN-AFTER",
        executed_at="2026-09-18T19:00:00+00:00"
    )

    gen = MubashirAutomationReportGenerator(manager=temp_db)
    report = gen.generate_report(target_date="2026-09-18")

    assert report["window_start_utc"] == "2026-09-17T19:00:00+00:00"
    assert report["window_end_utc"] == "2026-09-18T19:00:00+00:00"

    keys = [c["issue_key"] for c in report["comments"]]
    assert "TREN-START" in keys
    assert "TREN-END" in keys
    assert "TREN-BEFORE" not in keys
    assert "TREN-AFTER" not in keys
    assert report["total_comments"] == 2


# ==============================================================================
# 10. ORDERS BY EXECUTION TIME
# ==============================================================================

def test_report_orders_by_execution_time(temp_db):
    """Verify deterministic chronological ordering regardless of insertion order."""
    # Insert in reverse order: 15:00, 08:00, 11:00
    _insert_action(
        temp_db,
        action_id="act-3",
        target_id="TREN-LATE",
        executed_at="2026-09-18T15:00:00+00:00"
    )
    _insert_action(
        temp_db,
        action_id="act-1",
        target_id="TREN-EARLY",
        executed_at="2026-09-18T08:00:00+00:00"
    )
    _insert_action(
        temp_db,
        action_id="act-2",
        target_id="TREN-MID",
        executed_at="2026-09-18T11:00:00+00:00"
    )

    gen = MubashirAutomationReportGenerator(manager=temp_db)
    report = gen.generate_report(target_date="2026-09-18")

    assert [c["issue_key"] for c in report["comments"]] == ["TREN-EARLY", "TREN-MID", "TREN-LATE"]


# ==============================================================================
# 11. PRESERVES DUPLICATE ISSUE ACTIONS
# ==============================================================================

def test_report_preserves_duplicate_issue_actions(temp_db):
    """Verify distinct completed actions for the same Jira issue are not collapsed."""
    _insert_action(
        temp_db,
        action_id="act-dup-1",
        target_id="TREN-381",
        executed_at="2026-09-18T08:00:00+00:00",
        parameters={"title": "TREN-381 Morning", "comment": "Morning reminder"}
    )
    _insert_action(
        temp_db,
        action_id="act-dup-2",
        target_id="TREN-381",
        executed_at="2026-09-18T16:00:00+00:00",
        parameters={"title": "TREN-381 Afternoon", "comment": "Afternoon reminder"}
    )

    gen = MubashirAutomationReportGenerator(manager=temp_db)
    report = gen.generate_report(target_date="2026-09-18")

    assert report["total_comments"] == 2
    assert len(report["by_automation"]["MubashirStaleSupport"]) == 2
    assert report["comments"][0]["action_id"] == "act-dup-1"
    assert report["comments"][1]["action_id"] == "act-dup-2"


# ==============================================================================
# 12. DOES NOT CALL JIRA
# ==============================================================================

def test_report_does_not_call_jira(temp_db):
    """Verify generator performs purely read-only SQLite queries with zero external Jira calls."""
    _insert_action(
        temp_db,
        action_id="act-stale-1",
        target_id="TREN-381",
        executed_at="2026-09-18T05:00:00+00:00"
    )

    with patch("httpx.Client.request") as mock_http, patch("httpx.AsyncClient.request") as mock_async_http:
        gen = MubashirAutomationReportGenerator(manager=temp_db)
        report = gen.generate_report(target_date="2026-09-18")

        assert report["total_comments"] == 1
        mock_http.assert_not_called()
        mock_async_http.assert_not_called()


# ==============================================================================
# 13. FORMATTER: POPULATED REPORT
# ==============================================================================

def test_mubashir_formatter_populated_report():
    """Verify title, date, total, both automation sections, and ticket links in populated report."""
    report_data = {
        "formatted_date": "18 Sep 2026",
        "total_comments": 3,
        "by_automation": {
            "MubashirStaleSupport": [
                {"issue_key": "TREN-381", "jira_url": settings.get_jira_browse_url("TREN-381")},
                {"issue_key": "TREN-394", "jira_url": settings.get_jira_browse_url("TREN-394")},
            ],
            "MubashirSupportRule": [
                {"issue_key": "TREN-402", "jira_url": settings.get_jira_browse_url("TREN-402")},
            ],
        },
    }
    payload = DiscordFormatter.format_mubashir_automation_report(report_data)
    assert "embeds" in payload
    assert len(payload["embeds"]) == 1
    embed = payload["embeds"][0]

    assert embed["title"] == "🤖 Mubashir Automation Report"
    assert "**Date:** 18 Sep 2026" in embed["description"]
    assert "**Total Comments Added:** 3" in embed["description"]
    assert "🕒 **MUBASHIR_STALE_SUPPORT**" in embed["description"]
    assert "⚙️ **MUBASHIR_SUPPORT_RULE**" in embed["description"]

    # Clickable Jira Markdown links
    link_381 = f"[{'TREN-381'}]({settings.get_jira_browse_url('TREN-381')}) — Mubashir stale support reminder"
    link_394 = f"[{'TREN-394'}]({settings.get_jira_browse_url('TREN-394')}) — Mubashir stale support reminder"
    link_402 = f"[{'TREN-402'}]({settings.get_jira_browse_url('TREN-402')}) — Mubashir support creation workflow comment"

    assert link_381 in embed["description"]
    assert link_394 in embed["description"]
    assert link_402 in embed["description"]
    assert embed["footer"]["text"] == "PM Operations Agent — Mubashir Automation"


# ==============================================================================
# 14. FORMATTER: STALE SUPPORT ONLY
# ==============================================================================

def test_mubashir_formatter_stale_support_only():
    """Verify correct section and links when only MubashirStaleSupport has actions."""
    report_data = {
        "formatted_date": "18 Sep 2026",
        "total_comments": 2,
        "by_automation": {
            "MubashirStaleSupport": [
                {"issue_key": "TREN-381", "jira_url": settings.get_jira_browse_url("TREN-381")},
                {"issue_key": "TREN-394", "jira_url": settings.get_jira_browse_url("TREN-394")},
            ],
            "MubashirSupportRule": [],
        },
    }
    payload = DiscordFormatter.format_mubashir_automation_report(report_data)
    desc = payload["embeds"][0]["description"]
    assert "🕒 **MUBASHIR_STALE_SUPPORT**" in desc
    assert "TREN-381" in desc
    assert "TREN-394" in desc
    assert "MUBASHIR_SUPPORT_RULE" not in desc


# ==============================================================================
# 15. FORMATTER: SUPPORT RULE ONLY
# ==============================================================================

def test_mubashir_formatter_support_rule_only():
    """Verify correct section and links when only MubashirSupportRule has actions."""
    report_data = {
        "formatted_date": "18 Sep 2026",
        "total_comments": 1,
        "by_automation": {
            "MubashirStaleSupport": [],
            "MubashirSupportRule": [
                {"issue_key": "TREN-402", "jira_url": settings.get_jira_browse_url("TREN-402")},
            ],
        },
    }
    payload = DiscordFormatter.format_mubashir_automation_report(report_data)
    desc = payload["embeds"][0]["description"]
    assert "⚙️ **MUBASHIR_SUPPORT_RULE**" in desc
    assert "TREN-402" in desc
    assert "MUBASHIR_STALE_SUPPORT" not in desc


# ==============================================================================
# 16. FORMATTER: EMPTY STATE
# ==============================================================================

def test_mubashir_formatter_empty_state():
    """Verify green/empty state, exact no-comments message, and no automation sections."""
    report_data = {
        "formatted_date": "18 Sep 2026",
        "total_comments": 0,
        "by_automation": {
            "MubashirStaleSupport": [],
            "MubashirSupportRule": [],
        },
    }
    payload = DiscordFormatter.format_mubashir_automation_report(report_data)
    assert len(payload["embeds"]) == 1
    embed = payload["embeds"][0]
    assert embed["title"] == "🤖 Mubashir Automation Report"
    assert embed["color"] == COLOR_GREEN
    assert embed["description"] == "**Date:** 18 Sep 2026\n\n✅ No Mubashir automation comments were added."
    assert "MUBASHIR_STALE_SUPPORT" not in embed["description"]
    assert "MUBASHIR_SUPPORT_RULE" not in embed["description"]
    assert embed["footer"]["text"] == "PM Operations Agent — Mubashir Automation"


# ==============================================================================
# 17. FORMATTER: USES CANONICAL JIRA URLS
# ==============================================================================

def test_mubashir_formatter_uses_canonical_jira_urls():
    """Verify URL generation uses existing settings/helper."""
    report_data = {
        "formatted_date": "18 Sep 2026",
        "total_comments": 1,
        "by_automation": {
            "MubashirStaleSupport": [{"issue_key": "TREN-777"}],
            "MubashirSupportRule": [],
        },
    }
    prev_url = settings.JIRA_BASE_URL
    settings.JIRA_BASE_URL = "https://canonical-jira.company.com"
    try:
        payload = DiscordFormatter.format_mubashir_automation_report(report_data)
        desc = payload["embeds"][0]["description"]
        expected_url = settings.get_jira_browse_url("TREN-777")
        assert expected_url == "https://canonical-jira.company.com/browse/TREN-777"
        assert f"[TREN-777]({expected_url})" in desc
    finally:
        settings.JIRA_BASE_URL = prev_url


# ==============================================================================
# 18. FORMATTER: NO RAW URLS
# ==============================================================================

def test_mubashir_formatter_no_raw_urls():
    """Verify URLs only appear as Markdown links, never as raw URLs."""
    import re
    report_data = {
        "formatted_date": "18 Sep 2026",
        "total_comments": 3,
        "by_automation": {
            "MubashirStaleSupport": [
                {"issue_key": "TREN-381"},
                {"issue_key": "TREN-394"},
            ],
            "MubashirSupportRule": [
                {"issue_key": "TREN-402"},
            ],
        },
    }
    payload = DiscordFormatter.format_mubashir_automation_report(report_data)
    url_pattern = re.compile(r"https?://[^\s)]+")
    for embed in payload["embeds"]:
        desc = embed["description"]
        urls = url_pattern.findall(desc)
        assert len(urls) > 0
        for u in urls:
            assert f"]({u})" in desc


# ==============================================================================
# 19. FORMATTER: NO ASCII TABLE
# ==============================================================================

def test_mubashir_formatter_no_ascii_table():
    """Verify no ASCII or monospace tables are present."""
    report_data = {
        "formatted_date": "18 Sep 2026",
        "total_comments": 2,
        "by_automation": {
            "MubashirStaleSupport": [{"issue_key": "TREN-100"}],
            "MubashirSupportRule": [{"issue_key": "TREN-200"}],
        },
    }
    payload = DiscordFormatter.format_mubashir_automation_report(report_data)
    for embed in payload["embeds"]:
        desc = embed["description"]
        assert "+---" not in desc
        assert "|---" not in desc
        assert "| " not in desc
        assert "```" not in desc


# ==============================================================================
# 20. FORMATTER: OVERFLOW AND BUDGETING
# ==============================================================================

def test_mubashir_formatter_overflow():
    """Verify multiple embeds, budget <= 5800, description <= 3800, intact lines, and explicit omission line."""
    items_stale = [{"issue_key": f"TREN-{1000 + i}"} for i in range(80)]
    items_rule = [{"issue_key": f"TREN-{2000 + i}"} for i in range(70)]
    report_data = {
        "formatted_date": "18 Sep 2026",
        "total_comments": 150,
        "by_automation": {
            "MubashirStaleSupport": items_stale,
            "MubashirSupportRule": items_rule,
        },
    }
    payload = DiscordFormatter.format_mubashir_automation_report(report_data)
    embeds = payload["embeds"]
    assert len(embeds) > 1
    assert len(embeds) <= 10

    # Part naming convention
    assert embeds[0]["title"] == "🤖 Mubashir Automation Report"
    assert embeds[1]["title"] == "🤖 Mubashir Automation Report (Part 2)"

    cumulative_budget = 0
    for embed in embeds:
        desc = embed["description"]
        title = embed["title"]
        footer = embed["footer"]["text"]
        assert len(desc) <= 3800
        cumulative_budget += len(title) + len(desc) + len(footer)
        for line in desc.split("\n"):
            if line.startswith("• "):
                assert "—" in line or line.startswith("• ... and ")

    assert cumulative_budget <= 5800

    all_text = " ".join(e["description"] for e in embeds)
    assert "more automation comment(s)" in all_text


# ==============================================================================
# 21. FORMATTER: PRESERVES ORDER
# ==============================================================================

def test_mubashir_formatter_preserves_order():
    """Verify automation groups and ticket order remain deterministic."""
    stale_keys = ["TREN-10", "TREN-20", "TREN-30"]
    rule_keys = ["TREN-40", "TREN-50", "TREN-60"]
    report_data = {
        "formatted_date": "18 Sep 2026",
        "total_comments": 6,
        "by_automation": {
            "MubashirStaleSupport": [{"issue_key": k} for k in stale_keys],
            "MubashirSupportRule": [{"issue_key": k} for k in rule_keys],
        },
    }
    payload = DiscordFormatter.format_mubashir_automation_report(report_data)
    desc = payload["embeds"][0]["description"]

    idx_stale_header = desc.index("MUBASHIR_STALE_SUPPORT")
    idx_rule_header = desc.index("MUBASHIR_SUPPORT_RULE")
    assert idx_stale_header < idx_rule_header

    pos = 0
    for k in stale_keys + rule_keys:
        k_pos = desc.index(k, pos)
        assert k_pos >= pos
        pos = k_pos


# ==============================================================================
# 22. FORMATTER: UNEXPECTED SOURCE DOES NOT CRASH
# ==============================================================================

def test_mubashir_formatter_unexpected_source_does_not_crash():
    """Provide an unknown automation key and verify safe rendering without crashing."""
    report_data = {
        "formatted_date": "18 Sep 2026",
        "total_comments": 1,
        "by_automation": {
            "CustomUnregisteredAutomation": [
                {"issue_key": "TREN-999", "summary": "Custom task title"}
            ]
        },
    }
    payload = DiscordFormatter.format_mubashir_automation_report(report_data)
    assert len(payload["embeds"]) == 1
    desc = payload["embeds"][0]["description"]
    assert "TREN-999" in desc
    assert "CUSTOMUNREGISTEREDAUTOMATION" in desc
    assert "Custom task title" in desc


# ==============================================================================
# 23. FORMATTER: TEXT FALLBACK
# ==============================================================================

def test_mubashir_formatter_text_fallback():
    """Verify plain-text markdown fallback works cleanly with clickable links and no tables."""
    report_data = {
        "formatted_date": "18 Sep 2026",
        "total_comments": 2,
        "by_automation": {
            "MubashirStaleSupport": [{"issue_key": "TREN-381"}],
            "MubashirSupportRule": [{"issue_key": "TREN-402"}],
        },
    }
    text = DiscordFormatter.format_mubashir_automation_report_text(report_data)
    assert "🤖 **Mubashir Automation Report**" in text
    assert "TREN-381" in text
    assert "TREN-402" in text
    assert "|---" not in text


# ==============================================================================
# 24. SETTINGS DEFAULTS (Phase 7D Requirement 1)
# ==============================================================================

def test_mubashir_automation_report_settings_defaults():
    """Verify settings defaults: disabled by default, 08:40, Asia/Karachi, channel None."""
    s = Settings()
    assert s.MUBASHIR_AUTOMATION_REPORT_ENABLED is False
    assert s.MUBASHIR_AUTOMATION_REPORT_TIME == "08:40"
    assert s.MUBASHIR_AUTOMATION_REPORT_TIMEZONE == "Asia/Karachi"
    assert s.MUBASHIR_AUTOMATION_REPORT_CHANNEL is None


# ==============================================================================
# 25. DISABLED FLAG SKIPS SCHEDULER (Phase 7D Requirement 2)
# ==============================================================================

@pytest.mark.asyncio
async def test_scheduler_skips_when_mubashir_report_disabled(temp_db):
    """When MUBASHIR_AUTOMATION_REPORT_ENABLED is False, scheduler returns None immediately."""
    scheduler = PeriodicScheduler(manager=temp_db)
    with patch.object(settings, "MUBASHIR_AUTOMATION_REPORT_ENABLED", False):
        res = await scheduler._evaluate_mubashir_automation_report()
        assert res is None


# ==============================================================================
# 26. BEFORE SCHEDULED TIME SKIPS (Phase 7D Requirement 3)
# ==============================================================================

@pytest.mark.asyncio
async def test_scheduler_skips_before_scheduled_time(temp_db):
    """When current local time < MUBASHIR_AUTOMATION_REPORT_TIME, scheduler returns None."""
    scheduler = PeriodicScheduler(manager=temp_db)
    # 08:20 PKT is before 08:40
    fake_now = datetime.datetime(2026, 9, 19, 8, 20, tzinfo=zoneinfo.ZoneInfo("Asia/Karachi"))
    with patch.object(settings, "MUBASHIR_AUTOMATION_REPORT_ENABLED", True), \
         patch.object(settings, "MUBASHIR_AUTOMATION_REPORT_TIME", "08:40"), \
         patch("app.services.scheduler.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        res = await scheduler._evaluate_mubashir_automation_report()
        assert res is None


# ==============================================================================
# 27. AT/AFTER SCHEDULED TIME EXECUTES (Phase 7D Requirement 4)
# ==============================================================================

@pytest.mark.asyncio
async def test_scheduler_executes_at_or_after_scheduled_time(temp_db):
    """When current local time >= MUBASHIR_AUTOMATION_REPORT_TIME, scheduler triggers dispatch."""
    scheduler = PeriodicScheduler(manager=temp_db)
    fake_now = datetime.datetime(2026, 9, 19, 8, 40, tzinfo=zoneinfo.ZoneInfo("Asia/Karachi"))
    with patch.object(settings, "MUBASHIR_AUTOMATION_REPORT_ENABLED", True), \
         patch.object(settings, "MUBASHIR_AUTOMATION_REPORT_TIME", "08:40"), \
         patch.object(settings, "DRY_RUN", True), \
         patch("app.services.scheduler.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        res = await scheduler._evaluate_mubashir_automation_report()
        assert res is not None
        assert res["status"] in ("simulated", "sent")
        assert res["date"] == "2026-09-18"
        assert res["team_name"] == "Mursaleen Cluster"


# ==============================================================================
# 28. PREVIOUS KARACHI CALENDAR DAY SELECTED (Phase 7D Requirement 5)
# ==============================================================================

@pytest.mark.asyncio
async def test_previous_karachi_calendar_day_selected(temp_db):
    """Verify target date is previous calendar day in Asia/Karachi, not UTC date subtraction."""
    scheduler = PeriodicScheduler(manager=temp_db)
    # In PKT: 2026-09-19 09:00:00 -> previous day is 2026-09-18
    fake_now = datetime.datetime(2026, 9, 19, 9, 0, tzinfo=zoneinfo.ZoneInfo("Asia/Karachi"))
    with patch.object(settings, "MUBASHIR_AUTOMATION_REPORT_ENABLED", True), \
         patch.object(settings, "DRY_RUN", True), \
         patch("app.services.scheduler.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        res = await scheduler._evaluate_mubashir_automation_report()
        assert res is not None
        assert res["date"] == "2026-09-18"

    # Explicit target_date resolution
    date_str, formatted_date, start_utc, end_utc = resolve_calendar_day_window_utc(
        target_date="2026-09-18",
        tz_name="Asia/Karachi",
    )
    assert date_str == "2026-09-18"
    assert formatted_date == "18 Sep 2026"
    assert "2026-09-17T19:00:00+00:00" in start_utc
    assert "2026-09-18T19:00:00+00:00" in end_utc

    # History key check
    history_repo = DailyReportHistoryRepository(temp_db)
    assert history_repo.has_report_been_sent("Mursaleen Cluster", "2026-09-18", "mubashir_automation_report") is True

    # When no target_date is passed to resolve_calendar_day_window_utc with mocked now
    class MockDateTime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return fake_now

    with patch("app.core.reports.mubashir_report.datetime.datetime", MockDateTime):
        date_str_auto, _, _, _ = resolve_calendar_day_window_utc(
            target_date=None,
            tz_name="Asia/Karachi",
        )
        assert date_str_auto == "2026-09-18"


# ==============================================================================
# 29. DUPLICATE HISTORY PREVENTS SECOND DISPATCH (Phase 7D Requirement 6)
# ==============================================================================

@pytest.mark.asyncio
async def test_duplicate_history_prevents_second_dispatch(temp_db):
    """Ensure duplicate history prevents repeated dispatch across scheduler runs."""
    gen = MubashirAutomationReportGenerator(manager=temp_db)
    with patch.object(settings, "DRY_RUN", True):
        first_res = await gen.send_report_to_discord(target_date="2026-09-18", force=False)
        assert first_res["status"] in ("simulated", "sent")
        assert first_res["recorded_history"] is True

        # Second dispatch without force must skip
        second_res = await gen.send_report_to_discord(target_date="2026-09-18", force=False)
        assert second_res["status"] == "skipped_duplicate"
        assert second_res["recorded_history"] is False


# ==============================================================================
# 30. FORCE=TRUE BYPASSES DUPLICATE PROTECTION (Phase 7D Requirement 7)
# ==============================================================================

@pytest.mark.asyncio
async def test_force_bypasses_duplicate_protection(temp_db):
    """force=True bypasses duplicate protection and redispatches."""
    gen = MubashirAutomationReportGenerator(manager=temp_db)
    with patch.object(settings, "DRY_RUN", True):
        # Seed history
        gen.history_repo.record_report_sent(
            team_group="Mursaleen Cluster",
            report_date="2026-09-18",
            payload={"total_comments": 0},
            report_type="mubashir_automation_report",
        )
        assert gen.history_repo.has_report_been_sent("Mursaleen Cluster", "2026-09-18", "mubashir_automation_report") is True

        # Calling with force=True must bypass
        forced_res = await gen.send_report_to_discord(target_date="2026-09-18", force=True)
        assert forced_res["status"] in ("simulated", "sent")


# ==============================================================================
# 31. DRY_RUN RESULTS IN SIMULATION AND NO HTTP (Phase 7D Requirement 8)
# ==============================================================================

@pytest.mark.asyncio
async def test_dry_run_results_in_simulation_no_http(temp_db):
    """Under DRY_RUN=True, action is simulated and zero outbound HTTP requests are made."""
    gen = MubashirAutomationReportGenerator(manager=temp_db)
    with patch.object(settings, "DRY_RUN", True), \
         patch("httpx.AsyncClient.post") as mock_post, \
         patch("httpx.Client.request") as mock_sync_http, \
         patch("httpx.AsyncClient.request") as mock_async_http:
        res = await gen.send_report_to_discord(target_date="2026-09-18", force=True)
        assert res["status"] == "simulated"
        mock_post.assert_not_called()
        mock_sync_http.assert_not_called()
        mock_async_http.assert_not_called()


# ==============================================================================
# 32. REPORT INDEPENDENT OF MUTATION FLAGS (Phase 7D Requirement 9)
# ==============================================================================

@pytest.mark.asyncio
async def test_report_independent_of_mubashir_mutation_flags(temp_db):
    """Verify report includes actions even when both MUBASHIR_STALE_SUPPORT_ENABLED and MUBASHIR_SUPPORT_RULE_ENABLED are false."""
    _insert_action(
        temp_db,
        action_id="act-stale-flag-test",
        requested_by="MubashirStaleSupport",
        target_id="TREN-111",
        executed_at="2026-09-18T05:00:00+00:00",
    )
    _insert_action(
        temp_db,
        action_id="act-rule-flag-test",
        requested_by="MubashirSupportRule",
        target_id="TREN-222",
        executed_at="2026-09-18T06:00:00+00:00",
    )

    with patch.object(settings, "MUBASHIR_STALE_SUPPORT_ENABLED", False), \
         patch.object(settings, "MUBASHIR_SUPPORT_RULE_ENABLED", False), \
         patch.object(settings, "MUBASHIR_AUTOMATION_REPORT_ENABLED", True), \
         patch.object(settings, "DRY_RUN", True):
        gen = MubashirAutomationReportGenerator(manager=temp_db)
        res = await gen.send_report_to_discord(target_date="2026-09-18", force=True)
        assert res["status"] == "simulated"
        assert res["total_comments"] == 2
        assert len(res["report"]["by_automation"]["MubashirStaleSupport"]) == 1
        assert len(res["report"]["by_automation"]["MubashirSupportRule"]) == 1


# ==============================================================================
# 33. EMPTY REPORT IS DISPATCHABLE (Phase 7D Requirement 10)
# ==============================================================================

@pytest.mark.asyncio
async def test_empty_report_is_dispatchable(temp_db):
    """Empty report (0 comments) is safely formatted, dispatched, and recorded in history."""
    gen = MubashirAutomationReportGenerator(manager=temp_db)
    with patch.object(settings, "DRY_RUN", True):
        res = await gen.send_report_to_discord(target_date="2026-09-18", force=True)
        assert res["status"] == "simulated"
        assert res["total_comments"] == 0
        assert res["recorded_history"] is True


# ==============================================================================
# 34. SCHEDULER EXCEPTION ISOLATION (Phase 7D Requirement 11)
# ==============================================================================

@pytest.mark.asyncio
async def test_scheduler_exception_isolation(temp_db):
    """Exceptions raised in Mubashir Automation Report do not crash scheduler or cycle."""
    scheduler = PeriodicScheduler(manager=temp_db)
    fake_now = datetime.datetime(2026, 9, 19, 9, 0, tzinfo=zoneinfo.ZoneInfo("Asia/Karachi"))
    with patch.object(settings, "MUBASHIR_AUTOMATION_REPORT_ENABLED", True), \
         patch.object(settings, "DAILY_WORKLOG_REPORT_ENABLED", False), \
         patch.object(settings, "PERFORMANCE_ANALYSIS_ENABLED", False), \
         patch("app.services.scheduler.datetime") as mock_dt, \
         patch("app.core.reports.mubashir_report.MubashirAutomationReportGenerator.send_report_to_discord", side_effect=RuntimeError("Discord failure")):
        mock_dt.now.return_value = fake_now
        # _evaluate_mubashir_automation_report catches its own exception and returns status="failed"
        res = await scheduler._evaluate_mubashir_automation_report()
        assert res is not None
        assert res["status"] == "failed"

        # Also verify run_cycle does not crash
        cycle_res = await scheduler.run_cycle()
        assert cycle_res is not None
        assert cycle_res["mubashir_report_status"] == "failed"


# ==============================================================================
# 35. REPORT GENERATION MAKES ZERO JIRA API CALLS (Phase 7D Requirement 12)
# ==============================================================================

@pytest.mark.asyncio
async def test_report_generation_and_dispatch_makes_zero_jira_calls(temp_db):
    """Verify end-to-end report generation and dispatch makes zero Jira API calls."""
    _insert_action(
        temp_db,
        action_id="act-zero-jira",
        requested_by="MubashirStaleSupport",
        target_id="TREN-555",
        executed_at="2026-09-18T05:00:00+00:00",
    )
    gen = MubashirAutomationReportGenerator(manager=temp_db)
    with patch.object(settings, "DRY_RUN", True), \
         patch("httpx.AsyncClient.request") as mock_async_req, \
         patch("httpx.Client.request") as mock_sync_req:
        res = await gen.send_report_to_discord(target_date="2026-09-18", force=True)
        assert res["total_comments"] == 1
        mock_async_req.assert_not_called()
        mock_sync_req.assert_not_called()


# ==============================================================================
# 36. MUBASHIR SUPPORT RULE CONCISE PM COMMENT WORDING (Phase 7D.2 Correction 1)
# ==============================================================================

def test_mubashir_support_rule_concise_pm_comment_wording(temp_db):
    """Verify MubashirSupportRule generates concise, natural PM comment wording without robotic jargon."""
    from app.core.rules.mubashir_support_rule import MubashirSupportRule, MUBASHIR_CANONICAL_ACCOUNT_ID
    from app.core.events.types import TaskCreated

    with patch.object(settings, "MUBASHIR_SUPPORT_RULE_ENABLED", True):
        rule = MubashirSupportRule(temp_db)

        # 1. Missing both sprint and label
        event_both = TaskCreated(
            source="jira",
            task_key="TREN-100",
            title="Support Ticket Both Missing",
            actor_id=MUBASHIR_CANONICAL_ACCOUNT_ID,
            actor_name="Mubashir Butt",
            issue_type="Support",
            project_key="TREN",
            payload={"issue": {"fields": {"customfield_10020": [], "labels": []}}}
        )
        actions_both = rule.evaluate(event_both)
        assert len(actions_both) == 2
        comment_both = actions_both[0].parameters["comment"]
        assert f"[~accountid:{MUBASHIR_CANONICAL_ACCOUNT_ID}]" in comment_both
        assert "approved sprint" in comment_both
        assert "required label" in comment_both
        # Ensure professional PM style: concise, <= 2 sentences, no robotic phrases
        assert "Automated PM Workflow Check" not in comment_both
        assert "operational reminder" not in comment_both
        assert "• **Sprint Requirement:**" not in comment_both
        assert "• **Label Requirement:**" not in comment_both
        assert "triage" not in comment_both
        assert comment_both == (
            f"[~accountid:{MUBASHIR_CANONICAL_ACCOUNT_ID}] "
            "Please make sure this ticket is added to the approved sprint and has the required label."
        )

        # 2. Missing sprint only
        event_sprint = TaskCreated(
            source="jira",
            task_key="TREN-101",
            title="Support Ticket Missing Sprint",
            actor_id=MUBASHIR_CANONICAL_ACCOUNT_ID,
            actor_name="Mubashir Butt",
            issue_type="Support",
            project_key="TREN",
            payload={"issue": {"fields": {"customfield_10020": [], "labels": ["free"]}}}
        )
        actions_sprint = rule.evaluate(event_sprint)
        assert len(actions_sprint) == 2
        comment_sprint = actions_sprint[0].parameters["comment"]
        assert comment_sprint == (
            f"[~accountid:{MUBASHIR_CANONICAL_ACCOUNT_ID}] "
            "Please make sure this ticket is added to the approved sprint."
        )
        assert "Automated" not in comment_sprint

        # 3. Missing label only
        event_label = TaskCreated(
            source="jira",
            task_key="TREN-102",
            title="Support Ticket Missing Label",
            actor_id=MUBASHIR_CANONICAL_ACCOUNT_ID,
            actor_name="Mubashir Butt",
            issue_type="Support",
            project_key="TREN",
            payload={"issue": {"fields": {"customfield_10020": [{"name": "Support Board"}], "labels": []}}}
        )
        actions_label = rule.evaluate(event_label)
        assert len(actions_label) == 2
        comment_label = actions_label[0].parameters["comment"]
        assert comment_label == (
            f"[~accountid:{MUBASHIR_CANONICAL_ACCOUNT_ID}] "
            "Please make sure this ticket has the required label."
        )
        assert "Automated" not in comment_label

        # Verify missing_reasons in Discord notification action is preserved
        notif_action = actions_both[1]
        assert "Missing approved sprint" in notif_action.parameters["message"]
        assert "Missing product/service label" in notif_action.parameters["message"]


# ==============================================================================
# 37. CANONICAL JIRA URL WITH DYNAMIC JIRA_BASE_URL (Phase 7D.2 Correction 2)
# ==============================================================================

def test_report_canonical_jira_url_when_base_configured(temp_db):
    """Ensure report links use canonical Jira helper with dynamic JIRA_BASE_URL and no placeholder."""
    _insert_action(
        temp_db,
        action_id="act-url-test-1",
        requested_by="MubashirStaleSupport",
        target_id="TREN-378",
        status="COMPLETED",
        executed_at="2026-09-18T05:00:00+00:00",
    )

    with patch.object(settings, "JIRA_BASE_URL", "https://objectsws.atlassian.net"):
        gen = MubashirAutomationReportGenerator(manager=temp_db)
        report = gen.generate_report(target_date="2026-09-18")

        assert report["total_comments"] == 1
        item = report["by_automation"]["MubashirStaleSupport"][0]
        assert item["jira_url"] == "https://objectsws.atlassian.net/browse/TREN-378"

        # Format Discord Embed
        payload = DiscordFormatter.format_mubashir_automation_report(report)
        desc = payload["embeds"][0]["description"]
        assert "[TREN-378](https://objectsws.atlassian.net/browse/TREN-378)" in desc
        assert "your-domain.atlassian.net" not in desc

        # Format plain text
        text = DiscordFormatter.format_mubashir_automation_report_text(report)
        assert "[TREN-378](https://objectsws.atlassian.net/browse/TREN-378)" in text
        assert "your-domain.atlassian.net" not in text


# ==============================================================================
# 38. FORMATTER PREFERS ITEM JIRA URL (Phase 7D.2 Correction 2)
# ==============================================================================

def test_report_formatter_prefers_item_jira_url():
    """Ensure format_mubashir_automation_report uses jira_url from item dictionary when provided."""
    custom_url = "https://custom-jira.company.com/browse/PROJ-42"
    report_data = {
        "formatted_date": "18 Sep 2026",
        "total_comments": 1,
        "by_automation": {
            "MubashirStaleSupport": [
                {
                    "issue_key": "PROJ-42",
                    "jira_url": custom_url,
                    "description": "Stale 3 days reminder",
                }
            ],
            "MubashirSupportRule": [],
        },
    }

    payload = DiscordFormatter.format_mubashir_automation_report(report_data)
    desc = payload["embeds"][0]["description"]
    assert f"[PROJ-42]({custom_url})" in desc

    text = DiscordFormatter.format_mubashir_automation_report_text(report_data)
    assert f"[PROJ-42]({custom_url})" in text


# ==============================================================================
# 39. FORMATTER UPGRADES PLACEHOLDER JIRA URL (Phase 7D.2 Correction 2)
# ==============================================================================

def test_report_formatter_upgrades_placeholder_jira_url():
    """Ensure formatters replace placeholder URL with canonical URL when real base URL is set."""
    report_data = {
        "formatted_date": "18 Sep 2026",
        "total_comments": 1,
        "by_automation": {
            "MubashirSupportRule": [
                {
                    "issue_key": "TREN-378",
                    "jira_url": "https://your-domain.atlassian.net/browse/TREN-378",
                    "description": "Support ticket creation reminder",
                }
            ],
            "MubashirStaleSupport": [],
        },
    }

    with patch.object(settings, "JIRA_BASE_URL", "https://objectsws.atlassian.net"):
        payload = DiscordFormatter.format_mubashir_automation_report(report_data)
        desc = payload["embeds"][0]["description"]
        assert "[TREN-378](https://objectsws.atlassian.net/browse/TREN-378)" in desc
        assert "your-domain.atlassian.net" not in desc

        text = DiscordFormatter.format_mubashir_automation_report_text(report_data)
        assert "[TREN-378](https://objectsws.atlassian.net/browse/TREN-378)" in text
        assert "your-domain.atlassian.net" not in text


# ==============================================================================
# 40. FORMATTER FALLBACK WHEN ITEM URL MISSING (Phase 7D.2 Correction 2)
# ==============================================================================

def test_report_formatter_fallback_when_item_url_missing():
    """Ensure formatters fallback to settings.get_jira_browse_url when jira_url is missing."""
    report_data = {
        "formatted_date": "18 Sep 2026",
        "total_comments": 1,
        "by_automation": {
            "MubashirSupportRule": [
                {
                    "issue_key": "TREN-378",
                    # no jira_url provided
                    "description": "Support ticket creation reminder",
                }
            ],
            "MubashirStaleSupport": [],
        },
    }

    with patch.object(settings, "JIRA_BASE_URL", "https://objectsws.atlassian.net"):
        payload = DiscordFormatter.format_mubashir_automation_report(report_data)
        desc = payload["embeds"][0]["description"]
        assert "[TREN-378](https://objectsws.atlassian.net/browse/TREN-378)" in desc

        text = DiscordFormatter.format_mubashir_automation_report_text(report_data)
        assert "[TREN-378](https://objectsws.atlassian.net/browse/TREN-378)" in text

