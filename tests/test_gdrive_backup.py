"""Tests for Google Drive cloud backup, staging upload, verification, atomic replacement, and failure isolation."""

import os
import json
import sqlite3
import pytest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.services.backup.gdrive import (
    GoogleDriveClient,
    GoogleDriveError,
    GoogleDriveConfigError,
    GoogleDriveVerificationError,
    sanitize_error,
    DEFAULT_BACKUP_FILENAME,
    DEFAULT_CHECKSUM_FILENAME,
)
from app.services.backup.manager import BackupManager
from app.services.backup.crypto import encrypt_file, write_checksum_file, calculate_sha256


@pytest.fixture
def temp_env():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        db_file = tmp_path / "pm_operations.db"
        backup_dir = tmp_path / "backups"
        sa_file = tmp_path / "service_account.json"

        # Create minimal valid sqlite database
        conn = sqlite3.connect(str(db_file))
        conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, name TEXT);")
        conn.execute("INSERT INTO events (name) VALUES ('test_event');")
        conn.commit()
        conn.close()

        # Create dummy service account json
        sa_file.write_text(json.dumps({
            "type": "service_account",
            "project_id": "test-pm-project",
            "private_key_id": "12345",
            "private_key": "-----BEGIN PRIVATE KEY-----\nMIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQC3\n-----END PRIVATE KEY-----\n",
            "client_email": "pm-backup@test-pm-project.iam.gserviceaccount.com",
            "client_id": "98765",
        }))

        yield {
            "root": tmp_path,
            "db_file": db_file,
            "backup_dir": backup_dir,
            "sa_file": sa_file,
        }


def test_gdrive_client_is_configured_logic(temp_env):
    """Verify Google Drive client configuration requirements."""
    # 1. Disabled
    client1 = GoogleDriveClient(enabled=False)
    assert client1.is_configured() is False

    # 2. Enabled but missing folder ID
    client2 = GoogleDriveClient(
        service_account_file=str(temp_env["sa_file"]),
        folder_id="",
        enabled=True,
    )
    assert client2.is_configured() is False

    # 3. Enabled with folder ID and valid service account file
    client3 = GoogleDriveClient(
        service_account_file=str(temp_env["sa_file"]),
        folder_id="1a2b3c4d_folder_id",
        enabled=True,
    )
    assert client3.is_configured() is True


def test_sanitize_error_redacts_private_keys():
    """Verify that private keys and auth tokens are stripped from error logs."""
    raw_error = "API error with key -----BEGIN PRIVATE KEY-----\nSecretKey123\n-----END PRIVATE KEY-----"
    sanitized = sanitize_error(raw_error)
    assert "BEGIN PRIVATE KEY" not in sanitized
    assert "SecretKey123" not in sanitized
    assert "redacted" in sanitized


def test_backup_manager_runs_with_gdrive_disabled(temp_env):
    """Verify that when Google Drive is disabled, local backup succeeds and status is DISABLED."""
    manager = BackupManager(
        db_path=str(temp_env["db_file"]),
        backup_dir=str(temp_env["backup_dir"]),
        encryption_enabled=True,
        encryption_key="test-key-2026",
        gdrive_enabled=False,
    )

    result = manager.run_backup_pipeline()
    assert result["upload_status"] == "DISABLED"
    assert result["verification_status"] == "LOCAL_ONLY"
    assert Path(result["local_path"]).is_file()
    assert Path(result["checksum_path"]).is_file()


