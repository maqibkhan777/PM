#!/usr/bin/env bash
# ==============================================================================
# PM Operations Agent — SQLite Restore & Recovery Utility (Phases 5–7)
# Safely restores and verifies .db, .db.gz, and .db.gz.enc backups
# ==============================================================================

set -euo pipefail

if [ "$#" -lt 1 ]; then
    echo "Usage: $0 <path_to_backup_file> [optional_encryption_key]"
    echo "Supported formats: .db, .db.gz, .db.gz.enc"
    exit 1
fi

INPUT_BACKUP="$1"
ENCRYPTION_KEY="${2:-${BACKUP_ENCRYPTION_KEY:-}}"
REPO_DIR="${REPO_DIR:-/opt/pm/repository}"
COMPOSE_FILE="${COMPOSE_FILE:-${REPO_DIR}/docker-compose.yml}"
DATA_DIR="${DATA_DIR:-/opt/pm/data}"
TARGET_DB="${DATA_DIR}/pm_operations.db"
TEMP_RESTORE_DIR=$(mktemp -d)

trap 'rm -rf "${TEMP_RESTORE_DIR}"' EXIT

if [ ! -f "${INPUT_BACKUP}" ]; then
    echo "[ERROR] Specified backup file does not exist: ${INPUT_BACKUP}"
    exit 1
fi

echo "================================================================="
echo "PM Operations Agent — Safe Database Restore Pipeline"
echo "================================================================="
echo "Target Backup File : ${INPUT_BACKUP}"
echo "Target Database    : ${TARGET_DB}"
echo "-----------------------------------------------------------------"

# Step 1: Verify SHA-256 checksum if .sha256 file exists alongside backup
CHECKSUM_FILE="${INPUT_BACKUP}.sha256"
if [ -f "${CHECKSUM_FILE}" ]; then
    echo "[INFO] Verifying SHA-256 checksum..."
    python3 -c "
import sys, hashlib
with open('${INPUT_BACKUP}', 'rb') as f:
    calc_hash = hashlib.sha256(f.read()).hexdigest().lower()
with open('${CHECKSUM_FILE}', 'r') as f:
    expected_hash = f.read().strip().split()[0].lower()
if calc_hash == expected_hash:
    print('[PASS] SHA-256 Checksum verified OK')
    sys.exit(0)
else:
    print(f'[FAIL] Checksum mismatch! Expected {expected_hash}, calculated {calc_hash}')
    sys.exit(1)
"
fi

# Step 2: Decrypt if encrypted (.enc)
CURRENT_FILE="${INPUT_BACKUP}"
if [[ "${INPUT_BACKUP}" == *.enc ]]; then
    echo "[INFO] Encrypted backup detected. Decrypting with AES-256-GCM..."
    if [ -z "${ENCRYPTION_KEY}" ]; then
        echo "[ERROR] Backup is encrypted (.enc) but no encryption key was provided (set BACKUP_ENCRYPTION_KEY or pass as arg 2)."
        exit 1
    fi
    DECRYPTED_GZ="${TEMP_RESTORE_DIR}/decrypted.db.gz"
    PYTHONPATH="${REPO_DIR}:${PYTHONPATH:-}" python3 -c "
from app.services.backup.crypto import decrypt_file
decrypt_file('${INPUT_BACKUP}', '${DECRYPTED_GZ}', '${ENCRYPTION_KEY}')
"
    CURRENT_FILE="${DECRYPTED_GZ}"
fi

# Step 3: Decompress if gzipped (.gz)
if [[ "${CURRENT_FILE}" == *.gz ]]; then
    echo "[INFO] Decompressing ${CURRENT_FILE}..."
    DECOMPRESSED_FILE="${TEMP_RESTORE_DIR}/restore.db"
    gunzip -c "${CURRENT_FILE}" > "${DECOMPRESSED_FILE}"
    RESTORE_SRC="${DECOMPRESSED_FILE}"
else
    RESTORE_SRC="${CURRENT_FILE}"
fi

# Step 4: Verify restore file SQLite integrity, foreign keys, and schema
echo "[INFO] Verifying SQLite integrity and foreign key constraints..."
python3 -c "
import sqlite3, sys
conn = sqlite3.connect('${RESTORE_SRC}')
cursor = conn.cursor()

# Integrity check
cursor.execute('PRAGMA integrity_check;')
res = cursor.fetchone()
if not res or res[0] != 'ok':
    print(f'[FAIL] Backup integrity failed: {res}')
    sys.exit(1)

# Foreign key check
cursor.execute('PRAGMA foreign_key_check;')
fk_errs = cursor.fetchall()
if fk_errs:
    print(f'[FAIL] Foreign key check failed: {fk_errs}')
    sys.exit(1)

# Schema check for critical tables
cursor.execute(\"SELECT name FROM sqlite_master WHERE type='table';\")
tables = {row[0] for row in cursor.fetchall()}
required = ['events', 'jira_issue_state', 'retention_execution_history']
missing = [t for t in required if t not in tables]
if missing:
    print(f'[FAIL] Missing critical tables: {missing}')
    sys.exit(1)

print('[PASS] Integrity, foreign keys, and schema verified successfully: OK')
conn.close()
sys.exit(0)
"

# Step 5: Stop PM container safely before database swap
echo "[INFO] Stopping PM Agent container during database swap..."
docker compose -f "${COMPOSE_FILE}" stop pm-agent || true

# Step 6: Preserve existing DB as safety rollback snapshot
if [ -f "${TARGET_DB}" ]; then
    PRE_RESTORE_BACKUP="${DATA_DIR}/pm_operations_pre_restore_$(date +%Y%m%d_%H%M%S).db"
    echo "[INFO] Archiving current active database to rollback snapshot: ${PRE_RESTORE_BACKUP}..."
    cp "${TARGET_DB}" "${PRE_RESTORE_BACKUP}"
    rm -f "${TARGET_DB}-wal" "${TARGET_DB}-shm"
fi

# Step 7: Install restored DB
echo "[INFO] Installing restored database to ${TARGET_DB}..."
cp "${RESTORE_SRC}" "${TARGET_DB}"
chmod 644 "${TARGET_DB}"
chown 10001:10001 "${TARGET_DB}" 2>/dev/null || true

# Step 8: Restart container
echo "[INFO] Restarting PM Agent service..."
docker compose -f "${COMPOSE_FILE}" up -d pm-agent

# Step 9: Verify service health after restart
echo "[INFO] Waiting for service healthcheck..."
sleep 5
for i in {1..6}; do
    if curl -s -f http://127.0.0.1:8000/health >/dev/null 2>&1; then
        echo "[SUCCESS] PM Agent service is healthy and responding to /health."
        exit 0
    fi
    echo "Waiting for service to become ready ($i/6)..."
    sleep 5
done

echo "[WARNING] Service started, but /health did not return 200 within 30 seconds. Please inspect 'docker compose logs pm-agent'."
