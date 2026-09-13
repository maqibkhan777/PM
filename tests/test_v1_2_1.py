"""Comprehensive Unit and Integration Tests for PM Operations Agent v1.2.1."""

import asyncio
from datetime import datetime, timedelta
import json
import os
import zoneinfo
import pytest

from app.config.settings import settings
from app.connectors.discord.formatter import (
    DiscordFormatter,
    COLOR_ASSIGNMENT,
    COLOR_NOTIFICATION,
    COLOR_WORKLOG,
    COLOR_OVERDUE,
    COLOR_ATTENTION,
    COLOR_TICKET_CREATED,
    COLOR_GREEN,
    COLOR_BLUE,
    COLOR_PURPLE,
    COLOR_RED,
    COLOR_AMBER,
    COLOR_DARK_PINK,
)
from app.connectors.discord.gateway_client import build_pm_slash_command_schema
from app.connectors.discord.slash_commands import DiscordSlashCommandHandler
from app.core.actions.engine import ActionEngine
from app.core.events.types import TaskCreated
from app.core.models.enums import ActionStatus
from app.core.rules.engine import RulesEngine
from app.core.rules.mubashir_support_rule import (
    MubashirSupportRule,
    extract_sprint_names,
    extract_labels,
    MUBASHIR_CANONICAL_ACCOUNT_ID,
)
from app.core.rules.ticket_creation_policy import (
    TicketCreationPolicy,
    TicketCreationDecision,
    TAHIR_ALI_ACCOUNT_ID,
)
from app.database.connection import DatabaseManager
from app.database.repositories import (
    EmployeeRoleRepository,
    PluginBoardRepository,
    JiraIssueStateRepository,
    RuleRepository,
    UserRepository,
)
from app.database.schema import init_db
from app.services.epic_review_service import (
    EpicReviewService,
    AZAIN_HASSAN_ACCOUNT_ID,
)
from app.services.scheduler import PeriodicScheduler
from app.utils.time import calculate_business_days, is_business_day, utc_now


@pytest.fixture
def test_db(tmp_path):
    """Isolated SQLite database fixture."""
    db_file = str(tmp_path / "test_pm_v1_2_1.db")
    mgr = DatabaseManager(db_path=db_file)
    init_db(mgr)
    return mgr


# ==============================================================================
# 1. NOTIFICATION COLOR SYSTEM TESTS
# ==============================================================================

class TestNotificationColors:
    def test_centralized_color_constants(self):
        """Verify standardized decimal color constants and aliases."""
        assert COLOR_ASSIGNMENT == 3066993   # Green
        assert COLOR_NOTIFICATION == 3447003 # Blue
        assert COLOR_WORKLOG == 10181046     # Purple
        assert COLOR_OVERDUE == 15158332     # Red
        assert COLOR_ATTENTION == 15844367   # Yellow
        assert COLOR_TICKET_CREATED == 14689374 # Dark Pink / Red

        # Aliases
        assert COLOR_GREEN == COLOR_ASSIGNMENT
        assert COLOR_BLUE == COLOR_NOTIFICATION
        assert COLOR_PURPLE == COLOR_WORKLOG
        assert COLOR_RED == COLOR_OVERDUE
        assert COLOR_AMBER == COLOR_ATTENTION
        assert COLOR_DARK_PINK == COLOR_TICKET_CREATED

    def test_formatter_embed_colors(self):
        """Verify embed outputs use standardized colors."""
        # Overdue task -> Red
        overdue_embed = DiscordFormatter.format_overdue_task("WSSS-1", "Task", "Ahsan", "2026-09-10", 24)
        assert overdue_embed["embeds"][0]["color"] == COLOR_OVERDUE

        # Attention alert -> Yellow
        attn_embed = DiscordFormatter.format_stale_task("WSSS-2", "Task", "Ahsan", 48.0)
        assert attn_embed["embeds"][0]["color"] == COLOR_ATTENTION

        # Assignment -> Green
        assign_embed = DiscordFormatter.format_task_assigned("WSSS-3", "Task", "Ahsan", is_assigned_to_me=True)
        assert assign_embed["embeds"][0]["color"] == COLOR_ASSIGNMENT

        # Ticket Created -> Dark Pink/Red
        tc_embed = DiscordFormatter.format_ticket_creation_notification("WSSS-4", "Task", "Tahir", "Task", "WSSS", "2026-09-12T00:00:00Z", "EXPECTED", "Authorized")
        assert tc_embed["embeds"][0]["color"] == COLOR_TICKET_CREATED


