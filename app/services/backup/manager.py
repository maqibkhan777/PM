"""Central backup management orchestrator for SQLite hot backups, gzip compression,
AES-256-GCM encryption, local retention, quota monitoring, and Google Drive synchronization.
"""

import os
import gzip
import json
import uuid
import shutil
import sqlite3
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Union

from app.config.settings import settings
from app.services.backup.crypto import (
    encrypt_file,
    calculate_sha256,
    write_checksum_file,
    BackupCryptoError,
)
from app.services.backup.gdrive import GoogleDriveClient

logger = logging.getLogger(__name__)

BACKUP_QUOTA_WARN_BYTES = 4 * 1024 * 1024 * 1024   # 4 GB
BACKUP_QUOTA_CRIT_BYTES = 5 * 1024 * 1024 * 1024   # 5 GB
DEFAULT_RETENTION_DAYS = 14
METADATA_FILENAME = "latest_backup_metadata.json"


class BackupExecutionError(Exception):
    """Raised when a core local backup operation fails."""
    pass


class BackupManager:
    """Orchestrates SQLite hot backup creation, integrity verification, compression,
    encryption, local retention, quota protection, and Google Drive upload.
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        backup_dir: Optional[str] = None,
        retention_days: int = DEFAULT_RETENTION_DAYS,
        encryption_enabled: Optional[bool] = None,
        encryption_key: Optional[str] = None,
        gdrive_enabled: Optional[bool] = None,
        gdrive_folder_id: Optional[str] = None,
        gdrive_service_account_file: Optional[str] = None,
    ):
        self.db_path = db_path or getattr(settings, "DB_PATH", "data/pm_operations.db")
        self.backup_dir = backup_dir or getattr(settings, "BACKUP_DIR", "/opt/pm/backups")
        self.retention_days = retention_days
        
        # Encryption config
        self.encryption_enabled = (
            encryption_enabled if encryption_enabled is not None 
            else getattr(settings, "BACKUP_ENCRYPTION_ENABLED", True)
        )
        self.encryption_key = (
            encryption_key if encryption_key is not None 
            else getattr(settings, "BACKUP_ENCRYPTION_KEY", None)
        )
        
        # Google Drive config
        self.gdrive_enabled = (
            gdrive_enabled if gdrive_enabled is not None 
            else getattr(settings, "GDRIVE_ENABLED", False)
        )
        self.gdrive_folder_id = (
            gdrive_folder_id if gdrive_folder_id is not None 
            else getattr(settings, "GDRIVE_FOLDER_ID", None)
        )
        self.gdrive_service_account_file = (
            gdrive_service_account_file if gdrive_service_account_file is not None 
            else getattr(settings, "GDRIVE_SERVICE_ACCOUNT_FILE", None)
        )

        self.gdrive_client = GoogleDriveClient(
            service_account_file=self.gdrive_service_account_file,
            folder_id=self.gdrive_folder_id,
            enabled=self.gdrive_enabled,
        )

    def _ensure_backup_dir(self) -> Path:
        """Ensure backup directory exists with restricted permissions."""
        bpath = Path(self.backup_dir)
        bpath.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(bpath, 0o700)
        except Exception:
            pass
        return bpath

    def check_backup_quota(self) -> Dict[str, Any]:
        """Inspect backup directory size and filesystem usage against safety thresholds."""
        bdir = self._ensure_backup_dir()
        total_backup_bytes = 0
        file_count = 0

        for f in bdir.glob("*"):
            if f.is_file():
                file_count += 1
                total_backup_bytes += f.stat().st_size

        total_mb = round(total_backup_bytes / (1024 * 1024), 2)
        total_gb = round(total_backup_bytes / (1024 ** 3), 2)

        # Quota threshold checks
        quota_status = "OK"
        if total_backup_bytes >= BACKUP_QUOTA_CRIT_BYTES:
            quota_status = "CRITICAL"
        elif total_backup_bytes >= BACKUP_QUOTA_WARN_BYTES:
            quota_status = "WARNING"

        # Host filesystem disk usage
        fs_status = "OK"
        fs_used_pct = 0.0
        try:
            tot, used, free = shutil.disk_usage(str(bdir))
            fs_used_pct = round((used / max(tot, 1)) * 100, 1)
            if fs_used_pct >= 90.0:
                fs_status = "EMERGENCY"
            elif fs_used_pct >= 80.0:
                fs_status = "CRITICAL"
            elif fs_used_pct >= 70.0:
                fs_status = "WARNING"
        except Exception:
            pass

        return {
            "quota_status": quota_status,
            "filesystem_status": fs_status,
            "backup_dir": str(bdir),
            "total_backup_bytes": total_backup_bytes,
            "total_backup_mb": total_mb,
            "total_backup_gb": total_gb,
            "backup_file_count": file_count,
            "filesystem_used_percent": fs_used_pct,
            "quota_warning_bytes": BACKUP_QUOTA_WARN_BYTES,
            "quota_critical_bytes": BACKUP_QUOTA_CRIT_BYTES,
        }

    def create_local_backup(self) -> Dict[str, Any]:
        """Perform transaction-safe SQLite hot backup, PRAGMA integrity check,
        gzip compression, optional AES-256-GCM encryption, and SHA-256 checksum generation.
        """
        bdir = self._ensure_backup_dir()
        db_file = Path(self.db_path).resolve()

        if not db_file.is_file():
            raise FileNotFoundError(f"Active SQLite database not found at {db_file}")

        backup_id = f"backup_{uuid.uuid4().hex[:12]}"
        timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        raw_backup_path = bdir / f"pm_operations_backup_{timestamp_str}.db"
        gz_backup_path = bdir / f"pm_operations_backup_{timestamp_str}.db.gz"
        enc_backup_path = bdir / f"pm_operations_backup_{timestamp_str}.db.gz.enc"

        logger.info(f"Starting online hot backup of {db_file} to {raw_backup_path}...")

        # 1. Hot backup using SQLite online backup API (safe under WAL concurrency)
        try:
            src_conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
            dst_conn = sqlite3.connect(str(raw_backup_path))
            with dst_conn:
                src_conn.backup(dst_conn)
            dst_conn.close()
            src_conn.close()
        except Exception as e:
            if raw_backup_path.exists():
                raw_backup_path.unlink()
            raise BackupExecutionError(f"SQLite online hot backup failed: {e}") from e

        # 2. Verify integrity of raw snapshot before compression
        try:
            chk_conn = sqlite3.connect(str(raw_backup_path))
            cursor = chk_conn.cursor()
            cursor.execute("PRAGMA integrity_check;")
            res = cursor.fetchone()
            chk_conn.close()
            if not res or res[0] != "ok":
                raise BackupExecutionError(f"Raw backup failed PRAGMA integrity_check: {res}")
        except Exception as e:
            if raw_backup_path.exists():
                raw_backup_path.unlink()
            raise BackupExecutionError(f"Integrity check failed: {e}") from e

        # 3. Gzip compression
        logger.info(f"Compressing backup to {gz_backup_path}...")
        try:
            with open(raw_backup_path, "rb") as f_in, gzip.open(gz_backup_path, "wb", compresslevel=6) as f_out:
                shutil.copyfileobj(f_in, f_out)
        finally:
            if raw_backup_path.exists():
                raw_backup_path.unlink()

        final_artifact_path = gz_backup_path
        is_encrypted = False
        sha256_hash = ""

        # 4. Authenticated Encryption (if enabled and key configured)
        if self.encryption_enabled:
            if not self.encryption_key:
                logger.warning("Backup encryption enabled but BACKUP_ENCRYPTION_KEY not set. Storing as compressed .gz.")
            else:
                logger.info(f"Encrypting backup artifact to {enc_backup_path} with AES-256-GCM...")
                try:
                    sha256_hash = encrypt_file(
                        src_file=gz_backup_path,
                        dst_file=enc_backup_path,
                        secret_key=self.encryption_key,
                    )
                    final_artifact_path = enc_backup_path
                    is_encrypted = True
                finally:
                    if gz_backup_path.exists():
                        gz_backup_path.unlink()

        if not sha256_hash:
            sha256_hash = calculate_sha256(final_artifact_path)

        # 5. Write .sha256 checksum file covering final artifact
        checksum_file_path = write_checksum_file(final_artifact_path)

        # 6. Apply local retention policy
        purged_count = self.apply_local_retention()

        return {
            "backup_id": backup_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "local_path": str(final_artifact_path),
            "checksum_path": str(checksum_file_path),
            "is_encrypted": is_encrypted,
            "artifact_size_bytes": final_artifact_path.stat().st_size,
            "sha256": sha256_hash,
            "purged_old_backups": purged_count,
        }

    def apply_local_retention(self) -> int:
        """Purge local backup archives older than configured retention days."""
        bdir = self._ensure_backup_dir()
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
        purged = 0

        for ext in ["*.db.gz", "*.db.gz.enc", "*.db.gz.sha256", "*.db.gz.enc.sha256"]:
            for f in bdir.glob(ext):
                try:
                    mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
                    if mtime < cutoff:
                        f.unlink()
                        purged += 1
                        logger.info(f"Purged expired local backup file: {f.name}")
                except Exception as e:
                    logger.warning(f"Error checking retention for {f.name}: {e}")

        return purged

    def save_metadata(self, metadata: Dict[str, Any]) -> Path:
        """Save latest backup metadata record locally."""
        bdir = self._ensure_backup_dir()
        meta_file = bdir / METADATA_FILENAME
        with open(meta_file, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)
        return meta_file

    def get_latest_metadata(self) -> Optional[Dict[str, Any]]:
        """Load latest local backup metadata record if available."""
        meta_file = Path(self.backup_dir) / METADATA_FILENAME
        if meta_file.is_file():
            try:
                with open(meta_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return None
        return None

    def run_backup_pipeline(self) -> Dict[str, Any]:
        """Execute complete Phase 5 backup lifecycle:
        1. Local hot backup + compression + encryption + checksum
        2. Google Drive remote sync (if enabled)
        3. Metadata persistence
        4. Quota check
        
        Google Drive failures are strictly isolated and do NOT fail local backup.
        """
        start_time = datetime.now(timezone.utc)
        local_result = self.create_local_backup()

        gdrive_result = {
            "status": "DISABLED",
            "remote_file_id": None,
            "remote_checksum_file_id": None,
            "error": None,
        }

        # Google Drive Sync (isolated)
        if self.gdrive_enabled:
            if not self.gdrive_client.is_configured():
                logger.warning("Google Drive enabled but credentials/folder not configured. Remote sync skipped.")
                gdrive_result = {
                    "status": "UNCONFIGURED",
                    "error": "Google Drive credentials or folder ID missing.",
                    "remote_file_id": None,
                    "remote_checksum_file_id": None,
                }
            else:
                try:
                    logger.info("Starting Google Drive remote backup sync...")
                    gdrive_result = self.gdrive_client.sync_backup(
                        local_encrypted_path=local_result["local_path"],
                        local_checksum_path=local_result["checksum_path"],
                    )
                except Exception as e:
                    logger.error(f"Unexpected Google Drive error (isolated): {e}")
                    gdrive_result = {
                        "status": "FAILED",
                        "error": str(e),
                        "remote_file_id": None,
                        "remote_checksum_file_id": None,
                    }

        # Quota check
        quota_info = self.check_backup_quota()

        # Metadata record
        meta_record = {
            "backup_id": local_result["backup_id"],
            "created_at": local_result["created_at"],
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "local_path": local_result["local_path"],
            "checksum_path": local_result["checksum_path"],
            "is_encrypted": local_result["is_encrypted"],
            "encrypted_size": local_result["artifact_size_bytes"],
            "sha256": local_result["sha256"],
            "remote_file_id": gdrive_result.get("remote_file_id"),
            "remote_checksum_file_id": gdrive_result.get("remote_checksum_file_id"),
            "upload_status": gdrive_result.get("status"),
            "verification_status": "VERIFIED" if gdrive_result.get("status") == "SUCCESS" else (
                "LOCAL_ONLY" if gdrive_result.get("status") in ["DISABLED", "UNCONFIGURED"] else "FAILED"
            ),
            "verified_at": datetime.now(timezone.utc).isoformat() if gdrive_result.get("status") == "SUCCESS" else None,
            "error_summary": gdrive_result.get("error"),
            "quota": quota_info,
        }

        self.save_metadata(meta_record)
        return meta_record


# Global backup manager singleton
backup_manager = BackupManager()
