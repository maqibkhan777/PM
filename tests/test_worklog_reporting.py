"""Tests for Jira worklog normalization, persistence, daily report aggregation, exclusions, and idempotency."""

from unittest.mock import AsyncMock
import pytest
from app.config.settings import settings, Settings
from app.database.connection import DatabaseManager
from app.database.schema import init_db
from app.database.repositories import JiraWorklogRepository, DailyReportHistoryRepository
from app.core.reports.worklog_report import DailyWorklogReportGenerator, format_seconds, format_date_human
from app.connectors.discord.formatter import DiscordFormatter


# Standard 22 group members of Mursaleen Cluster for mock testing
MOCK_22_GROUP_MEMBERS = [
    {"accountId": "712020:1b564792-a3af-447c-951d-17aa5507b946", "displayName": "Abdul Subhan", "active": True},
    {"accountId": "712020:1ddac8e3-e006-48e7-b4c9-ee941efc8e6e", "displayName": "Azain Hassan", "active": True},
    {"accountId": "712020:bb2e5830-7156-4852-bba8-75fa773fc55d", "displayName": "Talha Bukhari", "active": True},
    {"accountId": "61ee41431c42100069344a09", "displayName": "Syed ali", "active": True},
    {"accountId": "712020:12e1da4b-147f-4f91-9d2d-965b66e19b61", "displayName": "Muhammad Bilal Khan", "active": True},
    {"accountId": "63da2ba4f1475ad42c584247", "displayName": "Ahsan Iftikhar", "active": True},
    {"accountId": "5fb3d908facfd6007697c25a", "displayName": "Muhammad Hamza", "active": True},
    {"accountId": "712020:c12d2371-1e5b-4797-a888-369c0c9c5a65", "displayName": "Muhammad Sufiyan", "active": True},
    {"accountId": "5f83e3937d9637006ffd0436", "displayName": "Syed Muhammad Usman", "active": True},
    {"accountId": "712020:fb8608cb-6393-48a7-a3ab-1ad744a2b7f6", "displayName": "Muhammad Usama Azad", "active": True},
    {"accountId": "638490c75fce844d606a16ef", "displayName": "Hamza Hanif", "active": True},
    {"accountId": "606570150a6b3f00698f9430", "displayName": "Muneeb Jalal", "active": True},
    {"accountId": "712020:e268bcd8-d981-4b4d-992d-d5694745df8b", "displayName": "Mubashir Butt", "active": True},
    {"accountId": "712020:a6d04898-c6d8-4a39-a521-103e4b8bfe7c", "displayName": "Muhammad Shahmeer Khan", "active": True},
    {"accountId": "712020:0eca0fb9-4f12-4532-a435-4c178f2d90e8", "displayName": "Nauman Sadiq", "active": True},
    {"accountId": "712020:2783ea21-c611-402d-9adb-0529f5b7066d", "displayName": "Muhammad Ali Siddiqui", "active": True},
    {"accountId": "63e362bd790148a180977179", "displayName": "Daniyal Raza", "active": True},
    {"accountId": "712020:32e5be05-80c9-4ece-ac19-301da7c9487d", "displayName": "shoaib hassan askari", "active": True},
    {"accountId": "638855b85fce844d606bb422", "displayName": "Tahir Ali", "active": True},
    {"accountId": "712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de", "displayName": "Ahsan Amin", "active": True},
    {"accountId": "557058:8b3f9c31-7d88-473a-9351-abacc5b84933", "displayName": "Mohammad Mursaleen", "active": True},
    {"accountId": "712020:566cad70-4a54-42bc-bf36-0c6132fe3cf0", "displayName": "Aqib Khan", "active": True},
]


@pytest.fixture
def isolated_worklog_env(tmp_path):
    """Provide isolated DatabaseManager and DailyWorklogReportGenerator."""
    db_file = tmp_path / "test_worklogs.db"
    mgr = DatabaseManager(str(db_file))
    init_db(mgr)
    gen = DailyWorklogReportGenerator(manager=mgr)
    return mgr, gen


