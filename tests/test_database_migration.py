"""Unit tests for SQLite schema migrations and team_group column handling."""

import os
import tempfile
import sqlite3
import pytest
from app.database.connection import DatabaseManager
from app.database.schema import init_db
from app.database.repositories import JiraIssueStateRepository


def test_migration_upgrades_legacy_database_without_team_group():
    """Test that a database created prior to the team_group column is upgraded seamlessly."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    try:
        # Create legacy table schema without team_group
        legacy_conn = sqlite3.connect(path)
        legacy_conn.executescript("""
        CREATE TABLE jira_issue_state (
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
            raw_reference TEXT
        );
        INSERT INTO jira_issue_state (
            jira_issue_key, summary, status, assignee, priority, due_date,
            updated_at, last_seen_at, last_activity_at, project_key, raw_reference
        ) VALUES (
            'LEGACY-101', 'Legacy Issue', 'In Progress', 'Developer Dave', 'High', '2026-09-30',
            '2026-09-09T10:00:00Z', '2026-09-09T10:00:00Z', '2026-09-09T10:00:00Z', 'LEGACY', '{}'
        );
        """)
        legacy_conn.commit()
        legacy_conn.close()

        # Run init_db on the legacy database
        mgr = DatabaseManager(db_path=path)
        init_db(mgr)

        # Verify team_group column was added
        with mgr.session() as conn:
            cursor = conn.execute("PRAGMA table_info(jira_issue_state)")
            columns = [row["name"] if hasattr(row, "keys") else row[1] for row in cursor.fetchall()]
            assert "team_group" in columns

            # Verify existing issue data remains intact
            cursor = conn.execute("SELECT jira_issue_key, summary, status, team_group FROM jira_issue_state WHERE jira_issue_key = 'LEGACY-101'")
            row = cursor.fetchone()
            assert row is not None
            assert row["jira_issue_key"] == "LEGACY-101"
            assert row["summary"] == "Legacy Issue"
            assert row["status"] == "In Progress"
            assert row["team_group"] is None

            # Verify index on team_group was created
            cursor = conn.execute("PRAGMA index_list(jira_issue_state)")
            index_names = [r["name"] if hasattr(r, "keys") else r[1] for r in cursor.fetchall()]
            assert "idx_jira_issue_state_team_group" in index_names

        # Verify repository queries on migrated table work cleanly
        repo = JiraIssueStateRepository(mgr)
        stale = repo.get_stale_candidates(threshold_hours=1, team_group="Engineering Team")
        assert len(stale) == 0  # Legacy-101 has NULL team_group, correctly excluded from Engineering Team

        # Update with team_group and query again
        repo.upsert(
            jira_issue_key="LEGACY-101",
            summary="Legacy Issue Updated",
            status="In Progress",
            assignee="Developer Dave",
            team_group="Engineering Team"
        )
        stale_after = repo.get_stale_candidates(threshold_hours=1, team_group="Engineering Team")
        assert len(stale_after) == 1
        assert stale_after[0]["jira_issue_key"] == "LEGACY-101"

    finally:
        if os.path.exists(path):
            os.remove(path)


def test_migration_is_idempotent():
    """Test that running init_db multiple times produces no errors and does not alter existing data."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    try:
        mgr = DatabaseManager(db_path=path)

        # Run 1: Fresh DB creation
        init_db(mgr)

        # Insert a record with team_group
        repo = JiraIssueStateRepository(mgr)
        repo.upsert(
            jira_issue_key="IDEM-1",
            summary="Idempotency Test",
            status="To Do",
            team_group="Mursaleen Cluster"
        )

        # Run 2: Re-running init_db on already migrated DB
        init_db(mgr)

        # Run 3: Third run
        init_db(mgr)

        # Data must remain intact
        issue = repo.get("IDEM-1")
        assert issue is not None
        assert issue["jira_issue_key"] == "IDEM-1"
        assert issue["team_group"] == "Mursaleen Cluster"

    finally:
        if os.path.exists(path):
            os.remove(path)
