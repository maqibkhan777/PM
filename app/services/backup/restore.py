"""Safe SQLite backup restoration and verification engine.

Supports .db, .db.gz, and .db.gz.enc formats with SHA-256 verification,
AES-256-GCM decryption, schema validation, and isolated smoke analysis.
"""

import os
import gzip
import time
import shutil
import sqlite3
import logging
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Union, Tuple

from app.services.backup.crypto import (
    decrypt_file,
    verify_sha256,
    read_checksum_file,
    calculate_sha256,
    BackupCryptoError,
)

logger = logging.getLogger(__name__)

# Critical application tables that must be present in a valid PM database
CRITICAL_PM_TABLES = [
    "events",
    "jira_issue_state",
]


class RestoreError(Exception):
    """Base exception for backup restore operations."""
    pass


class RestoreIntegrityError(RestoreError):
    """Raised when backup data integrity or foreign keys fail verification."""
    pass


class RestoreSchemaError(RestoreError):
    """Raised when restored database lacks critical PM schema tables."""
    pass


class RestoreManager:
    """Handles extracting, decrypting, and verifying SQLite backup archives."""

    def __init__(self, default_encryption_key: Optional[str] = None):
        self.default_encryption_key = default_encryption_key

    def extract_and_verify(
        self,
        backup_file_path: Union[str, Path],
        checksum_file_path: Optional[Union[str, Path]] = None,
        encryption_key: Optional[str] = None,
        target_output_db: Optional[Union[str, Path]] = None,
    ) -> Tuple[Path, Dict[str, Any]]:
        """Extract and verify any supported backup format (.db, .db.gz, .db.gz.enc).
        
        Returns:
            Tuple of (Path to validated raw SQLite .db file, metadata dictionary).
        """
        start_time = time.time()
        src_path = Path(backup_file_path).resolve()
        key = encryption_key or self.default_encryption_key

        if not src_path.is_file():
            raise FileNotFoundError(f"Specified backup file does not exist: {backup_file_path}")

        temp_work_dir = Path(tempfile.mkdtemp(prefix="pm_restore_work_"))
        metadata: Dict[str, Any] = {
            "source_path": str(src_path),
            "source_size_bytes": src_path.stat().st_size,
            "format": "unknown",
            "checksum_verified": False,
            "decrypted": False,
            "decompressed": False,
            "sqlite_integrity": "unknown",
            "foreign_key_integrity": "unknown",
            "schema_verified": False,
            "smoke_phase_a": "unknown",
            "smoke_phase_b": "unknown",
            "extracted_db_path": None,
            "duration_seconds": 0.0,
        }

        try:
            # 1. Checksum verification if checksum file exists or is specified
            expected_chk_path = checksum_file_path or src_path.with_name(f"{src_path.name}.sha256")
            if Path(expected_chk_path).is_file():
                expected_sha = read_checksum_file(expected_chk_path)
                if not verify_sha256(src_path, expected_sha):
                    raise RestoreIntegrityError(
                        f"SHA-256 checksum mismatch for backup file {src_path.name}. File may be corrupted or altered."
                    )
                metadata["checksum_verified"] = True
                metadata["expected_sha256"] = expected_sha

            current_artifact = src_path
            filename = src_path.name.lower()

            # 2. Decrypt if encrypted (.enc)
            if filename.endswith(".enc"):
                metadata["format"] = "encrypted_gzip"
                if not key:
                    raise RestoreError("Backup is encrypted (.enc) but no encryption key was provided.")
                
                decrypted_gz = temp_work_dir / "decrypted.db.gz"
                logger.info(f"Decrypting {src_path.name} with AES-256-GCM...")
                decrypt_file(current_artifact, decrypted_gz, key)
                metadata["decrypted"] = True
                current_artifact = decrypted_gz
                filename = decrypted_gz.name.lower()

            # 3. Decompress if gzipped (.gz)
            if filename.endswith(".gz"):
                if metadata["format"] == "unknown":
                    metadata["format"] = "gzip"
                decompressed_db = temp_work_dir / "restored.db"
                logger.info(f"Decompressing {current_artifact.name}...")
                with gzip.open(current_artifact, "rb") as f_in, open(decompressed_db, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
                metadata["decompressed"] = True
                current_artifact = decompressed_db
            elif metadata["format"] == "unknown":
                metadata["format"] = "raw_sqlite"
                copied_db = temp_work_dir / "restored.db"
                shutil.copyfile(current_artifact, copied_db)
                current_artifact = copied_db

            # 4. Deep SQLite verification
            smoke_results = self.verify_sqlite_database(current_artifact)
            metadata.update(smoke_results)

            if metadata["sqlite_integrity"] != "ok":
                raise RestoreIntegrityError(f"SQLite PRAGMA integrity_check failed: {metadata['sqlite_integrity']}")

            if metadata["foreign_key_integrity"] != "ok":
                raise RestoreIntegrityError(f"SQLite PRAGMA foreign_key_check failed with violations: {metadata['foreign_key_integrity']}")

            if not metadata["schema_verified"]:
                raise RestoreSchemaError("Critical PM database tables missing from restored backup.")

            # 5. Output file handling
            if target_output_db:
                final_dst = Path(target_output_db).resolve()
                final_dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(current_artifact, final_dst)
                metadata["extracted_db_path"] = str(final_dst)
                return final_dst, metadata
            else:
                metadata["extracted_db_path"] = str(current_artifact)
                return current_artifact, metadata

        except Exception:
            # Clean up temp files on error
            shutil.rmtree(temp_work_dir, ignore_errors=True)
            raise
        finally:
            metadata["duration_seconds"] = round(time.time() - start_time, 3)

    def verify_sqlite_database(self, db_path: Union[str, Path]) -> Dict[str, Any]:
        """Execute deep PRAGMA integrity checks, foreign key checks, schema verification,
        and Phase A / Phase B smoke queries on an extracted SQLite database.
        """
        path = Path(db_path)
        if not path.is_file():
            raise FileNotFoundError(f"Database file to verify does not exist: {db_path}")

        conn = sqlite3.connect(str(path))
        cursor = conn.cursor()

        # 1. PRAGMA integrity_check
        cursor.execute("PRAGMA integrity_check;")
        integrity_row = cursor.fetchone()
        integrity_ok = str(integrity_row[0]) if integrity_row else "failed"

        # 2. PRAGMA foreign_key_check
        cursor.execute("PRAGMA foreign_key_check;")
        fk_violations = cursor.fetchall()
        fk_status = "ok" if len(fk_violations) == 0 else f"violations ({len(fk_violations)})"

        # 3. Schema verification
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
        tables = {row[0] for row in cursor.fetchall()}
        missing_tables = [tbl for tbl in CRITICAL_PM_TABLES if tbl not in tables]
        schema_ok = (len(missing_tables) == 0)

        # 4. Phase A Smoke Analysis (historical intelligence / performance tables queryable)
        smoke_phase_a = "SKIPPED"
        try:
            if "historical_jira_tasks" in tables:
                cursor.execute("SELECT COUNT(*) FROM historical_jira_tasks;")
                _ = cursor.fetchone()[0]
                smoke_phase_a = "PASS"
            elif "performance_analysis_runs" in tables or "task_delivery_forecasts" in tables:
                cursor.execute("SELECT COUNT(*) FROM performance_analysis_runs;")
                _ = cursor.fetchone()[0]
                smoke_phase_a = "PASS"
            else:
                smoke_phase_a = "FAIL (missing historical/performance tables)"
        except Exception as e:
            smoke_phase_a = f"FAIL ({e})"

        # 5. Phase B Smoke Analysis (events / state / retention queryable)
        smoke_phase_b = "SKIPPED"
        try:
            if "events" in tables and "jira_issue_state" in tables:
                cursor.execute("SELECT COUNT(*) FROM events;")
                _ = cursor.fetchone()[0]
                cursor.execute("SELECT COUNT(*) FROM jira_issue_state;")
                _ = cursor.fetchone()[0]
                smoke_phase_b = "PASS"
            else:
                smoke_phase_b = "FAIL (missing events/jira_issue_state)"
        except Exception as e:
            smoke_phase_b = f"FAIL ({e})"

        conn.close()

        return {
            "sqlite_integrity": integrity_ok,
            "foreign_key_integrity": fk_status,
            "schema_verified": schema_ok,
            "missing_critical_tables": missing_tables,
            "existing_tables": sorted(list(tables)),
            "smoke_phase_a": smoke_phase_a,
            "smoke_phase_b": smoke_phase_b,
        }

    def safe_production_restore(
        self,
        backup_file_path: Union[str, Path],
        target_production_db: Union[str, Path],
        encryption_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Perform a safety-gated production database swap:
        1. Fully extracts and validates the candidate backup into an isolated temp location.
        2. Creates a verified rollback snapshot of current production DB before touching it.
        3. Swaps the verified database into production.
        4. Verifies the newly installed production DB.
        """
        prod_path = Path(target_production_db).resolve()
        rollback_snapshot_path = None

        # Step 1: Fully extract and verify source backup in isolation
        temp_extracted_db, meta = self.extract_and_verify(
            backup_file_path=backup_file_path,
            encryption_key=encryption_key,
        )

        try:
            # Step 2: Create safety rollback snapshot of active production DB if it exists
            if prod_path.is_file():
                timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                rollback_snapshot_path = prod_path.parent / f"pm_operations_pre_restore_{timestamp_str}.db"
                logger.info(f"Creating pre-restore rollback snapshot at {rollback_snapshot_path}...")
                
                # Transaction-safe online copy of active database
                src_conn = sqlite3.connect(f"file:{prod_path}?mode=ro", uri=True)
                dst_conn = sqlite3.connect(str(rollback_snapshot_path))
                with dst_conn:
                    src_conn.backup(dst_conn)
                dst_conn.close()
                src_conn.close()

                # Verify rollback snapshot
                rb_check = self.verify_sqlite_database(rollback_snapshot_path)
                if rb_check["sqlite_integrity"] != "ok":
                    logger.warning(f"Rollback snapshot integrity check returned: {rb_check['sqlite_integrity']}")

            # Step 3: Remove lingering WAL/SHM and swap restored DB into target
            wal_file = Path(f"{prod_path}-wal")
            shm_file = Path(f"{prod_path}-shm")
            if wal_file.exists():
                wal_file.unlink()
            if shm_file.exists():
                shm_file.unlink()

            shutil.copyfile(temp_extracted_db, prod_path)
            try:
                os.chmod(prod_path, 0o644)
            except Exception:
                pass

            # Step 4: Verify installed production database
            post_check = self.verify_sqlite_database(prod_path)
            if post_check["sqlite_integrity"] != "ok":
                # Rollback on critical swap failure
                if rollback_snapshot_path and rollback_snapshot_path.is_file():
                    logger.error("Post-restore integrity failed! Rolling back to snapshot...")
                    shutil.copyfile(rollback_snapshot_path, prod_path)
                raise RestoreIntegrityError("Post-restore production DB integrity check failed. Rolled back.")

            meta["production_restored"] = True
            meta["rollback_snapshot_path"] = str(rollback_snapshot_path) if rollback_snapshot_path else None
            meta["status"] = "SUCCESS"
            return meta

        finally:
            # Cleanup temp extracted DB parent directory
            if temp_extracted_db.parent.name.startswith("pm_restore_work_"):
                shutil.rmtree(temp_extracted_db.parent, ignore_errors=True)


# Global restore manager singleton
restore_manager = RestoreManager()