def test_format_seconds_utility():
    """Verify conversion of seconds to human-readable format."""
    assert format_seconds(0) == "0m"
    assert format_seconds(-10) == "0m"
    assert format_seconds(600) == "10m"
    assert format_seconds(3600) == "1h"
    assert format_seconds(22800) == "6h 20m"
    assert format_seconds(25800) == "7h 10m"


def test_format_date_human():
    """Verify conversion of YYYY-MM-DD to readable format."""
    assert format_date_human("2026-09-10") == "September 10, 2026"
    assert format_date_human("invalid-date") == "invalid-date"


@pytest.mark.asyncio
async def test_worklog_persistence_and_duplicate_prevention(isolated_worklog_env):
    """Test inserting worklogs and idempotent updates on duplicate worklog ID."""
    mgr, gen = isolated_worklog_env
    repo = JiraWorklogRepository(mgr)

    # 1. Insert worklog
    repo.upsert_worklog(
        worklog_id="wl-101",
        jira_issue_key="WSSS-326",
        time_spent_seconds=3600,
        started_at="2026-09-10T09:00:00Z",
        author_account_id="712020:e268bcd8-d981-4b4d-992d-d5694745df8b",
        author_display_name="Mubashir Butt",
        team_group="Mursaleen Cluster"
    )
    assert repo.count() == 1

    # 2. Re-insert same worklog with updated time (idempotent, no duplicate row)
    repo.upsert_worklog(
        worklog_id="wl-101",
        jira_issue_key="WSSS-326",
        time_spent_seconds=5400,
        started_at="2026-09-10T09:00:00Z",
        author_account_id="712020:e268bcd8-d981-4b4d-992d-d5694745df8b",
        author_display_name="Mubashir Butt",
        team_group="Mursaleen Cluster"
    )
    assert repo.count() == 1

    worklogs = repo.get_worklogs_for_date("2026-09-10")
    assert len(worklogs) == 1
    assert worklogs[0]["time_spent_seconds"] == 5400


@pytest.mark.asyncio
async def test_worklog_from_support_or_customer_ticket_included(isolated_worklog_env):
    """Req 1 & 2: Worklogs from customer/support tickets are included and attributed by author, not assignee."""
    mgr, gen = isolated_worklog_env
    repo = JiraWorklogRepository(mgr)

    # Customer ticket POST-10851, assigned to external support or unassigned
    repo.upsert_worklog(
        worklog_id="wl-cust-1",
        jira_issue_key="POST-10851",
        time_spent_seconds=600,  # 10 minutes
        started_at="2026-09-10T08:00:00Z",
        author_account_id="712020:e268bcd8-d981-4b4d-992d-d5694745df8b",
        author_display_name="Mubashir Butt",
        team_group="Mursaleen Cluster"
    )

    report = await gen.generate_report(target_date="2026-09-10", sync_jira=False)
    mubashir = next((m for m in report["members"] if m["display_name"] == "Mubashir Butt"), None)
    assert mubashir is not None
    assert mubashir["time_logged_human"] == "10m"
    assert "POST-10851" in mubashir["tickets"]


