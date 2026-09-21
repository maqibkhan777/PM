"""SQLite database schema and table initialization."""

from typing import Any, Dict, List, Optional
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
    team_group TEXT,
    issue_type TEXT,
    labels TEXT, -- JSON list of label strings
    components TEXT, -- JSON list of component names
    subtask_count INTEGER NOT NULL DEFAULT 0,
    original_estimate_seconds INTEGER,
    time_spent_seconds INTEGER,
    creator_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_jira_issue_state_status ON jira_issue_state(status);
CREATE INDEX IF NOT EXISTS idx_jira_issue_state_due_date ON jira_issue_state(due_date);
CREATE INDEX IF NOT EXISTS idx_jira_issue_state_last_activity ON jira_issue_state(last_activity_at);
CREATE INDEX IF NOT EXISTS idx_jira_issue_state_type ON jira_issue_state(issue_type);

-- Retention Run History table (Records execution of retention policies)
CREATE TABLE IF NOT EXISTS retention_run_history (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    policy_version TEXT NOT NULL DEFAULT '1.0.0',
    dry_run INTEGER NOT NULL DEFAULT 0,
    rows_deleted INTEGER NOT NULL DEFAULT 0,
    tables_processed INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'COMPLETED',
    error_summary TEXT,
    execution_duration_ms INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_retention_run_history_started ON retention_run_history(started_at);
CREATE INDEX IF NOT EXISTS idx_retention_run_history_status ON retention_run_history(status);

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

-- Jira Issue Links (Normalized local projection of directed Jira issue relationships)
CREATE TABLE IF NOT EXISTS jira_issue_links (
    id TEXT PRIMARY KEY,
    source_issue_key TEXT NOT NULL,
    target_issue_key TEXT NOT NULL,
    link_type_name TEXT NOT NULL,
    inward_description TEXT,
    outward_description TEXT,
    classification TEXT NOT NULL DEFAULT 'UNKNOWN',
    source_issue_id TEXT,
    target_issue_id TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_jira_issue_links_source ON jira_issue_links(source_issue_key);
CREATE INDEX IF NOT EXISTS idx_jira_issue_links_target ON jira_issue_links(target_issue_key);
CREATE INDEX IF NOT EXISTS idx_jira_issue_links_type ON jira_issue_links(link_type_name);
CREATE INDEX IF NOT EXISTS idx_jira_issue_links_class ON jira_issue_links(classification);

-- Project Artifacts (Normalized local representation of work products/deliverables)
CREATE TABLE IF NOT EXISTS project_artifacts (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    project_key TEXT NOT NULL,
    artifact_type TEXT NOT NULL DEFAULT 'GENERIC',
    status TEXT NOT NULL DEFAULT 'PLANNED',
    producer_issue_key TEXT,
    provenance TEXT NOT NULL DEFAULT 'EXPLICIT_JIRA_LABEL',
    confidence TEXT NOT NULL DEFAULT 'HIGH',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_project_artifacts_proj ON project_artifacts(project_key);
CREATE INDEX IF NOT EXISTS idx_project_artifacts_name ON project_artifacts(name);
CREATE INDEX IF NOT EXISTS idx_project_artifacts_prod ON project_artifacts(producer_issue_key);
CREATE INDEX IF NOT EXISTS idx_project_artifacts_prov ON project_artifacts(provenance);

-- Artifact Dependencies (Producer/Consumer relationships linking tasks to artifacts)
CREATE TABLE IF NOT EXISTS artifact_dependencies (
    id TEXT PRIMARY KEY,
    artifact_id TEXT NOT NULL,
    issue_key TEXT NOT NULL,
    relationship_type TEXT NOT NULL, -- 'PRODUCES' or 'CONSUMES'
    provenance TEXT NOT NULL DEFAULT 'EXPLICIT_JIRA_LABEL',
    confidence TEXT NOT NULL DEFAULT 'HIGH',
    is_inferred INTEGER NOT NULL DEFAULT 0,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_art_dep_art ON artifact_dependencies(artifact_id);
CREATE INDEX IF NOT EXISTS idx_art_dep_issue ON artifact_dependencies(issue_key);
CREATE INDEX IF NOT EXISTS idx_art_dep_rel ON artifact_dependencies(relationship_type);

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
    jira_queue_filter_id TEXT,
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

-- Phase B v1.1 Historical Intelligence & Evidence Tables

-- 1. Historical Intelligence Profiles (AI-ready comprehensive snapshot)
CREATE TABLE IF NOT EXISTS historical_intelligence_profiles (
    id TEXT PRIMARY KEY,
    analysis_run_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    designation TEXT,
    role_category TEXT,
    team_group TEXT,
    requested_history_days INTEGER NOT NULL DEFAULT 365,
    actual_available_history_days INTEGER NOT NULL DEFAULT 0,
    earliest_record_date TEXT,
    latest_record_date TEXT,
    total_logged_hours REAL NOT NULL DEFAULT 0.0,
    active_working_days INTEGER NOT NULL DEFAULT 0,
    average_logged_hours_per_active_day REAL NOT NULL DEFAULT 0.0,
    median_logged_hours_per_active_day REAL NOT NULL DEFAULT 0.0,
    nominal_daily_capacity_hours REAL NOT NULL DEFAULT 6.75,
    observed_daily_capacity_hours REAL NOT NULL DEFAULT 6.75,
    forecast_daily_capacity_hours REAL NOT NULL DEFAULT 6.75,
    current_active_tasks_count INTEGER NOT NULL DEFAULT 0,
    current_queue_inferred_remaining_hours REAL NOT NULL DEFAULT 0.0,
    capacity_difference_hours REAL NOT NULL DEFAULT 0.0,
    workload_pressure_level TEXT NOT NULL DEFAULT 'UNKNOWN',
    workload_pressure_explanation TEXT,
    data_quality_json TEXT,
    investigation_signals_json TEXT,
    profile_json TEXT NOT NULL,
    calculated_at TEXT NOT NULL,
    UNIQUE(analysis_run_id, account_id)
);

CREATE INDEX IF NOT EXISTS idx_hist_prof_acc ON historical_intelligence_profiles(account_id);
CREATE INDEX IF NOT EXISTS idx_hist_prof_run ON historical_intelligence_profiles(analysis_run_id);
CREATE INDEX IF NOT EXISTS idx_hist_prof_team ON historical_intelligence_profiles(team_group);

-- 2. Historical Task Mix
CREATE TABLE IF NOT EXISTS historical_task_mix (
    id TEXT PRIMARY KEY,
    analysis_run_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    total_tasks INTEGER NOT NULL DEFAULT 0,
    subtask_count INTEGER NOT NULL DEFAULT 0,
    primary_task_nature TEXT NOT NULL DEFAULT 'UNKNOWN',
    primary_issue_type TEXT NOT NULL DEFAULT 'Unknown',
    issue_type_distribution_json TEXT,
    task_nature_distribution_json TEXT,
    complexity_distribution_json TEXT,
    priority_distribution_json TEXT,
    project_distribution_json TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(analysis_run_id, account_id)
);

CREATE INDEX IF NOT EXISTS idx_hist_mix_acc ON historical_task_mix(account_id);
CREATE INDEX IF NOT EXISTS idx_hist_mix_run ON historical_task_mix(analysis_run_id);

-- 3. Historical Effort Benchmarks (11 segmentations)
CREATE TABLE IF NOT EXISTS historical_effort_benchmarks (
    id TEXT PRIMARY KEY,
    analysis_run_id TEXT NOT NULL,
    account_id TEXT,
    segmentation_tier TEXT NOT NULL,
    segment_type TEXT NOT NULL,
    segment_key TEXT NOT NULL,
    sample_count INTEGER NOT NULL DEFAULT 0,
    mean_hours REAL NOT NULL DEFAULT 0.0,
    median_hours REAL NOT NULL DEFAULT 0.0,
    p25_hours REAL NOT NULL DEFAULT 0.0,
    p75_hours REAL NOT NULL DEFAULT 0.0,
    min_hours REAL NOT NULL DEFAULT 0.0,
    max_hours REAL NOT NULL DEFAULT 0.0,
    stddev_hours REAL NOT NULL DEFAULT 0.0,
    confidence TEXT NOT NULL DEFAULT 'LOW',
    is_fallback INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_hist_bench_acc ON historical_effort_benchmarks(account_id, segment_type, segment_key);
CREATE INDEX IF NOT EXISTS idx_hist_bench_run ON historical_effort_benchmarks(analysis_run_id);
CREATE INDEX IF NOT EXISTS idx_hist_bench_tier ON historical_effort_benchmarks(segmentation_tier);

-- 4. Historical Trends (30d / 90d / 180d / 365d)
CREATE TABLE IF NOT EXISTS historical_trends (
    id TEXT PRIMARY KEY,
    analysis_run_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    value_30d REAL NOT NULL DEFAULT 0.0,
    value_90d REAL NOT NULL DEFAULT 0.0,
    value_180d REAL NOT NULL DEFAULT 0.0,
    value_365d REAL NOT NULL DEFAULT 0.0,
    direction TEXT NOT NULL DEFAULT 'INSUFFICIENT_DATA',
    explanation TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(analysis_run_id, account_id, metric_name)
);

CREATE INDEX IF NOT EXISTS idx_hist_trends_acc ON historical_trends(account_id);
CREATE INDEX IF NOT EXISTS idx_hist_trends_run ON historical_trends(analysis_run_id);

-- 5. Historical Workload Snapshots
CREATE TABLE IF NOT EXISTS historical_workload_snapshots (
    id TEXT PRIMARY KEY,
    analysis_run_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    pressure_level TEXT NOT NULL DEFAULT 'UNKNOWN',
    active_tasks_count INTEGER NOT NULL DEFAULT 0,
    inferred_remaining_workload_hours REAL NOT NULL DEFAULT 0.0,
    forecast_capacity_hours REAL NOT NULL DEFAULT 0.0,
    capacity_difference_hours REAL NOT NULL DEFAULT 0.0,
    tasks_due_within_7_days INTEGER NOT NULL DEFAULT 0,
    high_complexity_tasks_count INTEGER NOT NULL DEFAULT 0,
    active_blockers_count INTEGER NOT NULL DEFAULT 0,
    explanation TEXT,
    baselines_json TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(analysis_run_id, account_id)
);

CREATE INDEX IF NOT EXISTS idx_hist_workload_acc ON historical_workload_snapshots(account_id);
CREATE INDEX IF NOT EXISTS idx_hist_workload_run ON historical_workload_snapshots(analysis_run_id);

-- 6. Historical Delivery Context
CREATE TABLE IF NOT EXISTS historical_delivery_context (
    id TEXT PRIMARY KEY,
    analysis_run_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    total_completed_tasks INTEGER NOT NULL DEFAULT 0,
    completed_before_due_date INTEGER NOT NULL DEFAULT 0,
    completed_on_due_date INTEGER NOT NULL DEFAULT 0,
    completed_after_due_date INTEGER NOT NULL DEFAULT 0,
    currently_overdue INTEGER NOT NULL DEFAULT 0,
    tasks_without_due_date INTEGER NOT NULL DEFAULT 0,
    due_date_coverage_percent REAL NOT NULL DEFAULT 0.0,
    average_days_late REAL NOT NULL DEFAULT 0.0,
    correlated_blocker_count INTEGER NOT NULL DEFAULT 0,
    correlated_missing_estimates_count INTEGER NOT NULL DEFAULT 0,
    total_blocker_events INTEGER NOT NULL DEFAULT 0,
    total_blocked_hours REAL NOT NULL DEFAULT 0.0,
    reopened_tasks_count INTEGER NOT NULL DEFAULT 0,
    total_reopen_events INTEGER NOT NULL DEFAULT 0,
    rework_reasons_json TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(analysis_run_id, account_id)
);

CREATE INDEX IF NOT EXISTS idx_hist_deliv_acc ON historical_delivery_context(account_id);
CREATE INDEX IF NOT EXISTS idx_hist_deliv_run ON historical_delivery_context(analysis_run_id);

-- 7. Historical Evidence (Expanded immutable evidence ledger)
CREATE TABLE IF NOT EXISTS historical_evidence (
    id TEXT PRIMARY KEY,
    evidence_id TEXT NOT NULL UNIQUE,
    analysis_run_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    issue_key TEXT,
    evidence_type TEXT NOT NULL,
    metric TEXT NOT NULL,
    value REAL,
    comparison_baseline TEXT,
    source TEXT NOT NULL,
    confidence TEXT NOT NULL DEFAULT 'MEDIUM',
    timestamp TEXT NOT NULL,
    explanation TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_hist_ev_acc ON historical_evidence(account_id);
CREATE INDEX IF NOT EXISTS idx_hist_ev_run ON historical_evidence(analysis_run_id);
CREATE INDEX IF NOT EXISTS idx_hist_ev_type ON historical_evidence(evidence_type);
CREATE INDEX IF NOT EXISTS idx_hist_ev_key ON historical_evidence(issue_key);

-- Plugin Board Registry table
CREATE TABLE IF NOT EXISTS plugin_board_registry (
    id TEXT PRIMARY KEY,
    sr_no INTEGER,
    plugin_name TEXT NOT NULL,
    internal_board TEXT,
    internal_support_board TEXT,
    service_management_board TEXT,
    internal_project_key TEXT,
    support_project_key TEXT,
    service_management_project_key TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    source TEXT NOT NULL DEFAULT 'spreadsheet',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_plugin_board_name ON plugin_board_registry(plugin_name);
CREATE INDEX IF NOT EXISTS idx_plugin_board_sm_key ON plugin_board_registry(service_management_project_key);
CREATE INDEX IF NOT EXISTS idx_plugin_board_supp_key ON plugin_board_registry(support_project_key);
CREATE INDEX IF NOT EXISTS idx_plugin_board_int_key ON plugin_board_registry(internal_project_key);
"""


def _migrate_jira_issue_state(conn) -> None:
    """Idempotently ensure jira_issue_state schema contains all expected canonical columns and backfill."""
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

    expected_columns = {
        "team_group": "TEXT",
        "issue_type": "TEXT",
        "labels": "TEXT",
        "components": "TEXT",
        "subtask_count": "INTEGER NOT NULL DEFAULT 0",
        "original_estimate_seconds": "INTEGER",
        "time_spent_seconds": "INTEGER",
        "creator_id": "TEXT",
    }
    for col_name, col_type in expected_columns.items():
        if col_name not in existing_columns:
            logger.info(f"Migrating database: adding '{col_name}' column to jira_issue_state table...")
            conn.execute(f"ALTER TABLE jira_issue_state ADD COLUMN {col_name} {col_type}")

    # Backfill canonical columns from raw_reference JSON where canonical fields are unpopulated
    try:
        import json
        cur = conn.execute(
            """
            SELECT jira_issue_key, raw_reference, issue_type, labels, components,
                   subtask_count, original_estimate_seconds, time_spent_seconds, creator_id
            FROM jira_issue_state
            WHERE raw_reference IS NOT NULL AND raw_reference != ''
              AND (issue_type IS NULL OR labels IS NULL OR components IS NULL)
            """
        )
        backfill_rows = cur.fetchall()
        if backfill_rows:
            logger.info(f"Backfilling canonical fields for {len(backfill_rows)} jira_issue_state rows...")
            for r in backfill_rows:
                r_dict = dict(r)
                key = r_dict["jira_issue_key"]
                raw_ref_str = r_dict.get("raw_reference")
                if not raw_ref_str:
                    continue
                try:
                    raw_ref = json.loads(raw_ref_str)
                except Exception:
                    continue
                if not isinstance(raw_ref, dict):
                    continue
                fields = raw_ref.get("fields", {}) if isinstance(raw_ref.get("fields"), dict) else {}

                itype = r_dict.get("issue_type") or fields.get("issuetype", {}).get("name") or raw_ref.get("issue_type") or "Task"
                labels_raw = fields.get("labels") if fields.get("labels") is not None else raw_ref.get("labels", [])
                labels_val = json.dumps(labels_raw) if isinstance(labels_raw, list) else (str(labels_raw) if labels_raw is not None else None)

                comps_raw = fields.get("components") if fields.get("components") is not None else raw_ref.get("components", [])
                if isinstance(comps_raw, list):
                    comp_names = [c.get("name") if isinstance(c, dict) else str(c) for c in comps_raw]
                    comps_val = json.dumps(comp_names)
                else:
                    comps_val = json.dumps([])

                subtasks_raw = fields.get("subtasks") if fields.get("subtasks") is not None else raw_ref.get("subtasks", [])
                sub_count = len(subtasks_raw) if isinstance(subtasks_raw, list) else int(r_dict.get("subtask_count") or 0)

                orig_est = fields.get("timeoriginalestimate") or fields.get("timetracking", {}).get("originalEstimateSeconds") or r_dict.get("original_estimate_seconds")
                time_spent = fields.get("timespent") or fields.get("timetracking", {}).get("timeSpentSeconds") or r_dict.get("time_spent_seconds")
                creator = fields.get("creator", {}) if isinstance(fields.get("creator"), dict) else {}
                creator_id = creator.get("accountId") or creator.get("name") or r_dict.get("creator_id")

                conn.execute(
                    """
                    UPDATE jira_issue_state
                    SET issue_type = COALESCE(?, issue_type),
                        labels = COALESCE(?, labels),
                        components = COALESCE(?, components),
                        subtask_count = COALESCE(?, subtask_count),
                        original_estimate_seconds = COALESCE(?, original_estimate_seconds),
                        time_spent_seconds = COALESCE(?, time_spent_seconds),
                        creator_id = COALESCE(?, creator_id)
                    WHERE jira_issue_key = ?
                    """,
                    (itype, labels_val, comps_val, sub_count, orig_est, time_spent, creator_id, key)
                )
            logger.info("Canonical backfill complete.")
    except Exception as e:
        logger.warning(f"Non-blocking notice during canonical backfill: {e}")


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


def _migrate_employee_roles(conn) -> None:
    """Idempotently ensure employee_role_assignments schema contains all expected columns."""
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='employee_role_assignments'"
    )
    if not cursor.fetchone():
        return

    cursor = conn.execute("PRAGMA table_info(employee_role_assignments)")
    rows = cursor.fetchall()
    existing_columns = {
        row["name"] if hasattr(row, "keys") and "name" in row.keys() else row[1]
        for row in rows
    }

    if "jira_queue_filter_id" not in existing_columns:
        logger.info("Migrating database: adding 'jira_queue_filter_id' column to employee_role_assignments table...")
        conn.execute("ALTER TABLE employee_role_assignments ADD COLUMN jira_queue_filter_id TEXT")
        logger.info("Database migration complete: 'jira_queue_filter_id' column added successfully.")


def _migrate_jira_issue_links(conn) -> None:
    """Idempotently ensure jira_issue_links table and required columns exist."""
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='jira_issue_links'"
    )
    if not cursor.fetchone():
        logger.info("Migrating database: creating jira_issue_links table...")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jira_issue_links (
                id TEXT PRIMARY KEY,
                source_issue_key TEXT NOT NULL,
                target_issue_key TEXT NOT NULL,
                link_type_name TEXT NOT NULL,
                inward_description TEXT,
                outward_description TEXT,
                classification TEXT NOT NULL DEFAULT 'UNKNOWN',
                source_issue_id TEXT,
                target_issue_id TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        return

    cursor = conn.execute("PRAGMA table_info(jira_issue_links)")
    rows = cursor.fetchall()
    existing_columns = {
        row["name"] if hasattr(row, "keys") and "name" in row.keys() else row[1]
        for row in rows
    }

    expected_columns = {
        "source_issue_key": "TEXT",
        "target_issue_key": "TEXT",
        "link_type_name": "TEXT",
        "inward_description": "TEXT",
        "outward_description": "TEXT",
        "classification": "TEXT NOT NULL DEFAULT 'UNKNOWN'",
        "source_issue_id": "TEXT",
        "target_issue_id": "TEXT",
        "first_seen_at": "TEXT",
        "last_seen_at": "TEXT",
        "is_active": "INTEGER NOT NULL DEFAULT 1",
    }
    for col_name, col_type in expected_columns.items():
        if col_name not in existing_columns:
            logger.info(f"Migrating database: adding '{col_name}' column to jira_issue_links table...")
            conn.execute(f"ALTER TABLE jira_issue_links ADD COLUMN {col_name} {col_type}")


def _migrate_artifact_tables(conn) -> None:
    """Idempotently ensure project_artifacts and artifact_dependencies tables exist."""
    # 1. project_artifacts
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='project_artifacts'"
    )
    if not cursor.fetchone():
        logger.info("Migrating database: creating project_artifacts table...")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS project_artifacts (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                project_key TEXT NOT NULL,
                artifact_type TEXT NOT NULL DEFAULT 'GENERIC',
                status TEXT NOT NULL DEFAULT 'PLANNED',
                producer_issue_key TEXT,
                provenance TEXT NOT NULL DEFAULT 'EXPLICIT_JIRA_LABEL',
                confidence TEXT NOT NULL DEFAULT 'HIGH',
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1
            )
            """
        )
    else:
        cursor = conn.execute("PRAGMA table_info(project_artifacts)")
        existing = {
            row["name"] if hasattr(row, "keys") and "name" in row.keys() else row[1]
            for row in cursor.fetchall()
        }
        for col, col_type in {
            "name": "TEXT NOT NULL",
            "project_key": "TEXT NOT NULL",
            "artifact_type": "TEXT NOT NULL DEFAULT 'GENERIC'",
            "status": "TEXT NOT NULL DEFAULT 'PLANNED'",
            "producer_issue_key": "TEXT",
            "provenance": "TEXT NOT NULL DEFAULT 'EXPLICIT_JIRA_LABEL'",
            "confidence": "TEXT NOT NULL DEFAULT 'HIGH'",
            "first_seen_at": "TEXT NOT NULL",
            "last_seen_at": "TEXT NOT NULL",
            "is_active": "INTEGER NOT NULL DEFAULT 1",
        }.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE project_artifacts ADD COLUMN {col} {col_type}")

    # 2. artifact_dependencies
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='artifact_dependencies'"
    )
    if not cursor.fetchone():
        logger.info("Migrating database: creating artifact_dependencies table...")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS artifact_dependencies (
                id TEXT PRIMARY KEY,
                artifact_id TEXT NOT NULL,
                issue_key TEXT NOT NULL,
                relationship_type TEXT NOT NULL,
                provenance TEXT NOT NULL DEFAULT 'EXPLICIT_JIRA_LABEL',
                confidence TEXT NOT NULL DEFAULT 'HIGH',
                is_inferred INTEGER NOT NULL DEFAULT 0,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1
            )
            """
        )
    else:
        cursor = conn.execute("PRAGMA table_info(artifact_dependencies)")
        existing = {
            row["name"] if hasattr(row, "keys") and "name" in row.keys() else row[1]
            for row in cursor.fetchall()
        }
        for col, col_type in {
            "artifact_id": "TEXT NOT NULL",
            "issue_key": "TEXT NOT NULL",
            "relationship_type": "TEXT NOT NULL",
            "provenance": "TEXT NOT NULL DEFAULT 'EXPLICIT_JIRA_LABEL'",
            "confidence": "TEXT NOT NULL DEFAULT 'HIGH'",
            "is_inferred": "INTEGER NOT NULL DEFAULT 0",
            "first_seen_at": "TEXT NOT NULL",
            "last_seen_at": "TEXT NOT NULL",
            "is_active": "INTEGER NOT NULL DEFAULT 1",
        }.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE artifact_dependencies ADD COLUMN {col} {col_type}")


# Authoritative employee designations and Jira saved filter assignments
AUTHORITATIVE_EMPLOYEE_ROLES = [
    {
        "account_id": "712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de",
        "display_name": "Ahsan Amin",
        "designation": "Senior WordPress Developer",
        "role_category": "WordPress Development",
        "jira_queue_filter_id": "15370",
    },
    {
        "account_id": "63da2ba4f1475ad42c584247",
        "display_name": "Ahsan Iftikhar",
        "designation": "Senior BA",
        "role_category": "Business Analysis",
        "jira_queue_filter_id": "17081",
    },
    {
        "account_id": "712020:0eca0fb9-4f12-4532-a435-4c178f2d90e8",
        "display_name": "Nauman Sadiq",
        "designation": "Senior BA",
        "role_category": "Business Analysis",
        "jira_queue_filter_id": "16829",
    },
    {
        "account_id": "712020:a6d04898-c6d8-4a39-a521-103e4b8bfe7c",
        "display_name": "Muhammad Shahmeer Khan",
        "designation": "Junior BA",
        "role_category": "Business Analysis",
        "jira_queue_filter_id": "15517",
    },
    {
        "account_id": "712020:c12d2371-1e5b-4797-a888-369c0c9c5a65",
        "display_name": "Muhammad Sufiyan",
        "designation": "Senior QA Engineer",
        "role_category": "QA",
        "jira_queue_filter_id": "15515",
    },
    {
        "account_id": "712020:32e5be05-80c9-4ece-ac19-301da7c9487d",
        "display_name": "shoaib hassan askari",
        "designation": "Senior QA",
        "role_category": "QA",
        "jira_queue_filter_id": "15516",
    },
    {
        "account_id": "712020:12e1da4b-147f-4f91-9d2d-965b66e19b61",
        "display_name": "Muhammad Bilal Khan",
        "designation": "Mid-level QA",
        "role_category": "QA",
        "jira_queue_filter_id": "16817",
    },
    {
        "account_id": "63e362bd790148a180977179",
        "display_name": "Daniyal Raza",
        "designation": "Mid-level WordPress Developer",
        "role_category": "WordPress Development",
        "jira_queue_filter_id": "16826",
    },
    {
        "account_id": "5fb3d908facfd6007697c25a",
        "display_name": "Muhammad Hamza",
        "designation": "Mid-level WordPress Developer",
        "role_category": "WordPress Development",
        "jira_queue_filter_id": "16827",
    },
    {
        "account_id": "606570150a6b3f00698f9430",
        "display_name": "Muneeb Jalal",
        "designation": "Senior WordPress Developer",
        "role_category": "WordPress Development",
        "jira_queue_filter_id": "15369",
    },
    {
        "account_id": "61ee41431c42100069344a09",
        "display_name": "Syed ali",
        "designation": "Senior WordPress Developer",
        "role_category": "WordPress Development",
        "jira_queue_filter_id": "15368",
    },
    {
        "account_id": "638855b85fce844d606bb422",
        "display_name": "Tahir Ali",
        "designation": "Senior Content Writer / Marketing Strategist",
        "role_category": "Content / Marketing",
        "jira_queue_filter_id": "16828",
    },
    {
        "account_id": "638490c75fce844d606a16ef",
        "display_name": "Hamza Hanif",
        "designation": "SEO",
        "role_category": "SEO",
        "jira_queue_filter_id": "17010",
    },
    {
        "account_id": "712020:e268bcd8-d981-4b4d-992d-d5694745df8b",
        "display_name": "Mubashir Butt",
        "designation": "Customer Support Engineer",
        "role_category": "Customer Support",
        "jira_queue_filter_id": None,
    },
    {
        "account_id": "712020:fb8608cb-6393-48a7-a3ab-1ad744a2b7f6",
        "display_name": "Muhammad Usama Azad",
        "designation": "Front End Developer",
        "role_category": "Frontend Development",
        "jira_queue_filter_id": "15367",
    },
    {
        "account_id": "712020:2783ea21-c611-402d-9adb-0529f5b7066d",
        "display_name": "Muhammad Ali Siddiqui",
        "designation": "Junior Content Writer",
        "role_category": "Content",
        "jira_queue_filter_id": "17129",
    },
    {
        "account_id": "712020:bb2e5830-7156-4852-bba8-75fa773fc55d",
        "display_name": "Talha Bukhari",
        "designation": "Content Producer",
        "role_category": "Content",
        "jira_queue_filter_id": "17082",
    },
    {
        "account_id": "712020:1ddac8e3-e006-48e7-b4c9-ee941efc8e6e",
        "display_name": "Azain Hassan",
        "designation": "Designer",
        "role_category": "Design",
        "jira_queue_filter_id": "15371",
    },
    {
        "account_id": "5f83e3937d9637006ffd0436",
        "display_name": "Usman",
        "designation": "Engineering Manager",
        "role_category": "Unknown",
        "jira_queue_filter_id": "17124",
    },
]


# Generated 46 rows
SEED_PLUGIN_BOARD_REGISTRY: List[Dict[str, Any]] = [
    {
        "sr_no": 1,
        "plugin_name": 'Post SMTP Free',
        "internal_board": 'https://objectsws.atlassian.net/jira/software/projects/POSTSMTP/boards/132/backlog',
        "internal_support_board": 'https://objectsws.atlassian.net/jira/software/projects/SMTPSUPORT/boards/131',
        "service_management_board": 'https://objectsws.atlassian.net/browse/POST',
        "internal_project_key": 'POSTSMTP',
        "support_project_key": 'SMTPSUPORT',
        "service_management_project_key": 'POST',
    },
    {
        "sr_no": 2,
        "plugin_name": 'Post SMTP Addons',
        "internal_board": 'https://objectsws.atlassian.net/jira/software/projects/POSTSMTP/boards/132/backlog',
        "internal_support_board": 'https://objectsws.atlassian.net/jira/software/projects/SMTPSUPORT/boards/131',
        "service_management_board": 'https://objectsws.atlassian.net/browse/POST',
        "internal_project_key": 'POSTSMTP',
        "support_project_key": 'SMTPSUPORT',
        "service_management_project_key": 'POST',
    },
    {
        "sr_no": 3,
        "plugin_name": 'Post SMTP Mobile App',
        "internal_board": 'https://objectsws.atlassian.net/jira/software/projects/PSA/boards/172/backlog',
        "internal_support_board": 'https://objectsws.atlassian.net/jira/software/projects/SMTPSUPORT/boards/131',
        "service_management_board": 'https://objectsws.atlassian.net/browse/POST',
        "internal_project_key": 'PSA',
        "support_project_key": 'SMTPSUPORT',
        "service_management_project_key": 'POST',
    },
    {
        "sr_no": 4,
        "plugin_name": 'MainWP Post SMTP ORG',
        "internal_board": 'https://objectsws.atlassian.net/jira/software/projects/PSMEL/boards/116/',
        "internal_support_board": 'https://objectsws.atlassian.net/jira/software/projects/SMTPSUPORT/boards/131',
        "service_management_board": 'https://objectsws.atlassian.net/browse/POST',
        "internal_project_key": 'PSMEL',
        "support_project_key": 'SMTPSUPORT',
        "service_management_project_key": 'POST',
    },
    {
        "sr_no": 5,
        "plugin_name": 'AFM',
        "internal_board": 'https://objectsws.atlassian.net/jira/software/projects/AFM/boards/211/backlog',
        "internal_support_board": 'https://objectsws.atlassian.net/jira/software/projects/AFMIS/boards/216',
        "service_management_board": 'https://objectsws.atlassian.net/browse/AFMS',
        "internal_project_key": 'AFM',
        "support_project_key": 'AFMIS',
        "service_management_project_key": 'AFMS',
    },
    {
        "sr_no": 6,
        "plugin_name": 'Gutena Forms',
        "internal_board": 'https://objectsws.atlassian.net/jira/software/projects/GF/boards/1281/backlog',
        "internal_support_board": 'https://objectsws.atlassian.net/jira/software/projects/GFIS/boards/1282',
        "service_management_board": 'https://objectsws.atlassian.net/browse/GFS',
        "internal_project_key": 'GF',
        "support_project_key": 'GFIS',
        "service_management_project_key": 'GFS',
    },
    {
        "sr_no": 7,
        "plugin_name": 'AIO Login',
        "internal_board": 'https://objectsws.atlassian.net/jira/software/projects/AIOL/boards/179',
        "internal_support_board": 'https://objectsws.atlassian.net/jira/software/projects/AIOLAIS/boards/202',
        "service_management_board": 'https://objectsws.atlassian.net/browse/AIOS',
        "internal_project_key": 'AIOL',
        "support_project_key": 'AIOLAIS',
        "service_management_project_key": 'AIOS',
    },
    {
        "sr_no": 8,
        "plugin_name": 'SMTP & Email Logs',
        "internal_board": 'https://objectsws.atlassian.net/jira/software/projects/SEL/boards/952',
        "internal_support_board": 'No Board',
        "service_management_board": 'https://objectsws.atlassian.net/browse/SELS',
        "internal_project_key": 'SEL',
        "support_project_key": None,
        "service_management_project_key": 'SELS',
    },
    {
        "sr_no": 9,
        "plugin_name": 'Quick Contact Form',
        "internal_board": 'No Board',
        "internal_support_board": 'No Board',
        "service_management_board": 'No Board',
        "internal_project_key": None,
        "support_project_key": None,
        "service_management_project_key": None,
    },
    {
        "sr_no": 10,
        "plugin_name": 'WC Shop Sync Free',
        "internal_board": 'https://objectsws.atlassian.net/jira/software/projects/WSF/boards/161',
        "internal_support_board": 'https://objectsws.atlassian.net/jira/software/projects/WSSS/boards/178',
        "service_management_board": 'https://objectsws.atlassian.net/browse/WSPLUSSUP',
        "internal_project_key": 'WSF',
        "support_project_key": 'WSSS',
        "service_management_project_key": 'WSPLUSSUP',
    },
    {
        "sr_no": 11,
        "plugin_name": 'WC Shop Sync Pro',
        "internal_board": 'https://objectsws.atlassian.net/jira/software/projects/WP/boards/88',
        "internal_support_board": 'https://objectsws.atlassian.net/jira/software/projects/WSSS/boards/178',
        "service_management_board": 'https://objectsws.atlassian.net/browse/WSPLUSSUP',
        "internal_project_key": 'WP',
        "support_project_key": 'WSSS',
        "service_management_project_key": 'WSPLUSSUP',
    },
    {
        "sr_no": 12,
        "plugin_name": 'WC Square Recurring Payments',
        "internal_board": 'https://objectsws.atlassian.net/jira/software/projects/WP/boards/88',
        "internal_support_board": 'https://objectsws.atlassian.net/jira/software/projects/WSSS/boards/178',
        "service_management_board": 'https://objectsws.atlassian.net/browse/SIA',
        "internal_project_key": 'WP',
        "support_project_key": 'WSSS',
        "service_management_project_key": 'SIA',
    },
    {
        "sr_no": 13,
        "plugin_name": 'Woocommerce Booking Exporter',
        "internal_board": 'https://objectsws.atlassian.net/jira/software/projects/WBE/boards/155',
        "internal_support_board": 'No Board',
        "service_management_board": 'https://objectsws.atlassian.net/browse/WPS',
        "internal_project_key": 'WBE',
        "support_project_key": None,
        "service_management_project_key": 'WPS',
    },
    {
        "sr_no": 14,
        "plugin_name": 'WP Multi Store Locator',
        "internal_board": 'https://objectsws.atlassian.net/jira/software/projects/MULTILOCAT/boards/91/backlog',
        "internal_support_board": 'No Board',
        "service_management_board": 'https://objectsws.atlassian.net/browse/WPS',
        "internal_project_key": 'MULTILOCAT',
        "support_project_key": None,
        "service_management_project_key": 'WPS',
    },
    {
        "sr_no": 15,
        "plugin_name": 'Gravity Square',
        "internal_board": 'https://objectsws.atlassian.net/jira/software/projects/SQPST/boards/101/backlog',
        "internal_support_board": 'https://objectsws.atlassian.net/jira/software/projects/PGC/boards/85/backlog',
        "service_management_board": 'https://objectsws.atlassian.net/browse/SIA',
        "internal_project_key": 'SQPST',
        "support_project_key": 'PGC',
        "service_management_project_key": 'SIA',
    },
    {
        "sr_no": 16,
        "plugin_name": 'Formidible Square',
        "internal_board": 'https://objectsws.atlassian.net/jira/software/projects/SQPST/boards/101/backlog',
        "internal_support_board": 'https://objectsws.atlassian.net/jira/software/projects/PGC/boards/85/backlog',
        "service_management_board": 'https://objectsws.atlassian.net/browse/SIA',
        "internal_project_key": 'SQPST',
        "support_project_key": 'PGC',
        "service_management_project_key": 'SIA',
    },
    {
        "sr_no": 17,
        "plugin_name": 'Memberpress Square',
        "internal_board": 'https://objectsws.atlassian.net/jira/software/projects/SQPST/boards/101/backlog',
        "internal_support_board": 'https://objectsws.atlassian.net/jira/software/projects/PGC/boards/85/backlog',
        "service_management_board": 'https://objectsws.atlassian.net/browse/SIA',
        "internal_project_key": 'SQPST',
        "support_project_key": 'PGC',
        "service_management_project_key": 'SIA',
    },
    {
        "sr_no": 18,
        "plugin_name": 'WP Form Square',
        "internal_board": 'https://objectsws.atlassian.net/jira/software/projects/SQPST/boards/101/backlog',
        "internal_support_board": 'https://objectsws.atlassian.net/jira/software/projects/PGC/boards/85/backlog',
        "service_management_board": 'https://objectsws.atlassian.net/browse/SIA',
        "internal_project_key": 'SQPST',
        "support_project_key": 'PGC',
        "service_management_project_key": 'SIA',
    },
    {
        "sr_no": 19,
        "plugin_name": 'Ninja Square',
        "internal_board": 'https://objectsws.atlassian.net/jira/software/projects/SQPST/boards/101/backlog',
        "internal_support_board": 'https://objectsws.atlassian.net/jira/software/projects/PGC/boards/85/backlog',
        "service_management_board": 'https://objectsws.atlassian.net/browse/SIA',
        "internal_project_key": 'SQPST',
        "support_project_key": 'PGC',
        "service_management_project_key": 'SIA',
    },
    {
        "sr_no": 20,
        "plugin_name": 'Give Square',
        "internal_board": 'https://objectsws.atlassian.net/jira/software/projects/SQPST/boards/101/backlog',
        "internal_support_board": 'https://objectsws.atlassian.net/jira/software/projects/PGC/boards/85/backlog',
        "service_management_board": 'https://objectsws.atlassian.net/browse/SIA',
        "internal_project_key": 'SQPST',
        "support_project_key": 'PGC',
        "service_management_project_key": 'SIA',
    },
    {
        "sr_no": 21,
        "plugin_name": 'Gravity World Pay Square',
        "internal_board": 'https://objectsws.atlassian.net/jira/software/projects/SQPST/boards/101/backlog',
        "internal_support_board": 'https://objectsws.atlassian.net/jira/software/projects/PGC/boards/85/backlog',
        "service_management_board": 'https://objectsws.atlassian.net/browse/SIA',
        "internal_project_key": 'SQPST',
        "support_project_key": 'PGC',
        "service_management_project_key": 'SIA',
    },
    {
        "sr_no": 22,
        "plugin_name": 'SumUp Payment Gateway',
        "internal_board": 'No Board',
        "internal_support_board": 'No Board',
        "service_management_board": 'No Board',
        "internal_project_key": None,
        "support_project_key": None,
        "service_management_project_key": None,
    },
    {
        "sr_no": 23,
        "plugin_name": 'WP Contact Slider',
        "internal_board": 'https://objectsws.atlassian.net/jira/software/projects/WPCS/boards/183',
        "internal_support_board": 'No Board',
        "service_management_board": 'https://objectsws.atlassian.net/browse/WPCSS',
        "internal_project_key": 'WPCS',
        "support_project_key": None,
        "service_management_project_key": 'WPCSS',
    },
    {
        "sr_no": 24,
        "plugin_name": 'Password Protected',
        "internal_board": 'https://objectsws.atlassian.net/jira/software/projects/PP/boards/117',
        "internal_support_board": 'https://objectsws.atlassian.net/jira/software/projects/PPIS/boards/195',
        "service_management_board": 'https://objectsws.atlassian.net/browse/PAS',
        "internal_project_key": 'PP',
        "support_project_key": 'PPIS',
        "service_management_project_key": 'PAS',
    },
    {
        "sr_no": 25,
        "plugin_name": 'WP EasyPay Free',
        "internal_board": 'https://objectsws.atlassian.net/jira/software/projects/WEPF/boards/174/backlog',
        "internal_support_board": 'https://objectsws.atlassian.net/jira/software/projects/WEPIS/boards/190/backlog',
        "service_management_board": 'https://objectsws.atlassian.net/browse/WPEPSUP',
        "internal_project_key": 'WEPF',
        "support_project_key": 'WEPIS',
        "service_management_project_key": 'WPEPSUP',
    },
    {
        "sr_no": 26,
        "plugin_name": 'WP EasyPay Pro',
        "internal_board": 'https://objectsws.atlassian.net/jira/software/projects/WPEP/boards/40/backlog',
        "internal_support_board": 'https://objectsws.atlassian.net/jira/software/projects/WEPIS/boards/190/backlog',
        "service_management_board": 'https://objectsws.atlassian.net/browse/WPEPSUP',
        "internal_project_key": 'WPEP',
        "support_project_key": 'WEPIS',
        "service_management_project_key": 'WPEPSUP',
    },
    {
        "sr_no": 27,
        "plugin_name": 'CF7 Apps',
        "internal_board": 'https://objectsws.atlassian.net/jira/software/projects/HFCF7/boards/196/backlog',
        "internal_support_board": 'https://objectsws.atlassian.net/jira/software/projects/HPCF7IS/boards/223/backlog',
        "service_management_board": 'https://objectsws.atlassian.net/browse/CA',
        "internal_project_key": 'HFCF7',
        "support_project_key": 'HPCF7IS',
        "service_management_project_key": 'CA',
    },
    {
        "sr_no": 28,
        "plugin_name": 'Gutena Accordion – Beautiful FAQ Accordion Block',
        "internal_board": 'https://objectsws.atlassian.net/jira/software/projects/GUT/boards/1347/backlog',
        "internal_support_board": 'No Board',
        "service_management_board": 'https://objectsws.atlassian.net/browse/GFS',
        "internal_project_key": 'GUT',
        "support_project_key": None,
        "service_management_project_key": 'GFS',
    },
    {
        "sr_no": 29,
        "plugin_name": 'Gutena Kit',
        "internal_board": 'https://objectsws.atlassian.net/jira/software/projects/GUT/boards/1347/backlog',
        "internal_support_board": 'No Board',
        "service_management_board": 'https://objectsws.atlassian.net/browse/GFS',
        "internal_project_key": 'GUT',
        "support_project_key": None,
        "service_management_project_key": 'GFS',
    },
    {
        "sr_no": 30,
        "plugin_name": 'Gutena Newsletter',
        "internal_board": 'https://objectsws.atlassian.net/jira/software/projects/GUT/boards/1347/backlog',
        "internal_support_board": 'No Board',
        "service_management_board": 'https://objectsws.atlassian.net/browse/GFS',
        "internal_project_key": 'GUT',
        "support_project_key": None,
        "service_management_project_key": 'GFS',
    },
    {
        "sr_no": 31,
        "plugin_name": 'Gutena PhotoFeed',
        "internal_board": 'https://objectsws.atlassian.net/jira/software/projects/GUT/boards/1347/backlog',
        "internal_support_board": 'No Board',
        "service_management_board": 'https://objectsws.atlassian.net/browse/GFS',
        "internal_project_key": 'GUT',
        "support_project_key": None,
        "service_management_project_key": 'GFS',
    },
    {
        "sr_no": 32,
        "plugin_name": 'Gutena Recent Post Custom Tag',
        "internal_board": 'https://objectsws.atlassian.net/jira/software/projects/GUT/boards/1347/backlog',
        "internal_support_board": 'No Board',
        "service_management_board": 'https://objectsws.atlassian.net/browse/GFS',
        "internal_project_key": 'GUT',
        "support_project_key": None,
        "service_management_project_key": 'GFS',
    },
    {
        "sr_no": 33,
        "plugin_name": 'Gutena Star Ratings',
        "internal_board": 'https://objectsws.atlassian.net/jira/software/projects/GUT/boards/1347/backlog',
        "internal_support_board": 'No Board',
        "service_management_board": 'https://objectsws.atlassian.net/browse/GFS',
        "internal_project_key": 'GUT',
        "support_project_key": None,
        "service_management_project_key": 'GFS',
    },
    {
        "sr_no": 34,
        "plugin_name": 'Gutena Tabs',
        "internal_board": 'https://objectsws.atlassian.net/jira/software/projects/GUT/boards/1347/backlog',
        "internal_support_board": 'No Board',
        "service_management_board": 'https://objectsws.atlassian.net/browse/GFS',
        "internal_project_key": 'GUT',
        "support_project_key": None,
        "service_management_project_key": 'GFS',
    },
    {
        "sr_no": 35,
        "plugin_name": 'Gutena Team',
        "internal_board": 'https://objectsws.atlassian.net/jira/software/projects/GUT/boards/1347/backlog',
        "internal_support_board": 'No Board',
        "service_management_board": 'https://objectsws.atlassian.net/browse/GFS',
        "internal_project_key": 'GUT',
        "support_project_key": None,
        "service_management_project_key": 'GFS',
    },
    {
        "sr_no": 36,
        "plugin_name": 'Gutena Testimonial Slider',
        "internal_board": 'https://objectsws.atlassian.net/jira/software/projects/GUT/boards/1347/backlog',
        "internal_support_board": 'No Board',
        "service_management_board": 'https://objectsws.atlassian.net/browse/GFS',
        "internal_project_key": 'GUT',
        "support_project_key": None,
        "service_management_project_key": 'GFS',
    },
    {
        "sr_no": 37,
        "plugin_name": 'Gutena Video Lightbox',
        "internal_board": 'https://objectsws.atlassian.net/jira/software/projects/GUT/boards/1347/backlog',
        "internal_support_board": 'No Board',
        "service_management_board": 'https://objectsws.atlassian.net/browse/GFS',
        "internal_project_key": 'GUT',
        "support_project_key": None,
        "service_management_project_key": 'GFS',
    },
    {
        "sr_no": 38,
        "plugin_name": 'OGTag',
        "internal_board": 'https://objectsws.atlassian.net/jira/software/projects/OGTCT/boards/143/backlog',
        "internal_support_board": 'No Board',
        "service_management_board": 'https://objectsws.atlassian.net/browse/WPS',
        "internal_project_key": 'OGTCT',
        "support_project_key": None,
        "service_management_project_key": 'WPS',
    },
    {
        "sr_no": 39,
        "plugin_name": 'Login Designer',
        "internal_board": 'https://objectsws.atlassian.net/jira/software/projects/LD/boards/113/backlog',
        "internal_support_board": 'No Board',
        "service_management_board": 'https://objectsws.atlassian.net/browse/LDSB',
        "internal_project_key": 'LD',
        "support_project_key": None,
        "service_management_project_key": 'LDSB',
    },
    {
        "sr_no": 40,
        "plugin_name": 'WP Formify  (Discontinue)',
        "internal_board": 'https://objectsws.atlassian.net/jira/software/projects/WPF/boards/786/backlog',
        "internal_support_board": 'No Board',
        "service_management_board": 'https://objectsws.atlassian.net/browse/WPS',
        "internal_project_key": 'WPF',
        "support_project_key": None,
        "service_management_project_key": 'WPS',
    },
    {
        "sr_no": 41,
        "plugin_name": 'Custom Product Builder',
        "internal_board": 'https://objectsws.atlassian.net/jira/software/projects/VPD/boards/3547/backlog',
        "internal_support_board": 'https://objectsws.atlassian.net/jira/software/projects/VPDIS/boards/3548/backlog',
        "service_management_board": 'https://objectsws.atlassian.net/browse/VPDSSM',
        "internal_project_key": 'VPD',
        "support_project_key": 'VPDIS',
        "service_management_project_key": 'VPDSSM',
    },
    {
        "sr_no": 42,
        "plugin_name": 'NOWPayments',
        "internal_board": 'https://objectsws.atlassian.net/jira/software/projects/CDP/boards/3585/backlog',
        "internal_support_board": 'https://objectsws.atlassian.net/jira/software/projects/CIS/boards/4819/backlog',
        "service_management_board": 'https://objectsws.atlassian.net/browse/CPS',
        "internal_project_key": 'CDP',
        "support_project_key": 'CIS',
        "service_management_project_key": 'CPS',
    },
    {
        "sr_no": 43,
        "plugin_name": 'Coinbase Commerce',
        "internal_board": 'https://objectsws.atlassian.net/jira/software/projects/CDP/boards/3585/backlog',
        "internal_support_board": 'https://objectsws.atlassian.net/jira/software/projects/CIS/boards/4819/backlog',
        "service_management_board": 'https://objectsws.atlassian.net/browse/CPS',
        "internal_project_key": 'CDP',
        "support_project_key": 'CIS',
        "service_management_project_key": 'CPS',
    },
    {
        "sr_no": 44,
        "plugin_name": 'CF7 Booking',
        "internal_board": 'No Board',
        "internal_support_board": 'No Board',
        "service_management_board": 'No Board',
        "internal_project_key": None,
        "support_project_key": None,
        "service_management_project_key": None,
    },
    {
        "sr_no": 55,
        "plugin_name": 'Marketing Board',
        "internal_board": 'https://objectsws.atlassian.net/jira/software/projects/TREN/boards/207/backlog',
        "internal_support_board": None,
        "service_management_board": None,
        "internal_project_key": 'TREN',
        "support_project_key": None,
        "service_management_project_key": None,
    },
    {
        "sr_no": 56,
        "plugin_name": 'Trend Alliance Management',
        "internal_board": 'https://objectsws.atlassian.net/jira/core/projects/TAM/board?filter=&groupBy=none',
        "internal_support_board": None,
        "service_management_board": None,
        "internal_project_key": 'TAM',
        "support_project_key": None,
        "service_management_project_key": None,
    },
]



def _seed_plugin_board_registry(conn) -> None:
    """Idempotently seed the authoritative Plugin -> Board registry mapping from spreadsheet."""
    now_iso = "2026-09-12T00:00:00Z"
    for item in SEED_PLUGIN_BOARD_REGISTRY:
        rec_id = f"plugin:{item['sr_no']}:{item['plugin_name'].strip()}"
        conn.execute(
            """
            INSERT INTO plugin_board_registry (
                id, sr_no, plugin_name, internal_board, internal_support_board,
                service_management_board, internal_project_key, support_project_key,
                service_management_project_key, active, source, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'spreadsheet', ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                sr_no = excluded.sr_no,
                plugin_name = excluded.plugin_name,
                internal_board = excluded.internal_board,
                internal_support_board = excluded.internal_support_board,
                service_management_board = excluded.service_management_board,
                internal_project_key = excluded.internal_project_key,
                support_project_key = excluded.support_project_key,
                service_management_project_key = excluded.service_management_project_key,
                updated_at = excluded.updated_at
            """,
            (
                rec_id,
                item["sr_no"],
                item["plugin_name"],
                item["internal_board"],
                item["internal_support_board"],
                item["service_management_board"],
                item["internal_project_key"],
                item["support_project_key"],
                item["service_management_project_key"],
                now_iso,
                now_iso,
            ),
        )


def _seed_authoritative_roles(conn) -> None:
    """Idempotently seed the authoritative employee designation and role mappings."""
    now_iso = "2026-09-12T00:00:00Z"
    for r in AUTHORITATIVE_EMPLOYEE_ROLES:
        rec_id = f"role:{r['account_id']}"
        conn.execute(
            """
            INSERT INTO employee_role_assignments (
                id, account_id, display_name, designation, role_category,
                effective_from, effective_to, source, jira_queue_filter_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, NULL, 'authoritative_seed', ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                display_name = excluded.display_name,
                designation = excluded.designation,
                role_category = excluded.role_category,
                jira_queue_filter_id = excluded.jira_queue_filter_id,
                updated_at = excluded.updated_at
            """,
            (
                rec_id,
                r["account_id"],
                r["display_name"],
                r["designation"],
                r["role_category"],
                "2026-01-01",
                r.get("jira_queue_filter_id"),
                now_iso,
                now_iso,
            ),
        )


def _apply_migrations(conn) -> None:
    """Execute all registered schema migrations safely and idempotently."""
    _migrate_jira_issue_state(conn)
    _migrate_jira_worklogs(conn)
    _migrate_jira_issue_links(conn)
    _migrate_artifact_tables(conn)
    _migrate_actions(conn)
    _migrate_performance_tables(conn)
    _migrate_employee_roles(conn)


def _ensure_post_migration_indexes(conn) -> None:
    """Create indexes that depend on migrated columns safely after columns exist."""
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_jira_issue_state_team_group ON jira_issue_state(team_group)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_jira_issue_state_type ON jira_issue_state(issue_type)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_jira_worklogs_team ON jira_worklogs(team_group)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_jira_issue_links_source ON jira_issue_links(source_issue_key)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_jira_issue_links_target ON jira_issue_links(target_issue_key)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_jira_issue_links_type ON jira_issue_links(link_type_name)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_jira_issue_links_class ON jira_issue_links(classification)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_project_artifacts_proj ON project_artifacts(project_key)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_project_artifacts_name ON project_artifacts(name)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_project_artifacts_prod ON project_artifacts(producer_issue_key)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_project_artifacts_prov ON project_artifacts(provenance)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_art_dep_art ON artifact_dependencies(artifact_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_art_dep_issue ON artifact_dependencies(issue_key)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_art_dep_rel ON artifact_dependencies(relationship_type)"
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
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_hist_prof_acc ON historical_intelligence_profiles(account_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_hist_prof_run ON historical_intelligence_profiles(analysis_run_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_hist_ev_acc ON historical_evidence(account_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_hist_ev_run ON historical_evidence(analysis_run_id)"
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

        # 6. Seed authoritative plugin -> board registry
        _seed_plugin_board_registry(conn)
    logger.info("Database schema initialized successfully.")

