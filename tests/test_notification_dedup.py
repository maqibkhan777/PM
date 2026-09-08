"""Unit tests for notification deduplication and cooldown tracking."""

import pytest
from app.services.notification_deduplication import NotificationDeduplicationService


def test_notification_deduplication_cooldown(temp_db):
    """Test that repeated alerts within cooldown period are blocked."""
    service = NotificationDeduplicationService(manager=temp_db)

    rule = "StaleTaskRule"
    target = "CF7-421"
    cond = "Stale_24h"

    # First notification should be allowed
    assert service.should_notify(rule, target, cond, cooldown_minutes=60) is True

    # Record notification
    service.record_notification_sent(rule, target, cond)

    # Second notification within 60 minutes should be suppressed
    assert service.should_notify(rule, target, cond, cooldown_minutes=60) is False

    # Different target should still be allowed
    assert service.should_notify(rule, "CF7-999", cond, cooldown_minutes=60) is True
