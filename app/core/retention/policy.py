"""Authoritative declarative retention policies and table registries."""

from typing import Dict, List
from app.core.retention.models import RetentionClass, RetentionPolicyDefinition

# Authoritative policy definitions ordered by deletion hierarchy (children before parents)
RETENTION_POLICIES: List[RetentionPolicyDefinition] = [
    # -------------------------------------------------------------------------
    # 1. RAW_OPERATIONAL (Events: Monday-to-Monday Asia/Karachi, 7 days max)
    # -------------------------------------------------------------------------
    RetentionPolicyDefinition(
        table_name="events",
        retention_class=RetentionClass.RAW_OPERATIONAL,
        timestamp_column="created_at",
        retention_days=7,
        id_column="id",
        eligibility_predicate="processing_status = 'PROCESSED'",
        description="Processed events older than boundary (Monday 00:00 PKT / 7d). Failed events protected.",
    ),

    # -------------------------------------------------------------------------
    # 2. TRANSIENT (30 days)
    # -------------------------------------------------------------------------
    RetentionPolicyDefinition(
        table_name="notifications",
        retention_class=RetentionClass.TRANSIENT,
        timestamp_column="last_notified_at",
        retention_days=30,
        id_column="id",
        description="Notification deduplication records older than 30 days.",
    ),

    # -------------------------------------------------------------------------
    # 3. ANALYTICAL — Phase A Child Tables (30 days)
    # -------------------------------------------------------------------------
    RetentionPolicyDefinition(
        table_name="performance_signals",
        retention_class=RetentionClass.ANALYTICAL,
        timestamp_column="created_at",
        retention_days=30,
        id_column="id",
        description="Phase A performance signals older than 30 days.",
    ),
    RetentionPolicyDefinition(
        table_name="performance_evidence",
        retention_class=RetentionClass.ANALYTICAL,
        timestamp_column="timestamp",
        retention_days=30,
        id_column="evidence_id",
        description="Phase A performance evidence older than 30 days.",
    ),
    RetentionPolicyDefinition(
        table_name="task_delivery_forecasts",
        retention_class=RetentionClass.ANALYTICAL,
        timestamp_column="updated_at",
        retention_days=30,
        id_column="id",
        description="Phase A task delivery forecasts older than 30 days.",
    ),
    RetentionPolicyDefinition(
        table_name="resource_task_classifications",
        retention_class=RetentionClass.ANALYTICAL,
        timestamp_column="updated_at",
        retention_days=30,
        id_column="id",
        description="Phase A task classifications older than 30 days.",
    ),
    RetentionPolicyDefinition(
        table_name="resource_effort_statistics",
        retention_class=RetentionClass.ANALYTICAL,
        timestamp_column="updated_at",
        retention_days=30,
        id_column="id",
        description="Phase A resource effort statistics older than 30 days.",
    ),
    RetentionPolicyDefinition(
        table_name="resource_performance_profiles",
        retention_class=RetentionClass.ANALYTICAL,
        timestamp_column="created_at",
        retention_days=30,
        id_column="id",
        description="Phase A performance profiles older than 30 days.",
    ),
    RetentionPolicyDefinition(
        table_name="performance_validation_reports",
        retention_class=RetentionClass.ANALYTICAL,
        timestamp_column="created_at",
        retention_days=30,
        id_column="id",
        description="Phase A validation reports older than 30 days.",
    ),

    # -------------------------------------------------------------------------
    # 4. ANALYTICAL — Phase A Parent Runs (30 days)
    # -------------------------------------------------------------------------
    RetentionPolicyDefinition(
        table_name="performance_analysis_runs",
        retention_class=RetentionClass.ANALYTICAL,
        timestamp_column="calculated_at",
        retention_days=30,
        id_column="analysis_run_id",
        description="Phase A performance analysis run headers older than 30 days.",
    ),

    # -------------------------------------------------------------------------
    # 5. ANALYTICAL — Phase B Child Tables (30 days)
    # -------------------------------------------------------------------------
    RetentionPolicyDefinition(
        table_name="historical_delivery_context",
        retention_class=RetentionClass.ANALYTICAL,
        timestamp_column="created_at",
        retention_days=30,
        id_column="id",
        description="Phase B historical delivery context older than 30 days.",
    ),
    RetentionPolicyDefinition(
        table_name="historical_workload_snapshots",
        retention_class=RetentionClass.ANALYTICAL,
        timestamp_column="created_at",
        retention_days=30,
        id_column="id",
        description="Phase B historical workload snapshots older than 30 days.",
    ),
    RetentionPolicyDefinition(
        table_name="historical_trends",
        retention_class=RetentionClass.ANALYTICAL,
        timestamp_column="created_at",
        retention_days=30,
        id_column="id",
        description="Phase B historical rolling trends older than 30 days.",
    ),
    RetentionPolicyDefinition(
        table_name="historical_task_mix",
        retention_class=RetentionClass.ANALYTICAL,
        timestamp_column="created_at",
        retention_days=30,
        id_column="id",
        description="Phase B historical task mix older than 30 days.",
    ),
    RetentionPolicyDefinition(
        table_name="historical_effort_benchmarks",
        retention_class=RetentionClass.ANALYTICAL,
        timestamp_column="created_at",
        retention_days=30,
        id_column="id",
        description="Phase B historical effort benchmarks older than 30 days.",
    ),
    RetentionPolicyDefinition(
        table_name="historical_intelligence_profiles",
        retention_class=RetentionClass.ANALYTICAL,
        timestamp_column="calculated_at",
        retention_days=30,
        id_column="id",
        description="Phase B intelligence profiles older than 30 days.",
    ),

    # -------------------------------------------------------------------------
    # 6. ANALYTICAL & REPORTING (90 days)
    # -------------------------------------------------------------------------
    RetentionPolicyDefinition(
        table_name="historical_evidence",
        retention_class=RetentionClass.ANALYTICAL,
        timestamp_column="timestamp",
        retention_days=90,
        id_column="id",
        description="Phase B expanded historical evidence ledger older than 90 days.",
    ),
    RetentionPolicyDefinition(
        table_name="daily_report_history",
        retention_class=RetentionClass.ANALYTICAL,
        timestamp_column="generated_at",
        retention_days=90,
        id_column="id",
        description="Generated daily report idempotency history older than 90 days.",
    ),

    # -------------------------------------------------------------------------
    # 8. SECURITY_AUDIT (90 days)
    # -------------------------------------------------------------------------
    RetentionPolicyDefinition(
        table_name="audit_logs",
        retention_class=RetentionClass.SECURITY_AUDIT,
        timestamp_column="timestamp",
        retention_days=90,
        id_column="id",
        description="System audit logs older than 90 days.",
    ),
    RetentionPolicyDefinition(
        table_name="actions",
        retention_class=RetentionClass.SECURITY_AUDIT,
        timestamp_column="created_at",
        retention_days=90,
        id_column="id",
        eligibility_predicate="status IN ('COMPLETED', 'FAILED', 'REJECTED', 'DRY_RUN_SIMULATED', 'ACTION_UNSUPPORTED', 'USER_MAPPING_REQUIRED', 'SKIPPED')",
        description="Completed/failed terminal action records older than 90 days. Open/pending actions strictly protected.",
    ),

    # -------------------------------------------------------------------------
    # 9. CURRENT_STATE & CONFIGURATION (STRICTLY PROTECTED — ZERO DELETION)
    # -------------------------------------------------------------------------
    RetentionPolicyDefinition(
        table_name="jira_worklogs",
        retention_class=RetentionClass.CURRENT_STATE,
        timestamp_column="started_at",
        retention_days=365,
        id_column="worklog_id",
        is_protected=True,
        description="Canonical worklogs (protected for 365d minimum analytical history; never deleted by retention).",
    ),
    RetentionPolicyDefinition(
        table_name="jira_issue_state",
        retention_class=RetentionClass.CURRENT_STATE,
        is_protected=True,
        description="Canonical Jira issue state projection (protected current state).",
    ),
    RetentionPolicyDefinition(
        table_name="jira_polling_state",
        retention_class=RetentionClass.CURRENT_STATE,
        is_protected=True,
        description="Persistent polling checkpoint (protected current state).",
    ),
    RetentionPolicyDefinition(
        table_name="rules",
        retention_class=RetentionClass.CONFIGURATION,
        is_protected=True,
        description="System rules configuration (protected configuration).",
    ),
    RetentionPolicyDefinition(
        table_name="employee_role_assignments",
        retention_class=RetentionClass.CONFIGURATION,
        is_protected=True,
        description="Authoritative employee role assignments (protected configuration).",
    ),
    RetentionPolicyDefinition(
        table_name="plugin_board_registry",
        retention_class=RetentionClass.CONFIGURATION,
        is_protected=True,
        description="Plugin board registry (protected configuration).",
    ),
    RetentionPolicyDefinition(
        table_name="user_mappings",
        retention_class=RetentionClass.CONFIGURATION,
        is_protected=True,
        description="User identity mappings (protected configuration).",
    ),
    RetentionPolicyDefinition(
        table_name="users",
        retention_class=RetentionClass.CONFIGURATION,
        is_protected=True,
        description="User records (protected configuration).",
    ),
    RetentionPolicyDefinition(
        table_name="retention_run_history",
        retention_class=RetentionClass.SECURITY_AUDIT,
        is_protected=True,
        description="Retention run audit history (protected audit trail).",
    ),
]


def get_all_policies() -> List[RetentionPolicyDefinition]:
    """Return all defined retention policies."""
    return list(RETENTION_POLICIES)


get_retention_policies = get_all_policies


def get_active_policies() -> List[RetentionPolicyDefinition]:
    """Return only non-protected policies eligible for deletion passes."""
    return [p for p in RETENTION_POLICIES if not p.is_protected and p.retention_days is not None]


def get_policy_for_table(table_name: str) -> Optional[RetentionPolicyDefinition]:
    """Look up the retention policy definition for a specific table."""
    for p in RETENTION_POLICIES:
        if p.table_name == table_name:
            return p
    return None