# ==============================================================================
# 2. SLASH COMMAND ORDERING TESTS
# ==============================================================================

class TestSlashCommandOrdering:
    def test_exact_15_command_operational_order(self):
        """Verify slash commands schema matches exact 15-command operational order."""
        schema = build_pm_slash_command_schema()
        assert schema["name"] == "pm"
        opt_names = [o["name"] for o in schema["options"]]
        expected = [
            "help", "status", "report", "worklog", "overdue", "queue",
            "attention", "activity", "transition", "assign", "comment",
            "create", "update", "notify", "message"
        ]
        assert opt_names == expected
        assert len(opt_names) == 15

    def test_schema_options_include_date_selectors(self):
        """Verify report commands define date parameter in schema."""
        schema = build_pm_slash_command_schema()
        by_name = {o["name"]: o for o in schema["options"]}

        for cmd in ("report", "worklog", "overdue", "queue", "attention", "activity"):
            assert cmd in by_name
            opt_params = [p["name"] for p in by_name[cmd].get("options", [])]
            assert "date" in opt_params, f"Command {cmd} missing 'date' parameter in schema"


# ==============================================================================
# 3. DATE SELECTOR TESTS
# ==============================================================================

class TestDateSelectors:
    @pytest.mark.asyncio
    async def test_strict_date_validation(self, test_db):
        """Verify strict YYYY-MM-DD format and calendar validity."""
        handler = DiscordSlashCommandHandler(manager=test_db)
        settings.DISCORD_PM_ALLOWED_USERS = "*"

        # Invalid format
        res1 = await handler.execute_subcommand("overdue", {"date": "2026/09/12"}, discord_user_id="user1")
        assert "Invalid date format" in res1

        res2 = await handler.execute_subcommand("worklog", {"date": "yesterday"}, discord_user_id="user1")
        assert "Invalid date format" in res2

        # Invalid calendar date (e.g. Feb 31)
        res3 = await handler.execute_subcommand("overdue", {"date": "2026-02-31"}, discord_user_id="user1")
        assert "Invalid calendar date" in res3

        res4 = await handler.execute_subcommand("report", {"name": "attention", "date": "2026-13-45"}, discord_user_id="user1")
        assert "Invalid calendar date" in res4

    @pytest.mark.asyncio
    async def test_valid_date_dispatching(self, test_db):
        """Verify valid date is accepted and processed without error."""
        handler = DiscordSlashCommandHandler(manager=test_db)
        settings.DISCORD_PM_ALLOWED_USERS = "*"

        # Valid date for attention digest
        res = await handler.execute_subcommand("attention", {"date": "2026-09-10"}, discord_user_id="user1")
        assert "September 10, 2026" in res or "PM Attention Digest" in res


# ==============================================================================
# 4. PLUGIN BOARD REGISTRY TESTS
# ==============================================================================

