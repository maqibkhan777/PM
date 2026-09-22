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

    # AI Foundation (Phase 1 Decision Support — Disabled by default)
    AI_ENABLED: bool = False
    AI_PROVIDER: str = "mock"  # Provider identifier: "mock", "null", or future provider names
    AI_MODEL: Optional[str] = None  # Optional model identifier (e.g. "default")
    AI_BASE_URL: Optional[str] = None  # Optional custom base URL for provider endpoint
    AI_API_KEY: Optional[str] = None  # Provider API Key (treated strictly as sensitive secret)
    AI_TIMEOUT_SECONDS: float = 30.0  # Timeout for AI provider operations (must be > 0 and <= 300)
    AI_MAX_INPUT_TOKENS: int = 4000  # Conservative bounded context limit (must be > 0 and <= 128000)
    AI_MAX_OUTPUT_TOKENS: int = 2000  # Conservative response limit (must be > 0 and <= 16000)

    # SQLite Database
    DB_PATH: str = "data/pm_operations.db"
    DATABASE_PATH: Optional[str] = None  # Optional alias for DB_PATH
    BACKUP_DIR: str = "/opt/pm/backups"

    # Backup Encryption & Google Drive Integration
    BACKUP_ENCRYPTION_ENABLED: bool = True
    BACKUP_ENCRYPTION_KEY: Optional[str] = None
    GDRIVE_ENABLED: bool = False
    GDRIVE_FOLDER_ID: Optional[str] = None
    GDRIVE_SERVICE_ACCOUNT_FILE: Optional[str] = None
    GDRIVE_BACKUP_FILENAME: str = "PM_Operations_Latest.db.gz.enc"
    GDRIVE_CHECKSUM_FILENAME: str = "PM_Operations_Latest.db.gz.enc.sha256"

    # API Documentation Exposure
    DOCS_ENABLED: bool = True

    # Jira Cloud Connector
    JIRA_BASE_URL: str = "https://your-domain.atlassian.net"
    JIRA_EMAIL: str = "pm-agent@your-domain.com"
    JIRA_API_TOKEN: str = "placeholder_token"
    JIRA_WEBHOOK_SECRET: Optional[str] = None

    # Discord Connector (Webhook & Interactive Bot)
    DISCORD_WEBHOOK_URL: str = "https://discord.com/api/webhooks/placeholder"
    DISCORD_NOTIFICATIONS_WEBHOOK_URL: Optional[str] = None  # Dedicated webhook for #notifications
    DISCORD_BOT_TOKEN: Optional[str] = None
    DISCORD_APPLICATION_ID: Optional[str] = None
    DISCORD_GUILD_ID: Optional[str] = None  # Optional: specific guild ID for instant dev slash-command registration
    DISCORD_PM_ALLOWED_USERS: str = ""  # Comma-separated Discord user IDs allowed to run /pm commands
    DISCORD_PM_COMMAND_ENABLED: bool = True
    DISCORD_PM_CHANNEL_ID: Optional[str] = None  # Authoritative Discord Snowflake ID for #pm-alerts
    PM_DISCORD_CHANNEL: str = "pm-alerts"
    JIRA_NOTIFICATION_DISCORD_CHANNEL: str = "notifications"

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
    TICKET_CREATION_NOTIFY_PM: bool = True
    NOTIFICATION_COOLDOWN_MINUTES: int = 60

    # Production Automation Safety Flags (Default: False)
    EPIC_REVIEW_ENABLED: bool = False
    MUBASHIR_STALE_SUPPORT_ENABLED: bool = False
    MUBASHIR_SUPPORT_RULE_ENABLED: bool = False

    # Active Queue Filter Settings (Jira Cloud source of truth)
    JIRA_ACTIVE_QUEUE_FILTER_ID: Optional[str] = None
    JIRA_ACTIVE_QUEUE_JQL: Optional[str] = None
    COMMENT_NOTIFY_ALL: bool = False     # False = only notify when mentioned; customer/support replies stored silently
    ASSIGNMENT_NOTIFY_TEAM: bool = True  # True = notify on team member assignment for awareness

    # Centralized Reporting Configuration (Reports v1.2)
    REPORT_TIMEZONE: str = "Asia/Karachi"
    REPORT_DEFAULT_TIME: str = "08:40"  # 08:40 AM PKT for all standard reports
    WORKLOG_REPORT_TIME: str = "23:59"  # 11:59 PM PKT for Daily Worklog Report

    # Daily Worklog Reporting Settings
    DAILY_WORKLOG_REPORT_ENABLED: bool = True
    DAILY_WORKLOG_REPORT_TIME: str = "23:59"  # HH:MM format
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
    OVERDUE_DIGEST_TIME: str = "08:40"  # HH:MM format
    OVERDUE_DIGEST_TIMEZONE: str = "Asia/Karachi"
    OVERDUE_DIGEST_CHANNEL: Optional[str] = None  # None falls back to PM_DISCORD_CHANNEL

    # Daily PM Attention Digest Settings
    PM_ATTENTION_DIGEST_ENABLED: bool = False
    PM_ATTENTION_DIGEST_TIME: str = "08:40"  # HH:MM format
    PM_ATTENTION_DIGEST_TIMEZONE: str = "Asia/Karachi"
    PM_ATTENTION_DIGEST_CHANNEL: Optional[str] = None  # None falls back to PM_DISCORD_CHANNEL

    # Mubashir Automation Report Settings
    MUBASHIR_AUTOMATION_REPORT_ENABLED: bool = False
    MUBASHIR_AUTOMATION_REPORT_TIME: str = "08:40"
    MUBASHIR_AUTOMATION_REPORT_TIMEZONE: str = "Asia/Karachi"
    MUBASHIR_AUTOMATION_REPORT_CHANNEL: Optional[str] = None

    # Daily Activity Report Settings
    DAILY_ACTIVITY_REPORT_ENABLED: bool = False
    DAILY_ACTIVITY_REPORT_TIME: str = "08:40"  # HH:MM format
    DAILY_ACTIVITY_REPORT_TIMEZONE: str = "Asia/Karachi"
    DAILY_ACTIVITY_REPORT_CHANNEL: Optional[str] = None

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

    # Deterministic Planning Horizon (Phase 3)
    PLANNING_HORIZON_WORKING_DAYS: int = 10  # Standard 2-week planning horizon (Mon-Fri)

    # Live QA Framework Settings
    LIVE_QA_ENABLED: bool = False
    LIVE_QA_JIRA_ISSUE: str = "TREN-378"
    LIVE_QA_DISCORD_WEBHOOK: Optional[str] = None
    LIVE_QA_MATTERMOST_CHANNEL: Optional[str] = None
    LIVE_QA_STALE_HOURS: int = 1
    LIVE_QA_EVIDENCE_DIR: str = "qa/evidence"

    # Scheduler Settings
    SCHEDULER_ENABLED: bool = True
    SCHEDULER_INTERVAL_MINUTES: int = 15

    # HTTP Client / Network
    REQUEST_TIMEOUT_SECONDS: float = 10.0
    MAX_RETRIES: int = 3
    RETRY_BACKOFF_FACTOR: float = 1.5

    def get_database_path(self) -> str:
        """Ensure parent directory exists and return absolute database path."""
        effective_path = self.DATABASE_PATH or self.DB_PATH
        db_dir = os.path.dirname(os.path.abspath(effective_path))
        os.makedirs(db_dir, exist_ok=True)
        return os.path.abspath(effective_path)

    def is_production(self) -> bool:
        """Check if running in production environment."""
        return str(self.APP_ENV).strip().lower() == "production"

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

    def is_discord_notifications_configured(self) -> bool:
        """Check if dedicated Discord notifications webhook URL is meaningfully configured."""
        return bool(
            self.DISCORD_NOTIFICATIONS_WEBHOOK_URL
            and not self.DISCORD_NOTIFICATIONS_WEBHOOK_URL.endswith("placeholder")
            and not self.DISCORD_NOTIFICATIONS_WEBHOOK_URL.endswith("placeholder_notifications")
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
        
        - If discord_user_id is None or empty: returns False.
        - If DISCORD_PM_ALLOWED_USERS == "*": returns True.
        - If DISCORD_PM_ALLOWED_USERS is empty:
            * In production (APP_ENV=production): DENY ALL (False).
            * In development/local (APP_ENV != production): ALLOW ALL (True).
        - If DISCORD_PM_ALLOWED_USERS is configured: strictly checks membership.
        """
        if not discord_user_id or not str(discord_user_id).strip():
            return False
        raw_allowed = (self.DISCORD_PM_ALLOWED_USERS or "").strip()
        if raw_allowed == "*":
            return True
        allowed = self.get_discord_pm_allowed_users()
        if not allowed:
            if self.is_production():
                return False
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

    def is_gdrive_configured(self) -> bool:
        """Check if Google Drive backup integration is configured and enabled."""
        return bool(
            self.GDRIVE_ENABLED
            and self.GDRIVE_FOLDER_ID
            and self.GDRIVE_FOLDER_ID.strip()
            and self.GDRIVE_SERVICE_ACCOUNT_FILE
            and os.path.isfile(self.GDRIVE_SERVICE_ACCOUNT_FILE)
        )

    def is_backup_encryption_configured(self) -> bool:
        """Check if backup encryption is enabled and key is supplied."""
        return bool(
            self.BACKUP_ENCRYPTION_ENABLED
            and self.BACKUP_ENCRYPTION_KEY
            and self.BACKUP_ENCRYPTION_KEY.strip()
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

    @property
    def CANONICAL_EXCLUDED_ACCOUNT_IDS(self) -> Set[str]:
        """Property returning canonical excluded account IDs."""
        return self.get_canonical_excluded_account_ids()

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

    def get_live_qa_evidence_dir(self) -> str:
        """Ensure parent evidence directory exists and return absolute path."""
        evidence_dir = os.path.abspath(self.LIVE_QA_EVIDENCE_DIR)
        os.makedirs(evidence_dir, exist_ok=True)
        return evidence_dir

    def assert_live_qa_safe(self, target_issue: Optional[str] = None) -> None:
        """Assert that Live QA environment is explicitly enabled and target issue matches the safe fixture."""
        if not self.LIVE_QA_ENABLED:
            raise RuntimeError(
                "Live QA mutation blocked: LIVE_QA_ENABLED is False. "
                "Set LIVE_QA_ENABLED=true in environment to execute live operations."
            )
        if target_issue:
            clean_target = target_issue.strip().upper()
            clean_expected = (self.LIVE_QA_JIRA_ISSUE or "TREN-378").strip().upper()
            if clean_target != clean_expected:
                raise ValueError(
                    f"Live QA mutation blocked: target issue '{target_issue}' does not match "
                    f"configured safe fixture '{self.LIVE_QA_JIRA_ISSUE}'. "
                    f"Live mutation tests are restricted to '{self.LIVE_QA_JIRA_ISSUE}'."
                )

    def get_report_timezone(self) -> str:
        """Get centralized report timezone."""
        return self.REPORT_TIMEZONE or "Asia/Karachi"

    def get_default_report_time(self) -> str:
        """Get default scheduled dispatch time for standard reports (08:40 AM PKT)."""
        return self.REPORT_DEFAULT_TIME or "08:40"

    def get_worklog_report_time(self) -> str:
        """Get scheduled dispatch time for Daily Worklog Report (11:59 PM PKT)."""
        return self.WORKLOG_REPORT_TIME or "23:59"

    def get_active_queue_jql(
        self,
        assignee_account_id: Optional[str] = None,
        assignee_display_name: Optional[str] = None,
    ) -> str:
        """Derive the canonical Jira Active Queue JQL query, optionally parameterized for a resource.
        
        Preserves existing Jira Active Queue filter source of truth:
        1. If JIRA_ACTIVE_QUEUE_FILTER_ID is set, uses saved filter constraint: filter = <id>
        2. If JIRA_ACTIVE_QUEUE_JQL is set, uses explicit filter JQL: (<jql>)
        3. Default canonical active queue filter: statusCategory != Done AND assignee in membersOf("<team_group>")
        """
        team_group = (self.JIRA_TEAM_GROUP or "").strip()
        if self.JIRA_ACTIVE_QUEUE_FILTER_ID:
            base_jql = f"filter = {self.JIRA_ACTIVE_QUEUE_FILTER_ID}"
        elif self.JIRA_ACTIVE_QUEUE_JQL:
            base_jql = f"({self.JIRA_ACTIVE_QUEUE_JQL})"
        elif team_group:
            base_jql = f'statusCategory != Done AND assignee in membersOf("{team_group}")'
        else:
            base_jql = "statusCategory != Done"

        if assignee_account_id or assignee_display_name:
            user_clauses = []
            if assignee_account_id:
                user_clauses.append(f'assignee = "{assignee_account_id}"')
            if assignee_display_name and assignee_display_name != assignee_account_id:
                user_clauses.append(f'assignee = "{assignee_display_name}"')
            user_condition = " OR ".join(user_clauses)
            return f"({base_jql}) AND ({user_condition}) ORDER BY duedate ASC, updated DESC"

        return f"{base_jql} ORDER BY duedate ASC, updated DESC"


# Global settings instance
settings = Settings()
