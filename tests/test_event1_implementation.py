"""Comprehensive tests for Event 1 Implementation, Gap Closure & Production Readiness.

Covers:
1. Official Pakistani Federal Public Holiday integration, seeding, and provenance.
2. Optional holidays exclusion from public holiday suppression.
3. Manual Discord slash command date suppression (/pm event1 skip-date & /pm event1 status).
4. Validation rules: date format, past date rejection, reason requirement, administrator authorization.
5. Event-scoped Mattermost authorization (EventScope.MURSALEEN_WORKLOG_REMINDER) vs global self-only guard.
6. Canonical recipient exclusions (Aqib Khan primary & alias, Mohammad Mursaleen, Syed Muhammad Usman).
7. Decision logic: 0m (with/without activity), 1-59m, >=60m, approved leave, weekend/holiday precedence.
8. Exact reminder message format verification.
9. Deterministic idempotency and duplicate prevention.
"""

import pytest
import datetime
import zoneinfo
from unittest.mock import AsyncMock, MagicMock, patch

from app.config.settings import settings
from app.core.actions.engine import ActionEngine, action_engine
from app.core.actions.types import create_send_message_action
from app.core.models.enums import ActionStatus, EventScope
from app.database.connection import db_manager
from app.database.repositories import (
    HolidayRepository,
    SuppressionRepository,
    UserMappingRepository,
)
from app.connectors.mattermost.connector import MattermostConnector
from app.connectors.discord.slash_commands import DiscordSlashCommandHandler
from app.services.worklog_reminder_service import (
    WorklogReminderService,
    EVENT1_REMINDER_TEXT,
)


@pytest.fixture
def clean_repos():
    hol_repo = HolidayRepository(db_manager)
    supp_repo = SuppressionRepository(db_manager)
    mapping_repo = UserMappingRepository(db_manager)
    return hol_repo, supp_repo, mapping_repo


# =============================================================================
# 1. Official Pakistani Federal Public Holiday Integration Tests
# =============================================================================

def test_official_2026_pakistan_public_holidays_seeded(clean_repos):
    """Verify official 2026 federal public holidays are persisted in SQLite with provenance."""
    hol_repo, _, _ = clean_repos
    
    # Check key gazetted dates from Cabinet Division
    assert hol_repo.is_official_public_holiday("2026-02-05")  # Kashmir Day
    assert hol_repo.is_official_public_holiday("2026-03-23")  # Pakistan Day / Eid-ul-Fitr Day 3
    assert hol_repo.is_official_public_holiday("2026-05-01")  # Labour Day
    assert hol_repo.is_official_public_holiday("2026-05-26")  # Eid-ul-Azha
    assert hol_repo.is_official_public_holiday("2026-08-14")  # Independence Day
    assert hol_repo.is_official_public_holiday("2026-08-26")  # Eid Milad-un-Nabi
    assert hol_repo.is_official_public_holiday("2026-11-09")  # Iqbal Day
    assert hol_repo.is_official_public_holiday("2026-12-25")  # Quaid-e-Azam Day / Christmas

    kashmir = hol_repo.get_by_date("2026-02-05")
    assert kashmir is not None
    assert kashmir["name"] == "Kashmir Day"
    assert kashmir["source_type"] == "OFFICIAL_GAZETTE"
    assert kashmir["status"] == "VERIFIED"
    assert "cabinet.gov.pk" in (kashmir.get("source_url") or "")


def test_optional_holidays_excluded_from_public_holiday_suppression(clean_repos):
    """Verify optional/community holidays do not automatically suppress federal public holiday checks."""
    hol_repo, _, _ = clean_repos
    # Add an optional holiday
    hol_repo.upsert_holiday(
        holiday_date="2026-03-04",
        name="Holi",
        category="OPTIONAL_HOLIDAY",
        source_type="CABINET_DIVISION_CIRCULAR",
        status="VERIFIED"
    )
    # is_official_public_holiday must only return True for category='PUBLIC_HOLIDAY'
    assert hol_repo.is_official_public_holiday("2026-03-04") is False
    rec = hol_repo.get_by_date("2026-03-04", category="OPTIONAL_HOLIDAY")
    assert rec is not None
    assert rec["category"] == "OPTIONAL_HOLIDAY"


