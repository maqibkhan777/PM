"""Tests for VM Resource Hardening (Phase 7), Docker Compose configurations,
backup quota monitoring, and secret exposure protection.
"""

import os
import yaml
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch

from app.services.backup.manager import (
    BackupManager,
    BACKUP_QUOTA_WARN_BYTES,
    BACKUP_QUOTA_CRIT_BYTES,
)
from app.api.routes.health import get_database_health


def test_docker_compose_hardening_configurations():
    """Verify that deploy/oracle/docker-compose.yml and root docker-compose.yml contain Phase 7 hardening."""
    compose_paths = [
        Path("deploy/oracle/docker-compose.yml"),
        Path("docker-compose.yml"),
    ]

    for cpath in compose_paths:
        assert cpath.is_file(), f"Missing compose file: {cpath}"
        with open(cpath, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        service = data.get("services", {}).get("pm-agent", {})
        assert service, f"Missing pm-agent service in {cpath}"

        # 1. Log rotation
        logging_cfg = service.get("logging", {})
        assert logging_cfg.get("driver") == "json-file", f"Invalid logging driver in {cpath}"
        opts = logging_cfg.get("options", {})
        assert opts.get("max-size") == "50m", f"Expected max-size: 50m in {cpath}"
        assert str(opts.get("max-file")) == "3", f"Expected max-file: 3 in {cpath}"

        # 2. PID Limit
        assert service.get("pids_limit") == 100, f"Expected pids_limit: 100 in {cpath}"

        # 3. Linux Capabilities
        cap_drop = service.get("cap_drop", [])
        assert "ALL" in cap_drop, f"Expected cap_drop: [ALL] in {cpath}"

        # 4. Non-root user
        assert service.get("user") == "10001:10001", f"Expected user: 10001:10001 in {cpath}"

        # 5. Security options
        sec_opts = service.get("security_opt", [])
        assert "no-new-privileges:true" in sec_opts, f"Expected no-new-privileges:true in {cpath}"

        # 6. Loopback binding
        ports = service.get("ports", [])
        assert "127.0.0.1:8000:8000" in ports, f"Expected 127.0.0.1:8000:8000 in {cpath}"

        # 7. Restart policy
        assert service.get("restart") == "unless-stopped", f"Expected restart: unless-stopped in {cpath}"

        # 8. Resource constraints
        limits = service.get("deploy", {}).get("resources", {}).get("limits", {})
        assert limits.get("cpus") == "1.0", f"Expected cpus: 1.0 in {cpath}"
        assert limits.get("memory") == "1024M", f"Expected memory: 1024M in {cpath}"


def test_backup_quota_monitoring_thresholds():
    """Verify backup quota warning (4GB) and critical (5GB) monitoring."""
    with tempfile.TemporaryDirectory() as tmp:
        bdir = Path(tmp)
        manager = BackupManager(backup_dir=str(bdir))

        # Under 4 GB -> OK
        quota_ok = manager.check_backup_quota()
        assert quota_ok["quota_status"] == "OK"

        # Simulate 4.2 GB backup files
        dummy_file = bdir / "pm_operations_backup_20260919_010000.db.gz.enc"
        dummy_file.write_bytes(b"0" * 100)

        # Mock stat size
        with patch.object(Path, "stat") as mock_stat:
            mock_stat.return_value.st_size = int(4.2 * 1024 * 1024 * 1024)
            quota_warn = manager.check_backup_quota()
            assert quota_warn["quota_status"] == "WARNING"

            mock_stat.return_value.st_size = int(5.5 * 1024 * 1024 * 1024)
            quota_crit = manager.check_backup_quota()
            assert quota_crit["quota_status"] == "CRITICAL"


@pytest.mark.asyncio
async def test_health_database_no_secret_exposure():
    """Verify /health/database exposes encryption/cloud sync status without leaking secret keys or tokens."""
    res = await get_database_health()

    assert "backup" in res
    backup = res["backup"]
    assert "quota" in backup
    assert "encryption" in backup
    assert "cloud_sync" in backup

    json_str = str(res)
    # Ensure no secret strings or private key tokens exist in output
    assert "private_key" not in json_str.lower()
    assert "token" not in json_str.lower()
    assert "passphrase" not in json_str.lower()
    assert "secret" not in json_str.lower() or "secret_key" not in json_str.lower()
