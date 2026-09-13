# PM Operations Agent v1.2.2 — Oracle Always Free ARM64 Deployment Guide

This guide provides complete, step-by-step, reproducible instructions for deploying the **PM Operations Agent v1.2.2** on an **Oracle Cloud Infrastructure (OCI) Always Free ARM64 (Ampere A1) Virtual Machine**.

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
    [ Docker Container: pm-operations-agent:v1.2.2 ]
      ├── Non-root User: pmuser (UID 10001)
      ├── FastAPI + Uvicorn
      ├── Discord Bot Gateway (/pm slash commands)
      ├── Jira Poller & Event Pipeline
      ├── Periodic Scheduler (15-min cycle)
      └── SQLite Database (/app/data/pm_operations.db)
                  │ (Host Volume Mount)
                  ▼
          [ Host: /opt/pm/data/ ]
```

---

## 1. Oracle VM Prerequisites

- **Instance Type**: OCI VM.Standard.A1.Flex (Ampere ARM64)
- **OCPU**: 2 to 4 OCPUs (Always Free allows up to 4 OCPUs)
- **Memory**: 12 to 24 GB RAM (Always Free allows up to 24 GB)
- **Operating System**: Ubuntu 22.04 / 24.04 LTS (aarch64) or Oracle Linux 9 (aarch64)
- **Disk**: 50+ GB boot volume

### Verify ARM64 Architecture
SSH into your Oracle VM and run:
```bash
uname -m
```
*Expected Output:*
```
aarch64
```

---

## 2. Host Package & Docker Installation

### On Ubuntu ARM64:
```bash
sudo apt-get update && sudo apt-get upgrade -y
sudo apt-get install -y ca-certificates curl gnupg lsb-release sqlite3 gzip git

# Install Docker Engine & Compose plugin
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Enable and start Docker service
sudo systemctl enable --now docker
sudo usermod -aG docker $USER
```
*(Log out and log back in to apply group changes).*

---

## 3. Directory Setup & Repository Clone

Create the standard production directory structure under `/opt/pm`:
```bash
sudo mkdir -p /opt/pm/data /opt/pm/backups
sudo chown -R $USER:$USER /opt/pm
cd /opt/pm

# Clone repository
git clone https://github.com/maqibkhan777/PM.git repository
cd /opt/pm/repository
```

---

## 4. Production Environment Configuration (`.env`)

Create the production configuration file directly on the host VM:
```bash
cp .env.example /opt/pm/.env
```

Edit `/opt/pm/.env` with your preferred editor (`nano /opt/pm/.env`):
```ini
# ==============================================================================
# PM Operations Agent — Production Environment Configuration
# ==============================================================================
APP_ENV=production
DEBUG=false
HOST=0.0.0.0
PORT=8000

# Centralized Dry Run Mode (MANDATORY: Keep true for initial verification!)
DRY_RUN=true

# Database Path
DB_PATH=/app/data/pm_operations.db

# ------------------------------------------------------------------------------
# Connector: Jira Cloud REST API
# ------------------------------------------------------------------------------
JIRA_BASE_URL=https://objectsws.atlassian.net/
JIRA_EMAIL=your-service-account@objects.email
JIRA_API_TOKEN=your_actual_jira_api_token

JIRA_POLLING_ENABLED=true
JIRA_POLLING_INTERVAL_MINUTES=2
JIRA_POLLING_BATCH_SIZE=50
JIRA_POLLING_LOOKBACK_MINUTES=5
JIRA_POLLING_INITIAL_LOOKBACK_MINUTES=60
JIRA_TEAM_GROUP=Mursaleen Cluster

# ------------------------------------------------------------------------------
# Connector: Discord Webhook & Interactive Bot
# ------------------------------------------------------------------------------
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
DISCORD_BOT_TOKEN=your_actual_discord_bot_token
DISCORD_APPLICATION_ID=your_discord_application_id
DISCORD_GUILD_ID=your_discord_guild_id
DISCORD_PM_COMMAND_ENABLED=true
DISCORD_PM_ALLOWED_USERS=your_numeric_discord_user_id

# ------------------------------------------------------------------------------
# Reporting & Timezone Configuration
# ------------------------------------------------------------------------------
REPORT_TIMEZONE=Asia/Karachi
DAILY_WORKLOG_REPORT_ENABLED=true
DAILY_WORKLOG_REPORT_TIME=23:59
DAILY_WORKLOG_REPORT_TIMEZONE=Asia/Karachi
DAILY_WORKLOG_REPORT_CHANNEL=pm-alerts
DAILY_WORKLOG_EXCLUDED_ACCOUNT_IDS=712020:566cad70-4a54-42bc-bf36-0c6132fe3cf0,712020:1b564792-a3af-447c-951d-17aa5507b946,557058:8b3f9c31-7d88-473a-9351-abacc5b84933,5f83e3937d9637006ffd0436