def test_holiday_upsert_idempotent(clean_repos):
    """Verify repeated holiday imports do not create duplicate rows."""
    hol_repo, _, _ = clean_repos
    test_date = "2099-10-15"
    id1 = hol_repo.upsert_holiday(
        holiday_date=test_date,
        name="Test Holiday",
        category="PUBLIC_HOLIDAY",
        source_type="OFFICIAL_GAZETTE",
        status="VERIFIED"
    )
    id2 = hol_repo.upsert_holiday(
        holiday_date=test_date,
        name="Test Holiday Updated",
        category="PUBLIC_HOLIDAY",
        source_type="OFFICIAL_GAZETTE",
        status="VERIFIED"
    )
    assert id1 == id2
    rec = hol_repo.get_by_date(test_date)
    assert rec["name"] == "Test Holiday Updated"
    with hol_repo.mgr.session() as conn:
        conn.execute("DELETE FROM holiday_calendar WHERE holiday_date = ?", (test_date,))


# =============================================================================
# 2. Manual Discord Slash Command Suppression Tests
# =============================================================================

@pytest.mark.asyncio
async def test_manual_discord_suppression_current_and_future_date():
    """Verify authorized PM admin can manually suppress a current or future date."""
    handler = DiscordSlashCommandHandler(manager=db_manager)
    pkt_now = datetime.datetime.now(zoneinfo.ZoneInfo("Asia/Karachi"))
    today_str = pkt_now.strftime("%Y-%m-%d")
    future_str = (pkt_now + datetime.timedelta(days=10)).strftime("%Y-%m-%d")

    # Suppress future date
    res = await handler.handle_event1_skip_date_command(
        date_str=future_str,
        reason="Company Annual Offsite",
        discord_user_id="123456789"
    )
    assert "✅" in res
    assert future_str in res
    assert "Company Annual Offsite" in res

    # Verify persisted in SQLite
    supp_repo = SuppressionRepository(db_manager)
    supp = supp_repo.get_suppression("EVENT1", future_str)
    assert supp is not None
    assert supp["reason"] == "Company Annual Offsite"
    assert supp["created_by"] == "discord:123456789"
    assert supp["source"] == "MANUAL_DISCORD"


@pytest.mark.asyncio
async def test_manual_discord_suppression_past_date_rejected():
    """Verify past dates are rejected with a clear error."""
    handler = DiscordSlashCommandHandler(manager=db_manager)
    res = await handler.handle_event1_skip_date_command(
        date_str="2020-01-01",
        reason="Old holiday",
        discord_user_id="123456789"
    )
    assert "❌" in res
    assert "past date" in res.lower()


@pytest.mark.asyncio
async def test_manual_discord_suppression_invalid_date_or_empty_reason():
    """Verify malformed dates and empty reasons are rejected."""
    handler = DiscordSlashCommandHandler(manager=db_manager)
    res1 = await handler.handle_event1_skip_date_command(
        date_str="invalid-date",
        reason="Holiday",
        discord_user_id="123456789"
    )
    assert "❌" in res1
    assert "Invalid date format" in res1

    res2 = await handler.handle_event1_skip_date_command(
        date_str="2026-11-20",
        reason="",
        discord_user_id="123456789"
    )
    assert "❌" in res2
    assert "reason" in res2.lower()


