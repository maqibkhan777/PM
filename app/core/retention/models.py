"""Data models, enums, and structures for the centralized Retention Service."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class RetentionClass(str, Enum):
    """Authoritative retention classification categories."""
    RAW_OPERATIONAL = "RAW_OPERATIONAL"
    CURRENT_STATE = "CURRENT_STATE"
    ANALYTICAL = "ANALYTICAL"
    SECURITY_AUDIT = "SECURITY_AUDIT"
    CONFIGURATION = "CONFIGURATION"
    TRANSIENT = "TRANSIENT"


class RetentionScope(str, Enum):
    """Execution scopes for retention operations."""
    DAILY = "DAILY"
    WEEKLY_MONDAY = "WEEKLY_MONDAY"
    MANUAL = "MANUAL"


@dataclass
class RetentionPolicyDefinition:
    """Authoritative policy specification for a database table."""
    table_name: str
    retention_class: RetentionClass
    timestamp_column: Optional[str] = None
    retention_days: Optional[int] = None
    id_column: str = "id"
    is_protected: bool = False
    eligibility_predicate: Optional[str] = None  # SQL condition (e.g. "processing_status = 'PROCESSED'")
    parent_table: Optional[str] = None
    parent_fk_column: Optional[str] = None
    description: str = ""


@dataclass
class RetentionTableResult:
    """Execution result for a single database table in a retention pass."""
    table_name: str
    retention_class: RetentionClass
    eligible_rows: int = 0
    deleted_rows: int = 0
    status: str = "SUCCESS"  # SUCCESS, FAILED, SKIPPED_PROTECTED
    error: Optional[str] = None
    duration_ms: int = 0


@dataclass
class RetentionRunResult:
    """Comprehensive summary record of a completed retention run."""
    run_id: str
    started_at: str
    completed_at: str
    policy_version: str = "1.0.0"
    dry_run: bool = False
    scope: RetentionScope = RetentionScope.MANUAL
    total_rows_deleted: int = 0
    total_tables_processed: int = 0
    status: str = "COMPLETED"  # COMPLETED, PARTIAL_FAILURE, FAILED
    error_summary: Optional[str] = None
    execution_duration_ms: int = 0
    table_results: List[RetentionTableResult] = field(default_factory=list)

    @property
    def total_deleted_rows(self) -> int:
        return self.total_rows_deleted

    @property
    def rows_deleted(self) -> int:
        return self.total_rows_deleted