@pytest.mark.asyncio
async def test_four_members_excluded_from_daily_worklog_report(isolated_worklog_env):
    """Req 3, 4, 5, 6, 7: Four excluded members must NOT appear anywhere in the report, nor contribute to totals."""
    mgr, gen = isolated_worklog_env
    repo = JiraWorklogRepository(mgr)

    # Mock client.get_group_members returning all 22 members
    gen.client.get_group_members = AsyncMock(return_value=MOCK_22_GROUP_MEMBERS)

    # Insert worklogs for excluded members
    # 1. Aqib Khan
    repo.upsert_worklog(
        worklog_id="wl-ex-aqib",
        jira_issue_key="WSSS-1",
        time_spent_seconds=3600,
        started_at="2026-09-10T09:00:00Z",
        author_account_id="712020:566cad70-4a54-42bc-bf36-0c6132fe3cf0",
        author_display_name="Aqib Khan",
        team_group="Mursaleen Cluster"
    )
    # 2. Abdul Subhan
    repo.upsert_worklog(
        worklog_id="wl-ex-subhan",
        jira_issue_key="WSSS-2",
        time_spent_seconds=3600,
        started_at="2026-09-10T10:00:00Z",
        author_account_id="712020:1b564792-a3af-447c-951d-17aa5507b946",
        author_display_name="Abdul Subhan",
        team_group="Mursaleen Cluster"
    )
    # 3. Mohammad Mursaleen
    repo.upsert_worklog(
        worklog_id="wl-ex-mursaleen",
        jira_issue_key="WSSS-3",
        time_spent_seconds=3600,
        started_at="2026-09-10T11:00:00Z",
        author_account_id="557058:8b3f9c31-7d88-473a-9351-abacc5b84933",
        author_display_name="Mohammad Mursaleen",
        team_group="Mursaleen Cluster"
    )
    # 4. Syed Muhammad Usman
    repo.upsert_worklog(
        worklog_id="wl-ex-usman",
        jira_issue_key="WSSS-4",
        time_spent_seconds=3600,
        started_at="2026-09-10T12:00:00Z",
        author_account_id="5f83e3937d9637006ffd0436",
        author_display_name="Syed Muhammad Usman",
        team_group="Mursaleen Cluster"
    )
    # 5. Reportable member: Mubashir Butt (1h)
    repo.upsert_worklog(
        worklog_id="wl-valid-mubashir",
        jira_issue_key="WSSS-10",
        time_spent_seconds=3600,
        started_at="2026-09-10T13:00:00Z",
        author_account_id="712020:e268bcd8-d981-4b4d-992d-d5694745df8b",
        author_display_name="Mubashir Butt",
        team_group="Mursaleen Cluster"
    )

    report = await gen.generate_report(target_date="2026-09-10", sync_jira=False)

    # 1. Verify excluded users are not in members list
    member_names = {m["display_name"] for m in report["members"]}
    assert "Aqib Khan" not in member_names
    assert "Abdul Subhan" not in member_names
    assert "Mohammad Mursaleen" not in member_names
    assert "Syed Muhammad Usman" not in member_names

    # 2. Verify excluded users are not in zero_worklog_members
    zero_names = {m["display_name"] for m in report["zero_worklog_members"]}
    assert "Aqib Khan" not in zero_names
    assert "Abdul Subhan" not in zero_names
    assert "Mohammad Mursaleen" not in zero_names
    assert "Syed Muhammad Usman" not in zero_names

    # 3. Excluded users do NOT contribute to team total (only Mubashir's 1h = 3600s)
    assert report["total_time_seconds"] == 3600
    assert report["total_time_human"] == "1h"

    # 4. Excluded tickets do NOT contribute to tickets count
    assert report["tickets_worked_count"] == 1
    ticket_keys = {t["issue_key"] for t in report["tickets"]}
    assert ticket_keys == {"WSSS-10"}

    # 5. Underlying database still contains the worklogs (preserved, not deleted)
    assert repo.count() == 5


