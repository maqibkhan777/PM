"""Comprehensive Behavior-Based Tests for Phase 4A Production Safety Guards.

Tests:
1. test_epic_review_disabled_by_default
2. test_scheduler_skips_epic_review_when_flag_false
3. test_scheduler_executes_epic_review_when_flag_true
4. test_mubashir_stale_support_flag_disabled
5. test_mubashir_stale_support_flag_enabled
6. test_mubashir_support_creation_rule_flag_disabled
7. test_mubashir_support_creation_rule_flag_enabled
8. test_mubashir_support_creation_requires_both_flags
9. test_production_auth_empty_allowed_users_denies_all
10. test_production_auth_configured_allowed_users
11. test_development_auth_empty_allowed_users_remains_permissive
12. test_missing_discord_user_id_denied
13. test_slash_command_auth_regressions_and_edge_cases
"""

from datetime import datetime, timedelta
import zoneinfo
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.config.settings import Settings, settings
from app.connectors.discord.slash_commands import DiscordSlashCommandHandler
from app.core.actions.engine import ActionEngine
from app.core.events.types import TaskCreated
from app.core.rules.mubashir_support_rule import (
    MubashirSupportRule,
    MUBASHIR_CANONICAL_ACCOUNT_ID,
)
from app.database.repositories import JiraIssueStateRepository, UserRepository
from app.services.epic_review_service import EpicReviewService
from app.services.scheduler import PeriodicScheduler


# ==============================================================================
# FIXTURES
# ==============================================================================

@pytest.fixture
def slash_handler(temp_db):
    """Fixture providing a DiscordSlashCommandHandler instance."""
    user_repo = UserRepository(temp_db)
    action_engine = ActionEngine(manager=temp_db)
    return DiscordSlashCommandHandler(user_repo=user_repo, action_engine=action_engine)


# ==============================================================================
# 1. ACTIVE EPIC REVIEW FLAG TESTS
# ==============================================================================

def test_epic_review_disabled_by_default():
    """Verify EPIC_REVIEW_ENABLED is False by default in configuration."""
    fresh_settings = Settings(_env_file=None)
    assert fresh_settings.EPIC_REVIEW_ENABLED is False
    assert settings.EPIC_REVIEW_ENABLED is False


@pytest.mark.asyncio
async def test_scheduler_skips_epic_review_when_flag_false(temp_db, monkeypatch):
    """When EPIC_REVIEW_ENABLED is False, scheduler must skip epic review with zero Jira calls."""
    monkeypatch.setattr(settings, "EPIC_REVIEW_ENABLED", False)
    monkeypatch.setattr(settings, "JIRA_BASE_URL", "https://real-domain.atlassian.net")
    monkeypatch.setattr(settings, "JIRA_EMAIL", "pm@real-domain.com")
    monkeypatch.setattr(settings, "JIRA_API_TOKEN", "real_token_123")

    scheduler = PeriodicScheduler(temp_db)

    with patch.object(EpicReviewService, "run_review", new_callable=AsyncMock) as mock_review:
        res = await scheduler._evaluate_active_epic_review()
        assert res is None
        mock_review.assert_not_called()


@pytest.mark.asyncio
async def test_scheduler_executes_epic_review_when_flag_true(temp_db, monkeypatch):
    """When EPIC_REVIEW_ENABLED is True and Jira configured, scheduler runs EpicReviewService."""
    monkeypatch.setattr(settings, "EPIC_REVIEW_ENABLED", True)
    monkeypatch.setattr(type(settings), "is_jira_configured", lambda self: True)

    scheduler = PeriodicScheduler(temp_db)

    mock_results = [{"epic_key": "EPIC-10", "status": "In Development", "action": "transition"}]
    with patch.object(EpicReviewService, "run_review", new_callable=AsyncMock, return_value=mock_results) as mock_review:
        res = await scheduler._evaluate_active_epic_review()
        assert res is not None
        assert res["status"] == "completed"
        assert res["epics_reviewed"] == 1
        assert res["details"] == mock_results
        mock_review.assert_called_once()


# ==============================================================================
# 2. MUBASHIR STALE SUPPORT FLAG TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_mubashir_stale_support_flag_disabled(temp_db, monkeypatch):
    """When MUBASHIR_STALE_SUPPORT_ENABLED is False, evaluator produces no actions or Jira API calls."""
    monkeypatch.setattr(settings, "MUBASHIR_STALE_SUPPORT_ENABLED", False)
    monkeypatch.setattr(type(settings), "is_jira_configured", lambda self: False)

    state_repo = JiraIssueStateRepository(temp_db)
    scheduler = PeriodicScheduler(temp_db)

    tz = zoneinfo.ZoneInfo("Asia/Karachi")
    now_tz = datetime.now(tz)
    past_iso = (now_tz - timedelta(days=6)).isoformat()

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
    assert actions == []


