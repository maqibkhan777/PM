"""Application settings and configuration management."""

import os
import urllib.parse
from typing import List, Optional, Set
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

    # Mattermost Connector (Optional)
    MATTERMOST_URL: Optional[str] = None
    MATTERMOST_TOKEN: Optional[str] = None
    MATTERMOST_TEAM_NAME: Optional[str] = None

    # Jira Polling Settings
    JIRA_POLLING_ENABLED: bool = True
    JIRA_POLLING_INTERVAL_MINUTES: int = 2
    JIRA_POLLING_BATCH_SIZE: int = 50
    JIRA_POLLING_LOOKBACK_MINUTES: int = 5
    JIRA_POLLING_INITIAL_LOOKBACK_MINUTES: int = 60
    JIRA_TEAM_GROUP: Optional[str] = None

    # User Identity (Optional explicit overrides; defaults to /rest/api/3/myself)
    MY_JIRA_ACCOUNT_ID: Optional[str] = None
    MY_JIRA_EMAIL: Optional[str] = None
    MY_JIRA_DISPLAY_NAME: Optional[str] = None

    # Rules & Notification Policies
    STALE_TASK_HOURS: int = 24
    STALE_TASK_NOTIFY_ASSIGNEE: bool = True
    STALE_TASK_NOTIFY_PM: bool = False  # Stale detection kept, Discord notification muted
    OVERDUE_NOTIFY_PM: bool = False      # Overdue detection kept, Discord notification muted
    WORKFLOW_VIOLATION_NOTIFY_PM: bool = True
    NOTIFICATION_COOLDOWN_MINUTES: int = 60
    COMMENT_NOTIFY_ALL: bool = False     # False = only notify when mentioned; customer/support replies stored silently
    ASSIGNMENT_NOTIFY_TEAM: bool = True  # True = notify on team member assignment for awareness

    # Daily Worklog Reporting Settings
    DAILY_WORKLOG_REPORT_ENABLED: bool = True
    DAILY_WORKLOG_REPORT_TIME: str = "18:00"  # HH:MM format
    DAILY_WORKLOG_REPORT_TIMEZONE: str = "Asia/Karachi"
    DAILY_WORKLOG_REPORT_CHANNEL: str = "pm-alerts"
    DAILY_WORKLOG_EXCLUDED_ACCOUNT_IDS: str = (
        "712020:566cad70-4a54-42bc-bf36-0c6132fe3cf0,"
        "712020:1b564792-a3ab-447c-951d-17aa5507b946,"
        "712020:1b564792-a3af-447c-951d-17aa5507b946,"
        "557058:8b3f9c31-7d88-473a-9351-abacc5b84933,"
        "5f83e3937d9637006ffd0436"
    )

    # Daily Overdue Digest Settings
    OVERDUE_DIGEST_ENABLED: bool = False
    OVERDUE_DIGEST_TIME: str = "09:00"  # HH:MM format
    OVERDUE_DIGEST_TIMEZONE: str = "Asia/Karachi"
    OVERDUE_DIGEST_CHANNEL: Optional[str] = None  # None falls back to PM_DISCORD_CHANNEL

    # Daily PM Attention Digest Settings
    PM_ATTENTION_DIGEST_ENABLED: bool = False
    PM_ATTENTION_DIGEST_TIME: str = "09:00"  # HH:MM format
    PM_ATTENTION_DIGEST_TIMEZONE: str = "Asia/Karachi"
    PM_ATTENTION_DIGEST_CHANNEL: Optional[str] = None  # None falls back to PM_DISCORD_CHANNEL

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
            and self.MATTERMOST_URL.strip()
            and not self.MATTERMOST_URL.startswith("https://mattermost.your-domain")
            and self.MATTERMOST_TOKEN
            and self.MATTERMOST_TOKEN.strip()
            and self.MATTERMOST_TOKEN != "placeholder_mm_token"
        )

    def is_jira_team_group_configured(self) -> bool:
        """Check if Jira team group scoping is configured."""
        return bool(self.JIRA_TEAM_GROUP and self.JIRA_TEAM_GROUP.strip())

    def get_jira_browse_url(self, task_key: str) -> str:
        """Return the clickable Jira issue URL for a task key."""
        base = (self.JIRA_BASE_URL or "https://jira.atlassian.net").rstrip("/")
        clean_key = (task_key or "").strip()
        return f"{base}/browse/{clean_key}"

    def get_jira_issue_navigator_url(self, issue_keys: List[str]) -> str:
        """Return the clickable Jira Issue Navigator URL for a list of issue keys."""
        base = (self.JIRA_BASE_URL or "https://jira.atlassian.net").rstrip("/")
        clean_keys = sorted(list({k.strip() for k in issue_keys if k and k.strip()}))
        if not clean_keys:
            return f"{base}/issues/"
        jql = f"issuekey in ({', '.join(clean_keys)})"
        encoded_jql = urllib.parse.quote(jql, safe="")
        return f"{base}/issues/?jql={encoded_jql}"

    def get_daily_worklog_excluded_account_ids(self) -> Set[str]:
        """Return the set of Jira account IDs excluded from daily worklog reporting."""
        if not self.DAILY_WORKLOG_EXCLUDED_ACCOUNT_IDS:
            return set()
        return {
            x.strip()
            for x in self.DAILY_WORKLOG_EXCLUDED_ACCOUNT_IDS.split(",")
            if x.strip()
        }


# Global settings instance
settings = Settings()