@pytest.mark.asyncio
async def test_all_remaining_members_appear_with_dynamic_denominator_and_zero_time(isolated_worklog_env):
    """Req 8, 9, 10: Denominator is 18 (22 - 4), members with no worklog appear as 0m / 0 tickets."""
    mgr, gen = isolated_worklog_env
    repo = JiraWorklogRepository(mgr)
    gen.client.get_group_members = AsyncMock(return_value=MOCK_22_GROUP_MEMBERS)

    # 3 people log time:
    # 1. Mubashir (1h 55m = 6900s)
    repo.upsert_worklog(
        worklog_id="wl-1",
        jira_issue_key="WSSS-100",
        time_spent_seconds=6900,
        started_at="2026-09-10T09:00:00Z",
        author_account_id="712020:e268bcd8-d981-4b4d-992d-d5694745df8b",
        author_display_name="Mubashir Butt",
        team_group="Mursaleen Cluster"
    )
    # 2. Muhammad Ali Siddiqui (1h = 3600s)
    repo.upsert_worklog(
        worklog_id="wl-2",
        jira_issue_key="WSSS-101",
        time_spent_seconds=3600,
        started_at="2026-09-10T10:00:00Z",
        author_account_id="712020:2783ea21-c611-402d-9adb-0529f5b7066d",
        author_display_name="Muhammad Ali Siddiqui",
        team_group="Mursaleen Cluster"
    )
    # 3. Hamza Hanif (30m = 1800s)
    repo.upsert_worklog(
        worklog_id="wl-3",
        jira_issue_key="WSSS-102",
        time_spent_seconds=1800,
        started_at="2026-09-10T11:00:00Z",
        author_account_id="638490c75fce844d606a16ef",
        author_display_name="Hamza Hanif",
        team_group="Mursaleen Cluster"
    )

    report = await gen.generate_report(target_date="2026-09-10", sync_jira=False)

    # Total population is 22 - 4 = 18 reportable members
    assert report["reportable_members_count"] == 18
    assert report["members_logged_count"] == 3
    assert report["members_logged_ratio"] == "3 / 18"
    assert len(report["members"]) == 18

    # Members who logged time have their actual time
    mubashir = next(m for m in report["members"] if m["display_name"] == "Mubashir Butt")
    assert mubashir["time_logged_human"] == "1h 55m"
    assert mubashir["tickets_count"] == 1

    # Members who didn't log time have 0m and 0 tickets
    zero_members = [m for m in report["members"] if m["time_logged_seconds"] == 0]
    assert len(zero_members) == 15
    for zm in zero_members:
        assert zm["time_logged_human"] == "0m"
        assert zm["tickets_count"] == 0


@pytest.mark.asyncio
async def test_ticket_aggregation_and_distinct_counts(isolated_worklog_env):
    """Req 8, 11, 12, 13: Multiple worklogs on one ticket by same member count as 1 ticket; multiple members aggregate correctly."""
    mgr, gen = isolated_worklog_env
    repo = JiraWorklogRepository(mgr)

    # Mubashir logs 10m on WSSS-100 and 20m on WSSS-100
    repo.upsert_worklog(
        worklog_id="wl-mub-1",
        jira_issue_key="WSSS-100",
        time_spent_seconds=600,  # 10m
        started_at="2026-09-10T09:00:00Z",
        author_account_id="712020:e268bcd8-d981-4b4d-992d-d5694745df8b",
        author_display_name="Mubashir Butt",
        team_group="Mursaleen Cluster"
    )
    repo.upsert_worklog(
        worklog_id="wl-mub-2",
        jira_issue_key="WSSS-100",
        time_spent_seconds=1200,  # 20m
        started_at="2026-09-10T10:00:00Z",
        author_account_id="712020:e268bcd8-d981-4b4d-992d-d5694745df8b",
        author_display_name="Mubashir Butt",
        team_group="Mursaleen Cluster"
    )

    # Ali logs 15m on WSSS-100 as well
    repo.upsert_worklog(
        worklog_id="wl-ali-1",
        jira_issue_key="WSSS-100",
        time_spent_seconds=900,  # 15m
        started_at="2026-09-10T11:00:00Z",
        author_account_id="712020:2783ea21-c611-402d-9adb-0529f5b7066d",
        author_display_name="Muhammad Ali Siddiqui",
        team_group="Mursaleen Cluster"
    )

    report = await gen.generate_report(target_date="2026-09-10", sync_jira=False)

    # Member Mubashir: 30m, 1 distinct ticket
    mubashir = next(m for m in report["members"] if m["display_name"] == "Mubashir Butt")
    assert mubashir["time_logged_human"] == "30m"
    assert mubashir["tickets_count"] == 1

    # Member Ali: 15m, 1 distinct ticket
    ali = next(m for m in report["members"] if m["display_name"] == "Muhammad Ali Siddiqui")
    assert ali["time_logged_human"] == "15m"
    assert ali["tickets_count"] == 1

    # Ticket WSSS-100: 45m, 3 worklogs
    assert len(report["tickets"]) == 1
    t100 = report["tickets"][0]
    assert t100["issue_key"] == "WSSS-100"
    assert t100["time_logged_human"] == "45m"
    assert t100["worklogs_count"] == 3

    # Team tickets count: exactly 1 distinct ticket
    assert report["tickets_worked_count"] == 1


