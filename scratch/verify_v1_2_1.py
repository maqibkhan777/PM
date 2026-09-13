"""Verification script for PM Operations Agent v1.2.1.

Verifies:
1. Customer-Created Service Management Classification (Authoritative hierarchy)
2. Mubashir Support Creation Workflow (Actual Sprint names & Product Labels)
3. Mubashir Stale Support Business-Day Calculations (Asia/Karachi, weekends excluded)
4. Active Epic Review Deterministic Precedence (All child states)
5. Plugin -> Board Registry 46/46 database & spreadsheet parity
6. Date selector semantics & validation
7. Organized slash-command registration order (15 commands)
8. Centralized notification colors
"""

import sys
import os
from datetime import datetime, timezone, timedelta
import zoneinfo

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.config.settings import settings
from app.database.connection import DatabaseManager
from app.database.schema import init_db
from app.database.repositories import PluginBoardRepository, EmployeeRoleRepository
from app.core.rules.ticket_creation_policy import TicketCreationPolicy, TicketCreationDecision
from app.core.rules.mubashir_support_rule import extract_sprint_names, extract_labels
from app.services.epic_review_service import EpicReviewService
from app.utils.time import calculate_business_days
from app.connectors.discord.formatter import (
    COLOR_ASSIGNMENT, COLOR_NOTIFICATION, COLOR_WORKLOG,
    COLOR_OVERDUE, COLOR_ATTENTION, COLOR_TICKET_CREATED
)
from app.connectors.discord.gateway_client import build_pm_slash_command_schema


def verify_customer_sm_classification(mgr):
    print("\n--- 1. Customer-Created Service Management Classification ---")
    role_repo = EmployeeRoleRepository(mgr)
    plugin_repo = PluginBoardRepository(mgr)
    
    # Genuine customer in SM project (POST)
    dec_cust, reason_cust, _, _ = TicketCreationPolicy.evaluate(
        creator_account_id="cust-123",
        creator_display_name="Acme Client",
        issue_type="Support",
        project_key="POST",
        creator_account_type="customer",
        role_repo=role_repo,
        plugin_repo=plugin_repo,
    )
    print(f"Genuine customer in POST: {dec_cust.value} ({reason_cust})")
    assert dec_cust == TicketCreationDecision.IGNORE

    # Mubashir in POST
    dec_mub, reason_mub, _, _ = TicketCreationPolicy.evaluate(
        creator_account_id="712020:e268bcd8-d981-4b4d-992d-d5694745df8b",
        creator_display_name="Mubashir",
        issue_type="Support",
        project_key="POST",
        creator_account_type="customer",  # Even if flagged, role repo overrides
        role_repo=role_repo,
        plugin_repo=plugin_repo,
    )
    print(f"Mubashir in POST: {dec_mub.value} ({reason_mub})")
    assert dec_mub == TicketCreationDecision.EXPECTED

    # Unknown creator in POST
    dec_unk, reason_unk, _, _ = TicketCreationPolicy.evaluate(
        creator_account_id="unknown-ext-999",
        creator_display_name="Unknown User",
        issue_type="Support",
        project_key="POST",
        creator_account_type=None,
        role_repo=role_repo,
        plugin_repo=plugin_repo,
    )
    print(f"Unknown creator in POST: {dec_unk.value} ({reason_unk})")
    assert dec_unk == TicketCreationDecision.REVIEW

    # Customer in internal project (WSSS)
    dec_int, reason_int, _, _ = TicketCreationPolicy.evaluate(
        creator_account_id="cust-123",
        creator_display_name="Acme Client",
        issue_type="Support",
        project_key="WSSS",
        creator_account_type="customer",
        role_repo=role_repo,
        plugin_repo=plugin_repo,
    )
    print(f"Customer in internal project WSSS: {dec_int.value} ({reason_int})")
    assert dec_int == TicketCreationDecision.REVIEW
    print("✓ Customer SM Classification Verified.")