class TestPluginBoardRegistry:
    def test_all_46_records_seeded(self, test_db):
        """Verify all 46 rows from spreadsheet are correctly seeded in SQLite."""
        repo = PluginBoardRepository(test_db)
        plugins = repo.get_all()
        assert len(plugins) == 46

    def test_lookup_and_project_key_resolution(self, test_db):
        """Verify project keys and board lookup."""
        repo = PluginBoardRepository(test_db)

        # Gutena Forms -> internal GF, support GFIS, SM GFS
        gf = repo.get_by_plugin_name("Gutena Forms")
        assert gf is not None
        assert gf["internal_project_key"] == "GF"
        assert gf["support_project_key"] == "GFIS"
        assert gf["service_management_project_key"] == "GFS"

        # Service Management project check
        assert repo.is_service_management_project("POST") is True
        assert repo.is_service_management_project("GFS") is True
        assert repo.is_service_management_project("AFMS") is True
        assert repo.is_service_management_project("UNKNOWN_PROJECT") is False

        # No Board preservation
        qcf = repo.get_by_plugin_name("Quick Contact Form")
        assert qcf is not None
        assert qcf["internal_board"] == "No Board"
        assert qcf["internal_support_board"] == "No Board"
        assert qcf["service_management_board"] == "No Board"

    def test_source_spreadsheet_parity(self, test_db):
        """Compare database registry against source spreadsheet rows."""
        import openpyxl
        wb = openpyxl.load_workbook(r"C:\Users\Objects\Downloads\Untitled spreadsheet.xlsx")
        ws = wb.active
        spreadsheet_rows = []
        for r in range(2, ws.max_row + 1):
            vals = [ws.cell(r, c).value for c in range(1, ws.max_column + 1)]
            if any(vals):
                spreadsheet_rows.append(vals)

        repo = PluginBoardRepository(test_db)
        db_rows = repo.get_all()
        assert len(spreadsheet_rows) == len(db_rows) == 46


# ==============================================================================
# 5. CUSTOMER-CREATED SERVICE MANAGEMENT EXCLUSION TESTS
# ==============================================================================

class TestCustomerServiceManagementExclusion:
    def test_genuine_customer_in_sm_project_ignored(self, test_db):
        """Customer creating ticket in SM project (POST) is ignored from ticket-creation alerts."""
        role_repo = EmployeeRoleRepository(test_db)
        plugin_repo = PluginBoardRepository(test_db)

        # External customer with accountType: customer
        decision, reason, _, _ = TicketCreationPolicy.evaluate(
            creator_account_id="customer-acc-123",
            creator_display_name="John Customer",
            creator_email="john@customer.com",
            creator_account_type="customer",
            issue_type="Support",
            project_key="POST",
            role_repo=role_repo,
            plugin_repo=plugin_repo,
        )
        assert decision == TicketCreationDecision.IGNORE
        assert "Customer-created Service Management" in reason

    def test_mubashir_in_sm_project_not_ignored(self, test_db):
        """Mubashir creating Support ticket in SM project is NOT classified as customer."""
        role_repo = EmployeeRoleRepository(test_db)
        plugin_repo = PluginBoardRepository(test_db)

        decision, reason, can_id, _ = TicketCreationPolicy.evaluate(
            creator_account_id=MUBASHIR_CANONICAL_ACCOUNT_ID,
            creator_display_name="Mubashir Butt",
            creator_email="mubashir@objects.ws",
            issue_type="Support",
            project_key="POST",
            role_repo=role_repo,
            plugin_repo=plugin_repo,
        )
        assert decision == TicketCreationDecision.EXPECTED
        assert can_id == MUBASHIR_CANONICAL_ACCOUNT_ID

    def test_tahir_in_sm_project_not_ignored(self, test_db):
        """Tahir creating ticket in SM project is NOT classified as customer."""
        role_repo = EmployeeRoleRepository(test_db)
        plugin_repo = PluginBoardRepository(test_db)

        decision, reason, can_id, _ = TicketCreationPolicy.evaluate(
            creator_account_id=TAHIR_ALI_ACCOUNT_ID,
            creator_display_name="Tahir Ali",
            creator_email="tahir@objects.ws",
            issue_type="Task",
            project_key="POST",
            role_repo=role_repo,
            plugin_repo=plugin_repo,
        )
        assert decision == TicketCreationDecision.EXPECTED

    def test_qa_in_sm_project_not_ignored(self, test_db):
        """QA creating Bug in SM project is evaluated under QA policy."""
        role_repo = EmployeeRoleRepository(test_db)
        plugin_repo = PluginBoardRepository(test_db)

        decision, reason, _, _ = TicketCreationPolicy.evaluate(
            creator_account_id="712020:c12d2371-1e5b-4797-a888-369c0c9c5a65", # Muhammad Sufiyan
            creator_display_name="Muhammad Sufiyan",
            issue_type="Bug",
            project_key="POST",
            role_repo=role_repo,
            plugin_repo=plugin_repo,
        )
        assert decision == TicketCreationDecision.EXPECTED

    def test_unknown_creator_in_sm_project_requires_review(self, test_db):
        """Unknown creator in SM project without customer evidence returns REVIEW."""
        role_repo = EmployeeRoleRepository(test_db)
        plugin_repo = PluginBoardRepository(test_db)

        decision, reason, _, _ = TicketCreationPolicy.evaluate(
            creator_account_id="unknown-id-999",
            creator_display_name="Random User",
            creator_email=None,
            creator_account_type=None,
            issue_type="Support",
            project_key="POST",
            role_repo=role_repo,
            plugin_repo=plugin_repo,
        )
        assert decision == TicketCreationDecision.REVIEW
        assert "without authoritative customer evidence" in reason

    def test_customer_in_ordinary_internal_project_requires_review(self, test_db):
        """Customer-looking creator in non-SM internal project (POSTSMTP) is not silently ignored."""
        role_repo = EmployeeRoleRepository(test_db)
        plugin_repo = PluginBoardRepository(test_db)

        decision, reason, _, _ = TicketCreationPolicy.evaluate(
            creator_account_id="customer-acc-123",
            creator_display_name="John Customer",
            creator_account_type="customer",
            issue_type="Task",
            project_key="POSTSMTP", # Internal project, not SM
            role_repo=role_repo,
            plugin_repo=plugin_repo,
        )
        assert decision == TicketCreationDecision.REVIEW