@pytest.mark.asyncio
async def test_report_sending_is_idempotent(isolated_worklog_env):
    """Verify that sending a report for the same date twice is idempotent and does not send duplicates."""
    mgr, gen = isolated_worklog_env
    repo = JiraWorklogRepository(mgr)
    history_repo = DailyReportHistoryRepository(mgr)

    prev_dry_run = settings.DRY_RUN
    settings.DRY_RUN = True

    repo.upsert_worklog(
        worklog_id="wl-5",
        jira_issue_key="WSSS-280",
        time_spent_seconds=3600,
        started_at="2026-09-10T10:00:00Z",
        author_account_id="acc-1",
        author_display_name="Daniyal Raza",
        team_group="Mursaleen Cluster"
    )

    # First dispatch -> should send
    res1 = await gen.send_report_to_discord(target_date="2026-09-10", force=False, sync_jira=False)
    assert res1["status"] == "sent"
    assert history_repo.has_report_been_sent("Mursaleen Cluster", "2026-09-10") is True

    # Second dispatch without force -> should skip
    res2 = await gen.send_report_to_discord(target_date="2026-09-10", force=False, sync_jira=False)
    assert res2["status"] == "skipped"
    assert res2["reason"] == "already_sent_today"

    # Third dispatch with force=True -> should re-send
    res3 = await gen.send_report_to_discord(target_date="2026-09-10", force=True, sync_jira=False)
    assert res3["status"] == "sent"

    settings.DRY_RUN = prev_dry_run


def test_discord_daily_worklog_report_formatting():
    """Verify Discord embed formatting has single complete member table with clickable links and removed summary fields."""
    mock_report = {
        "team_name": "Mursaleen Cluster",
        "report_date": "2026-09-10",
        "formatted_date": "September 10, 2026",
        "total_time_human": "3h 25m",
        "members_logged_count": 3,
        "reportable_members_count": 18,
        "tickets_worked_count": 25,
        "members": [
            {"display_name": "Mubashir Butt", "time_logged_seconds": 6900, "time_logged_human": "1h 55m", "tickets_count": 2, "tickets": ["WSSS-326", "WSSS-301"]},
            {"display_name": "Muhammad Ali Siddiqui", "time_logged_seconds": 3600, "time_logged_human": "1h", "tickets_count": 1, "tickets": ["WSSS-301"]},
            {"display_name": "Hamza Hanif", "time_logged_seconds": 1800, "time_logged_human": "30m", "tickets_count": 1, "tickets": ["WSSS-100"]},
            {"display_name": "Azain Hassan", "time_logged_seconds": 0, "time_logged_human": "0m", "tickets_count": 0, "tickets": []},
        ],
        "tickets": [
            {"issue_key": "WSSS-326", "summary": "Fix authentication bug", "time_logged_human": "1h 55m", "worklogs_count": 5},
            {"issue_key": "WSSS-301", "summary": "Database migration", "time_logged_human": "1h", "worklogs_count": 1},
        ],
        "zero_worklog_members": [
            {"display_name": "Azain Hassan"}
        ]
    }

    embed = DiscordFormatter.format_daily_worklog_report(mock_report)
    embed_obj = embed["embeds"][0]

    assert embed_obj["title"] == "📊 Mursaleen Cluster — Daily Worklog"
    assert "**Date:** September 10, 2026" in embed_obj["description"]
    assert "**Tickets Worked:** 25" in embed_obj["description"]
    assert "Total Time Logged" not in embed_obj["description"]
    assert "Members Logged" not in embed_obj["description"]

    # Table is in description as a single unified table
    assert "👥 Team Worklog" in embed_obj["description"]
    assert "Mubashir Butt" in embed_obj["description"]
    assert "1h 55m" in embed_obj["description"]
    assert "[2](https://" in embed_obj["description"]
    assert "🔴 Azain Hassan" in embed_obj["description"]
    assert "0m" in embed_obj["description"]
    assert len(embed_obj["description"]) <= 4096

    # Verify no split table fields exist and fields is empty (no ticket list below table)
    assert not embed_obj.get("fields")


