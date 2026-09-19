"""Tests for backup verification, multi-format restore (.db, .db.gz, .db.gz.enc),
schema validation, foreign key checks, Phase A/B smoke analysis, and rollback safety.
"""

import os
import gzip
import sqlite3
import pytest
import tempfile
from pathlib import Path

from app.services.backup.crypto import encrypt_file, write_checksum_file, calculate_sha256
from app.services.backup.restore import (
    RestoreManager,
    RestoreError,
    RestoreIntegrityError,
    RestoreSchemaError,
)


@pytest.fixture
def temp_workspace():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        db_file = tmp_path / "valid_pm_operations.db"

        # Create valid SQLite PM schema
        conn = sqlite3.connect(str(db_file))
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("""
            CREATE TABLE events (
                id INTEGER PRIMARY KEY,
                event_type TEXT NOT NULL,
                created_at TEXT
            );
        """)
        conn.execute("""
            CREATE TABLE jira_issue_state (
                issue_key TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                assignee_account_id TEXT,
                updated_at TEXT
            );
        """)
        conn.execute("""
            CREATE TABLE retention_execution_history (
                run_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                rows_deleted INTEGER DEFAULT 0
            );
        """)
        conn.execute("""
            CREATE TABLE historical_jira_tasks (
                task_key TEXT PRIMARY KEY,
                summary TEXT,
                status TEXT,
                logged_hours REAL DEFAULT 0.0
            );
        """)
        conn.execute("""
            CREATE TABLE action_audit_log (
                id INTEGER PRIMARY KEY,
                action_type TEXT NOT NULL,
                status TEXT
            );
        """)

        # Insert valid test data
        conn.execute("INSERT INTO events (event_type, created_at) VALUES ('issue_created', '2026-09-19T12:00:00Z');")
        conn.execute("INSERT INTO jira_issue_state (issue_key, status) VALUES ('PROJ-101', 'In Progress');")
        conn.execute("INSERT INTO historical_jira_tasks (task_key, summary, logged_hours) VALUES ('PROJ-101', 'Test Task', 4.5);")
        conn.execute("INSERT INTO retention_execution_history (run_id, status) VALUES ('run_001', 'COMPLETED');")
        conn.commit()
        conn.close()

        yield {
            "root": tmp_path,
            "db_file": db_file,
        }


def test_restore_plain_sqlite_db(temp_workspace):
    """Verify restoring and validating a plain uncompressed .db file."""
    manager = RestoreManager()
    output_db = temp_workspace["root"] / "restored_plain.db"

    extracted_db, meta = manager.extract_and_verify(
        backup_file_path=temp_workspace["db_file"],
        target_output_db=output_db,
    )

    assert Path(extracted_db).is_file()
    assert meta["format"] == "raw_sqlite"
    assert meta["sqlite_integrity"] == "ok"
    assert meta["foreign_key_integrity"] == "ok"
    assert meta["schema_verified"] is True
    assert meta["smoke_phase_a"] == "PASS"
    assert meta["smoke_phase_b"] == "PASS"


def test_restore_gzipped_db(temp_workspace):
    """Verify restoring and validating a .db.gz archive."""
    manager = RestoreManager()
    gz_file = temp_workspace["root"] / "backup.db.gz"

    with open(temp_workspace["db_file"], "rb") as f_in, gzip.open(gz_file, "wb") as f_out:
        f_out.write(f_in.read())

    output_db = temp_workspace["root"] / "restored_gz.db"
    extracted_db, meta = manager.extract_and_verify(
        backup_file_path=gz_file,
        target_output_db=output_db,
    )

    assert Path(extracted_db).is_file()
    assert meta["format"] == "gzip"
    assert meta["decompressed"] is True
    assert meta["sqlite_integrity"] == "ok"
    assert meta["schema_verified"] is True


def test_restore_encrypted_db_with_checksum(temp_workspace):
    """Verify restoring an authenticated AES-256-GCM encrypted .db.gz.enc archive with .sha256 checksum."""
    manager = RestoreManager()
    passphrase = "SecurePassphraseRestore2026!"

    # 1. Gzip
    gz_file = temp_workspace["root"] / "backup.db.gz"
    with open(temp_workspace["db_file"], "rb") as f_in, gzip.open(gz_file, "wb") as f_out:
        f_out.write(f_in.read())

    # 2. Encrypt
    enc_file = temp_workspace["root"] / "backup.db.gz.enc"
    encrypt_file(gz_file, enc_file, passphrase)

    # 3. Checksum
    chk_file = write_checksum_file(enc_file)

    # 4. Extract and verify
    output_db = temp_workspace["root"] / "restored_enc.db"
    extracted_db, meta = manager.extract_and_verify(
        backup_file_path=enc_file,
        checksum_file_path=chk_file,
        encryption_key=passphrase,
        target_output_db=output_db,
    )

    assert Path(extracted_db).is_file()
    assert meta["format"] == "encrypted_gzip"
    assert meta["checksum_verified"] is True
    assert meta["decrypted"] is True
    assert meta["decompressed"] is True
    assert meta["sqlite_integrity"] == "ok"
    assert meta["schema_verified"] is True
    assert meta["smoke_phase_a"] == "PASS"
    assert meta["smoke_phase_b"] == "PASS"


