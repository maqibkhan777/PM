"""Centralized Retention Service orchestrating deterministic, chunked data lifecycle cleanup."""

from datetime import datetime, timedelta, timezone
import time
import uuid
import zoneinfo
from typing import Any, Dict, List, Optional

from app.core.retention.models import (
    RetentionClass,
    RetentionPolicyDefinition,
    RetentionRunResult,
    RetentionScope,
    RetentionTableResult,
)
from app.core.retention.policy import (
    RETENTION_POLICIES,
    get_active_policies,
    get_all_policies,
    get_policy_for_table,
)
from app.core.retention.repository import RetentionRepository
from app.database.connection import db_manager, DatabaseManager
from app.utils.logger import logger
from app.utils.time import utc_now, utc_now_iso, format_iso


class RetentionService:
    """Authoritative centralized retention management service.

    Enforces deterministic data retention policies across all storage categories:
    - RAW_OPERATIONAL: 7-day Monday-to-Monday Asia/Karachi boundary on processed events.
    - ANALYTICAL: 30-day bounded retention on Phase A/B generated runs and snapshots.
    - SECURITY_AUDIT: 90-day retention on audit logs and completed/terminal actions.
    - TRANSIENT: 30-day retention on notification cooldown deduplication records.
    - CURRENT_STATE / CONFIGURATION: Strictly protected 365-day canonical history and settings.
    """

    def __init__(
        self,
        manager: Optional[DatabaseManager] = None,
        db_manager: Optional[DatabaseManager] = None,
        repository: Optional[RetentionRepository] = None,
        repo: Optional[RetentionRepository] = None,
    ):
        self.mgr = manager or db_manager or globals()["db_manager"]
        self.repo = repository or repo or RetentionRepository(self.mgr)
        self.repository = self.repo
        self.tz_business = zoneinfo.ZoneInfo("Asia/Karachi")

    def compute_monday_raw_boundary(
        self,
        now_utc: Optional[datetime] = None,
        days: int = 7,
    ) -> str:
        """Calculate the authoritative Monday-to-Monday boundary for raw events.

        Business timezone: Asia/Karachi (PKT).
        Maintenance boundary:
            current_pkt minus days, normalized to Monday 00:00:00 PKT,
            then converted to UTC ISO-8601 string for database comparison.
        """
        now = now_utc or utc_now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        pkt_now = now.astimezone(self.tz_business)

        # Go back `days` days
        prev_target = pkt_now - timedelta(days=days)
        # Normalize to Monday (weekday 0) at 00:00:00
        days_since_monday = prev_target.weekday()
        last_monday_pkt = prev_target - timedelta(days=days_since_monday)
        last_monday_midnight_pkt = last_monday_pkt.replace(hour=0, minute=0, second=0, microsecond=0)

        # Convert back to UTC ISO string
        utc_boundary = last_monday_midnight_pkt.astimezone(timezone.utc)
        return format_iso(utc_boundary)

    def compute_cutoff_for_days(self, days: int, now_utc: Optional[datetime] = None) -> str:
        """Calculate standard UTC ISO timestamp cutoff for a given retention period in days."""
        now = now_utc or utc_now()
        cutoff_dt = now - timedelta(days=days)
        return format_iso(cutoff_dt)

    def execute(
        self,
        dry_run: bool = False,
        policies: Optional[List[RetentionPolicyDefinition]] = None,
        scope: RetentionScope = RetentionScope.MANUAL,
        batch_size: int = 500,
    ) -> RetentionRunResult:
        """Execute a retention pass across the specified policies.

        Args:
            dry_run: If True, calculates eligible rows without modifying database records.
            policies: List of policies to execute (defaults to all active non-protected policies).
            scope: Execution scope identifier for auditability.
            batch_size: Batch size for bounded chunked deletion (default 500).
        """
        run_id = f"ret_{uuid.uuid4().hex[:12]}"
        started_at = utc_now_iso()
        start_mono = time.monotonic()

        target_policies = policies if policies is not None else get_active_policies()
        logger.info(
            f"Retention run started (run_id={run_id}, dry_run={dry_run}, "
            f"scope={scope.value}, target_tables={len(target_policies)})."
        )

        table_results: List[RetentionTableResult] = []
        total_rows_deleted = 0
        total_tables_processed = 0
        has_errors = False
        error_messages: List[str] = []

        for policy in target_policies:
            table_start_mono = time.monotonic()
            tbl_name = policy.table_name

            # Protected table safeguard
            if policy.is_protected or not policy.timestamp_column or policy.retention_days is None:
                table_results.append(
                    RetentionTableResult(
                        table_name=tbl_name,
                        retention_class=policy.retention_class,
                        eligible_rows=0,
                        deleted_rows=0,
                        status="SKIPPED_PROTECTED",
                        duration_ms=0,
                    )
                )
                continue

            try:
                # Compute cutoff timestamp for table
                if tbl_name == "events":
                    cutoff_iso = self.compute_monday_raw_boundary()
                else:
                    cutoff_iso = self.compute_cutoff_for_days(policy.retention_days)

                # Count eligible rows
                eligible_count = self.repo.count_eligible_rows(policy, cutoff_iso)
                deleted_count = 0

                if not dry_run and eligible_count > 0:
                    deleted_count = self.repo.delete_chunked(
                        policy=policy,
                        cutoff_iso=cutoff_iso,
                        batch_size=batch_size,
                    )
                    total_rows_deleted += deleted_count

                table_duration_ms = int((time.monotonic() - table_start_mono) * 1000)
                total_tables_processed += 1

                table_results.append(
                    RetentionTableResult(
                        table_name=tbl_name,
                        retention_class=policy.retention_class,
                        eligible_rows=eligible_count,
                        deleted_rows=deleted_count if not dry_run else 0,
                        status="SUCCESS",
                        duration_ms=table_duration_ms,
                    )
                )
                logger.info(
                    f"Retention [{tbl_name}]: eligible={eligible_count}, "
                    f"deleted={deleted_count if not dry_run else 0} (dry_run={dry_run}, "
                    f"cutoff={cutoff_iso}, duration={table_duration_ms}ms)."
                )

            except Exception as e:
                has_errors = True
                err_msg = f"{tbl_name}: {str(e)}"
                error_messages.append(err_msg)
                table_duration_ms = int((time.monotonic() - table_start_mono) * 1000)
                table_results.append(
                    RetentionTableResult(
                        table_name=tbl_name,
                        retention_class=policy.retention_class,
                        status="FAILED",
                        error=str(e),
                        duration_ms=table_duration_ms,
                    )
                )
                logger.error(f"Retention failed for table '{tbl_name}': {e}", exc_info=True)

        completed_at = utc_now_iso()
        total_duration_ms = int((time.monotonic() - start_mono) * 1000)
        overall_status = "COMPLETED" if not has_errors else ("PARTIAL_FAILURE" if total_tables_processed > 0 else "FAILED")
        error_summary = "; ".join(error_messages) if error_messages else None

        result = RetentionRunResult(
            run_id=run_id,
            started_at=started_at,
            completed_at=completed_at,
            policy_version="1.0.0",
            dry_run=dry_run,
            scope=scope,
            total_rows_deleted=total_rows_deleted,
            total_tables_processed=total_tables_processed,
            status=overall_status,
            error_summary=error_summary,
            execution_duration_ms=total_duration_ms,
            table_results=table_results,
        )

        # Audit live and dry runs persistently
        try:
            self.repo.record_run(result)
        except Exception as audit_err:
            logger.warning(f"Could not persist retention_run_history: {audit_err}")

        logger.info(
            f"Retention run finished (run_id={run_id}, status={overall_status}, "
            f"rows_deleted={total_rows_deleted}, duration={total_duration_ms}ms)."
        )
        return result

    def run_daily_maintenance(self, dry_run: bool = False) -> RetentionRunResult:
        """Run daily lightweight retention pass.

        Prunes:
        - 30-day analytical tables (Phase A & B analysis child tables + runs).
        - 90-day historical evidence & daily report history.
        - 90-day security audit logs & completed terminal actions.
        - 30-day transient notification records.
        """
        daily_policies = [
            p for p in get_active_policies()
            if p.table_name != "events"  # Events are handled in Monday maintenance
        ]
        return self.execute(
            dry_run=dry_run,
            policies=daily_policies,
            scope=RetentionScope.DAILY,
        )

    def run_weekly_monday_maintenance(self, dry_run: bool = False) -> RetentionRunResult:
        """Run Monday weekly raw operational maintenance pass.

        Prunes processed events older than the Asia/Karachi Monday 00:00:00 boundary (max 7 days).
        """
        events_policy = get_policy_for_table("events")
        target_policies = [events_policy] if events_policy else []
        return self.execute(
            dry_run=dry_run,
            policies=target_policies,
            scope=RetentionScope.WEEKLY_MONDAY,
        )

    def get_last_run(self) -> Optional[Dict[str, Any]]:
        """Retrieve latest retention run status."""
        return self.repo.get_last_run()

    def get_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieve historical retention executions."""
        return self.repo.get_run_history(limit=limit)

    def get_next_maintenance_windows(self) -> Dict[str, Any]:
        """Compute the next scheduled daily and weekly maintenance windows in Asia/Karachi and UTC."""
        now_pkt = utc_now().astimezone(self.tz_business)

        # Daily window: next 04:00 AM PKT
        next_daily_pkt = now_pkt.replace(hour=4, minute=0, second=0, microsecond=0)
        if next_daily_pkt <= now_pkt:
            next_daily_pkt += timedelta(days=1)

        # Monday weekly window: next Monday at 03:00 AM PKT
        days_ahead = (0 - now_pkt.weekday()) % 7
        next_monday_pkt = now_pkt.replace(hour=3, minute=0, second=0, microsecond=0) + timedelta(days=days_ahead)
        if next_monday_pkt <= now_pkt:
            next_monday_pkt += timedelta(days=7)

        return {
            "timezone": "Asia/Karachi",
            "next_daily_maintenance_pkt": next_daily_pkt.isoformat(),
            "next_daily_maintenance_utc": format_iso(next_daily_pkt.astimezone(timezone.utc)),
            "next_weekly_monday_maintenance_pkt": next_monday_pkt.isoformat(),
            "next_weekly_monday_maintenance_utc": format_iso(next_monday_pkt.astimezone(timezone.utc)),
        }


# Global retention service instance
retention_service = RetentionService()

