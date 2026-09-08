"""Application settings and configuration management."""

import os
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    """Application configuration settings loaded from environment or .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # General
    APP_ENV: str = "development"
    DEBUG: bool = True
    HOST: str = "127.0.0.1"
    PORT: int = 8000

    # Dry Run Mode (Default: True for safety)
    DRY_RUN: bool = True

    # SQLite Database
    DB_PATH: str = "data/pm_operations.db"

    # Jira Cloud Connector
    JIRA_BASE_URL: str = "https://your-domain.atlassian.net"
    JIRA_EMAIL: str = "pm-agent@your-domain.com"
    JIRA_API_TOKEN: str = "placeholder_token"
    JIRA_WEBHOOK_SECRET: Optional[str] = None

    # Discord Connector (Webhook)
    DISCORD_WEBHOOK_URL: str = "https://discord.com/api/webhooks/placeholder"
    PM_DISCORD_CHANNEL: str = "pm-alerts"

    # Mattermost Connector
    MATTERMOST_URL: str = "https://mattermost.your-domain.com"
    MATTERMOST_TOKEN: str = "placeholder_mm_token"
    MATTERMOST_TEAM_NAME: str = "main"

    # Rules & Notification Policies
    STALE_TASK_HOURS: int = 24
    STALE_TASK_NOTIFY_ASSIGNEE: bool = True
    STALE_TASK_NOTIFY_PM: bool = True
    OVERDUE_NOTIFY_PM: bool = True
    WORKFLOW_VIOLATION_NOTIFY_PM: bool = True
    NOTIFICATION_COOLDOWN_MINUTES: int = 60

    # Scheduler Settings
    SCHEDULER_ENABLED: bool = True
    SCHEDULER_INTERVAL_MINUTES: int = 15

    # HTTP Client / Network
    REQUEST_TIMEOUT_SECONDS: float = 10.0
    MAX_RETRIES: int = 3
    RETRY_BACKOFF_FACTOR: float = 1.5

    def get_database_path(self) -> str:
        """Ensure parent directory exists and return absolute database path."""
        db_dir = os.path.dirname(os.path.abspath(self.DB_PATH))
        os.makedirs(db_dir, exist_ok=True)
        return os.path.abspath(self.DB_PATH)

    def is_jira_configured(self) -> bool:
        """Check if Jira credentials are meaningfully configured."""
        return bool(
            self.JIRA_BASE_URL
            and not self.JIRA_BASE_URL.startswith("https://your-domain")
            and self.JIRA_EMAIL
            and not self.JIRA_EMAIL.startswith("pm-agent@your-domain")
            and self.JIRA_API_TOKEN
            and self.JIRA_API_TOKEN != "placeholder_token"
        )

    def is_discord_configured(self) -> bool:
        """Check if Discord webhook URL is meaningfully configured."""
        return bool(
            self.DISCORD_WEBHOOK_URL
            and not self.DISCORD_WEBHOOK_URL.endswith("placeholder")
        )

    def is_mattermost_configured(self) -> bool:
        """Check if Mattermost credentials are meaningfully configured."""
        return bool(
            self.MATTERMOST_URL
            and not self.MATTERMOST_URL.startswith("https://mattermost.your-domain")
            and self.MATTERMOST_TOKEN
            and self.MATTERMOST_TOKEN != "placeholder_mm_token"
        )


# Global settings instance
settings = Settings()