# ==============================================================================
# 6. MUBASHIR SUPPORT CREATION WORKFLOW TESTS
# ==============================================================================

class TestMubashirSupportRule:
    def test_sprint_name_extraction(self):
        """Verify sprint name extraction from various Jira Cloud field formats."""
        # 1. customfield_10020 with list of dicts
        f1 = {"customfield_10020": [{"id": 1, "name": "Support Board", "state": "active"}]}
        assert extract_sprint_names(f1) == ["Support Board"]

        # 2. Feature Request sprint
        f2 = {"customfield_10020": [{"id": 2, "name": "Feature Request", "state": "active"}]}
        assert extract_sprint_names(f2) == ["Feature Request"]

        # 3. Serialized sprint string
        f3 = {"customfield_10020": ["com.atlassian.greenhopper.service.sprint.Sprint@123[id=1,name=Support Board,state=ACTIVE]"]}
        assert extract_sprint_names(f3) == ["Support Board"]

        # 4. Empty / missing
        assert extract_sprint_names({}) == []

    def test_mubashir_support_valid_creation(self, test_db):
        """Mubashir + Support + 'Support Board' sprint + label -> PASSES (no actions)."""
        rule = MubashirSupportRule(test_db)
        event = TaskCreated(
            source="jira",
            task_key="POST-101",
            title="Customer Issue",
            actor_id=MUBASHIR_CANONICAL_ACCOUNT_ID,
            actor_name="Mubashir Butt",
            issue_type="Support",
            project_key="POST",
            payload={
                "issue": {
                    "fields": {
                        "customfield_10020": [{"id": 1, "name": "Support Board"}],
                        "labels": ["free"],
                    }
                }
            }
        )
        actions = rule.evaluate(event)
        assert len(actions) == 0

    def test_mubashir_support_missing_sprint(self, test_db):
        """Mubashir + Support + missing sprint -> generates reminder comment & notification."""
        rule = MubashirSupportRule(test_db)
        event = TaskCreated(
            source="jira",
            task_key="POST-102",
            title="Customer Issue 2",
            actor_id=MUBASHIR_CANONICAL_ACCOUNT_ID,
            actor_name="Mubashir Butt",
            issue_type="Support",
            project_key="POST",
            payload={
                "issue": {
                    "fields": {
                        "customfield_10020": [],
                        "labels": ["pro"],
                    }
                }
            }
        )
        actions = rule.evaluate(event)
        assert len(actions) == 2
        comment_act = actions[0]
        assert comment_act.action_type.value == "AddComment"
        assert "Support Board" in comment_act.parameters["comment"]
        assert MUBASHIR_CANONICAL_ACCOUNT_ID in comment_act.parameters["comment"]

    def test_mubashir_support_missing_label(self, test_db):
        """Mubashir + Support + missing label -> generates reminder comment."""
        rule = MubashirSupportRule(test_db)
        event = TaskCreated(
            source="jira",
            task_key="POST-103",
            title="Customer Issue 3",
            actor_id=MUBASHIR_CANONICAL_ACCOUNT_ID,
            actor_name="Mubashir Butt",
            issue_type="Support",
            project_key="POST",
            payload={
                "issue": {
                    "fields": {
                        "customfield_10020": [{"id": 1, "name": "Feature Request"}],
                        "labels": [],
                    }
                }
            }
        )
        actions = rule.evaluate(event)
        assert len(actions) == 2
        comment_act = actions[0]
        assert "Label Requirement" in comment_act.parameters["comment"]

    def test_mubashir_support_missing_both(self, test_db):
        """Mubashir + Support + missing sprint & label -> SINGLE unified comment & alert."""
        rule = MubashirSupportRule(test_db)
        event = TaskCreated(
            source="jira",
            task_key="POST-104",
            title="Customer Issue 4",
            actor_id=MUBASHIR_CANONICAL_ACCOUNT_ID,
            actor_name="Mubashir Butt",
            issue_type="Support",
            project_key="POST",
            payload={"issue": {"fields": {"customfield_10020": [], "labels": []}}}
        )
        actions = rule.evaluate(event)
        assert len(actions) == 2 # 1 comment + 1 notification (no duplicate spam)
        comment_act = actions[0]
        assert "Sprint Requirement" in comment_act.parameters["comment"]
        assert "Label Requirement" in comment_act.parameters["comment"]

    def test_other_creator_support_rule_ignored(self, test_db):
        """Other creator creating Support ticket does NOT trigger MubashirSupportRule."""
        rule = MubashirSupportRule(test_db)
        event = TaskCreated(
            source="jira",
            task_key="POST-105",
            title="Customer Issue 5",
            actor_id="712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de", # Ahsan Amin
            actor_name="Ahsan Amin",
            issue_type="Support",
            project_key="POST",
            payload={"issue": {"fields": {}}}
        )
        actions = rule.evaluate(event)
        assert len(actions) == 0