# ------------------------------------------------------------------------------
# Background Scheduler
# ------------------------------------------------------------------------------
SCHEDULER_ENABLED=true
SCHEDULER_INTERVAL_MINUTES=15
```

### Enforce Strict File Permissions
```bash
chmod 600 /opt/pm/.env
```
*(Ensures that only root and the deployment owner can read production tokens).*

---

## 5. Build and Launch the Container

From `/opt/pm/repository`:
```bash
# Build native ARM64 Docker image
docker compose build

# Start PM Agent service in background
docker compose up -d
```

### Check Container Status
```bash
docker compose ps
```
*Expected Status:* `Up (healthy)`.

---

## 6. Verification and Health Checks

### Check Internal Health Endpoint
```bash
curl -s http://127.0.0.1:8000/health | python3 -m json.tool
```
*Expected Response:*
```json
{
    "application": "OK",
    "version": "0.1.0",
    "dry_run": true,
    "database": "OK",
    "jira": "connected",
    "mattermost": "not_configured",
    "discord": "connected",
    "discord_bot": "connected",
    "scheduler_running": true
}
```

### Inspect Container Logs
```bash
docker compose logs -f
```
Verify that:
- Database schema is initialized at `/app/data/pm_operations.db`.
- Orchestrator connects to Jira and Discord Gateway.
- Slash commands are registered.
- Scheduler starts background 15-minute polling loop.
- **NO credential tokens appear anywhere in logs.**

---

## 7. Reverse Proxy & Firewall Configuration

### OCI Security List Rules
In the OCI Console (Networking -> VCN -> Security Lists):
- **Ingress Rule 1**: TCP Port `22` (SSH) — Restricted to admin IPs.
- **Ingress Rule 2**: TCP Port `443` (HTTPS) — Source `0.0.0.0/0`.
- **Ingress Rule 3**: TCP Port `80` (HTTP) — Source `0.0.0.0/0` (for Let's Encrypt renewal).
- **DO NOT** add any ingress rule for Port `8000`!

### Nginx Reverse Proxy Setup
Install Nginx and Certbot on the host:
```bash
sudo apt-get install -y nginx certbot python3-certbot-nginx
```

Configure `/etc/nginx/sites-available/pm-agent.conf`:
```nginx
server {
    listen 80;
    server_name pm.yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # WebSocket support for interactive endpoints
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

Enable site and provision SSL certificate:
```bash
sudo ln -s /etc/nginx/sites-available/pm-agent.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d pm.yourdomain.com
```

---

## 8. Automated SQLite Hot Backups

Configure daily automated hot backups of the SQLite database without stopping the agent:

Make backup scripts executable:
```bash
chmod +x /opt/pm/repository/deploy/oracle/backup_sqlite.sh
chmod +x /opt/pm/repository/deploy/oracle/restore_sqlite.sh
```

### Configure Nightly Cron Job
Add to host crontab (`crontab -e`):
```cron
# Daily atomic SQLite hot backup at 02:00 AM PKT
0 2 * * * DATA_DIR=/opt/pm/data BACKUP_DIR=/opt/pm/backups /opt/pm/repository/deploy/oracle/backup_sqlite.sh >> /var/log/pm_backup.log 2>&1
```

### Manual Backup Test
```bash
DATA_DIR=/opt/pm/data BACKUP_DIR=/opt/pm/backups /opt/pm/repository/deploy/oracle/backup_sqlite.sh
```
Check generated backup:
```bash
ls -lh /opt/pm/backups/
```

### Disaster Recovery / Database Restore
To restore a snapshot:
```bash
DATA_DIR=/opt/pm/data /opt/pm/repository/deploy/oracle/restore_sqlite.sh /opt/pm/backups/pm_operations_backup_YYYYMMDD_HHMMSS.db.gz
```

---

## 9. Container Lifecycle & VM Reboot Persistence

- **Restart Container**:
  ```bash
  docker compose restart
  ```
- **Recreate Container (Preserving Database)**:
  ```bash
  docker compose down
  docker compose up -d
  ```
  *(Never pass `-v` to avoid deleting host volumes).*
- **VM Reboot Persistence**:
  Docker service and `restart: unless-stopped` ensure that upon host reboot, the container starts automatically and re-attaches to `/opt/pm/data/pm_operations.db`.

---

## 10. Upgrading to Future Releases

When pulling new releases (e.g. v1.2.3 / v1.3):
```bash
cd /opt/pm/repository
git fetch origin
git checkout main
git pull

# Rebuild image and restart container seamlessly
docker compose build
docker compose up -d
```
All persistent database state and configuration remain intact.
