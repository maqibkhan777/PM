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

    # Discord Connector (Webhook & Interactive Bot)
    DISCORD_WEBHOOK_URL: str = "https://discord.com/api/webhooks/placeholder"
    DISCORD_BOT_TOKEN: Optional[str] = None
    DISCORD_APPLICATION_ID: Optional[str] = None
    DISCORD_GUILD_ID: Optional[str] = None  # Optional: specific guild ID for instant dev slash-command registration
    DISCORD_PM_ALLOWED_USERS: str = ""  # Comma-separated Discord user IDs allowed to run /pm commands
    DISCORD_PM_COMMAND_ENABLED: bool = True
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

    # Performance Data Foundation (Phase A v1.0)
    PERFORMANCE_ANALYSIS_ENABLED: bool = True
    PERFORMANCE_HISTORY_DAYS: int = 365
    PERFORMANCE_MIN_HISTORY_DAYS: int = 30
    PERFORMANCE_WORKDAY_HOURS: float = 6.75
    PERFORMANCE_WORKDAY_MIN_HOURS: float = 6.5
    PERFORMANCE_WORKDAY_MAX_HOURS: float = 7.0
    PERFORMANCE_REVIEW_BUFFER_PERCENT: float = 15.0
    PERFORMANCE_DEFAULT_CONFIDENCE: str = "LOW"
    PERFORMANCE_TIMEZONE: str = "Asia/Karachi"
    PERFORMANCE_MIN_SEGMENT_SAMPLES: int = 5
    PERFORMANCE_ANALYSIS_INTERVAL_MINUTES: int = 60
    PERFORMANCE_RISK_GREEN_BUFFER_DAYS: float = 2.0
    PERFORMANCE_RISK_YELLOW_BUFFER_DAYS: float = 1.0
    PERFORMANCE_RISK_ORANGE_BUFFER_DAYS: float = 0.0
    PERFORMANCE_RISK_RED_DAYS_LATE: float = 1.0
    PERFORMANCE_CONFIDENCE_HIGH_DAYS: int = 90
    PERFORMANCE_CONFIDENCE_HIGH_TASKS: int = 30
    PERFORMANCE_CONFIDENCE_MEDIUM_DAYS: int = 60
    PERFORMANCE_CONFIDENCE_MEDIUM_TASKS: int = 15
    PERFORMANCE_ROLE_MAPPINGS: str = ""  # Comma-separated account_id:role or display_name:role

    # Deterministic Fallback Hours per Complexity Score (1 to 5)
    PERFORMANCE_FALLBACK_HOURS_COMPLEXITY_1: float = 1.5
    PERFORMANCE_FALLBACK_HOURS_COMPLEXITY_2: float = 3.0
    PERFORMANCE_FALLBACK_HOURS_COMPLEXITY_3: float = 5.0
    PERFORMANCE_FALLBACK_HOURS_COMPLEXITY_4: float = 8.0
    PERFORMANCE_FALLBACK_HOURS_COMPLEXITY_5: float = 14.0

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

    def is_discord_bot_configured(self) -> bool:
        """Check if Discord Bot credentials (Token and Application ID) are configured."""
        return bool(
            self.DISCORD_BOT_TOKEN
            and self.DISCORD_BOT_TOKEN.strip()
            and self.DISCORD_BOT_TOKEN != "placeholder_bot_token"
            and self.DISCORD_APPLICATION_ID
            and self.DISCORD_APPLICATION_ID.strip()
            and self.DISCORD_APPLICATION_ID != "placeholder_application_id"
        )

    def get_discord_pm_allowed_users(self) -> Set[str]:
        """Return the set of Discord user IDs authorized to execute /pm commands."""
        if not self.DISCORD_PM_ALLOWED_USERS:
            return set()
        return {
            x.strip()
            for x in self.DISCORD_PM_ALLOWED_USERS.split(",")
            if x.strip()
        }

    def is_discord_user_allowed(self, discord_user_id: Optional[str]) -> bool:
        """Check if a Discord user ID is allowed to run PM commands.
        
        If DISCORD_PM_ALLOWED_USERS is empty, in development it defaults to allowed;
        if populated, strictly enforces the allowlist.
        """
        if not discord_user_id:
            return False
        allowed = self.get_discord_pm_allowed_users()
        if not allowed:
            # If no explicit allowlist is configured, permit (e.g. initial dev/testing)
            return True
        return str(discord_user_id).strip() in allowed

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

    def get_canonical_excluded_account_ids(self) -> Set[str]:
        """Return the canonical single-source-of-truth set of Jira account IDs excluded globally."""
        if not self.DAILY_WORKLOG_EXCLUDED_ACCOUNT_IDS:
            return set()
        return {
            x.strip()
            for x in self.DAILY_WORKLOG_EXCLUDED_ACCOUNT_IDS.split(",")
            if x.strip()
        }

    def get_daily_worklog_excluded_account_ids(self) -> Set[str]:
        """Alias to canonical excluded account IDs for backward compatibility."""
        return self.get_canonical_excluded_account_ids()

    def is_canonical_excluded(self, account_id: Optional[str], display_name: Optional[str] = None) -> bool:
        """Check whether a given Jira account_id or display_name is globally excluded."""
        excluded_ids = self.get_canonical_excluded_account_ids()
        if account_id and str(account_id).strip() in excluded_ids:
            return True
        return False

    def get_fallback_hours_for_complexity(self, complexity_score: int) -> float:
        """Return configurable deterministic fallback hours for a given complexity score (1 to 5)."""
        fallback_map = {
            1: float(self.PERFORMANCE_FALLBACK_HOURS_COMPLEXITY_1),
            2: float(self.PERFORMANCE_FALLBACK_HOURS_COMPLEXITY_2),
            3: float(self.PERFORMANCE_FALLBACK_HOURS_COMPLEXITY_3),
            4: float(self.PERFORMANCE_FALLBACK_HOURS_COMPLEXITY_4),
            5: float(self.PERFORMANCE_FALLBACK_HOURS_COMPLEXITY_5),
        }
        return fallback_map.get(complexity_score, float(self.PERFORMANCE_FALLBACK_HOURS_COMPLEXITY_3))

    def get_performance_role_mappings(self) -> dict:
        """Return explicit account_id/name -> role mapping dictionary."""
        if not self.PERFORMANCE_ROLE_MAPPINGS:
            return {}
        mappings = {}
        for pair in self.PERFORMANCE_ROLE_MAPPINGS.split(","):
            if ":" in pair:
                k, v = pair.split(":", 1)
                if k.strip() and v.strip():
                    mappings[k.strip().lower()] = v.strip()
        return mappings


# Global settings instance
settings = Settings()
