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
"""


def init_db(manager: Optional[DatabaseManager] = None) -> None:
    """Initialize database tables and indexes."""
    mgr = manager or db_manager
    logger.info(f"Initializing SQLite database schema at: {mgr.db_path}")
    with mgr.session() as conn:
        conn.executescript(SCHEMA_SQL)
    logger.info("Database schema initialized successfully.")
