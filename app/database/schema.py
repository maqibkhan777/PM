"""SQLite database schema and table initialization."""

from typing import Optional
from app.database.connection import db_manager, DatabaseManager
from app.utils.logger import logger

SCHEMA_SQL = """
-- Events table
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    source TEXT NOT NULL,
    external_event_id TEXT,
    timestamp TEXT NOT NULL,
    actor_id TEXT,
    actor_name TEXT,
    project_id TEXT,
    task_id TEXT,
    payload TEXT NOT NULL, -- JSON string
    processing_status TEXT NOT NULL DEFAULT 'RECEIVED', -- RECEIVED, PROCESSING, PROCESSED, FAILED, RETRY_PENDING
    processing_attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    processed_at TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_task_id ON events(task_id);
CREATE INDEX IF NOT EXISTS idx_events_processing_status ON events(processing_status);
CREATE INDEX IF NOT EXISTS idx_events_external_id ON events(source, external_event_id);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);

-- Users table
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    external_system TEXT NOT NULL,
    external_user_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    email TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    UNIQUE(external_system, external_user_id)
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_system_id ON users(external_system, external_user_id);

-- Jira <-> Mattermost User Mappings table
CREATE TABLE IF NOT EXISTS user_mappings (
    id TEXT PRIMARY KEY,
    jira_user_id TEXT NOT NULL UNIQUE,
    mattermost_user_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_user_mappings_jira ON user_mappings(jira_user_id);
CREATE INDEX IF NOT EXISTS idx_user_mappings_mm ON user_mappings(mattermost_user_id);

-- Configurable Rules table
CREATE TABLE IF NOT EXISTS rules (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    configuration TEXT NOT NULL, -- JSON string
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Actions table
CREATE TABLE IF NOT EXISTS actions (
    id TEXT PRIMARY KEY,
    action_id TEXT NOT NULL,
    idempotency_key TEXT UNIQUE,
    action_type TEXT NOT NULL,
    target_system TEXT NOT NULL,
    target_id TEXT NOT NULL,
    parameters TEXT NOT NULL, -- JSON string
    status TEXT NOT NULL, -- PENDING_APPROVAL, APPROVED, REJECTED, EXECUTING, COMPLETED, FAILED, DRY_RUN_SIMULATED, ACTION_UNSUPPORTED, USER_MAPPING_REQUIRED
    attempt_count INTEGER NOT NULL DEFAULT 0,
    dry_run INTEGER NOT NULL DEFAULT 0,
    preview TEXT, -- JSON string
    last_error TEXT,
    created_at TEXT NOT NULL,
    executed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_actions_status ON actions(status);
CREATE INDEX IF NOT EXISTS idx_actions_idempotency ON actions(idempotency_key);
CREATE INDEX IF NOT EXISTS idx_actions_created ON actions(created_at);

-- Notifications deduplication table
CREATE TABLE IF NOT EXISTS notifications (
    id TEXT PRIMARY KEY,
    rule_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    condition TEXT NOT NULL,
    channel TEXT NOT NULL,
    last_notified_at TEXT NOT NULL,
    notification_count INTEGER NOT NULL DEFAULT 1,
    UNIQUE(rule_id, target_id, condition)
);

CREATE INDEX IF NOT EXISTS idx_notifications_lookup ON notifications(rule_id, target_id, condition);

-- Audit Logs table
CREATE TABLE IF NOT EXISTS audit_logs (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT NOT NULL,
    result TEXT NOT NULL,
    details TEXT -- JSON string
);

CREATE INDEX IF NOT EXISTS idx_audit_logs_timestamp ON audit_logs(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_logs_action ON audit_logs(action);

-- Persistent Jira Polling Checkpoint table
CREATE TABLE IF NOT EXISTS jira_polling_state (
    id TEXT PRIMARY KEY,
    connector TEXT NOT NULL UNIQUE,
    last_successful_poll TEXT,
    updated_at TEXT NOT NULL
);

-- Jira Issue State Projection (Local cache for change detection and stale/overdue monitoring)
CREATE TABLE IF NOT EXISTS jira_issue_state (
    jira_issue_key TEXT PRIMARY KEY,
    summary TEXT,
    status TEXT NOT NULL,
    assignee TEXT,
    priority TEXT,
    due_date TEXT,
    updated_at TEXT,
    last_seen_at TEXT NOT NULL,
    last_activity_at TEXT NOT NULL,
    project_key TEXT,
    raw_reference TEXT, -- JSON string
    team_group TEXT
);

CREATE INDEX IF NOT EXISTS idx_jira_issue_state_status ON jira_issue_state(status);
CREATE INDEX IF NOT EXISTS idx_jira_issue_state_due_date ON jira_issue_state(due_date);
CREATE INDEX IF NOT EXISTS idx_jira_issue_state_last_activity ON jira_issue_state(last_activity_at);

-- Jira Worklogs (Local normalized worklog data for team reporting)
CREATE TABLE IF NOT EXISTS jira_worklogs (
    worklog_id TEXT PRIMARY KEY,
    jira_issue_key TEXT NOT NULL,
    jira_issue_id TEXT,
    author_account_id TEXT,
    author_display_name TEXT,
    time_spent_seconds INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    created_at TEXT,
    updated_at TEXT,
    comment TEXT,
    team_group TEXT,
    source TEXT DEFAULT 'jira'
);

CREATE INDEX IF NOT EXISTS idx_jira_worklogs_issue ON jira_worklogs(jira_issue_key);
CREATE INDEX IF NOT EXISTS idx_jira_worklogs_started ON jira_worklogs(started_at);
CREATE INDEX IF NOT EXISTS idx_jira_worklogs_author ON jira_worklogs(author_account_id);
CREATE INDEX IF NOT EXISTS idx_jira_worklogs_team ON jira_worklogs(team_group);

-- Daily Report History (Records generated/sent reports for idempotency)
CREATE TABLE IF NOT EXISTS daily_report_history (
    id TEXT PRIMARY KEY,
    team_group TEXT NOT NULL,
    report_date TEXT NOT NULL,
    report_type TEXT NOT NULL DEFAULT 'daily_worklog',
    generated_at TEXT NOT NULL,
    sent_to_discord INTEGER DEFAULT 0,
    report_payload TEXT
);

CREATE INDEX IF NOT EXISTS idx_daily_report_history_date ON daily_report_history(team_group, report_date);
"""


