#!/usr/bin/env bash
# ==============================================================================
# PM Operations Agent — SQLite Restore Utility
# Safely restores a backup snapshot into the active data volume
# ==============================================================================

set -euo pipefail

if [ "$#" -ne 1 ]; then
    echo "Usage: $0 <path_to_backup.db or path_to_backup.db.gz>"
    exit 1
fi

INPUT_BACKUP="$1"
DATA_DIR="${DATA_DIR:-/opt/pm/data}"
TARGET_DB="${DATA_DIR}/pm_operations.db"
TEMP_RESTORE_DIR=$(mktemp -d)

trap 'rm -rf "${TEMP_RESTORE_DIR}"' EXIT

if [ ! -f "${INPUT_BACKUP}" ]; then
    echo "[ERROR] Specified backup file does not exist: ${INPUT_BACKUP}"
    exit 1
fi

# Decompress if gzipped
if [[ "${INPUT_BACKUP}" == *.gz ]]; then
    echo "[INFO] Decompressing ${INPUT_BACKUP}..."
    DECOMPRESSED_FILE="${TEMP_RESTORE_DIR}/restore.db"
    gunzip -c "${INPUT_BACKUP}" > "${DECOMPRESSED_FILE}"
    RESTORE_SRC="${DECOMPRESSED_FILE}"
else
    RESTORE_SRC="${INPUT_BACKUP}"
fi

# Verify restore file integrity before touching production DB
echo "[INFO] Verifying restore file integrity..."
python3 -c "
import sqlite3, sys
conn = sqlite3.connect('${RESTORE_SRC}')
cursor = conn.cursor()
cursor.execute('PRAGMA integrity_check;')
res = cursor.fetchone()
if res and res[0] == 'ok':
    print('[PASS] Integrity verified: OK')
    sys.exit(0)
else:
    print(f'[FAIL] Backup integrity failed: {res}')
    sys.exit(1)
"

# Stop container if running
echo "[INFO] Ensuring PM container is temporarily paused during database swap..."
docker compose stop pm-agent || true

# Preserve existing DB as safety rollback
if [ -f "${TARGET_DB}" ]; then
    PRE_RESTORE_BACKUP="${DATA_DIR}/pm_operations_pre_restore_$(date +%Y%m%d_%H%M%S).db"
    echo "[INFO] Archiving current database to ${PRE_RESTORE_BACKUP}..."
    cp "${TARGET_DB}" "${PRE_RESTORE_BACKUP}"
    rm -f "${TARGET_DB}-wal" "${TARGET_DB}-shm"
fi

# Install restored DB
echo "[INFO] Installing restored database to ${TARGET_DB}..."
cp "${RESTORE_SRC}" "${TARGET_DB}"
chmod 644 "${TARGET_DB}"

# Restart container
echo "[INFO] Restarting PM Agent service..."
docker compose up -d pm-agent

echo "[SUCCESS] Restore completed successfully."