@pytest.mark.asyncio
async def test_manual_send_performs_jira_sync_and_does_not_generate_false_0m(isolated_worklog_env):
    """Task 1 & 3: Manual send performs Jira sync before report generation and fails safely if sync fails."""
    mgr, gen = isolated_worklog_env
    gen.client.get_group_members = AsyncMock(return_value=MOCK_22_GROUP_MEMBERS)

    # Mock search_issues returning 1 issue with 1 worklog for today
    mock_issue = {
        "id": "10050",
        "key": "POST-10851",
        "fields": {
            "summary": "Customer inquiry",
            "status": {"name": "Closed"},
            "assignee": {"displayName": "External Agent"},
            "worklog": {
                "total": 1,
                "worklogs": [
                    {
                        "id": "wl-sync-1",
                        "author": {"accountId": "712020:e268bcd8-d981-4b4d-992d-d5694745df8b", "displayName": "Mubashir Butt"},
                        "timeSpentSeconds": 1800,  # 30m
                        "started": "2026-09-10T10:00:00Z"
                    }
                ]
            }
        }
    }
    gen.client.search_issues = AsyncMock(return_value={"issues": [mock_issue], "total": 1})

    prev_dry_run = settings.DRY_RUN
    settings.DRY_RUN = True

    # Call send_report_to_discord with default sync_jira (should sync and NOT produce 0m)
    res = await gen.send_report_to_discord(target_date="2026-09-10", force=True)
    assert res["status"] == "sent"
    rep = res["report"]
    assert rep["total_time_human"] == "30m"
    assert rep["members_logged_count"] == 1
    assert rep["tickets_worked_count"] == 1

    # Verify that if Jira sync fails, it raises an exception and does NOT dispatch a false 0m report
    gen.client.search_issues = AsyncMock(side_effect=RuntimeError("Jira API 503 Service Unavailable"))
    with pytest.raises(RuntimeError, match="Failed to sync Jira worklogs"):
        await gen.send_report_to_discord(target_date="2026-09-11", force=True)

    settings.DRY_RUN = prev_dry_run


@pytest.mark.asyncio
async def test_manual_send_and_get_report_use_same_logic(isolated_worklog_env):
    """Task 3B: Manual send and GET report produce identical report metrics."""
    mgr, gen = isolated_worklog_env
    repo = JiraWorklogRepository(mgr)
    gen.client.get_group_members = AsyncMock(return_value=MOCK_22_GROUP_MEMBERS)

    repo.upsert_worklog(
        worklog_id="wl-eq-1",
        jira_issue_key="WSSS-500",
        time_spent_seconds=7200,
        started_at="2026-09-10T09:00:00Z",
        author_account_id="712020:e268bcd8-d981-4b4d-992d-d5694745df8b",
        author_display_name="Mubashir Butt",
        team_group="Mursaleen Cluster"
    )

    prev_dry_run = settings.DRY_RUN
    settings.DRY_RUN = True

    get_report = await gen.generate_report(target_date="2026-09-10", sync_jira=False)
    send_res = await gen.send_report_to_discord(target_date="2026-09-10", force=True, sync_jira=False)

    assert send_res["report"]["total_time_seconds"] == get_report["total_time_seconds"]
    assert send_res["report"]["members_logged_count"] == get_report["members_logged_count"]
    assert send_res["report"]["reportable_members_count"] == get_report["reportable_members_count"]
    assert len(send_res["report"]["members"]) == len(get_report["members"])
    assert len(send_res["report"]["tickets"]) == len(get_report["tickets"])

    settings.DRY_RUN = prev_dry_run