def _migrate_jira_issue_state(conn) -> None:
    """Idempotently ensure jira_issue_state schema contains all expected columns."""
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='jira_issue_state'"
    )
    if not cursor.fetchone():
        return

    cursor = conn.execute("PRAGMA table_info(jira_issue_state)")
    rows = cursor.fetchall()
    existing_columns = {
        row["name"] if hasattr(row, "keys") and "name" in row.keys() else row[1]
        for row in rows
    }

    if "team_group" not in existing_columns:
        logger.info("Migrating database: adding 'team_group' column to jira_issue_state table...")
        conn.execute("ALTER TABLE jira_issue_state ADD COLUMN team_group TEXT")
        logger.info("Database migration complete: 'team_group' column added successfully.")


def _migrate_jira_worklogs(conn) -> None:
    """Idempotently ensure jira_worklogs schema contains all expected columns."""
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='jira_worklogs'"
    )
    if not cursor.fetchone():
        return

    cursor = conn.execute("PRAGMA table_info(jira_worklogs)")
    rows = cursor.fetchall()
    existing_columns = {
        row["name"] if hasattr(row, "keys") and "name" in row.keys() else row[1]
        for row in rows
    }

    expected_columns = {
        "worklog_id": "TEXT",
        "jira_issue_key": "TEXT",
        "jira_issue_id": "TEXT",
        "author_account_id": "TEXT",
        "author_display_name": "TEXT",
        "time_spent_seconds": "INTEGER",
        "started_at": "TEXT",
        "created_at": "TEXT",
        "updated_at": "TEXT",
        "comment": "TEXT",
        "team_group": "TEXT",
        "source": "TEXT",
    }
    for col_name, col_type in expected_columns.items():
        if col_name not in existing_columns:
            logger.info(f"Migrating database: adding '{col_name}' column to jira_worklogs table...")
            conn.execute(f"ALTER TABLE jira_worklogs ADD COLUMN {col_name} {col_type}")


def _apply_migrations(conn) -> None:
    """Execute all registered schema migrations safely and idempotently."""
    _migrate_jira_issue_state(conn)
    _migrate_jira_worklogs(conn)


def _ensure_post_migration_indexes(conn) -> None:
    """Create indexes that depend on migrated columns safely after columns exist."""
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_jira_issue_state_team_group ON jira_issue_state(team_group)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_jira_worklogs_team ON jira_worklogs(team_group)"
    )



def init_db(manager: Optional[DatabaseManager] = None) -> None:
    """Initialize database tables, migrations, and indexes."""
    mgr = manager or db_manager
    logger.info(f"Initializing SQLite database schema at: {mgr.db_path}")
    with mgr.session() as conn:
        # 1. Run migrations first so pre-existing tables are upgraded before any schema execution
        _apply_migrations(conn)

        # 2. Execute table creations and baseline indexes
        conn.executescript(SCHEMA_SQL)

        # 3. Safeguard: re-check migrations for any freshly created or altered tables
        _apply_migrations(conn)

        # 4. Create post-migration indexes now that all columns are guaranteed to exist
        _ensure_post_migration_indexes(conn)
    logger.info("Database schema initialized successfully.")
