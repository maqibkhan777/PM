"""Unit tests for the in-process Event Bus."""

import asyncio
import pytest
from app.core.events.bus import EventBus
from app.core.events.types import TaskStatusChanged, TaskCommentAdded


@pytest.mark.asyncio
async def test_event_bus_pub_sub():
    """Test subscribing and receiving specific event types."""
    bus = EventBus()
    received = []

    bus.subscribe(TaskStatusChanged, lambda e: received.append(e))

    event = TaskStatusChanged(source="jira", task_key="CF7-1", old_status="To Do", new_status="In Progress")
    await bus.publish(event)

    assert len(received) == 1
    assert received[0].task_key == "CF7-1"


@pytest.mark.asyncio
async def test_event_bus_wildcard_subscription():
    """Test wildcard '*' subscriber receives all event types."""
    bus = EventBus()
    all_events = []

    bus.subscribe("*", lambda e: all_events.append(e))

    e1 = TaskStatusChanged(source="jira", task_key="CF7-1", old_status="To Do", new_status="In Progress")
    e2 = TaskCommentAdded(source="jira", task_key="CF7-2", comment_body="Hello world")

    await bus.publish(e1)
    await bus.publish(e2)

    assert len(all_events) == 2


@pytest.mark.asyncio
async def test_event_bus_deduplication():
    """Test that publishing the exact same event ID twice is deduplicated."""
    bus = EventBus()
    received = []
    bus.subscribe(TaskStatusChanged, lambda e: received.append(e))

    event = TaskStatusChanged(id="fixed-event-id-123", source="jira", task_key="CF7-1", old_status="To Do", new_status="In Progress")

    await bus.publish(event)
    await bus.publish(event)  # Duplicate

    assert len(received) == 1


@pytest.mark.asyncio
async def test_event_bus_error_isolation():
    """Test that a failing subscriber handler does not crash or block other subscribers."""
    bus = EventBus()
    results = []

    def broken_handler(e):
        raise RuntimeError("Subscriber handler failure!")

    def working_handler(e):
        results.append(e.task_key)

    bus.subscribe(TaskStatusChanged, broken_handler)
    bus.subscribe(TaskStatusChanged, working_handler)

    event = TaskStatusChanged(source="jira", task_key="CF7-Safe", old_status="To Do", new_status="In Progress")
    # Should not raise exception
    await bus.publish(event)

    assert results == ["CF7-Safe"]