# ==============================================================================
# 7. MUBASHIR STALE SUPPORT BUSINESS-DAY TESTS
# ==============================================================================

class TestMubashirStaleSupport:
    def test_business_days_calculation_scenarios(self):
        """Verify Asia/Karachi business day calculations with weekend exclusion."""
        tz = zoneinfo.ZoneInfo("Asia/Karachi")

        # 1. Mon 10:00 -> Thu 10:00 = 3.0 business days
        m1 = datetime(2026, 9, 7, 10, 0, tzinfo=tz)
        th1 = datetime(2026, 9, 10, 10, 0, tzinfo=tz)
        assert calculate_business_days(m1, th1) == 3.0

        # 2. Fri 10:00 -> Tue 10:00 = 2.0 business days (spans weekend)
        f1 = datetime(2026, 9, 4, 10, 0, tzinfo=tz)
        tu1 = datetime(2026, 9, 8, 10, 0, tzinfo=tz)
        assert calculate_business_days(f1, tu1) == 2.0

        # 3. Fri 10:00 -> Wed 10:00 = 3.0 business days
        we1 = datetime(2026, 9, 9, 10, 0, tzinfo=tz)
        assert calculate_business_days(f1, we1) == 3.0

        # 4. Weekend only (Sat 12:00 -> Sun 12:00) = 0.0 business days
        sa1 = datetime(2026, 9, 5, 12, 0, tzinfo=tz)
        su1 = datetime(2026, 9, 6, 12, 0, tzinfo=tz)
        assert calculate_business_days(sa1, su1) == 0.0

    @pytest.mark.asyncio
    async def test_stale_support_evaluation_and_idempotency(self, test_db, monkeypatch):
        """Test stale evaluation and idempotency for Mubashir's support tickets."""
        monkeypatch.setattr(type(settings), "is_jira_configured", lambda self: False)
        state_repo = JiraIssueStateRepository(test_db)
        scheduler = PeriodicScheduler(test_db)

        tz = zoneinfo.ZoneInfo("Asia/Karachi")
        now_tz = datetime.now(tz)
        # 4 days ago on a Monday
        past_dt = now_tz - timedelta(days=5)
        past_iso = past_dt.isoformat()

        # Insert eligible Support ticket created by Mubashir in 'Support team review'
        state_repo.upsert(
            jira_issue_key="POST-200",
            summary="Stale Support Issue",
            status="Support team review",
            last_seen_at=past_iso,
            last_activity_at=past_iso,
            project_key="POST",
            raw_reference={
                "fields": {
                    "summary": "Stale Support Issue",
                    "status": {"name": "Support team review"},
                    "issuetype": {"name": "Support"},
                    "creator": {"accountId": MUBASHIR_CANONICAL_ACCOUNT_ID, "displayName": "Mubashir Butt"},
                }
            }
        )

        actions = await scheduler._evaluate_mubashir_stale_support_tickets()
        assert len(actions) == 1
        assert "Automated Stale Update Reminder" in actions[0].parameters["comment"]

        # Duplicate run with same activity timestamp -> idempotent (0 actions)
        actions2 = await scheduler._evaluate_mubashir_stale_support_tickets()
        assert len(actions2) == 0


