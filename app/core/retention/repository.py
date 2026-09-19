"""Repository for retention run audit logging and chunked deletion operations."""

import sqlite3
import uuid
from typing import Any, Dict, List, Optional
from app.core.retention.models import RetentionPolicyDefinition, RetentionRunResult
from app.database.connection import db_manager, DatabaseManager
from app.utils.logger import logger
from app.utils.time import utc_now_iso


class RetentionRepository:
    """Handles execution and persistence of retention run records and bounded chunked deletions."""

    def __init__(self, manager: Optional[DatabaseManager] = None):
        self.mgr = manager or db_manager

    def record_run(self, run: RetentionRunResult) -> None:
        """Persist a completed retention run record into retention_run_history."""
        rec_id = str(uuid.uuid4())
        with self.mgr.session() as conn:
            conn.execute(
                """
                INSERT INTO retention_run_history (
                    id, run_id, started_at, completed_at, policy_version,
                    dry_run, rows_deleted, tables_processed, status,
                    error_summary, execution_duration_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rec_id,
                    run.run_id,
                    run.started_at,
                    run.completed_at,
                    run.policy_version,
                    1 if run.dry_run else 0,
                    run.total_rows_deleted,
                    run.total_tables_processed,
                    run.status,
                    run.error_summary,
                    run.execution_duration_ms,
                ),
            )

    def get_last_run(self) -> Optional[Dict[str, Any]]:
        """Retrieve the most recent retention run record."""
        with self.mgr.session() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM retention_run_history
                ORDER BY started_at DESC LIMIT 1
                """
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_last_successful_run(self) -> Optional[Dict[str, Any]]:
        """Retrieve the most recent successful, live retention run record."""
        with self.mgr.session() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM retention_run_history
                WHERE status = 'COMPLETED' AND dry_run = 0
                ORDER BY started_at DESC LIMIT 1
                """
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_run_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieve historical retention runs."""
        with self.mgr.session() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM retention_run_history
                ORDER BY started_at DESC LIMIT ?
                """,
                (limit,),
            )
            return [dict(r) for r in cursor.fetchall()]

    def count_eligible_rows(
        self,
        policy: RetentionPolicyDefinition,
        cutoff_iso: str
    ) -> int:
        """Count rows in a table eligible for retention deletion under the policy and cutoff."""
        if policy.is_protected or not policy.timestamp_column:
            return 0

        where_parts = [f"{policy.timestamp_column} < ?"]
        params = [cutoff_iso]

        if policy.eligibility_predicate:
            where_parts.append(f"({policy.eligibility_predicate})")

        where_clause = " AND ".join(where_parts)
        query = f"SELECT COUNT(*) FROM {policy.table_name} WHERE {where_clause}"

        with self.mgr.session() as conn:
            cursor = conn.execute(query, tuple(params))
            row = cursor.fetchone()
            return int(row[0]) if row else 0

    def delete_chunked(
        self,
        policy: RetentionPolicyDefinition,
        cutoff_iso: str,
        batch_size: int = 500,
        chunk_limit: Optional[int] = None,
        chunk_size: Optional[int] = None,
    ) -> int:
        effective_batch_size = chunk_limit or chunk_size or batch_size
        """Execute bounded chunked deletion in short transactions to prevent long SQLite locks.

        Repeats:
            DELETE FROM table
            WHERE id IN (
                SELECT id FROM table
                WHERE timestamp < cutoff AND eligibility_predicate
                LIMIT batch_size
            )
        until 0 rows are affected.
        """
        if policy.is_protected or not policy.timestamp_column:
            logger.info(f"Skipping chunked delete on protected table '{policy.table_name}'.")
            return 0

        where_parts = [f"{policy.timestamp_column} < ?"]
        params = [cutoff_iso]

        if policy.eligibility_predicate:
            where_parts.append(f"({policy.eligibility_predicate})")

        where_clause = " AND ".join(where_parts)
        id_col = policy.id_column

        delete_query = f"""
            DELETE FROM {policy.table_name}
            WHERE {id_col} IN (
                SELECT {id_col} FROM {policy.table_name}
                WHERE {where_clause}
                LIMIT ?
            )
        """

        total_deleted = 0
        iteration = 0

        while True:
            iteration += 1
            batch_params = tuple(params + [effective_batch_size])
            with self.mgr.session() as conn:
                cursor = conn.execute(delete_query, batch_params)
                deleted_in_batch = cursor.rowcount

            if deleted_in_batch <= 0:
                break

            total_deleted += deleted_in_batch
            logger.debug(
                f"Retention [{policy.table_name}]: Batch {iteration} deleted {deleted_in_batch} rows "
                f"(total so far: {total_deleted})."
            )

        return total_deleted
