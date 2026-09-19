#!/usr/bin/env bash
# ==============================================================================
# PM Operations Agent — Automated SQLite Hot Backup & Cloud Sync Utility
# Uses SQLite hot backup, AES-256-GCM encryption, SHA-256 checksums, and Google Drive sync
# ==============================================================================

set -euo pipefail

REPO_DIR="${REPO_DIR:-/opt/pm/repository}"
BACKUP_DIR="${BACKUP_DIR:-/opt/pm/backups}"
DATA_DIR="${DATA_DIR:-/opt/pm/data}"
DB_FILE="${DATA_DIR}/pm_operations.db"
RETENTION_DAYS="${RETENTION_DAYS:-14}"

# Create backup directory with restricted permissions (0700)
mkdir -p "${BACKUP_DIR}"
chmod 700 "${BACKUP_DIR}"

if [ ! -f "${DB_FILE}" ]; then
    echo "[ERROR] Database file not found at ${DB_FILE}"
    exit 1
fi

echo "[INFO] Starting PM Operations Hot Backup Pipeline..."

# Execute Python Backup Manager if available (handles encryption, checksums, quota, gdrive sync)
if [ -d "${REPO_DIR}/app/services/backup" ] || python3 -c "import app.services.backup" >/dev/null 2>&1; then
    echo "[INFO] Running Python Backup Orchestrator from ${REPO_DIR}..."
    PYTHONPATH="${REPO_DIR}:${PYTHONPATH:-}" python3 "${REPO_DIR}/scripts/gdrive_backup.py" \
        --db-path "${DB_FILE}" \
        --backup-dir "${BACKUP_DIR}"
else
    # Fallback to local hot backup, gzip, and SHA-256 checksum
    echo "[INFO] Executing standard hot backup fallback..."
    TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
    BACKUP_FILE="${BACKUP_DIR}/pm_operations_backup_${TIMESTAMP}.db"

    # Online hot backup
    if command -v sqlite3 >/dev/null 2>&1; then
        sqlite3 "${DB_FILE}" ".backup '${BACKUP_FILE}'"
    else
        python3 -c "
import sqlite3
src = sqlite3.connect('${DB_FILE}')
dst = sqlite3.connect('${BACKUP_FILE}')
with dst:
    src.backup(dst)
dst.close()
src.close()
"
    fi

    # Verify integrity
    python3 -c "
import sqlite3, sys
conn = sqlite3.connect('${BACKUP_FILE}')
cursor = conn.cursor()
cursor.execute('PRAGMA integrity_check;')
res = cursor.fetchone()
if res and res[0] == 'ok':
    print('[PASS] Backup integrity check passed: OK')
    sys.exit(0)
else:
    print(f'[FAIL] Backup integrity check failed: {res}')
    sys.exit(1)
"

    # Compress
    gzip -f "${BACKUP_FILE}"
    FINAL_ARTIFACT="${BACKUP_FILE}.gz"
    echo "[INFO] Backup completed and compressed: ${FINAL_ARTIFACT}"

    # Calculate and store SHA-256 checksum
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "${FINAL_ARTIFACT}" > "${FINAL_ARTIFACT}.sha256"
    fi

    # Clean up backups older than RETENTION_DAYS
    find "${BACKUP_DIR}" \( -name "pm_operations_backup_*.db.gz*" -o -name "pm_operations_backup_*.db.gz.enc*" \) -mtime +"${RETENTION_DAYS}" -delete
    echo "[INFO] Retention policy applied: purged backups older than ${RETENTION_DAYS} days."
fi

# Backup Quota Check
BACKUP_SIZE_KB=$(du -sk "${BACKUP_DIR}" | awk '{print $1}')
BACKUP_SIZE_MB=$(( BACKUP_SIZE_KB / 1024 ))
echo "[INFO] Current local backup storage usage: ${BACKUP_SIZE_MB} MB"

if [ "${BACKUP_SIZE_MB}" -ge 5120 ]; then
    echo "[CRITICAL] Backup directory usage exceeds 5 GB quota! (${BACKUP_SIZE_MB} MB)"
elif [ "${BACKUP_SIZE_MB}" -ge 4096 ]; then
    echo "[WARNING] Backup directory usage exceeds 4 GB quota warning! (${BACKUP_SIZE_MB} MB)"
fi

echo "[SUCCESS] Backup pipeline finished."