@pytest.mark.asyncio
async def test_mubashir_stale_support_flag_enabled(temp_db, monkeypatch):
    """When MUBASHIR_STALE_SUPPORT_ENABLED is True, stale support tickets trigger comment actions."""
    monkeypatch.setattr(settings, "MUBASHIR_STALE_SUPPORT_ENABLED", True)
    monkeypatch.setattr(type(settings), "is_jira_configured", lambda self: False)

    state_repo = JiraIssueStateRepository(temp_db)
    scheduler = PeriodicScheduler(temp_db)

    tz = zoneinfo.ZoneInfo("Asia/Karachi")
    now_tz = datetime.now(tz)
    past_iso = (now_tz - timedelta(days=6)).isoformat()

    state_repo.upsert(
        jira_issue_key="POST-201",
        summary="Stale Support Issue Active",
        status="Support team review",
        last_seen_at=past_iso,
        last_activity_at=past_iso,
        project_key="POST",
        raw_reference={
            "fields": {
                "summary": "Stale Support Issue Active",
                "status": {"name": "Support team review"},
                "issuetype": {"name": "Support"},
                "creator": {"accountId": MUBASHIR_CANONICAL_ACCOUNT_ID, "displayName": "Mubashir Butt"},
            }
        }
    )

    actions = await scheduler._evaluate_mubashir_stale_support_tickets()
    assert len(actions) == 1
    assert actions[0].action_type.value == "AddComment"
    assert MUBASHIR_CANONICAL_ACCOUNT_ID in actions[0].parameters["comment"]


# ==============================================================================
# 3. MUBASHIR SUPPORT CREATION RULE FLAG TESTS
# ==============================================================================

def test_mubashir_support_creation_rule_flag_disabled(temp_db, monkeypatch):
    """When MUBASHIR_SUPPORT_RULE_ENABLED is False, rule produces zero actions even if rule.enabled is True."""
    monkeypatch.setattr(settings, "MUBASHIR_SUPPORT_RULE_ENABLED", False)

    rule = MubashirSupportRule(temp_db)
    rule.enabled = True

    event = TaskCreated(
        source="jira",
        task_key="POST-102",
        title="Customer Issue Missing Sprint",
        actor_id=MUBASHIR_CANONICAL_ACCOUNT_ID,
        actor_name="Mubashir Butt",
        issue_type="Support",
        project_key="POST",
        payload={
            "issue": {
                "fields": {
                    "customfield_10020": [],
                    "labels": [],
                }
            }
        }
    )

    actions = rule.evaluate(event)
    assert actions == []


def test_mubashir_support_creation_rule_flag_enabled(temp_db, monkeypatch):
    """When MUBASHIR_SUPPORT_RULE_ENABLED is True and rule.enabled is True, missing requirements generate actions."""
    monkeypatch.setattr(settings, "MUBASHIR_SUPPORT_RULE_ENABLED", True)

    rule = MubashirSupportRule(temp_db)
    rule.enabled = True

    event = TaskCreated(
        source="jira",
        task_key="POST-103",
        title="Customer Issue Missing Sprint",
        actor_id=MUBASHIR_CANONICAL_ACCOUNT_ID,
        actor_name="Mubashir Butt",
        issue_type="Support",
        project_key="POST",
        payload={
            "issue": {
                "fields": {
                    "customfield_10020": [],
                    "labels": [],
                }
            }
        }
    )

    actions = rule.evaluate(event)
    assert len(actions) == 2
    assert any(a.action_type.value == "AddComment" for a in actions)
    assert any(a.action_type.value == "SendNotification" for a in actions)


def test_mubashir_support_creation_requires_both_flags(temp_db, monkeypatch):
    """Rule must require BOTH MUBASHIR_SUPPORT_RULE_ENABLED=True AND rule.enabled=True."""
    monkeypatch.setattr(settings, "MUBASHIR_SUPPORT_RULE_ENABLED", True)

    rule = MubashirSupportRule(temp_db)
    rule.enabled = False  # Disabled via DB / rule state

    event = TaskCreated(
        source="jira",
        task_key="POST-104",
        title="Customer Issue Disabled Rule",
        actor_id=MUBASHIR_CANONICAL_ACCOUNT_ID,
        actor_name="Mubashir Butt",
        issue_type="Support",
        project_key="POST",
        payload={
            "issue": {
                "fields": {
                    "customfield_10020": [],
                    "labels": [],
                }
            }
        }
    )

    actions = rule.evaluate(event)
    assert actions == []