def test_gdrive_atomic_sync_success_workflow(temp_env):
    """Verify complete mock Google Drive upload, verification, promotion, and old backup deletion."""
    client = GoogleDriveClient(
        service_account_file=str(temp_env["sa_file"]),
        folder_id="target_folder_123",
        enabled=True,
    )

    # Create dummy local encrypted backup & checksum
    local_enc = temp_env["root"] / "test.db.gz.enc"
    local_enc.write_bytes(b"EncryptedBackupBytes1234567890")
    local_chk = temp_env["root"] / "test.db.gz.enc.sha256"
    local_chk.write_text(f"{calculate_sha256(local_enc)}  test.db.gz.enc\n")

    # Mock Drive service interactions
    old_backup_id = "old_canonical_backup_id_001"
    old_chk_id = "old_canonical_chk_id_002"
    new_staging_backup_id = "new_staging_backup_id_101"
    new_staging_chk_id = "new_staging_chk_id_102"

    with patch.object(client, "find_files") as mock_find, \
         patch.object(client, "upload_file") as mock_upload, \
         patch.object(client, "get_file_metadata") as mock_get_meta, \
         patch.object(client, "download_file") as mock_download, \
         patch.object(client, "rename_file") as mock_rename, \
         patch.object(client, "delete_file") as mock_delete:

        # Step 1: find existing files
        def find_side_effect(filename, folder_id=None):
            if filename == DEFAULT_BACKUP_FILENAME:
                return [{"id": old_backup_id, "name": DEFAULT_BACKUP_FILENAME}]
            elif filename == DEFAULT_CHECKSUM_FILENAME:
                return [{"id": old_chk_id, "name": DEFAULT_CHECKSUM_FILENAME}]
            return []
        mock_find.side_effect = find_side_effect

        # Step 2: upload staging files
        def upload_side_effect(local_path, remote_name, folder_id=None, mime_type=None):
            if "sha256" in remote_name:
                return {"id": new_staging_chk_id, "name": remote_name}
            return {"id": new_staging_backup_id, "name": remote_name}
        mock_upload.side_effect = upload_side_effect

        # Step 3: remote size check
        mock_get_meta.return_value = {"id": new_staging_backup_id, "size": local_enc.stat().st_size}

        # Step 4: download staging file for sha256 verification
        def download_side_effect(file_id, dst_path):
            Path(dst_path).write_bytes(local_enc.read_bytes())
            return Path(dst_path)
        mock_download.side_effect = download_side_effect

        # Execute sync
        res = client.sync_backup(local_enc, local_chk)

        assert res["status"] == "SUCCESS"
        assert res["remote_file_id"] == new_staging_backup_id
        assert res["remote_checksum_file_id"] == new_staging_chk_id

        # Verify rename (promotion) called for staging files
        mock_rename.assert_any_call(new_staging_backup_id, DEFAULT_BACKUP_FILENAME)
        mock_rename.assert_any_call(new_staging_chk_id, DEFAULT_CHECKSUM_FILENAME)

        # Verify old files deleted ONLY after promotion
        mock_delete.assert_any_call(old_backup_id)
        mock_delete.assert_any_call(old_chk_id)


def test_gdrive_sync_size_mismatch_aborts_and_preserves_old(temp_env):
    """Verify that remote size mismatch aborts promotion and does NOT delete old backups."""
    client = GoogleDriveClient(
        service_account_file=str(temp_env["sa_file"]),
        folder_id="target_folder_123",
        enabled=True,
    )

    local_enc = temp_env["root"] / "test.db.gz.enc"
    local_enc.write_bytes(b"EncryptedBackupBytes12345")
    local_chk = temp_env["root"] / "test.db.gz.enc.sha256"
    local_chk.write_text(f"{calculate_sha256(local_enc)}  test.db.gz.enc\n")

    old_backup_id = "old_backup_id_001"
    staging_id = "staging_id_101"

    with patch.object(client, "find_files") as mock_find, \
         patch.object(client, "upload_file") as mock_upload, \
         patch.object(client, "get_file_metadata") as mock_get_meta, \
         patch.object(client, "rename_file") as mock_rename, \
         patch.object(client, "delete_file") as mock_delete:

        mock_find.return_value = [{"id": old_backup_id, "name": DEFAULT_BACKUP_FILENAME}]
        mock_upload.return_value = {"id": staging_id}
        # Simulate wrong remote size
        mock_get_meta.return_value = {"id": staging_id, "size": 999999}

        res = client.sync_backup(local_enc, local_chk)

        assert res["status"] == "FAILED"
        assert "size mismatch" in res["error"]

        # Rename was NEVER called
        mock_rename.assert_not_called()

        # Old backup was NOT deleted
        for call in mock_delete.mock_calls:
            assert old_backup_id not in str(call)

        # Staging file was cleaned up
        mock_delete.assert_any_call(staging_id)


def test_gdrive_api_exception_isolated_from_pm(temp_env):
    """Verify that a network failure or 500 error in Google Drive does not crash BackupManager."""
    manager = BackupManager(
        db_path=str(temp_env["db_file"]),
        backup_dir=str(temp_env["backup_dir"]),
        encryption_enabled=True,
        encryption_key="test-key-2026",
        gdrive_enabled=True,
        gdrive_folder_id="folder_123",
        gdrive_service_account_file=str(temp_env["sa_file"]),
    )

    # Simulate network crash during sync_backup
    with patch.object(manager.gdrive_client, "sync_backup", side_effect=Exception("Connection reset by peer")):
        result = manager.run_backup_pipeline()

        # Local backup still succeeds
        assert Path(result["local_path"]).is_file()
        assert Path(result["checksum_path"]).is_file()
        # Google Drive status recorded as FAILED
        assert result["upload_status"] == "FAILED"
        assert "Connection reset by peer" in result["error_summary"]
        assert result["verification_status"] == "FAILED"