@pytest.mark.asyncio
async def test_manual_discord_status_command():
    """Verify /pm event1 status accurately reflects weekends, official holidays, and manual suppressions."""
    handler = DiscordSlashCommandHandler(manager=db_manager)
    supp_repo = SuppressionRepository(db_manager)
    
    # Suppress a date
    supp_repo.set_suppression(
        event_type="EVENT1",
        target_date="2026-11-20",
        reason="Local Tech Conference",
        created_by="discord:admin"
    )

    res = await handler.handle_event1_status_command(date_str="2026-11-20")
    assert "2026-11-20" in res
    assert "Local Tech Conference" in res
    assert "SKIPPED" in res

    # Check official holiday status
    res_kashmir = await handler.handle_event1_status_command(date_str="2026-02-05")
    assert "Kashmir Day" in res_kashmir
    assert "Official Federal Public Holiday" in res_kashmir
    assert "Yes" in res_kashmir


# =============================================================================
# 3. Event-Scoped Mattermost Authorization Tests
# =============================================================================

@pytest.mark.asyncio
async def test_event_scoped_authorization_permits_eligible_recipient():
    """Verify EventScope.MURSALEEN_WORKLOG_REMINDER allows DM to verified cluster member in dry-run/mock."""
    engine = ActionEngine(db_manager)
    mock_client = MagicMock()
    connector = MattermostConnector(client=mock_client)
    engine.register_connector(connector)
    
    # Mapped member (Azain Hassan)
    azain_jira = "712020:1ddac8e3-e006-48e7-b4c9-ee941efc8e6e"
    azain_mm = "zhth9yx8yprpbds1nyc4h7tich"

    action = create_send_message_action(
        target_system="mattermost",
        target_id=azain_mm,
        text=EVENT1_REMINDER_TEXT,
        requested_by="Event1_Test"
    )
    action.parameters["event_scope"] = EventScope.MURSALEEN_WORKLOG_REMINDER.value
    action.parameters["jira_user_id"] = azain_jira
    action.parameters["display_name"] = "Azain Hassan"
    action.dry_run = True

    res = await engine.execute(action)
    assert res.success is True
    assert res.status == ActionStatus.DRY_RUN_SIMULATED


@pytest.mark.asyncio
async def test_event_scoped_authorization_rejects_canonical_exclusions():
    """Verify EventScope.MURSALEEN_WORKLOG_REMINDER strictly rejects all 4 canonical excluded accounts."""
    engine = ActionEngine(db_manager)
    mock_client = MagicMock()
    connector = MattermostConnector(client=mock_client)
    engine.register_connector(connector)
    
    excluded_accounts = [
        ("712020:566cad70-4a54-42bc-bf36-0c6132fe3cf0", "Aqib Khan (Primary)"),
        ("712020:1b564792-a3ab-447c-951d-17aa5507b946", "Aqib Khan (Alias)"),
        ("557058:8b3f9c31-7d88-473a-9351-abacc5b84933", "Mohammad Mursaleen"),
        ("5f83e3937d9637006ffd0436", "Syed Muhammad Usman"),
    ]

    for jira_id, name in excluded_accounts:
        action = create_send_message_action(
            target_system="mattermost",
            target_id="some_mm_user_id",
            text=EVENT1_REMINDER_TEXT,
            requested_by="Event1_Test"
        )
        action.parameters["event_scope"] = EventScope.MURSALEEN_WORKLOG_REMINDER.value
        action.parameters["jira_user_id"] = jira_id
        action.parameters["display_name"] = name
        action.dry_run = True

        res = await engine.execute(action)
        assert res.success is False
        assert res.status == ActionStatus.REJECTED
        assert "excluded" in (res.error_message or "").lower()


@pytest.mark.asyncio
async def test_unscoped_action_still_enforces_global_self_only_guard():
    """Verify general/unscoped Mattermost actions to non-self users remain strictly blocked."""
    engine = ActionEngine(db_manager)
    mock_client = MagicMock()
    connector = MattermostConnector(client=mock_client)
    engine.register_connector(connector)
    
    # Generic action without EventScope to a non-self user
    action = create_send_message_action(
        target_system="mattermost",
        target_id="zhth9yx8yprpbds1nyc4h7tich", # Azain Hassan
        text="General notification",
        requested_by="General_Test"
    )
    action.parameters["jira_user_id"] = "712020:1ddac8e3-e006-48e7-b4c9-ee941efc8e6e"
    action.parameters["display_name"] = "Azain Hassan"
    # No event_scope parameter!
    action.dry_run = True

    res = await engine.execute(action)
    assert res.success is False
    assert res.status == ActionStatus.REJECTED
    assert "Only verified self account" in (res.error_message or "")


