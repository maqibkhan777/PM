import os
import shutil
import sqlite3
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from fastapi import APIRouter
from app.services.orchestrator import orchestrator
from app.database.connection import db_manager
from app.config.settings import settings
from app.core.retention.service import retention_service

router = APIRouter(tags=["Health"])


@router.get("/health")
async def get_health():
    """Overall system health check."""
    db_ok = False
    try:
        with db_manager.session() as conn:
            cursor = conn.execute("SELECT 1")
            db_ok = cursor.fetchone() is not None
    except Exception:
        db_ok = False

    jira_health = await orchestrator.jira_connector.health_check()
    mm_health = await orchestrator.mattermost_connector.health_check()
    discord_health = await orchestrator.discord_webhook_connector.health_check()
    discord_bot_health = await orchestrator.discord_bot_connector.health_check()

    return {
        "application": "OK",
        "version": "1.2.3",
        "dry_run": settings.DRY_RUN,
        "database": "OK" if db_ok else "ERROR",
        "jira": jira_health.status,
        "mattermost": mm_health.status,
        "discord": discord_health.status,
        "discord_bot": discord_bot_health.status,
        "scheduler_running": orchestrator.periodic_scheduler.is_running if hasattr(orchestrator, "periodic_scheduler") else False
    }


@router.get("/health/connectors")
async def get_connectors_health():
    """Detailed health, polling status, and capabilities of all registered connectors."""
    jira_h = await orchestrator.jira_connector.health_check()
    mm_h = await orchestrator.mattermost_connector.health_check()
    discord_h = await orchestrator.discord_webhook_connector.health_check()
    discord_bot_h = await orchestrator.discord_bot_connector.health_check()

    return {
        "jira": {
            "connected": jira_h.is_connected,
            "status": jira_h.status,
            "polling_enabled": settings.JIRA_POLLING_ENABLED,
            "polling_status": jira_h.details.get("polling_status", "disabled"),
            "last_poll_success": jira_h.details.get("last_poll_success"),
            "team_group": settings.JIRA_TEAM_GROUP,
            "team_group_scoped": settings.is_jira_team_group_configured()
        },
        "mattermost": {
            "configured": mm_h.details.get("configured", False),
            "connected": mm_h.is_connected,
            "status": mm_h.details.get("status", "not_configured")
        },
        "discord": {
            "configured": settings.is_discord_configured(),
            "connected": discord_h.is_connected,
            "status": discord_h.status
        },
        "connectors": [
            {
                "name": orchestrator.jira_connector.name,
                "system_type": orchestrator.jira_connector.system_type,
                "status": jira_h.status,
                "is_connected": jira_h.is_connected,
                "capabilities": [c.value for c in orchestrator.jira_connector.get_capabilities()],
                "details": jira_h.details
            },
            {
                "name": orchestrator.mattermost_connector.name,
                "system_type": orchestrator.mattermost_connector.system_type,
                "status": mm_h.status,
                "is_connected": mm_h.is_connected,
                "capabilities": [c.value for c in orchestrator.mattermost_connector.get_capabilities()],
                "details": mm_h.details
            },
            {
                "name": orchestrator.discord_webhook_connector.name,
                "system_type": orchestrator.discord_webhook_connector.system_type,
                "status": discord_h.status,
                "is_connected": discord_h.is_connected,
                "capabilities": [c.value for c in orchestrator.discord_webhook_connector.get_capabilities()],
                "details": discord_h.details
            },
            {
                "name": orchestrator.discord_bot_connector.name,
                "system_type": orchestrator.discord_bot_connector.system_type,
                "status": discord_bot_h.status,
                "is_connected": discord_bot_h.is_connected,
                "capabilities": [c.value for c in orchestrator.discord_bot_connector.get_capabilities()],
                "details": discord_bot_h.details
            }
        ]
    }


