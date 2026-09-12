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
    status TEXT NOT NULL, -- REQUESTED, VALIDATED, PENDING_APPROVAL, APPROVED, REJECTED, EXECUTING, COMPLETED, FAILED, DRY_RUN_SIMULATED, ACTION_UNSUPPORTED, USER_MAPPING_REQUIRED
    attempt_count INTEGER NOT NULL DEFAULT 0,
    dry_run INTEGER NOT NULL DEFAULT 0,
    preview TEXT, -- JSON string
    last_error TEXT,
    requested_by TEXT,
    requires_approval INTEGER NOT NULL DEFAULT 0,
    approved_by TEXT,
    approved_at TEXT,
    rejected_by TEXT,
    rejected_at TEXT,
    rejection_reason TEXT,
    result_data TEXT, -- JSON string
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

-- Performance Analysis Runs (Immutable analysis metadata for auditing)
CREATE TABLE IF NOT EXISTS performance_analysis_runs (
    analysis_run_id TEXT PRIMARY KEY,
    calculated_at TEXT NOT NULL,
    analysis_window_start TEXT NOT NULL,
    analysis_window_end TEXT NOT NULL,
    requested_history_days INTEGER NOT NULL DEFAULT 365,
    actual_available_history_days INTEGER NOT NULL DEFAULT 0,
    algorithm_version TEXT NOT NULL DEFAULT '1.0.0',
    team_group TEXT,
    resources_analyzed INTEGER NOT NULL DEFAULT 0,
    tasks_analyzed INTEGER NOT NULL DEFAULT 0,
    unresolved_employees_count INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'COMPLETED',
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_perf_runs_date ON performance_analysis_runs(calculated_at);
CREATE INDEX IF NOT EXISTS idx_perf_runs_team ON performance_analysis_runs(team_group);

-- Authoritative Employee Role & Designation Assignments
CREATE TABLE IF NOT EXISTS employee_role_assignments (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    designation TEXT NOT NULL,
    role_category TEXT NOT NULL,
    effective_from TEXT,
    effective_to TEXT,
    source TEXT NOT NULL DEFAULT 'authoritative_seed',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_emp_role_acc ON employee_role_assignments(account_id);
CREATE INDEX IF NOT EXISTS idx_emp_role_cat ON employee_role_assignments(role_category);

-- Resource Performance Profiles (Deterministic summary per resource & analysis run)
CREATE TABLE IF NOT EXISTS resource_performance_profiles (
    id TEXT PRIMARY KEY,
    analysis_run_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'Unknown',
    designation TEXT,
    role_category TEXT,
    team_group TEXT,
    analysis_start TEXT NOT NULL,
    analysis_end TEXT NOT NULL,
    history_days INTEGER NOT NULL,
    requested_history_days INTEGER NOT NULL DEFAULT 365,
    actual_available_history_days INTEGER NOT NULL DEFAULT 0,
    completed_tasks INTEGER NOT NULL DEFAULT 0,
    active_working_days INTEGER NOT NULL DEFAULT 0,
    total_logged_seconds INTEGER NOT NULL DEFAULT 0,
    average_logged_hours_per_active_day REAL NOT NULL DEFAULT 0.0,
    median_logged_hours_per_active_day REAL NOT NULL DEFAULT 0.0,
    tasks_due INTEGER NOT NULL DEFAULT 0,
    tasks_completed_on_time INTEGER NOT NULL DEFAULT 0,
    tasks_completed_late INTEGER NOT NULL DEFAULT 0,
    on_time_rate REAL NOT NULL DEFAULT 0.0,
    average_days_late REAL NOT NULL DEFAULT 0.0,
    median_days_late REAL NOT NULL DEFAULT 0.0,
    average_task_hours REAL NOT NULL DEFAULT 0.0,
    median_task_hours REAL NOT NULL DEFAULT 0.0,
    p25_task_hours REAL NOT NULL DEFAULT 0.0,
    p75_task_hours REAL NOT NULL DEFAULT 0.0,
    estimated_tasks INTEGER NOT NULL DEFAULT 0,
    average_estimated_hours REAL NOT NULL DEFAULT 0.0,
    average_actual_hours REAL NOT NULL DEFAULT 0.0,
    estimation_variance_percent REAL NOT NULL DEFAULT 0.0,
    median_estimation_variance_percent REAL NOT NULL DEFAULT 0.0,
    reopened_tasks INTEGER NOT NULL DEFAULT 0,
    reopen_rate REAL NOT NULL DEFAULT 0.0,
    blocker_count INTEGER NOT NULL DEFAULT 0,
    blocked_seconds INTEGER NOT NULL DEFAULT 0,
    average_blocker_hours REAL NOT NULL DEFAULT 0.0,
    nominal_daily_capacity_hours REAL NOT NULL DEFAULT 6.75,
    observed_daily_capacity_hours REAL NOT NULL DEFAULT 6.75,
    forecast_daily_capacity_hours REAL NOT NULL DEFAULT 6.75,
    current_queue_task_count INTEGER NOT NULL DEFAULT 0,
    current_queue_expected_hours REAL NOT NULL DEFAULT 0.0,
    current_queue_review_buffer_hours REAL NOT NULL DEFAULT 0.0,
    current_queue_total_expected_hours REAL NOT NULL DEFAULT 0.0,
    available_capacity_hours REAL NOT NULL DEFAULT 0.0,
    capacity_difference_hours REAL NOT NULL DEFAULT 0.0,
    forecast_status TEXT NOT NULL DEFAULT 'GREEN',
    forecast_reason TEXT,
    projected_queue_completion_date TEXT,
    confidence_level TEXT NOT NULL DEFAULT 'LOW',
    raw_profile_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_perf_profiles_acc ON resource_performance_profiles(account_id);
CREATE INDEX IF NOT EXISTS idx_perf_profiles_run ON resource_performance_profiles(analysis_run_id);
CREATE INDEX IF NOT EXISTS idx_perf_profiles_team ON resource_performance_profiles(team_group);

-- Resource Effort Statistics (P25, median, mean, P75 segmented metrics)
CREATE TABLE IF NOT EXISTS resource_effort_statistics (
    id TEXT PRIMARY KEY,
    analysis_run_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    segment_type TEXT NOT NULL, -- overall, issue_type, complexity, priority, project
    segment_key TEXT NOT NULL,
    sample_count INTEGER NOT NULL DEFAULT 0,
    mean_hours REAL NOT NULL DEFAULT 0.0,
    median_hours REAL NOT NULL DEFAULT 0.0,
    p25_hours REAL NOT NULL DEFAULT 0.0,
    p75_hours REAL NOT NULL DEFAULT 0.0,
    min_hours REAL NOT NULL DEFAULT 0.0,
    max_hours REAL NOT NULL DEFAULT 0.0,
    confidence TEXT NOT NULL DEFAULT 'LOW',
    is_fallback INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_perf_effort_acc ON resource_effort_statistics(account_id, segment_type, segment_key);
CREATE INDEX IF NOT EXISTS idx_perf_effort_run ON resource_effort_statistics(analysis_run_id);

-- Resource Task Classifications (Task complexity and categorization)
CREATE TABLE IF NOT EXISTS resource_task_classifications (
    id TEXT PRIMARY KEY,
    analysis_run_id TEXT NOT NULL,
    issue_key TEXT NOT NULL,
    issue_type TEXT,
    priority TEXT,
    project_key TEXT,
    components TEXT,
    labels TEXT,
    complexity_score INTEGER NOT NULL DEFAULT 3,
    complexity_factors TEXT,
    complexity_confidence TEXT NOT NULL DEFAULT 'MEDIUM',
    estimated_seconds INTEGER,
    actual_logged_seconds INTEGER NOT NULL DEFAULT 0,
    status TEXT,
    is_completed INTEGER NOT NULL DEFAULT 0,
    reopen_count INTEGER NOT NULL DEFAULT 0,
    blocker_detected INTEGER NOT NULL DEFAULT 0,
    blocker_hours REAL NOT NULL DEFAULT 0.0,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_perf_class_key ON resource_task_classifications(issue_key);
CREATE INDEX IF NOT EXISTS idx_perf_class_run ON resource_task_classifications(analysis_run_id);

-- Task Delivery Forecasts (Remaining effort, projected completion, risk band)
CREATE TABLE IF NOT EXISTS task_delivery_forecasts (
    id TEXT PRIMARY KEY,
    analysis_run_id TEXT NOT NULL,
    issue_key TEXT NOT NULL,
    account_id TEXT,
    due_date TEXT,
    jira_remaining_hours REAL,
    inferred_expected_hours REAL NOT NULL DEFAULT 0.0,
    inferred_remaining_hours REAL NOT NULL DEFAULT 0.0,
    expected_base_hours REAL NOT NULL DEFAULT 0.0,
    review_buffer_hours REAL NOT NULL DEFAULT 0.0,
    total_expected_hours REAL NOT NULL DEFAULT 0.0,
    logged_hours REAL NOT NULL DEFAULT 0.0,
    remaining_hours REAL NOT NULL DEFAULT 0.0,
    expected_effort_source TEXT NOT NULL,
    expected_effort_confidence TEXT NOT NULL DEFAULT 'medium',
    sample_size INTEGER NOT NULL DEFAULT 0,
    designation TEXT,
    role_category TEXT,
    projected_completion_date TEXT,
    slack_hours REAL NOT NULL DEFAULT 0.0,
    risk_level TEXT NOT NULL DEFAULT 'GREEN', -- GREEN, YELLOW, ORANGE, RED
    risk_reason TEXT,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_perf_fcst_key ON task_delivery_forecasts(issue_key);
CREATE INDEX IF NOT EXISTS idx_perf_fcst_acc ON task_delivery_forecasts(account_id);
CREATE INDEX IF NOT EXISTS idx_perf_fcst_risk ON task_delivery_forecasts(risk_level);
CREATE INDEX IF NOT EXISTS idx_perf_fcst_run ON task_delivery_forecasts(analysis_run_id);

-- Performance Signals (Deterministic, traceable signals)
CREATE TABLE IF NOT EXISTS performance_signals (
    id TEXT PRIMARY KEY,
    analysis_run_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    signal_value REAL,
    threshold_value REAL,
    evidence_text TEXT NOT NULL,
    confidence TEXT NOT NULL DEFAULT 'MEDIUM',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_perf_signals_acc ON performance_signals(account_id);
CREATE INDEX IF NOT EXISTS idx_perf_signals_type ON performance_signals(signal_type);
CREATE INDEX IF NOT EXISTS idx_perf_signals_run ON performance_signals(analysis_run_id);

-- Performance Evidence (Immutable, auditable evidence ledger)
CREATE TABLE IF NOT EXISTS performance_evidence (
    evidence_id TEXT PRIMARY KEY,
    analysis_run_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    issue_key TEXT,
    evidence_type TEXT NOT NULL,
    observed_value REAL,
    expected_value REAL,
    difference REAL,
    source TEXT NOT NULL,
    confidence TEXT NOT NULL DEFAULT 'MEDIUM',
    explanation TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    snapshot_date TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_perf_evidence_acc ON performance_evidence(account_id);
CREATE INDEX IF NOT EXISTS idx_perf_evidence_type ON performance_evidence(evidence_type);
CREATE INDEX IF NOT EXISTS idx_perf_evidence_run ON performance_evidence(analysis_run_id);
CREATE INDEX IF NOT EXISTS idx_perf_evidence_issue ON performance_evidence(issue_key);

-- Performance Validation Reports (Data Quality & Analytics Validation)
CREATE TABLE IF NOT EXISTS performance_validation_reports (
    id TEXT PRIMARY KEY,
    analysis_run_id TEXT NOT NULL,
    team_group TEXT,
    recommendation TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    raw_report_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_perf_val_run ON performance_validation_reports(analysis_run_id);
CREATE INDEX IF NOT EXISTS idx_perf_val_team ON performance_validation_reports(team_group);
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


def _migrate_actions(conn) -> None:
    """Idempotently ensure actions schema contains all expected columns."""
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='actions'"
    )
    if not cursor.fetchone():
        return

    cursor = conn.execute("PRAGMA table_info(actions)")
    rows = cursor.fetchall()
    existing_columns = {
        row["name"] if hasattr(row, "keys") and "name" in row.keys() else row[1]
        for row in rows
    }

    expected_columns = {
        "requested_by": "TEXT",
        "requires_approval": "INTEGER NOT NULL DEFAULT 0",
        "approved_by": "TEXT",
        "approved_at": "TEXT",
        "rejected_by": "TEXT",
        "rejected_at": "TEXT",
        "rejection_reason": "TEXT",
        "result_data": "TEXT",
    }
    for col_name, col_type in expected_columns.items():
        if col_name not in existing_columns:
            logger.info(f"Migrating database: adding '{col_name}' column to actions table...")
            conn.execute(f"ALTER TABLE actions ADD COLUMN {col_name} {col_type}")


def _migrate_performance_tables(conn) -> None:
    """Idempotently ensure performance tables contain all required columns."""
    # 1. resource_performance_profiles
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='resource_performance_profiles'"
    )
    if cursor.fetchone():
        cursor = conn.execute("PRAGMA table_info(resource_performance_profiles)")
        rows = cursor.fetchall()
        existing = {
            row["name"] if hasattr(row, "keys") and "name" in row.keys() else row[1]
            for row in rows
        }
        profile_expected = {
            "forecast_daily_capacity_hours": "REAL NOT NULL DEFAULT 6.75",
            "analysis_run_id": "TEXT NOT NULL DEFAULT ''",
            "designation": "TEXT",
            "role_category": "TEXT",
            "requested_history_days": "INTEGER NOT NULL DEFAULT 365",
            "actual_available_history_days": "INTEGER NOT NULL DEFAULT 0",
            "raw_profile_json": "TEXT",
        }
        for col_name, col_type in profile_expected.items():
            if col_name not in existing:
                logger.info(f"Migrating database: adding '{col_name}' to resource_performance_profiles...")
                conn.execute(f"ALTER TABLE resource_performance_profiles ADD COLUMN {col_name} {col_type}")

    # 2. task_delivery_forecasts
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='task_delivery_forecasts'"
    )
    if cursor.fetchone():
        cursor = conn.execute("PRAGMA table_info(task_delivery_forecasts)")
        rows = cursor.fetchall()
        existing = {
            row["name"] if hasattr(row, "keys") and "name" in row.keys() else row[1]
            for row in rows
        }
        fcst_expected = {
            "jira_remaining_hours": "REAL",
            "inferred_expected_hours": "REAL NOT NULL DEFAULT 0.0",
            "inferred_remaining_hours": "REAL NOT NULL DEFAULT 0.0",
            "expected_effort_confidence": "TEXT NOT NULL DEFAULT 'medium'",
            "sample_size": "INTEGER NOT NULL DEFAULT 0",
            "designation": "TEXT",
            "role_category": "TEXT",
        }
        for col_name, col_type in fcst_expected.items():
            if col_name not in existing:
                logger.info(f"Migrating database: adding '{col_name}' to task_delivery_forecasts...")
                conn.execute(f"ALTER TABLE task_delivery_forecasts ADD COLUMN {col_name} {col_type}")

    # 3. performance_analysis_runs
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='performance_analysis_runs'"
    )
    if cursor.fetchone():
        cursor = conn.execute("PRAGMA table_info(performance_analysis_runs)")
        rows = cursor.fetchall()
        existing = {
            row["name"] if hasattr(row, "keys") and "name" in row.keys() else row[1]
            for row in rows
        }
        run_expected = {
            "requested_history_days": "INTEGER NOT NULL DEFAULT 365",
            "actual_available_history_days": "INTEGER NOT NULL DEFAULT 0",
            "unresolved_employees_count": "INTEGER NOT NULL DEFAULT 0",
        }
        for col_name, col_type in run_expected.items():
            if col_name not in existing:
                logger.info(f"Migrating database: adding '{col_name}' to performance_analysis_runs...")
                conn.execute(f"ALTER TABLE performance_analysis_runs ADD COLUMN {col_name} {col_type}")

    # 4. performance_validation_reports
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS performance_validation_reports (
            id TEXT PRIMARY KEY,
            analysis_run_id TEXT NOT NULL,
            team_group TEXT,
            recommendation TEXT NOT NULL,
            summary_json TEXT NOT NULL,
            raw_report_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_perf_val_run ON performance_validation_reports(analysis_run_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_perf_val_team ON performance_validation_reports(team_group)")


# Authoritative 18 employee designations seeded by exact account_id and exact designation
AUTHORITATIVE_EMPLOYEE_ROLES = [
    {
        "account_id": "712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de",
        "display_name": "Ahsan Amin",
        "designation": "Senior WordPress Developer",
        "role_category": "WordPress Development",
    },
    {
        "account_id": "63da2ba4f1475ad42c584247",
        "display_name": "Ahsan Iftikhar",
        "designation": "Senior BA",
        "role_category": "Business Analysis",
    },
    {
        "account_id": "712020:0eca0fb9-4f12-4532-a435-4c178f2d90e8",
        "display_name": "Nauman Sadiq",
        "designation": "Senior BA",
        "role_category": "Business Analysis",
    },
    {
        "account_id": "712020:a6d04898-c6d8-4a39-a521-103e4b8bfe7c",
        "display_name": "Muhammad Shahmeer Khan",
        "designation": "Junior BA",
        "role_category": "Business Analysis",
    },
    {
        "account_id": "712020:c12d2371-1e5b-4797-a888-369c0c9c5a65",
        "display_name": "Muhammad Sufiyan",
        "designation": "Senior QA Engineer",
        "role_category": "QA",
    },
    {
        "account_id": "712020:32e5be05-80c9-4ece-ac19-301da7c9487d",
        "display_name": "shoaib hassan askari",
        "designation": "Senior QA",
        "role_category": "QA",
    },
    {
        "account_id": "712020:12e1da4b-147f-4f91-9d2d-965b66e19b61",
        "display_name": "Muhammad Bilal Khan",
        "designation": "Mid-level QA",
        "role_category": "QA",
    },
    {
        "account_id": "63e362bd790148a180977179",
        "display_name": "Daniyal Raza",
        "designation": "Mid-level WordPress Developer",
        "role_category": "WordPress Development",
    },
    {
        "account_id": "5fb3d908facfd6007697c25a",
        "display_name": "Muhammad Hamza",
        "designation": "Mid-level WordPress Developer",
        "role_category": "WordPress Development",
    },
    {
        "account_id": "606570150a6b3f00698f9430",
        "display_name": "Muneeb Jalal",
        "designation": "Senior WordPress Developer",
        "role_category": "WordPress Development",
    },
    {
        "account_id": "61ee41431c42100069344a09",
        "display_name": "Syed ali",
        "designation": "Senior WordPress Developer",
        "role_category": "WordPress Development",
    },
    {
        "account_id": "638855b85fce844d606bb422",
        "display_name": "Tahir Ali",
        "designation": "Senior Content Writer / Marketing Strategist",
        "role_category": "Content / Marketing",
    },
    {
        "account_id": "638490c75fce844d606a16ef",
        "display_name": "Hamza Hanif",
        "designation": "SEO",
        "role_category": "SEO",
    },
    {
        "account_id": "712020:e268bcd8-d981-4b4d-992d-d5694745df8b",
        "display_name": "Mubashir Butt",
        "designation": "Customer Support Engineer",
        "role_category": "Customer Support",
    },
    {
        "account_id": "712020:fb8608cb-6393-48a7-a3ab-1ad744a2b7f6",
        "display_name": "Muhammad Usama Azad",
        "designation": "Front End Developer",
        "role_category": "Frontend Development",
    },
    {
        "account_id": "712020:2783ea21-c611-402d-9adb-0529f5b7066d",
        "display_name": "Muhammad Ali Siddiqui",
        "designation": "Junior Content Writer",
        "role_category": "Content",
    },
    {
        "account_id": "712020:bb2e5830-7156-4852-bba8-75fa773fc55d",
        "display_name": "Talha Bukhari",
        "designation": "Content Producer",
        "role_category": "Content",
    },
    {
        "account_id": "712020:1ddac8e3-e006-48e7-b4c9-ee941efc8e6e",
        "display_name": "Azain Hassan",
        "designation": "Designer",
        "role_category": "Design",
    },
]


def _seed_authoritative_roles(conn) -> None:
    """Idempotently seed the authoritative employee designation and role mappings."""
    now_iso = "2026-09-12T00:00:00Z"
    for r in AUTHORITATIVE_EMPLOYEE_ROLES:
        rec_id = f"role:{r['account_id']}"
        conn.execute(
            """
            INSERT INTO employee_role_assignments (
                id, account_id, display_name, designation, role_category,
                effective_from, effective_to, source, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, NULL, 'authoritative_seed', ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                display_name = excluded.display_name,
                designation = excluded.designation,
                role_category = excluded.role_category,
                updated_at = excluded.updated_at
            """,
            (
                rec_id,
                r["account_id"],
                r["display_name"],
                r["designation"],
                r["role_category"],
                "2026-01-01",
                now_iso,
                now_iso,
            ),
        )


def _apply_migrations(conn) -> None:
    """Execute all registered schema migrations safely and idempotently."""
    _migrate_jira_issue_state(conn)
    _migrate_jira_worklogs(conn)
    _migrate_actions(conn)
    _migrate_performance_tables(conn)


def _ensure_post_migration_indexes(conn) -> None:
    """Create indexes that depend on migrated columns safely after columns exist."""
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_jira_issue_state_team_group ON jira_issue_state(team_group)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_jira_worklogs_team ON jira_worklogs(team_group)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_perf_profiles_run ON resource_performance_profiles(analysis_run_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_emp_role_acc ON employee_role_assignments(account_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_emp_role_cat ON employee_role_assignments(role_category)"
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

        # 5. Seed authoritative employee role assignments
        _seed_authoritative_roles(conn)
    logger.info("Database schema initialized successfully.")

