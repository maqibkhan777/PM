"""Tests for Phase 4: Database Health Monitoring Endpoint (/health/database)."""

import os
import tempfile
import pytest
from httpx import AsyncClient, ASGITransport
from app.api.app import app
from app.database.connection import DatabaseManager, db_manager
from app.database.schema import init_db
from app.core.retention.service import retention_service
from app.core.retention.models import RetentionScope


@pytest.mark.asyncio
async def test_health_endpoint_basic():
    """Verify standard /health endpoint returns expected status."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["application"] == "OK"
        assert data["version"] == "1.2.3"
        assert "database" in data


@pytest.mark.asyncio
async def test_database_health_endpoint_structure():
    """Verify /health/database endpoint returns comprehensive and correct structure."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/health/database")
        assert response.status_code == 200
        data = response.json()

        # Top-level sections
        assert "status" in data
        assert "database" in data
        assert "storage" in data
        assert "pragmas" in data
        assert "growth_diagnostics" in data
        assert "tables" in data
        assert "host_disk" in data
        assert "retention" in data
        assert "backup" in data

        # Database engine
        assert data["database"]["engine"] == "SQLite"
        assert "sqlite_version" in data["database"]
        assert "file_path" in data["database"]
        assert data["database"]["connected"] is True
        assert data["database"]["integrity_status"] in ["ok", "OK"]

        # Storage metrics
        storage = data["storage"]
        assert "size_bytes" in storage
        assert "size_mb" in storage
        assert storage["page_size"] > 0
        assert storage["page_count"] >= 0
        assert storage["freelist_count"] >= 0
        assert storage["freelist_bytes"] >= 0
        assert storage["freelist_mb"] >= 0
        assert storage["freelist_ratio"] >= 0.0

        # PRAGMAs
        pragmas = data["pragmas"]
        assert "journal_mode" in pragmas
        assert "synchronous" in pragmas
        assert "foreign_keys" in pragmas
        assert "busy_timeout" in pragmas

        # Growth diagnostics
        growth = data["growth_diagnostics"]
        assert growth["space_reclamation_opportunity"] in ["LOW", "MODERATE", "HIGH"]
        assert isinstance(growth["reusable_freelist_pages_available"], bool)
        assert isinstance(growth["vacuum_recommended"], bool)
        assert "routine_vacuum_policy" in growth

        # Tables
        tables = data["tables"]
        assert tables["total_tables"] >= 20
        assert tables["total_rows"] >= 0
        assert len(tables["top_10_largest_tables_by_rows"]) <= 10

        # Host disk
        disk = data["host_disk"]
        assert disk["status"] in ["OK", "WARNING", "CRITICAL"]
        assert disk["total_gb"] > 0
        assert 0.0 <= disk["used_percent"] <= 100.0

        # Retention diagnostics
        retention = data["retention"]
        assert "next_scheduled_windows" in retention
        assert "next_daily_maintenance_pkt" in retention["next_scheduled_windows"]
        assert "next_weekly_monday_maintenance_pkt" in retention["next_scheduled_windows"]

        # Backup diagnostics
        backup = data["backup"]
        assert backup["status"] in ["available", "unavailable"]
        assert "backup_count" in backup


@pytest.mark.asyncio
async def test_database_health_backup_detection(monkeypatch, tmp_path):
    """Verify /health/database discovers local backup archives when present."""
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    # Create dummy backup archives with distinct timestamps
    f1 = backup_dir / "pm_operations_backup_20260918_040000.db.gz"
    f1.write_bytes(b"backup-data-1" * 100)
    os.utime(f1, (1700000000, 1700000000))

    f2 = backup_dir / "pm_operations_backup_20260919_040000.db.gz"
    f2.write_bytes(b"backup-data-2" * 200)
    os.utime(f2, (1700100000, 1700100000))

    from app.config.settings import settings
    monkeypatch.setattr(settings, "BACKUP_DIR", str(backup_dir))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/health/database")
        assert response.status_code == 200
        data = response.json()

        backup = data["backup"]
        assert backup["status"] == "available"
        assert backup["backup_count"] == 2
        assert backup["latest_backup"]["file_name"] == "pm_operations_backup_20260919_040000.db.gz"
        assert backup["latest_backup"]["size_bytes"] == len(b"backup-data-2" * 200)


@pytest.mark.asyncio
async def test_database_health_retention_history_display():
    """Verify /health/database surfaces the latest retention run history."""
    # Execute a dry run retention to populate run history
    result = retention_service.execute(scope=RetentionScope.DAILY, dry_run=True)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/health/database")
        assert response.status_code == 200
        data = response.json()

        retention = data["retention"]
        assert retention["last_run"] is not None
        assert retention["last_run"]["run_id"] == result.run_id
        assert retention["last_run"]["dry_run"] is True
        assert retention["last_run"]["status"] == "COMPLETED"