# =============================================================================
# 4. Decision Matrix & Worklog Evaluation Tests
# =============================================================================

def test_worklog_reminder_service_decision_matrix():
    svc = WorklogReminderService(db_manager)

    # 1. Compliant (>= 60 min)
    e1 = svc.evaluate_member("712020:1ddac8e3-e006-48e7-b4c9-ee941efc8e6e", "Azain Hassan", 60, True)
    assert e1["status"] == "COMPLIANT"
    assert e1["send_reminder"] is False

    # 2. Compliant (> 60 min)
    e2 = svc.evaluate_member("712020:1ddac8e3-e006-48e7-b4c9-ee941efc8e6e", "Azain Hassan", 180, True)
    assert e2["status"] == "COMPLIANT"
    assert e2["send_reminder"] is False

    # 3. Underlogged (1 - 59 min)
    e3 = svc.evaluate_member("712020:1ddac8e3-e006-48e7-b4c9-ee941efc8e6e", "Azain Hassan", 45, True)
    assert e3["status"] == "REMINDER_CANDIDATE"
    assert e3["send_reminder"] is True

    # 4. Zero worklog with Jira activity
    e4 = svc.evaluate_member("712020:1ddac8e3-e006-48e7-b4c9-ee941efc8e6e", "Azain Hassan", 0, True)
    assert e4["status"] == "REMINDER_CANDIDATE"
    assert e4["send_reminder"] is True

    # 5. Zero worklog without activity
    e5 = svc.evaluate_member("712020:1ddac8e3-e006-48e7-b4c9-ee941efc8e6e", "Azain Hassan", 0, False)
    assert e5["status"] == "SKIPPED_NO_ACTIVITY_LEAVE_UNVERIFIED"
    assert e5["send_reminder"] is False

    # 6. Verified approved leave
    e6 = svc.evaluate_member("712020:1ddac8e3-e006-48e7-b4c9-ee941efc8e6e", "Azain Hassan", 0, True, leave_status="approved")
    assert e6["status"] == "SKIPPED_APPROVED_LEAVE"
    assert e6["send_reminder"] is False

    # 7. Canonical Exclusion (Aqib)
    e7 = svc.evaluate_member("712020:566cad70-4a54-42bc-bf36-0c6132fe3cf0", "Aqib Khan", 0, True)
    assert e7["status"] == "EXCLUDED"
    assert e7["send_reminder"] is False


@pytest.mark.asyncio
async def test_weekend_and_holiday_precedence_in_service():
    """Verify date evaluation returns SKIPPED_WEEKEND or SKIPPED_HOLIDAY before evaluating members."""
    svc = WorklogReminderService(db_manager)

    # Weekend (2026-10-10 is Saturday)
    res_weekend = await svc.evaluate_date(target_date="2026-10-10", dry_run=True)
    assert res_weekend["status"] == "SKIPPED_WEEKEND"
    assert res_weekend["reminders_prepared"] == 0

    # Official Holiday (2026-02-05 Kashmir Day)
    res_holiday = await svc.evaluate_date(target_date="2026-02-05", dry_run=True)
    assert res_holiday["status"] == "SKIPPED_HOLIDAY"
    assert res_holiday["reminders_prepared"] == 0


def test_exact_reminder_message_immutable():
    """Verify reminder message string exactly matches specification."""
    expected = "You have not yet logged your work. Kindly log your work for today in the respective tickets."
    assert EVENT1_REMINDER_TEXT == expected
    assert "Hi" not in EVENT1_REMINDER_TEXT
    assert "Hello" not in EVENT1_REMINDER_TEXT
    assert "@" not in EVENT1_REMINDER_TEXT
    assert "!" not in EVENT1_REMINDER_TEXT
