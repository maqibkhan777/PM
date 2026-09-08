"""Notification deduplication and cooldown tracking."""

from datetime import datetime, timezone
from typing import Optional
from app.database.repositories import NotificationRepository
from app.database.connection import db_manager, DatabaseManager
from app.config.settings import settings
from app.utils.time import parse_iso_datetime, utc_now, hours_between
from app.utils.logger import logger


class NotificationDeduplicationService:
    """Tracks sent notifications and enforces cooldown windows to avoid spam."""

    def __init__(self, manager: Optional[DatabaseManager] = None):
        self.mgr = manager or db_manager
        self.repo = NotificationRepository(self.mgr)

    def should_notify(
        self,
        rule_id: str,
        target_id: str,
        condition: str,
        cooldown_minutes: Optional[int] = None
    ) -> bool:
        """Check if a notification for this rule, target, and condition is permitted."""
        cooldown_min = cooldown_minutes if cooldown_minutes is not None else settings.NOTIFICATION_COOLDOWN_MINUTES
        last_rec = self.repo.get_last_notification(rule_id=rule_id, target_id=target_id, condition=condition)

        if not last_rec:
            return True

        last_notified_str = last_rec.get("last_notified_at")
        last_dt = parse_iso_datetime(last_notified_str)
        if not last_dt:
            return True

        elapsed_hours = hours_between(last_dt)
        elapsed_minutes = elapsed_hours * 60.0

        if elapsed_minutes < cooldown_min:
            logger.debug(
                f"Notification suppressed by deduplication: rule={rule_id}, target={target_id}, "
                f"condition={condition} (last notified {elapsed_minutes:.1f}m ago, cooldown={cooldown_min}m)"
            )
            return False

        return True

    def record_notification_sent(
        self,
        rule_id: str,
        target_id: str,
        condition: str,
        channel: str = "default"
    ) -> None:
        """Record that a notification was dispatched."""
        self.repo.record_notification(
            rule_id=rule_id,
            target_id=target_id,
            condition=condition,
            channel=channel
        )

    def clear_all(self) -> None:
        """Clear all notification records (useful in test setups)."""
        with self.mgr.session() as conn:
            conn.execute("DELETE FROM notifications")


# Global notification deduplication service instance
notification_dedup_service = NotificationDeduplicationService()
