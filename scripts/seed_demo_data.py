"""Seed sample demo data (users, mappings, rules, and initial events) into SQLite."""

import os
import sys

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database.connection import db_manager
from app.database.schema import init_db
from app.database.repositories import UserRepository, UserMappingRepository, RuleRepository, EventRepository
from app.utils.time import utc_now_iso, format_iso
from datetime import datetime, timezone, timedelta
from app.utils.logger import logger


def seed():
    """Seed sample data."""
    logger.info("Initializing database and seeding demo data...")
    init_db()

    user_repo = UserRepository()
    mapping_repo = UserMappingRepository()
    rule_repo = RuleRepository()
    event_repo = EventRepository()

    # 1. Seed External Users
    logger.info("Seeding users...")
    user_repo.upsert(
        external_system="jira",
        external_user_id="jira-user-ahsan",
        display_name="Ahsan Amin",
        email="ahsan.amin@example.com"
    )
    user_repo.upsert(
        external_system="mattermost",
        external_user_id="mm-user-ahsan-456",
        display_name="Ahsan Amin",
        email="ahsan.amin@example.com"
    )

    user_repo.upsert(
        external_system="jira",
        external_user_id="jira-user-sara",
        display_name="Sara Connor",
        email="sara.connor@example.com"
    )
    user_repo.upsert(
        external_system="mattermost",
        external_user_id="mm-user-sara-789",
        display_name="Sara Connor",
        email="sara.connor@example.com"
    )

    # 2. Seed Explicit User Mappings
    logger.info("Seeding user mappings...")
    mapping_repo.upsert_mapping(
        jira_user_id="jira-user-ahsan",
        mattermost_user_id="mm-user-ahsan-456",
        display_name="Ahsan Amin"
    )
    mapping_repo.upsert_mapping(
        jira_user_id="jira-user-sara",
        mattermost_user_id="mm-user-sara-789",
        display_name="Sara Connor"
    )

    # 3. Seed Sample Historical Events
    logger.info("Seeding sample historical events...")
    now = datetime.now(timezone.utc)
    yesterday = format_iso(now - timedelta(days=1))
    two_days_ago = format_iso(now - timedelta(days=2))

    event_repo.insert(
        event_type="TaskCreated",
        source="jira",
        external_event_id="jira:created:CF7-421",
        timestamp=two_days_ago,
        actor_id="jira-user-ahsan",
        actor_name="Ahsan Amin",
        project_id="PROJ-CF7",
        task_id="CF7-421",
        payload={
            "issue": {
                "key": "CF7-421",
                "fields": {
                    "summary": "Payment Gateway Testing",
                    "status": {"name": "To Do"},
                    "project": {"key": "CF7", "name": "CF7 Apps"},
                    "assignee": {"accountId": "jira-user-ahsan", "displayName": "Ahsan Amin"}
                }
            }
        },
        processing_status="PROCESSED"
    )

    logger.info("Demo data seeded successfully!")


if __name__ == "__main__":
    seed()