@pytest.mark.asyncio
async def test_daily_worklog_presentation_all_21_criteria(isolated_worklog_env):
    """Verify all 21 requirements for the finalized Daily Worklog Discord presentation."""
    import urllib.parse
    import re

    mgr, gen = isolated_worklog_env
    repo = JiraWorklogRepository(mgr)
    gen.client.get_group_members = AsyncMock(return_value=MOCK_22_GROUP_MEMBERS)

    # Setup worklogs on 2026-09-09
    # 1. Hamza Hanif works on 6 distinct tickets: WPEP-1592, PP-843, HFCF-757, TREN-1464, AIOL-681, HFCF-615
    # Including multiple worklogs on one ticket (PP-843 logged twice)
    hamza_id = "638490c75fce844d606a16ef"
    hamza_tickets = ["WPEP-1592", "PP-843", "HFCF-757", "TREN-1464", "AIOL-681", "HFCF-615"]
    for i, tkey in enumerate(hamza_tickets):
        repo.upsert_worklog(
            worklog_id=f"wl-hamza-{i}",
            jira_issue_key=tkey,
            time_spent_seconds=3600,
            started_at="2026-09-09T09:00:00Z",
            author_account_id=hamza_id,
            author_display_name="Hamza Hanif",
            team_group="Mursaleen Cluster"
        )
    # Additional worklog on PP-843 by Hamza (multiple worklogs on one ticket)
    repo.upsert_worklog(
        worklog_id="wl-hamza-extra-pp843",
        jira_issue_key="PP-843",
        time_spent_seconds=1800,
        started_at="2026-09-09T14:00:00Z",
        author_account_id=hamza_id,
        author_display_name="Hamza Hanif",
        team_group="Mursaleen Cluster"
    )

    # 2. Daniyal Raza works on 4 tickets
    daniyal_id = "63e362bd790148a180977179"
    daniyal_tickets = ["TREN-100", "TREN-101", "TREN-102", "TREN-103"]
    for i, tkey in enumerate(daniyal_tickets):
        repo.upsert_worklog(
            worklog_id=f"wl-daniyal-{i}",
            jira_issue_key=tkey,
            time_spent_seconds=3600,
            started_at="2026-09-09T10:00:00Z",
            author_account_id=daniyal_id,
            author_display_name="Daniyal Raza",
            team_group="Mursaleen Cluster"
        )

    # 3. Worklog on a ticket currently assigned to someone else (e.g. Mubashir Butt assigned, but Hamza logged work)
    # Already demonstrated by WPEP-1592 logged by Hamza.
    # Mubashir Butt logs NO worklogs on this day, so Mubashir has 0m and 0 tickets despite being an assignee of tickets.

    # Generate report and Discord embed
    report = await gen.generate_report(target_date="2026-09-09", sync_jira=False)
    embed = DiscordFormatter.format_daily_worklog_report(report)
    embed_obj = embed["embeds"][0]
    desc = embed_obj["description"]

    # 1. Header contains Date
    assert "**Date:**" in desc
    assert "September 09, 2026" in desc

    # 2. Header contains Tickets Worked
    assert "**Tickets Worked:**" in desc
    assert "10" in desc  # 6 distinct for Hamza + 4 distinct for Daniyal = 10 total distinct

    # 3. Header does NOT contain Total Time Logged
    assert "Total Time Logged" not in desc
    assert "Total Time Logged" not in str(embed_obj)

    # 4. Header does NOT contain Members Logged
    assert "Members Logged" not in desc
    assert "Members Logged" not in str(embed_obj)

    # 5. Exactly one Team Worklog table is generated
    assert desc.count("👥 Team Worklog") == 1
    assert desc.count("Team Member") == 1
    assert desc.count("Time Logged") == 1

    # 6. All 18 reportable members appear
    reportable_names = [
        "Daniyal Raza", "Hamza Hanif", "Ahsan Iftikhar", "Ahsan Amin",
        "Muhammad Sufiyan", "Muhammad Hamza", "Nauman Sadiq", "shoaib hassan askari",
        "Syed ali", "Muhammad Usama Azad", "Azain Hassan", "Mubashir Butt",
        "Muhammad Ali Siddiqui", "Muhammad Bilal Khan", "Muhammad Shahmeer Khan",
        "Muneeb Jalal", "Tahir Ali", "Talha Bukhari"
    ]
    for member_name in reportable_names:
        assert member_name in desc, f"Missing member {member_name} in table"

    # 7. Members with work show their actual time
    # Hamza: 6 * 1h + 30m = 6h 30m
    assert "6h 30m" in desc
    # Daniyal: 4 * 1h = 4h
    assert "4h" in desc

    # 8. Members with work show their distinct ticket count
    assert "[6]" in desc
    assert "[4]" in desc

    # 9. Members with zero work show 0m and 0
    mubashir_match = re.search(r"\|\s+🔴\s+Mubashir Butt\s+\|\s+0m\s+\|\s+0\s+\|", desc)
    assert mubashir_match is not None, "Mubashir Butt should show 0m and 0"
    talha_match = re.search(r"\|\s+🔴\s+Talha Bukhari\s+\|\s+0m\s+\|\s+0\s+\|", desc)
    assert talha_match is not None, "Talha Bukhari should show 0m and 0"

    # 10. Zero-work members receive the 🔴 indicator
    assert "🔴 Mubashir Butt" in desc
    assert "🔴 Talha Bukhari" in desc
    assert "🔴 Muhammad Ali Siddiqui" in desc

    # 11. Members with work do NOT receive the 🔴 indicator
    assert "🔴 Hamza Hanif" not in desc
    assert "🔴 Daniyal Raza" not in desc

    # 12. Ticket count for a working member is a clickable Jira Issue Navigator link
    hamza_link_match = re.search(r"\|\s+Hamza Hanif\s+\|\s+6h 30m\s+\|\s+`?\[6\]\((https://[^)]+)\)`?\s+\|", desc)
    assert hamza_link_match is not None, "Hamza Hanif tickets column must be a clickable link [6](url)"
    hamza_url = hamza_link_match.group(1)

    # 13. The Jira link contains only that member's distinct worked ticket keys
    parsed_jql = urllib.parse.unquote(hamza_url)
    assert "issuekey in (" in parsed_jql
    for tkey in hamza_tickets:
        assert tkey in parsed_jql
    # Does NOT contain Daniyal's tickets
    for tkey in daniyal_tickets:
        assert tkey not in parsed_jql

    # 14. Ticket links are based on worklog authors, NOT current assignees
    # Even if Mubashir was the assignee, the tickets logged by Hamza are in Hamza's link
    # And Mubashir has 0 tickets and no link
    assert "Mubashir Butt" in desc
    assert "[0](" not in desc

    # 15. Multiple worklogs on one ticket count as one ticket
    # PP-843 was logged twice by Hamza, but Hamza's count is 6 (not 7)
    assert "[6]" in desc
    assert "[7]" not in desc

    # 16. Zero-ticket members have no hyperlink
    assert "[0]" not in desc
    for zero_member in ["Mubashir Butt", "Talha Bukhari", "Azain Hassan"]:
        assert f"[{zero_member}]" not in desc
        # The 0 in tickets column is plain text
        assert re.search(rf"\|\s+🔴\s+{re.escape(zero_member)}\s+\|\s+0m\s+\|\s+0\s+\|", desc) is not None

    # 17. No "Tickets Worked" detailed section is generated below the table
    assert "🎫" not in str(embed_obj)
    for field in embed_obj.get("fields", []):
        assert "Tickets Worked" not in field.get("name", "")

    # 18. No individual ticket list is generated
    assert not embed_obj.get("fields")

    # 19. Jira base URL comes from configuration
    expected_base = (settings.JIRA_BASE_URL or "https://jira.atlassian.net").rstrip("/")
    assert hamza_url.startswith(f"{expected_base}/issues/?jql=")

    # 20. JQL is correctly URL encoded
    assert "%28" in hamza_url  # '(' encoded
    assert "%29" in hamza_url  # ')' encoded
    assert "%20" in hamza_url  # ' ' encoded
    assert "(" not in hamza_url.split("?jql=")[1]  # No raw '(' in query
    assert ")" not in hamza_url.split("?jql=")[1]  # No raw ')' in query

    # 21. The table is not split into Part 1 / Part 2
    assert "Part 1" not in str(embed_obj)
    assert "Part 2" not in str(embed_obj)


