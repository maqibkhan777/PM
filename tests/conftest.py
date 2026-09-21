"""Pytest configuration and shared fixtures."""

import os
import tempfile
import time
import pytest
from app.database.connection import DatabaseManager
from app.database.schema import init_db
from app.config.settings import settings


@pytest.fixture(autouse=True)
def disable_background_scheduler():
    """Ensure background periodic scheduler does not keep test event loops alive."""
    prev = settings.SCHEDULER_ENABLED
    settings.SCHEDULER_ENABLED = False
    yield
    settings.SCHEDULER_ENABLED = prev


@pytest.fixture(autouse=True)
def clean_notification_dedup():
    """Ensure clean notification history across test runs."""
    from app.services.notification_deduplication import notification_dedup_service
    notification_dedup_service.clear_all()
    yield
    notification_dedup_service.clear_all()


@pytest.fixture(autouse=True)
def isolate_test_settings():
    """Ensure mutable global settings are isolated and restored across tests."""
    prev_channel_id = getattr(settings, "DISCORD_PM_CHANNEL_ID", None)
    prev_allowed_users = getattr(settings, "DISCORD_PM_ALLOWED_USERS", None)
    prev_cmd_enabled = getattr(settings, "DISCORD_PM_COMMAND_ENABLED", True)
    prev_dry_run = getattr(settings, "DRY_RUN", False)
    yield
    settings.DISCORD_PM_CHANNEL_ID = prev_channel_id
    settings.DISCORD_PM_ALLOWED_USERS = prev_allowed_users
    settings.DISCORD_PM_COMMAND_ENABLED = prev_cmd_enabled
    settings.DRY_RUN = prev_dry_run


@pytest.fixture
def temp_db():
    """Create an isolated temporary SQLite database for testing."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    mgr = DatabaseManager(db_path=path)
    init_db(mgr)
    yield mgr
    try:
        os.remove(path)
    except OSError:
        pass


@pytest.fixture
def sample_jira_status_payload():
    """Sample Jira webhook payload for status transition."""
    unique_id = int(time.time() * 1000)
    return {
        "webhookEvent": "jira:issue_updated",
        "timestamp": unique_id,
        "user": {
            "accountId": "jira-user-ahsan",
            "displayName": "Ahsan Amin",
            "emailAddress": "ahsan@example.com"
        },
        "issue": {
            "id": f"10001-{unique_id}",
            "key": "CF7-421",
            "fields": {
                "summary": "Payment Gateway Testing",
                "status": {"name": "In Progress"},
                "project": {"id": "100", "key": "CF7", "name": "CF7 Apps"},
                "assignee": {"accountId": "jira-user-ahsan", "displayName": "Ahsan Amin"}
            }
        },
        "changelog": {
            "id": f"chg-{unique_id}",
            "items": [
                {
                    "field": "status",
                    "fromString": "To Do",
                    "toString": "In Progress"
                }
            ]
        }
    }


@pytest.fixture
def sample_jira_comment_payload():
    """Sample Jira webhook payload for added comment."""
    unique_id = int(time.time() * 1000)
    return {
        "webhookEvent": "jira:comment_created",
        "timestamp": unique_id,
        "user": {
            "accountId": "jira-user-ahsan",
            "displayName": "Ahsan Amin"
        },
        "issue": {
            "id": f"10002-{unique_id}",
            "key": "CF7-422",
            "fields": {
                "summary": "Implement Webhooks",
                "status": {"name": "To Do"},
                "project": {"id": "100", "key": "CF7", "name": "CF7 Apps"},
                "assignee": {"accountId": "jira-user-ahsan", "displayName": "Ahsan Amin"}
            }
        },
        "comment": {
            "id": f"comment-{unique_id}",
            "author": {"accountId": "jira-user-ahsan", "displayName": "Ahsan Amin"},
            "body": "Started working on this issue."
        }
    }