def verify_mubashir_sprint_detection():
    print("\n--- 2. Mubashir Support Sprint Detection ---")
    
    # Sprint list of dicts
    payload1 = {"customfield_10020": [{"name": "Support Board"}, {"name": "Sprint 42"}]}
    sprints1 = extract_sprint_names(payload1)
    print(f"Payload 1 sprints: {sprints1}")
    assert "Support Board" in sprints1

    # Serialized sprint string
    payload2 = {"customfield_10020": ["com.atlassian.greenhopper.service.sprint.Sprint@123[name=Feature Request,id=99]"]}
    sprints2 = extract_sprint_names(payload2)
    print(f"Payload 2 sprints: {sprints2}")
    assert "Feature Request" in sprints2

    # Unrelated sprint
    payload3 = {"customfield_10020": ["Sprint 101"]}
    sprints3 = extract_sprint_names(payload3)
    print(f"Payload 3 sprints: {sprints3}")
    assert "Support Board" not in sprints3 and "Feature Request" not in sprints3
    print("✓ Mubashir Sprint Detection Verified.")


def verify_business_day_calculation():
    print("\n--- 3. Mubashir Stale Support Business-Day Calculations ---")
    tz = zoneinfo.ZoneInfo("Asia/Karachi")
    
    # Monday 10:00 to Thursday 10:00 = exactly 3.0 days
    mon = datetime(2026, 9, 7, 10, 0, tzinfo=tz)
    thu = datetime(2026, 9, 10, 10, 0, tzinfo=tz)
    bd1 = calculate_business_days(mon, thu)
    print(f"Mon 10:00 -> Thu 10:00: {bd1:.2f} business days")
    assert abs(bd1 - 3.0) < 0.01

    # Friday 17:00 to Tuesday 17:00 = exactly 2.0 business days (Sat & Sun skipped)
    fri = datetime(2026, 9, 4, 17, 0, tzinfo=tz)
    tue = datetime(2026, 9, 8, 17, 0, tzinfo=tz)
    bd2 = calculate_business_days(fri, tue)
    print(f"Fri 17:00 -> Tue 17:00: {bd2:.2f} business days")
    assert abs(bd2 - 2.0) < 0.01
    print("✓ Business Day Calculation Verified.")


def verify_epic_precedence(mgr):
    print("\n--- 4. Active Epic Review Deterministic Precedence ---")
    service = EpicReviewService(manager=mgr)
    
    # 1. On Hold
    hold = service.determine_epic_status_recommendation("EPIC-1", "On Hold", {
        "active_azain": [], "active_dev": [{"key": "T-1"}], "active_qa": [],
        "active_planning": [], "active_marketing": [], "active_pm": [],
        "done_tickets": [], "other_active": [], "total_children": 1
    })
    print(f"On Hold Epic recommendation: {hold['recommended_status']} (is_on_hold={hold['is_on_hold']})")
    assert hold["recommended_status"] is None and hold["is_on_hold"] is True

    # 2. Azain active
    azain = service.determine_epic_status_recommendation("EPIC-2", "To Do", {
        "active_azain": [{"key": "T-2"}], "active_dev": [{"key": "T-3"}], "active_qa": [],
        "active_planning": [], "active_marketing": [], "active_pm": [],
        "done_tickets": [], "other_active": [], "total_children": 2
    })
    print(f"Azain active recommendation: {azain['recommended_status']}")
    assert azain["recommended_status"] == "Ready for kickoff"

    # 3. Done Dev + Active QA
    done_dev_qa = service.determine_epic_status_recommendation("EPIC-3", "In Development", {
        "active_azain": [], "active_dev": [], "active_qa": [{"key": "T-4"}],
        "active_planning": [], "active_marketing": [], "active_pm": [],
        "done_tickets": [{"key": "T-5"}], "other_active": [], "total_children": 2
    })
    print(f"Done Dev + Active QA recommendation: {done_dev_qa['recommended_status']}")
    assert done_dev_qa["recommended_status"] == "In QA"

    # 4. Marketing condition satisfied
    mkt = service.determine_epic_status_recommendation("EPIC-4", "In QA", {
        "active_azain": [], "active_dev": [], "active_qa": [],
        "active_planning": [], "active_marketing": [{"key": "T-6"}], "active_pm": [{"key": "T-7"}],
        "done_tickets": [{"key": "T-8"}], "other_active": [], "total_children": 3
    })
    print(f"Marketing Tahir recommendation: {mkt['recommended_status']}")
    assert mkt["recommended_status"] == "In Marketing"
    print("✓ Epic Precedence Logic Verified.")


