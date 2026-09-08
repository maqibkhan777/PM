"""Unit tests for Jira event normalization."""

import pytest
from app.connectors.jira.normalizer import JiraEventNormalizer
from app.core.events.types import (
    TaskCreated,
    TaskStatusChanged,
    TaskAssigned,
    TaskCommentAdded,
    TaskPriorityChanged,
    TaskCompleted,
    TaskReopened,
    TaskWorklogged,
)


def test_normalize_status_change(sample_jira_status_payload):
    """Test normalizing status transition from 'To Do' to 'In Progress'."""
    event = JiraEventNormalizer.normalize(sample_jira_status_payload)
    assert isinstance(event, TaskStatusChanged)
    assert event.source == "jira"
    assert event.task_key == "CF7-421"
    assert event.old_status == "To Do"
    assert event.new_status == "In Progress"
    assert event.actor_name == "Ahsan Amin"
    assert event.external_event_id.startswith("jira:changelog:")


def test_normalize_comment_added(sample_jira_comment_payload):
    """Test normalizing Jira comment creation."""
    event = JiraEventNormalizer.normalize(sample_jira_comment_payload)
    assert isinstance(event, TaskCommentAdded)
    assert event.task_key == "CF7-422"
    assert event.comment_body == "Started working on this issue."
    assert event.actor_name == "Ahsan Amin"
    assert event.comment_id == sample_jira_comment_payload["comment"]["id"]


def test_normalize_reopened_task():
    """Test normalizing task reopened from 'Done' to 'In Progress'."""
    payload = {
        "webhookEvent": "jira:issue_updated",
        "timestamp": 1757248800000,
        "user": {"displayName": "PM Manager"},
        "issue": {"key": "CF7-100", "fields": {"summary": "Bug Fix"}},
        "changelog": {
            "items": [{"field": "status", "fromString": "Done", "toString": "In Progress"}]
        }
    }
    event = JiraEventNormalizer.normalize(payload)
    assert isinstance(event, TaskReopened)
    assert event.previous_status == "Done"
    assert event.new_status == "In Progress"


def test_normalize_completed_task():
    """Test normalizing task completion transition to 'Done'."""
    payload = {
        "webhookEvent": "jira:issue_updated",
        "timestamp": 1757248800000,
        "user": {"displayName": "Dev One"},
        "issue": {"key": "CF7-101", "fields": {"summary": "Feature X"}},
        "changelog": {
            "items": [{"field": "status", "fromString": "In Progress", "toString": "Done"}]
        }
    }
    event = JiraEventNormalizer.normalize(payload)
    assert isinstance(event, TaskCompleted)
    assert event.task_key == "CF7-101"


def test_normalize_assignee_change():
    """Test normalizing issue assignment changelog."""
    payload = {
        "webhookEvent": "jira:issue_updated",
        "timestamp": 1757248800000,
        "user": {"displayName": "Lead"},
        "issue": {"key": "CF7-102", "fields": {"summary": "Refactor"}},
        "changelog": {
            "items": [
                {
                    "field": "assignee",
                    "from": "user-1",
                    "fromString": "Old Assignee",
                    "to": "user-2",
                    "toString": "New Assignee"
                }
            ]
        }
    }
    event = JiraEventNormalizer.normalize(payload)
    assert isinstance(event, TaskAssigned)
    assert event.old_assignee_name == "Old Assignee"
    assert event.new_assignee_name == "New Assignee"


def test_normalize_worklog():
    """Test normalizing worklog creation."""
    payload = {
        "webhookEvent": "jira:worklog_created",
        "timestamp": 1757248800000,
        "user": {"displayName": "Developer"},
        "issue": {"key": "CF7-103", "fields": {"summary": "API Client"}},
        "worklog": {
            "id": "wl-1",
            "timeSpentSeconds": 7200,
            "timeSpent": "2h",
            "comment": "Completed authentication endpoints."
        }
    }
    event = JiraEventNormalizer.normalize(payload)
    assert isinstance(event, TaskWorklogged)
    assert event.time_spent_seconds == 7200
    assert event.time_spent_human == "2h"