# ==============================================================================
# 8. ACTIVE EPIC REVIEW & PRECEDENCE TESTS
# ==============================================================================

class TestActiveEpicReview:
    def test_on_hold_epic_remains_unchanged(self, test_db):
        """On Hold Epic remains unchanged regardless of child ticket states."""
        service = EpicReviewService(test_db)
        child_tickets = [
            {"key": "WSSS-11", "fields": {"summary": "Dev", "status": "In Progress", "assignee": {"accountId": "dev-1"}}}
        ]
        classified = service.classify_child_tickets(child_tickets)
        res = service.determine_epic_status_recommendation("WSSS-10", "On Hold", classified)
        assert res["is_on_hold"] is True
        assert res["recommended_status"] is None

    def test_azain_active_ticket_precedence(self, test_db):
        """Any active ticket assigned to Azain sets Epic status to 'Ready for kickoff'."""
        service = EpicReviewService(test_db)
        child_tickets = [
            {"key": "WSSS-21", "fields": {"summary": "Kickoff", "status": "To Do", "assignee": {"accountId": AZAIN_HASSAN_ACCOUNT_ID, "displayName": "Azain Hassan"}}},
            {"key": "WSSS-22", "fields": {"summary": "Dev Task", "status": "In Progress", "assignee": {"accountId": "712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de", "displayName": "Ahsan Amin"}}},
        ]
        classified = service.classify_child_tickets(child_tickets)
        res = service.determine_epic_status_recommendation("WSSS-20", "To Do", classified)
        assert res["recommended_status"] == "Ready for kickoff"

    def test_active_dev_work_precedence(self, test_db):
        """Active Dev ticket sets status to 'In Development'."""
        service = EpicReviewService(test_db)
        child_tickets = [
            {"key": "WSSS-31", "fields": {"summary": "Dev Task", "status": "In Progress", "assignee": {"accountId": "712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de", "displayName": "Ahsan Amin"}}},
            {"key": "WSSS-32", "fields": {"summary": "QA Task", "status": "To Do", "assignee": {"accountId": "712020:c12d2371-1e5b-4797-a888-369c0c9c5a65", "displayName": "Muhammad Sufiyan"}}},
        ]
        classified = service.classify_child_tickets(child_tickets)
        res = service.determine_epic_status_recommendation("WSSS-30", "In Planning", classified)
        assert res["recommended_status"] == "In Development"

    def test_done_dev_with_active_qa_progresses_to_in_qa(self, test_db):
        """Done Dev ticket + active QA ticket progresses Epic to 'In QA'."""
        service = EpicReviewService(test_db)
        child_tickets = [
            {"key": "WSSS-41", "fields": {"summary": "Dev Task", "status": "Done", "assignee": {"accountId": "712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de", "displayName": "Ahsan Amin"}}},
            {"key": "WSSS-42", "fields": {"summary": "QA Task", "status": "In Progress", "assignee": {"accountId": "712020:c12d2371-1e5b-4797-a888-369c0c9c5a65", "displayName": "Muhammad Sufiyan"}}},
        ]
        classified = service.classify_child_tickets(child_tickets)
        res = service.determine_epic_status_recommendation("WSSS-40", "In Development", classified)
        assert res["recommended_status"] == "In QA"

    def test_active_planning_by_ba(self, test_db):
        """Active planning ticket assigned to BA sets status to 'In Planning'."""
        service = EpicReviewService(test_db)
        child_tickets = [
            {"key": "WSSS-51", "fields": {"summary": "BA Spec", "status": "In Progress", "assignee": {"accountId": "63da2ba4f1475ad42c584247", "displayName": "Ahsan Iftikhar"}}},
        ]
        classified = service.classify_child_tickets(child_tickets)
        res = service.determine_epic_status_recommendation("WSSS-50", "To Do", classified)
        assert res["recommended_status"] == "In Planning"

    def test_marketing_condition_satisfied(self, test_db):
        """Marketing ticket assigned to Tahir Ali + all other tickets Done -> 'In Marketing'."""
        service = EpicReviewService(test_db)
        child_tickets = [
            {"key": "WSSS-61", "fields": {"summary": "Marketing Launch", "status": "In Progress", "assignee": {"accountId": TAHIR_ALI_ACCOUNT_ID, "displayName": "Tahir Ali"}}},
            {"key": "WSSS-62", "fields": {"summary": "Dev Work", "status": "Done", "assignee": {"accountId": "712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de", "displayName": "Ahsan Amin"}}},
            {"key": "WSSS-63", "fields": {"summary": "QA Work", "status": "Done", "assignee": {"accountId": "712020:c12d2371-1e5b-4797-a888-369c0c9c5a65", "displayName": "Muhammad Sufiyan"}}},
        ]
        classified = service.classify_child_tickets(child_tickets)
        res = service.determine_epic_status_recommendation("WSSS-60", "In QA", classified)
        assert res["recommended_status"] == "In Marketing"

    def test_marketing_condition_blocked_by_active_dev(self, test_db):
        """Marketing ticket assigned to Tahir Ali BUT active Dev ticket exists -> does NOT force In Marketing."""
        service = EpicReviewService(test_db)
        child_tickets = [
            {"key": "WSSS-71", "fields": {"summary": "Marketing", "status": "In Progress", "assignee": {"accountId": TAHIR_ALI_ACCOUNT_ID, "displayName": "Tahir Ali"}}},
            {"key": "WSSS-72", "fields": {"summary": "Dev Still Active", "status": "In Progress", "assignee": {"accountId": "712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de", "displayName": "Ahsan Amin"}}},
        ]
        classified = service.classify_child_tickets(child_tickets)
        res = service.determine_epic_status_recommendation("WSSS-70", "In Development", classified)
        assert res["recommended_status"] == "In Development" # Dev takes precedence!

    def test_status_already_correct_results_in_no_mutation(self, test_db):
        """If recommended status matches current status, no transition action is required."""
        service = EpicReviewService(test_db)
        child_tickets = [
            {"key": "WSSS-81", "fields": {"summary": "Dev", "status": "In Progress", "assignee": {"accountId": "712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de", "displayName": "Ahsan Amin"}}},
        ]
        classified = service.classify_child_tickets(child_tickets)
        res = service.determine_epic_status_recommendation("WSSS-80", "In Development", classified)
        assert res["recommended_status"] == "In Development"
        assert res["current_status"].lower() == res["recommended_status"].lower()