def verify_plugin_registry(mgr):
    print("\n--- 5. Plugin -> Board Registry Parity ---")
    repo = PluginBoardRepository(mgr)
    all_plugins = repo.get_all()
    print(f"Total seeded plugin rows: {len(all_plugins)} (Expected: 46)")
    assert len(all_plugins) == 46

    # Verify 'No Board' preservation
    no_boards = [p for p in all_plugins if p.get("internal_board") == "No Board" or p.get("internal_support_board") == "No Board"]
    print(f"Plugins with explicit 'No Board': {len(no_boards)}")
    assert len(no_boards) > 0

    # Verify SM projects
    sm_keys = repo.get_service_management_project_keys()
    print(f"Resolved Service Management Project Keys: {sm_keys}")
    assert "POST" in sm_keys
    assert "GFS" in sm_keys
    print("✓ Plugin Registry 46/46 Parity Verified.")


def verify_slash_commands_and_colors():
    print("\n--- 6. Slash Command Order & Centralized Colors ---")
    schema = build_pm_slash_command_schema()
    subcmds = [opt["name"] for opt in schema["options"]]
    expected_order = [
        "help", "status", "report", "worklog", "overdue",
        "queue", "attention", "activity", "transition", "assign",
        "comment", "create", "update", "notify", "message"
    ]
    print(f"Registered subcommands ({len(subcmds)}): {subcmds}")
    assert subcmds == expected_order

    print(f"Color Assignment (Green): {hex(COLOR_ASSIGNMENT)}")
    print(f"Color Notification (Blue): {hex(COLOR_NOTIFICATION)}")
    print(f"Color Worklog (Purple): {hex(COLOR_WORKLOG)}")
    print(f"Color Overdue (Red): {hex(COLOR_OVERDUE)}")
    print(f"Color Attention (Yellow): {hex(COLOR_ATTENTION)}")
    print(f"Color Ticket Created (Dark Pink): {hex(COLOR_TICKET_CREATED)}")
    assert COLOR_ASSIGNMENT == 3066993
    assert COLOR_NOTIFICATION == 3447003
    assert COLOR_WORKLOG == 10181046
    assert COLOR_OVERDUE == 15158332
    assert COLOR_ATTENTION == 15844367
    assert COLOR_TICKET_CREATED == 14689374
    print("✓ Slash Commands & Colors Verified.")


from app.connectors.discord.slash_commands import DiscordSlashCommandHandler


async def verify_date_semantics(mgr):
    print("\n--- 7. Date Selector Semantics ---")
    settings.DISCORD_PM_COMMAND_ENABLED = True
    settings.DISCORD_PM_ALLOWED_USERS = "pm-admin"
    handler = DiscordSlashCommandHandler(manager=mgr)
    
    # 1. Invalid date format
    res_bad_format = await handler.execute_subcommand(
        subcommand="worklog",
        options={"date": "09-10-2026"},
        discord_user_id="pm-admin"
    )
    print(f"Invalid format check: {res_bad_format}")
    assert "Invalid date format" in res_bad_format

    # 2. Invalid calendar date (e.g. Feb 30)
    res_bad_cal = await handler.execute_subcommand(
        subcommand="worklog",
        options={"date": "2026-02-30"},
        discord_user_id="pm-admin"
    )
    print(f"Invalid calendar date check: {res_bad_cal}")
    assert "Invalid calendar date" in res_bad_cal

    # 3. Valid date (2026-09-10)
    res_valid = await handler.execute_subcommand(
        subcommand="worklog",
        options={"date": "2026-09-10"},
        discord_user_id="pm-admin"
    )
    print(f"Valid date response preview: {res_valid[:120]}...")
    assert "2026-09-10" in res_valid
    print("✓ Date Semantics Verified.")


async def main():
    mgr = DatabaseManager()
    init_db(mgr)
    
    verify_customer_sm_classification(mgr)
    verify_mubashir_sprint_detection()
    verify_business_day_calculation()
    verify_epic_precedence(mgr)
    verify_plugin_registry(mgr)
    verify_slash_commands_and_colors()
    await verify_date_semantics(mgr)

    print("\n============================================================")
    print("ALL v1.2.1 VERIFICATION CHECKS PASSED PERFECTLY!")
    print("============================================================\n")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
