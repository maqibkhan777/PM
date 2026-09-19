# PM Operations Agent v1.2.3 — Oracle Always Free ARM64 Deployment Guide

This guide provides complete, step-by-step, reproducible instructions for deploying the **PM Operations Agent v1.2.3** on an **Oracle Cloud Infrastructure (OCI) Always Free ARM64 (Ampere A1) Virtual Machine**, including authenticated AES-256-GCM backups, unattended Google Drive cloud sync, disaster recovery procedures, and container security hardening.

---

## Architecture Overview

```
[ Internet / Jira Cloud / Discord Users ]
                  │
                  ▼ (HTTPS / Port 443)
     [ OCI Security List / UFW Firewall ]
                  │
                  ▼
     [ Nginx / Cloudflare Reverse Proxy ]
                  │
                  ▼ (Internal HTTP: 127.0.0.1:8000)
    [ Docker Container: pm-operations-agent:v1.2.3 ]
      ├── Non-root User: pmuser (UID/GID 10001:10001)
      ├── Security: cap_drop [ALL], no-new-privileges:true, pids_limit: 100
      ├── Logging: json-file (max-size: 50m, max-file: 3)
      ├── FastAPI + Uvicorn
      ├── Discord Bot Gateway (/pm slash commands)
      ├── Jira Poller & Event Pipeline
      ├── Periodic Scheduler (15-min cycle)
      └── SQLite Database (/app/data/pm_operations.db)
                  │ (Host Volume Mounts)
                  ├── [ Host: /opt/pm/data/ ]
                  ├── [ Host: /opt/pm/backups/ ]
                  └── [ Host: /opt/pm/secrets/ (ro) ]
```

---

## 1. Oracle VM Specifications & Prerequisites

- **Instance Type**: OCI VM.Standard.A1.Flex (Ampere ARM64)
- **Deployment Root**: `/opt/pm`
- **Operating System**: Ubuntu 22.04 / 24.04 LTS (aarch64) or Oracle Linux 9 (aarch64)
- **Container Limits**:
  - `cpus: "1.0"`
  - `memory: 1024M`
  - `reservations: memory: 256M`
  - `pids_limit: 100`

### Verify Architecture
```bash
uname -m
# Expected: aarch64
```

---

## 2. Directory Structure & Permissions

Create the standard production directory layout:
```bash
sudo mkdir -p /opt/pm/data /opt/pm/backups /opt/pm/secrets
sudo chown -R $USER:$USER /opt/pm
sudo chown -R 10001:10001 /opt/pm/data
sudo chmod 700 /opt/pm/backups /opt/pm/secrets
cd /opt/pm

# Clone repository
git clone https://github.com/maqibkhan777/PM.git repository
cd /opt/pm/repository
```

---

## 3. Production Environment Configuration (`.env`)

Copy and configure the production environment file:
```bash
cp .env.example /opt/pm/.env
chmod 600 /opt/pm/.env
```

### Essential Settings in `/opt/pm/.env`:
```ini
APP_ENV=production
DEBUG=false
HOST=0.0.0.0
PORT=8000
DRY_RUN=true
DB_PATH=/app/data/pm_operations.db
BACKUP_DIR=/opt/pm/backups

# Phase 5: Backup Encryption (AES-256-GCM)
BACKUP_ENCRYPTION_ENABLED=true
BACKUP_ENCRYPTION_KEY=your_secure_random_backup_passphrase_here

# Phase 5: Unattended Google Drive Cloud Backup (Optional)
GDRIVE_ENABLED=false
GDRIVE_FOLDER_ID=your_google_drive_folder_id_here
GDRIVE_SERVICE_ACCOUNT_FILE=/opt/pm/secrets/google_service_account.json
GDRIVE_BACKUP_FILENAME=PM_Operations_Latest.db.gz.enc
GDRIVE_CHECKSUM_FILENAME=PM_Operations_Latest.db.gz.enc.sha256
```

---

## 4. Google Drive Cloud Backup Setup (Phase 5)

To enable unattended cloud backups:
1. Create a Google Cloud Service Account in Google Cloud Console.
2. Enable the **Google Drive API**.
3. Create and download a Service Account JSON key.
4. Place the key on the host:
   ```bash
   cp /path/to/key.json /opt/pm/secrets/google_service_account.json
   chmod 600 /opt/pm/secrets/google_service_account.json
   ```
5. Create a dedicated Google Drive folder (e.g. `PM_Backups`) and share it with the Service Account email address with **Editor** permissions.
6. Set `GDRIVE_ENABLED=true` and `GDRIVE_FOLDER_ID=<folder_id>` in `/opt/pm/.env`.

---

## 5. Build and Launch the Container

```bash
cd /opt/pm/repository
docker compose build
docker compose up -d
```

### Check Health & Container Status
```bash
docker compose ps
curl -s http://127.0.0.1:8000/health | python3 -m json.tool
curl -s http://127.0.0.1:8000/health/database | python3 -m json.tool
```

---

## 6. Automated Backup Pipeline (Phase 5)

The backup pipeline executes:
1. Online SQLite hot backup (`PRAGMA integrity_check`)
2. Gzip compression
3. AES-256-GCM authenticated encryption (`.db.gz.enc`)
4. SHA-256 checksum generation (`.sha256`)
5. Local retention enforcement (default: 14 days)
6. Quota monitoring (warning at 4 GB, critical at 5 GB)
7. Atomic remote Google Drive sync (staging upload -> download verify -> promote -> delete previous)

### Triggering Backup Manually:
```bash
/opt/pm/repository/deploy/oracle/backup_sqlite.sh
```

### Nightly Cron Job (`crontab -e`):
```cron
# Daily atomic SQLite hot backup at 02:00 AM PKT
0 2 * * * DATA_DIR=/opt/pm/data BACKUP_DIR=/opt/pm/backups REPO_DIR=/opt/pm/repository /opt/pm/repository/deploy/oracle/backup_sqlite.sh >> /var/log/pm_backup.log 2>&1
```

---

## 7. Disaster Recovery & Restore Procedures (Phase 6)

The restore utility supports `.db`, `.db.gz`, and encrypted `.db.gz.enc` backups with SHA-256 verification and automated pre-restore rollback snapshotting.

### A. Local Restore
```bash
/opt/pm/repository/deploy/oracle/restore_sqlite.sh /opt/pm/backups/pm_operations_backup_20260919_120000.db.gz.enc "your_encryption_key"
```

### B. Google Drive Recovery (`scripts/gdrive_restore.py`)
To download, verify, and extract the latest Google Drive backup in an isolated sandbox without modifying production:
```bash
python3 /opt/pm/repository/scripts/gdrive_restore.py \
    --gdrive-sa-file /opt/pm/secrets/google_service_account.json \
    --gdrive-folder-id <folder_id> \
    --encryption-key "your_encryption_key" \
    --output-db /tmp/verified_recovery.db
```

To apply the restored database to active production (requires explicit operator flags):
```bash
python3 /opt/pm/repository/scripts/gdrive_restore.py \
    --gdrive-sa-file /opt/pm/secrets/google_service_account.json \
    --gdrive-folder-id <folder_id> \
    --encryption-key "your_encryption_key" \
    --apply-to-production \
    --confirm-production-swap
```

---

## 8. VM Security Hardening Verification (Phase 7)

To execute the automated 18-point deployment verification suite:
```bash
chmod +x /opt/pm/repository/deploy/oracle/verify_oracle_deployment.sh
/opt/pm/repository/deploy/oracle/verify_oracle_deployment.sh
```