@router.get("/health/database")
async def get_database_health() -> Dict[str, Any]:
    """Comprehensive SQLite storage, PRAGMA, table metrics, growth diagnostics, disk space, retention, and backup health."""
    db_path = db_manager.db_path
    abs_db_path = os.path.abspath(db_path)
    db_exists = os.path.exists(abs_db_path)

    # 1. File sizes
    db_size_bytes = os.path.getsize(abs_db_path) if db_exists else 0
    wal_path = f"{abs_db_path}-wal"
    wal_size_bytes = os.path.getsize(wal_path) if os.path.exists(wal_path) else 0
    shm_path = f"{abs_db_path}-shm"
    shm_size_bytes = os.path.getsize(shm_path) if os.path.exists(shm_path) else 0

    # 2. SQLite PRAGMA inspection and Table stats
    sqlite_ver = sqlite3.sqlite_version
    page_size = 4096
    page_count = 0
    freelist_count = 0
    journal_mode = "unknown"
    synchronous = "unknown"
    foreign_keys = 0
    busy_timeout = 0
    integrity_status = "unknown"
    table_counts: Dict[str, int] = {}
    db_connected = False

    try:
        with db_manager.session() as conn:
            db_connected = True
            try:
                page_size = int(conn.execute("PRAGMA page_size;").fetchone()[0])
            except Exception:
                pass
            try:
                page_count = int(conn.execute("PRAGMA page_count;").fetchone()[0])
            except Exception:
                pass
            try:
                freelist_count = int(conn.execute("PRAGMA freelist_count;").fetchone()[0])
            except Exception:
                pass
            try:
                journal_mode = str(conn.execute("PRAGMA journal_mode;").fetchone()[0]).upper()
            except Exception:
                pass
            try:
                sync_val = conn.execute("PRAGMA synchronous;").fetchone()[0]
                sync_map = {0: "OFF", 1: "NORMAL", 2: "FULL", 3: "EXTRA"}
                synchronous = sync_map.get(sync_val, str(sync_val))
            except Exception:
                pass
            try:
                foreign_keys = int(conn.execute("PRAGMA foreign_keys;").fetchone()[0])
            except Exception:
                pass
            try:
                busy_timeout = int(conn.execute("PRAGMA busy_timeout;").fetchone()[0])
            except Exception:
                pass
            try:
                integrity_row = conn.execute("PRAGMA quick_check;").fetchone()
                integrity_status = str(integrity_row[0]) if integrity_row else "unknown"
            except Exception as e:
                integrity_status = f"error: {e}"

            # Table statistics
            try:
                cursor = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name;"
                )
                tables = [r[0] for r in cursor.fetchall()]
                for tbl in tables:
                    try:
                        cnt_row = conn.execute(f'SELECT COUNT(*) FROM "{tbl}";').fetchone()
                        table_counts[tbl] = int(cnt_row[0]) if cnt_row else 0
                    except Exception:
                        table_counts[tbl] = -1
            except Exception:
                pass
    except Exception as e:
        db_connected = False
        integrity_status = f"connection_error: {e}"

    # Storage calculations
    freelist_bytes = freelist_count * page_size
    freelist_mb = round(freelist_bytes / (1024 * 1024), 2)
    freelist_ratio = round(freelist_count / max(page_count, 1), 4)

    # Opportunity level
    if freelist_mb > 50.0 or freelist_ratio > 0.20:
        reclaim_opp = "HIGH"
        vac_rec = True
    elif freelist_mb > 10.0 or freelist_ratio > 0.10:
        reclaim_opp = "MODERATE"
        vac_rec = False
    else:
        reclaim_opp = "LOW"
        vac_rec = False

    # 3. Host Disk Space
    try:
        db_dir = os.path.dirname(abs_db_path) or "."
        total_b, used_b, free_b = shutil.disk_usage(db_dir)
        used_pct = round((used_b / max(total_b, 1)) * 100, 1)
        disk_status = "CRITICAL" if used_pct >= 95.0 else ("WARNING" if used_pct >= 85.0 else "OK")
        disk_metrics = {
            "status": disk_status,
            "path": db_dir,
            "total_bytes": total_b,
            "total_gb": round(total_b / (1024 ** 3), 2),
            "used_bytes": used_b,
            "used_gb": round(used_b / (1024 ** 3), 2),
            "free_bytes": free_b,
            "free_gb": round(free_b / (1024 ** 3), 2),
            "used_percent": used_pct,
            "warning_threshold_percent": 85.0,
            "critical_threshold_percent": 95.0,
        }
    except Exception as e:
        disk_metrics = {"status": "ERROR", "error": str(e)}

    # 4. Retention Diagnostics
    retention_diag: Dict[str, Any] = {
        "next_scheduled_windows": retention_service.get_next_maintenance_windows(),
        "last_run": None,
    }
    try:
        last_run = retention_service.repository.get_last_run()
        if last_run:
            retention_diag["last_run"] = {
                "run_id": last_run.get("run_id"),
                "dry_run": bool(last_run.get("dry_run")),
                "status": last_run.get("status"),
                "started_at": last_run.get("started_at"),
                "completed_at": last_run.get("completed_at"),
                "duration_ms": last_run.get("execution_duration_ms", 0),
                "total_deleted_rows": last_run.get("rows_deleted", 0),
                "tables_processed": last_run.get("tables_processed", 0),
                "error_summary": last_run.get("error_summary"),
            }
    except Exception as e:
        retention_diag["error"] = str(e)

    # 5. Backup & Cloud Sync Diagnostics
    backup_dir = getattr(settings, "BACKUP_DIR", "/opt/pm/backups")
    backup_info: Dict[str, Any] = {
        "status": "unavailable",
        "backup_directory": backup_dir,
        "message": "No local backup archives found in configured backup path",
        "backup_count": 0,
        "total_backup_bytes": 0,
        "total_backup_mb": 0.0,
        "quota": {
            "status": "OK",
            "warning_threshold_bytes": 4 * 1024 * 1024 * 1024,
            "critical_threshold_bytes": 5 * 1024 * 1024 * 1024,
            "warning_threshold_gb": 4.0,
            "critical_threshold_gb": 5.0,
        },
        "encryption": {
            "enabled": bool(getattr(settings, "BACKUP_ENCRYPTION_ENABLED", True)),
            "configured": bool(settings.is_backup_encryption_configured() if hasattr(settings, "is_backup_encryption_configured") else False),
        },
        "cloud_sync": {
            "provider": "Google Drive",
            "enabled": bool(getattr(settings, "GDRIVE_ENABLED", False)),
            "configured": bool(settings.is_gdrive_configured() if hasattr(settings, "is_gdrive_configured") else False),
        },
        "latest_backup": None,
        "latest_metadata": None,
    }

    candidate_dirs = [backup_dir]
    if not os.path.isabs(backup_dir):
        candidate_dirs.append(os.path.abspath(backup_dir))
    candidate_dirs.append(os.path.abspath("./backups"))

    valid_dir = None
    for cdir in candidate_dirs:
        if os.path.isdir(cdir):
            valid_dir = cdir
            break

    if valid_dir:
        backup_files = []
        total_backup_bytes = 0
        try:
            for f in os.listdir(valid_dir):
                if f.endswith(".db.gz.enc") or f.endswith(".db.gz") or f.endswith(".db") or f.endswith(".tar.gz"):
                    fpath = os.path.join(valid_dir, f)
                    if os.path.isfile(fpath):
                        stat = os.stat(fpath)
                        total_backup_bytes += stat.st_size
                        backup_files.append({
                            "file_name": f,
                            "file_path": fpath,
                            "is_encrypted": f.endswith(".enc"),
                            "size_bytes": stat.st_size,
                            "size_mb": round(stat.st_size / (1024 * 1024), 2),
                            "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                            "mtime": stat.st_mtime,
                        })

            # Check quota thresholds
            quota_status = "OK"
            if total_backup_bytes >= 5 * 1024 * 1024 * 1024:
                quota_status = "CRITICAL"
            elif total_backup_bytes >= 4 * 1024 * 1024 * 1024:
                quota_status = "WARNING"

            backup_info["total_backup_bytes"] = total_backup_bytes
            backup_info["total_backup_mb"] = round(total_backup_bytes / (1024 * 1024), 2)
            backup_info["quota"]["status"] = quota_status

            # Read latest metadata json if present
            meta_json_path = os.path.join(valid_dir, "latest_backup_metadata.json")
            if os.path.isfile(meta_json_path):
                try:
                    import json
                    with open(meta_json_path, "r", encoding="utf-8") as mf:
                        backup_info["latest_metadata"] = json.load(mf)
                except Exception:
                    pass

            if backup_files:
                backup_files.sort(key=lambda x: (x["mtime"], x["file_name"]), reverse=True)
                latest = backup_files[0]
                backup_info["status"] = "available"
                backup_info["backup_directory"] = valid_dir
                backup_info["backup_count"] = len(backup_files)
                backup_info["latest_backup"] = {
                    "file_name": latest["file_name"],
                    "is_encrypted": latest["is_encrypted"],
                    "size_bytes": latest["size_bytes"],
                    "size_mb": latest["size_mb"],
                    "modified_at": latest["modified_at"],
                }
        except Exception as e:
            backup_info["error"] = str(e)

    total_rows = sum(cnt for cnt in table_counts.values() if cnt > 0)
    top_tables = sorted(
        [{"table": k, "row_count": v} for k, v in table_counts.items() if v >= 0],
        key=lambda x: x["row_count"],
        reverse=True
    )[:10]

    return {
        "status": "HEALTHY" if (db_connected and integrity_status == "ok") else "DEGRADED",
        "database": {
            "engine": "SQLite",
            "sqlite_version": sqlite_ver,
            "file_path": abs_db_path,
            "connected": db_connected,
            "integrity_status": integrity_status,
        },
        "storage": {
            "size_bytes": db_size_bytes,
            "size_mb": round(db_size_bytes / (1024 * 1024), 2),
            "page_size": page_size,
            "page_count": page_count,
            "freelist_count": freelist_count,
            "freelist_bytes": freelist_bytes,
            "freelist_mb": freelist_mb,
            "freelist_ratio": freelist_ratio,
            "wal_size_bytes": wal_size_bytes,
            "wal_size_mb": round(wal_size_bytes / (1024 * 1024), 2),
            "shm_size_bytes": shm_size_bytes,
            "shm_size_mb": round(shm_size_bytes / (1024 * 1024), 2),
        },
        "pragmas": {
            "journal_mode": journal_mode,
            "synchronous": synchronous,
            "foreign_keys": foreign_keys,
            "busy_timeout": busy_timeout,
        },
        "growth_diagnostics": {
            "space_reclamation_opportunity": reclaim_opp,
            "reusable_freelist_pages_available": freelist_count > 0,
            "vacuum_recommended": vac_rec,
            "routine_vacuum_policy": "disabled (one-off manual maintenance only)",
        },
        "tables": {
            "total_tables": len(table_counts),
            "populated_tables": sum(1 for v in table_counts.values() if v > 0),
            "total_rows": total_rows,
            "top_10_largest_tables_by_rows": top_tables,
            "row_counts_by_table": table_counts,
        },
        "host_disk": disk_metrics,
        "retention": retention_diag,
        "backup": backup_info,
    }
