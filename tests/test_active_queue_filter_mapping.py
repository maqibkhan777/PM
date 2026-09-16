"""Unit tests for Phase 1: Authoritative Jira saved-filter mapping to employee/resource configuration."""

import pytest
import sqlite3
from typing import Dict, Optional
from app.database.connection import DatabaseManager
from app.database.schema import (
    init_db,
    AUTHORITATIVE_EMPLOYEE_ROLES,
    _migrate_employee_roles,
    _seed_authoritative_roles,
)
from app.database.repositories import EmployeeRoleRepository
from app.core.performance.roles import (
    get_employee_queue_filter_id,
    resolve_canonical_account_id,
    get_account_aliases,
    get_employee_designation_and_category,
)
from app.core.models.performance import EmployeeRoleAssignment


EXPECTED_FILTER_MAPPING: Dict[str, Optional[str]] = {
    "Ahsan Amin": "15370",
    "Ahsan Iftikhar": "17081",
    "Azain Hassan": "15371",
    "Daniyal Raza": "16826",
    "Hamza Hanif": "17010",
    "Muhammad Ali Siddiqui": "17129",
    "Muhammad Bilal Khan": "16817",
    "Muhammad Hamza": "16827",
    "Muhammad Shahmeer Khan": "15517",
    "Muhammad Sufiyan": "15515",
    "Muhammad Usama Azad": "15367",
    "Muneeb Jalal": "15369",
    "Nauman Sadiq": "16829",
    "shoaib hassan askari": "15516",
    "Syed ali": "15368",
    "Tahir Ali": "16828",
    "Talha Bukhari": "17082",
    "Usman": "17124",
    "Mubashir Butt": None,
}


def test_authoritative_employee_seed_definitions():
    """Verify that AUTHORITATIVE_EMPLOYEE_ROLES contains all expected filter mappings."""
    seeded_by_name = {r["display_name"]: r.get("jira_queue_filter_id") for r in AUTHORITATIVE_EMPLOYEE_ROLES}

    for name, expected_filter_id in EXPECTED_FILTER_MAPPING.items():
        assert name in seeded_by_name, f"Missing employee {name} in AUTHORITATIVE_EMPLOYEE_ROLES"
        assert seeded_by_name[name] == expected_filter_id, (
            f"Filter ID mismatch for {name}: expected {expected_filter_id}, got {seeded_by_name[name]}"
        )


def test_employee_role_repository_filter_lookups(temp_db):
    """Verify EmployeeRoleRepository retrieves correct filter ID by account_id and display_name."""
    init_db(temp_db)
    repo = EmployeeRoleRepository(temp_db)

    # 1. Test each mapped resource by display name
    for name, expected_filter_id in EXPECTED_FILTER_MAPPING.items():
        filter_id = repo.get_queue_filter_id_by_display_name(name)
        assert filter_id == expected_filter_id, f"Failed for {name}: expected {expected_filter_id}, got {filter_id}"

    # 2. Test lookup by account_id for Ahsan Amin
    ahsan_filter = repo.get_queue_filter_id("712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de")
    assert ahsan_filter == "15370"

    # 3. Test lookup via legacy alias
    alias_filter = repo.get_queue_filter_id("ahsan.amin")
    assert alias_filter == "15370"

    # 4. Test Mubashir Butt returns None
    mubashir_filter = repo.get_queue_filter_id("712020:e268bcd8-d981-4b4d-992d-d5694745df8b")
    assert mubashir_filter is None

    # 5. Test Usman alias lookup
    usman_filter = repo.get_queue_filter_id("usman")
    assert usman_filter == "17124"


def test_get_employee_queue_filter_id_helper(temp_db):
    """Verify get_employee_queue_filter_id helper function in roles.py."""
    init_db(temp_db)
    repo = EmployeeRoleRepository(temp_db)

    # By account ID
    assert get_employee_queue_filter_id("63e362bd790148a180977179", role_repo=repo) == "16826"

    # By display name
    assert get_employee_queue_filter_id(None, display_name="Daniyal Raza", role_repo=repo) == "16826"
    assert get_employee_queue_filter_id(None, display_name="Mubashir Butt", role_repo=repo) is None
    assert get_employee_queue_filter_id(None, display_name="Nonexistent User", role_repo=repo) is None


def test_schema_migration_on_preexisting_database(tmp_path):
    """Verify schema migration adds jira_queue_filter_id column idempotently to an existing database."""
    db_file = tmp_path / "old_test.db"
    conn = sqlite3.connect(str(db_file))

    # Create old employee_role_assignments table without jira_queue_filter_id
    conn.execute(
        """
        CREATE TABLE employee_role_assignments (
            id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            display_name TEXT NOT NULL,
            designation TEXT NOT NULL,
            role_category TEXT NOT NULL,
            effective_from TEXT,
            effective_to TEXT,
            source TEXT NOT NULL DEFAULT 'authoritative_seed',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO employee_role_assignments (
            id, account_id, display_name, designation, role_category, created_at, updated_at
        ) VALUES ('role:custom', 'custom-123', 'Custom User', 'Developer', 'WordPress Development', '2026-01-01', '2026-01-01')
        """
    )
    conn.commit()

    # Apply migration
    _migrate_employee_roles(conn)
    conn.commit()

    # Check that column exists now
    cur = conn.execute("PRAGMA table_info(employee_role_assignments)")
    cols = [r[1] for r in cur.fetchall()]
    assert "jira_queue_filter_id" in cols

    # Verify existing record is intact with NULL filter_id
    row = conn.execute("SELECT account_id, display_name, jira_queue_filter_id FROM employee_role_assignments WHERE account_id='custom-123'").fetchone()
    assert row[0] == "custom-123"
    assert row[1] == "Custom User"
    assert row[2] is None

    # Re-running migration should be safely idempotent
    _migrate_employee_roles(conn)
    conn.close()


def test_employee_role_assignment_model():
    """Verify EmployeeRoleAssignment Pydantic model parses correctly with and without filter ID."""
    m1 = EmployeeRoleAssignment(
        id="role:1",
        account_id="acc-1",
        display_name="Ahsan Amin",
        designation="Senior WordPress Developer",
        role_category="WordPress Development",
        jira_queue_filter_id="15370",
        created_at="2026-01-01",
        updated_at="2026-01-01",
    )
    assert m1.jira_queue_filter_id == "15370"

    m2 = EmployeeRoleAssignment(
        id="role:2",
        account_id="acc-2",
        display_name="Mubashir Butt",
        designation="Customer Support Engineer",
        role_category="Customer Support",
        created_at="2026-01-01",
        updated_at="2026-01-01",
    )
    assert m2.jira_queue_filter_id is None


def test_no_regression_in_existing_employee_lookups(temp_db):
    """Verify designation, role_category, and canonical resolution remain completely unaffected."""
    init_db(temp_db)
    repo = EmployeeRoleRepository(temp_db)

    desig, cat, resolved = get_employee_designation_and_category("712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de", role_repo=repo)
    assert resolved is True
    assert desig == "Senior WordPress Developer"
    assert cat == "WordPress Development"

    # Alias resolution
    can_id = resolve_canonical_account_id("ahsan.amin", role_repo=repo)
    assert can_id == "712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de"
