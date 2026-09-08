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