# ==============================================================================
# 4. DISCORD AUTHORIZATION SAFETY TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_production_auth_empty_allowed_users_denies_all(slash_handler, monkeypatch):
    """In production (APP_ENV=production) with empty DISCORD_PM_ALLOWED_USERS, deny all users."""
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "DISCORD_PM_ALLOWED_USERS", "")
    monkeypatch.setattr(settings, "DISCORD_PM_COMMAND_ENABLED", True)

    assert settings.is_production() is True
    assert settings.is_discord_user_allowed("123456789") is False
    assert slash_handler._is_authorized_pm("123456789") is False

    res = await slash_handler.execute_subcommand(
        subcommand="help",
        options={},
        discord_user_id="123456789"
    )
    assert res == "❌ You are not authorized to use PM commands."


def test_production_auth_configured_allowed_users(slash_handler, monkeypatch):
    """In production with configured DISCORD_PM_ALLOWED_USERS, allow configured users and deny others."""
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "DISCORD_PM_ALLOWED_USERS", "111,222")
    monkeypatch.setattr(settings, "DISCORD_PM_COMMAND_ENABLED", True)

    assert settings.is_discord_user_allowed("111") is True
    assert settings.is_discord_user_allowed("222") is True
    assert settings.is_discord_user_allowed("999") is False

    assert slash_handler._is_authorized_pm("111") is True
    assert slash_handler._is_authorized_pm("222") is True
    assert slash_handler._is_authorized_pm("999") is False


@pytest.mark.asyncio
async def test_development_auth_empty_allowed_users_remains_permissive(slash_handler, monkeypatch):
    """In development (APP_ENV=development) with empty allowlist, preserve permissive dev behavior."""
    monkeypatch.setattr(settings, "APP_ENV", "development")
    monkeypatch.setattr(settings, "DISCORD_PM_ALLOWED_USERS", "")
    monkeypatch.setattr(settings, "DISCORD_PM_COMMAND_ENABLED", True)
    monkeypatch.setattr(settings, "DISCORD_PM_CHANNEL_ID", "1547090800771604482")

    assert settings.is_production() is False
    assert settings.is_discord_user_allowed("123456789") is True
    assert slash_handler._is_authorized_pm("123456789") is True

    res = await slash_handler.execute_subcommand(
        subcommand="help",
        options={},
        discord_user_id="123456789",
        channel_id=settings.DISCORD_PM_CHANNEL_ID,
    )
    assert "PM Commands" in res


def test_missing_discord_user_id_denied(slash_handler, monkeypatch):
    """Missing, None, or empty Discord user IDs must always be denied regardless of environment."""
    for env in ("production", "development", "testing"):
        monkeypatch.setattr(settings, "APP_ENV", env)
        monkeypatch.setattr(settings, "DISCORD_PM_ALLOWED_USERS", "")

        assert settings.is_discord_user_allowed(None) is False
        assert settings.is_discord_user_allowed("") is False
        assert settings.is_discord_user_allowed("   ") is False

        assert slash_handler._is_authorized_pm(None) is False
        assert slash_handler._is_authorized_pm("") is False
        assert slash_handler._is_authorized_pm("   ") is False


# ==============================================================================
# 5. REGRESSION & EDGE CASE TESTS
# ==============================================================================

def test_slash_command_auth_regressions_and_edge_cases(slash_handler, monkeypatch):
    """Verify command disable flag, wildcard support, and whitespace handling."""
    # 1. DISCORD_PM_COMMAND_ENABLED=False denies all even if user is in allowlist
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "DISCORD_PM_ALLOWED_USERS", "111,222")
    monkeypatch.setattr(settings, "DISCORD_PM_COMMAND_ENABLED", False)

    assert slash_handler._is_authorized_pm("111") is False
    assert slash_handler._is_authorized_pm("222") is False

    # 2. Wildcard "*" explicitly allows any user even in production
    monkeypatch.setattr(settings, "DISCORD_PM_COMMAND_ENABLED", True)
    monkeypatch.setattr(settings, "DISCORD_PM_ALLOWED_USERS", "*")

    assert settings.is_discord_user_allowed("any_user_123") is True
    assert slash_handler._is_authorized_pm("any_user_123") is True

    # 3. Comma-separated allowlist with arbitrary whitespace
    monkeypatch.setattr(settings, "DISCORD_PM_ALLOWED_USERS", " 111 ,  222   ")

    assert settings.is_discord_user_allowed("111") is True
    assert settings.is_discord_user_allowed("222") is True
    assert slash_handler._is_authorized_pm("111") is True
    assert slash_handler._is_authorized_pm("222") is True
    assert slash_handler._is_authorized_pm("333") is False
