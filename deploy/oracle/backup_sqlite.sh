#!/usr/bin/env bash
# ==============================================================================
# PM Operations Agent — Automated SQLite Hot Backup Utility
# Uses SQLite .backup command for transaction-safe snapshotting under WAL mode
# ==============================================================================

set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/opt/pm/backups}"
DATA_DIR="${DATA_DIR:-/opt/pm/data}"
DB_FILE="${DATA_DIR}/pm_operations.db"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="${BACKUP_DIR}/pm_operations_backup_${TIMESTAMP}.db"
RETENTION_DAYS="${RETENTION_DAYS:-14}"

# Create backup directory with restricted permissions
mkdir -p "${BACKUP_DIR}"
chmod 700 "${BACKUP_DIR}"

if [ ! -f "${DB_FILE}" ]; then
    echo "[ERROR] Database file not found at ${DB_FILE}"
    exit 1
fi

echo "[INFO] Starting hot backup of ${DB_FILE} to ${BACKUP_FILE}..."

# Execute transactionally safe online backup via sqlite3 CLI
if command -v sqlite3 >/dev/null 2>&1; then
    sqlite3 "${DB_FILE}" ".backup '${BACKUP_FILE}'"
else
    # Fallback via Python standard library sqlite3 module
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

# Verify backup integrity
echo "[INFO] Verifying backup integrity..."
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

# Compress backup
gzip -f "${BACKUP_FILE}"
echo "[INFO] Backup completed and compressed: ${BACKUP_FILE}.gz"

# Clean up backups older than RETENTION_DAYS
find "${BACKUP_DIR}" -name "pm_operations_backup_*.db.gz" -mtime +"${RETENTION_DAYS}" -delete
echo "[INFO] Retention policy applied: purged backups older than ${RETENTION_DAYS} days."