def test_restore_checksum_mismatch_raises(temp_workspace):
    """Verify that SHA-256 checksum mismatch aborts restore immediately."""
    manager = RestoreManager()
    src_db = temp_workspace["db_file"]
    chk_file = temp_workspace["root"] / "bad.sha256"
    chk_file.write_text("0" * 64 + "  valid_pm_operations.db\n")

    with pytest.raises(RestoreIntegrityError, match="SHA-256 checksum mismatch"):
        manager.extract_and_verify(
            backup_file_path=src_db,
            checksum_file_path=chk_file,
        )


def test_restore_wrong_encryption_key_raises(temp_workspace):
    """Verify that wrong decryption key raises an exception and does not produce database."""
    manager = RestoreManager()
    enc_file = temp_workspace["root"] / "backup.db.gz.enc"
    encrypt_file(temp_workspace["db_file"], enc_file, "correct-key")

    with pytest.raises(Exception):
        manager.extract_and_verify(
            backup_file_path=enc_file,
            encryption_key="wrong-key",
        )


def test_restore_missing_schema_tables_raises(temp_workspace):
    """Verify that a database missing critical PM tables is rejected."""
    manager = RestoreManager()
    incomplete_db = temp_workspace["root"] / "incomplete.db"
    
    conn = sqlite3.connect(str(incomplete_db))
    conn.execute("CREATE TABLE some_random_table (id INT);")
    conn.commit()
    conn.close()

    with pytest.raises(RestoreSchemaError, match="Critical PM database tables missing"):
        manager.extract_and_verify(backup_file_path=incomplete_db)


def test_restore_foreign_key_violation_raises(temp_workspace):
    """Verify that a database with active foreign key violations is rejected."""
    manager = RestoreManager()
    fk_violating_db = temp_workspace["root"] / "fk_bad.db"

    conn = sqlite3.connect(str(fk_violating_db))
    conn.execute("PRAGMA foreign_keys = OFF;")
    conn.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY);")
    conn.execute("CREATE TABLE child (id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES parent(id));")
    conn.execute("CREATE TABLE events (id INT);")
    conn.execute("CREATE TABLE jira_issue_state (id INT);")
    conn.execute("CREATE TABLE retention_execution_history (id INT);")
    conn.execute("CREATE TABLE historical_jira_tasks (id INT);")
    conn.execute("CREATE TABLE action_audit_log (id INT);")
    # Insert orphan child
    conn.execute("INSERT INTO child (id, parent_id) VALUES (1, 999);")
    conn.commit()
    conn.close()

    with pytest.raises(RestoreIntegrityError, match="foreign_key_check failed with violations"):
        manager.extract_and_verify(backup_file_path=fk_violating_db)


def test_safe_production_restore_and_rollback_snapshot(temp_workspace):
    """Verify safe production database swap with automatic pre-restore rollback snapshot creation."""
    manager = RestoreManager()
    prod_db = temp_workspace["root"] / "active_prod.db"

    # Seed initial production DB
    conn = sqlite3.connect(str(prod_db))
    conn.execute("CREATE TABLE events (id INT);")
    conn.execute("CREATE TABLE jira_issue_state (id INT);")
    conn.execute("CREATE TABLE retention_execution_history (id INT);")
    conn.execute("CREATE TABLE historical_jira_tasks (id INT);")
    conn.execute("CREATE TABLE action_audit_log (id INT);")
    conn.execute("INSERT INTO events (id) VALUES (999);")
    conn.commit()
    conn.close()

    # Restore new database
    result = manager.safe_production_restore(
        backup_file_path=temp_workspace["db_file"],
        target_production_db=prod_db,
    )

    assert result["status"] == "SUCCESS"
    assert result["production_restored"] is True
    assert result["rollback_snapshot_path"] is not None
    assert Path(result["rollback_snapshot_path"]).is_file()

    # Verify active production DB now has the new content
    conn = sqlite3.connect(str(prod_db))
    cur = conn.cursor()
    cur.execute("SELECT event_type FROM events LIMIT 1;")
    event_type = cur.fetchone()[0]
    conn.close()
    assert event_type == "issue_created"
