# PM Operations Agent — V0.1

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688.svg)](https://fastapi.tiangolo.com)
[![SQLite](https://img.shields.io/badge/SQLite-WAL_Mode-003B57.svg)](https://www.sqlite.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A local-first, connector-based automation platform designed for Project Managers to monitor team activity, enforce workflow rules, dispatch notifications and direct messages, generate daily reports, and safely execute project management actions through a centralized Action & Approval Engine.

---

## 1. Core Architectural Principles

1. **Decoupled Core**: The application core operates strictly on generic domain models (`Task`, `Project`, `User`, `Event`, `Action`, `Rule`, `Notification`, `Report`). The core has **zero** direct dependencies on Jira, Mattermost, or Discord.
2. **Connector Architecture**: External systems translate incoming webhooks into normalized domain events (`TaskCreated`, `TaskStatusChanged`, `TaskCommentAdded`, etc.) and translate domain actions (`SendMessage`, `TransitionTask`) into external API calls.
3. **Idempotent Action Engine**: Every action carries a deterministic `idempotency_key` and is checked against the database before execution to prevent duplicate writes on network retries.
4. **Centralized Dry Run**: When `DRY_RUN=true`, the Action Engine intercepts and simulates all external mutating calls centrally, producing human-readable previews and audit logs without making mutating external network requests.
5. **Strict User Mapping**: The system strictly resolves Jira users to Mattermost accounts (Explicit Mapping ➔ Verified Email ➔ Exact Name). If unresolved, the system **never guesses**—it creates a `USER_MAPPING_REQUIRED` state and alerts the PM via Discord.
6. **In-Process Background Scheduler**: Periodically evaluates time-based rules (`StaleTaskRule`, `OverdueRule`) without requiring external infrastructure like Redis or Celery.

---

## 2. High-Level Architecture

```
                          EXTERNAL SYSTEMS
                                 │
                 ┌───────────────┼───────────────┐
                 │               │               │
                 ▼               ▼               ▼
            Jira Cloud       Mattermost       Discord
                 │               │               │
                 └───────────────┼───────────────┘
                                 │
                          CONNECTOR LAYER
                                 │
                                 ▼
                        EVENT NORMALIZATION
                                 │
                                 ▼
                             EVENT BUS
                                 │
              ┌──────────────────┼──────────────────┐
              ▼                  ▼                  ▼
        RULES ENGINE       REPORT ENGINE       FUTURE AI
              │                  │
              └─────────┬────────┘
                        ▼
                  ACTION ENGINE
                        │
                 APPROVAL ENGINE
                        │
                 CENTRAL DRY RUN
                        │
                  AUDIT LOGGER
                        │
                 CONNECTOR LAYER
                        │
              ┌─────────┼─────────┐
              ▼         ▼         ▼
            Jira    Mattermost Discord
```

---

## 3. Project Structure

```
pm-operations-agent/
│
├── app/
│   ├── config/
│   │   └── settings.py               # Pydantic environment configuration & settings
│   ├── database/
│   │   ├── connection.py             # Thread-safe SQLite connection manager
│   │   ├── schema.py                 # DDL definitions & table initialization
│   │   └── repositories.py           # Repositories for Events, Users, Mappings, Rules, Actions, Audits
│   ├── core/
│   │   ├── models/
│   │   │   ├── domain.py             # Generic domain models (Task, User, Message, ActionPreview)
│   │   │   └── enums.py              # TaskStatus, ActionType, Capability, SecurityLevel
│   │   ├── events/
│   │   │   ├── base.py               # BaseEvent abstract class
│   │   │   ├── types.py              # Concrete normalized & domain events
│   │   │   └── bus.py                # In-process asynchronous Event Bus with deduplication
│   │   ├── rules/
│   │   │   ├── base.py               # BaseRule interface
│   │   │   ├── engine.py             # RulesEngine coordinator
│   │   │   └── builtin.py            # ActiveWorkRule, StaleTaskRule, OverdueRule, BlockedRule, ReopenedRule
│   │   ├── actions/
│   │   │   ├── base.py               # BaseAction & ActionResult
│   │   │   ├── types.py              # Action factory constructors
│   │   │   └── engine.py             # ActionEngine router, idempotency & central Dry Run
│   │   ├── approvals/
│   │   │   └── engine.py             # Approval classification (AUTO, APPROVAL_REQUIRED, BLOCKED)
│   │   └── reports/
│   │       └── daily_report.py       # Daily PM Activity Report generator
│   ├── connectors/
│   │   ├── base/
│   │   │   └── connector.py          # BaseConnector ABC & Capability enums
│   │   ├── jira/
│   │   │   ├── client.py             # Jira REST API client (httpx)
│   │   │   ├── normalizer.py         # Jira webhook JSON -> BaseEvent normalizer
│   │   │   └── connector.py          # JiraConnector implementation
│   │   ├── discord/
│   │   │   ├── formatter.py          # Rich Embed builder (color-coded alerts)
│   │   │   ├── webhook_connector.py  # DiscordWebhookConnector (outbound alerts)
│   │   │   └── bot_connector.py      # DiscordBotConnector skeleton for future commands
│   │   └── mattermost/
│   │       ├── client.py             # Mattermost REST API v4 client
│   │       └── connector.py          # MattermostConnector (DMs & channel posts)
│   ├── services/
│   │   ├── user_mapping_service.py   # Jira <-> Mattermost strict user resolution
│   │   ├── notification_deduplication.py # Cooldown tracker to prevent notification spam
│   │   ├── scheduler.py              # Background worker for time-based rules
│   │   ├── audit_service.py          # Sensitive-data redacted audit logger
│   │   └── orchestrator.py           # Startup, lifecycle, and event wiring
│   ├── api/
│   │   ├── app.py                    # FastAPI application setup
│   │   └── routes/                   # Health, Webhooks, Events, Actions, Rules, Reports, Mappings
│   └── utils/
│       ├── logger.py                 # Structured logger with automatic credential redaction
│       └── time.py                   # Timezone-aware ISO 8601 utilities
├── tests/                            # Comprehensive Pytest test suite (41 tests, 100% pass)
├── scripts/
│   ├── seed_demo_data.py             # Pre-seed users, mappings, rules, and events
│   └── simulate_webhook.py           # CLI tool to test Scenarios A, B, C, D locally
├── data/                             # SQLite persistent storage (created automatically)
├── .env.example                      # Template environment variables
├── .gitignore                        # Git exclusion rules
├── pytest.ini                        # Pytest configuration
├── requirements.txt                  # Python dependencies
├── README.md                         # This documentation
└── run.py                            # Server entrypoint
```

---

## 4. Local Installation & Setup

### Prerequisites
* Python 3.10 or higher
* Windows, macOS, or Linux

### Step 1: Clone and Create Virtual Environment
```powershell
# Create virtual environment
python -m venv .venv

# Activate virtual environment
# On Windows (PowerShell):
.venv\Scripts\Activate.ps1
# On Linux/macOS:
source .venv/bin/activate
```

### Step 2: Install Dependencies
```powershell
pip install -r requirements.txt
```

### Step 3: Configure Environment Variables
Copy the template configuration:
```powershell
copy .env.example .env
```
Edit `.env` to configure your credentials:
```ini
APP_ENV=development
DEBUG=true
DRY_RUN=true

# Jira Cloud
JIRA_BASE_URL=https://your-company.atlassian.net
JIRA_EMAIL=pm-agent@your-company.com
JIRA_API_TOKEN=your_jira_api_token

# Discord Webhook
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/your/webhook/url

# Mattermost
MATTERMOST_URL=https://mattermost.your-company.com
MATTERMOST_TOKEN=your_bot_access_token

# Rule Thresholds
STALE_TASK_HOURS=24
SCHEDULER_ENABLED=true
SCHEDULER_INTERVAL_MINUTES=15
```

### Step 4: Seed Demo Data
```powershell
python scripts/seed_demo_data.py
```

### Step 5: Run the Server
```powershell
python run.py
```
The server will start at `http://127.0.0.1:8000`. Interactive OpenAPI documentation is available at `http://127.0.0.1:8000/docs`.

---

## 5. Running Tests

Run the complete automated test suite (mocking all external APIs with zero required credentials):
```powershell
python -m pytest -v
```
All 41 unit and integration tests will execute and pass in under 3 seconds.

---

## 6. End-to-End Local Simulation

Use the simulation script to test all built-in scenarios without a live Jira webhook:

```powershell
# Simulate all scenarios
python scripts/simulate_webhook.py --scenario all

# Or simulate individual scenarios:
python scripts/simulate_webhook.py --scenario a  # Status transition 'To Do' -> 'In Progress'
python scripts/simulate_webhook.py --scenario b  # Comment added on 'To Do' (WorkflowViolation)
python scripts/simulate_webhook.py --scenario c  # Inactivity > 24h (StaleTask alert + DM reminder)
python scripts/simulate_webhook.py --scenario d  # Unmapped user (USER_MAPPING_REQUIRED alert)
```

---

## 7. Webhook & Cloudflare Tunnel Setup

To connect live Jira Cloud webhooks to your local machine for free:

### 1. Start Cloudflare Tunnel
Install [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/) and run:
```powershell
cloudflared tunnel --url http://127.0.0.1:8000
```
Cloudflare will output a public HTTPS URL such as:
`https://random-subdomain.trycloudflare.com`

### 2. Configure Jira Cloud Webhook
1. Go to **Jira Settings** ➔ **System** ➔ **WebHooks** (`https://your-domain.atlassian.net/plugins/servlet/webhooks`).
2. Click **Create a Webhook**.
3. Set URL to:
   ```
   https://random-subdomain.trycloudflare.com/webhooks/jira
   ```
4. Check Events:
   * **Issue**: Created, Updated
   * **Comment**: Created
   * **Worklog**: Created
5. Click **Save**.

The `/webhooks/jira` endpoint will receive payloads, validate, deduplicate, persist with status `RECEIVED`, and acknowledge Jira in <20ms while processing through the Event Bus in the background.

---

## 8. REST API Reference

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/` | Application status banner and version info |
| `GET` | `/health` | System health (App, DB, Jira, Mattermost, Discord, Dry Run) |
| `GET` | `/health/connectors` | Detailed connector health and machine-readable capabilities |
| `POST` | `/webhooks/jira` | Rapid webhook ingestion endpoint (returns HTTP 202) |
| `GET` | `/events` | Paginated list of normalized events (`?limit=50&offset=0`) |
| `GET` | `/events/{id}` | Retrieve specific event details |
| `POST` | `/events/{id}/retry` | Manually reprocess a failed or retry-pending event |
| `GET` | `/actions` | List generated actions and execution status |
| `GET` | `/actions/{id}` | Retrieve specific action details and preview |
| `POST` | `/actions/{id}/approve` | Approve and execute a pending approval action |
| `GET` | `/rules` | List all workflow rules, configurations, and enabled state |
| `POST` | `/rules/{name}/toggle` | Dynamically enable or disable a workflow rule |
| `GET` | `/reports/daily` | Generate structured Daily PM Activity Report (`?date=YYYY-MM-DD`) |
| `POST` | `/reports/daily/send` | Generate and broadcast Daily PM Activity Report to Discord |
| `GET` | `/user-mappings` | List all Jira ↔ Mattermost user mappings |
| `POST` | `/user-mappings` | Create or update Jira ↔ Mattermost user mapping |

---

## 9. Built-in Workflow Rules

1. **Active Work Detection (`ActiveWorkRule`)**:
   * **Trigger**: Task status is `To Do` and an activity occurs (comment added, work logged, subtask progressed).
   * **Action**: Emits `WorkflowViolation` alert to Discord PM channel. Does not modify Jira status automatically.
2. **Stale Task Detection (`StaleTaskRule`)**:
   * **Trigger**: Task status is `In Progress` and no activity has occurred for `> STALE_TASK_HOURS` (default 24h).
   * **Action**: Dispatches Discord alert to PM and sends a direct message reminder to the assigned resource on Mattermost.
3. **Overdue Task Detection (`OverdueRule`)**:
   * **Trigger**: `due_date < current_time` and task status is not `Done`/`Closed`.
   * **Action**: Dispatches Discord overdue alert with due date and assignee.
4. **Blocked Task Detection (`BlockedRule`)**:
   * **Trigger**: Task is transitioned to `Blocked` or flagged.
   * **Action**: Dispatches Discord high-priority alert.
5. **Reopened Task Detection (`ReopenedRule`)**:
   * **Trigger**: Task moves from `Done`/`Resolved` back to active status (`To Do` / `In Progress`).
   * **Action**: Dispatches Discord alert detailing who reopened the issue.

---

## 10. Example Discord & Mattermost Messages

### Discord Workflow Violation Alert (Red Embed)
```text
🚨 Jira Workflow Alert
Activity detected on CF7-421 while remaining in To Do.

Issue:      CF7-421
Project:    CF7 Apps
Resource:   Ahsan Amin
Title:      Payment Gateway Testing
Details:    Activity detected (TaskCommentAdded) while task remains in 'To Do' status.
```

### Discord Stale Task Alert (Amber Embed)
```text
⚠️ Stale Task Alert
Task CF7-421 has been In Progress for >24h without activity.

Issue:       CF7-421
Assigned to: Ahsan Amin
Inactivity:  28.5 hours
Title:       Payment Gateway Testing
```

### Mattermost Direct Message to Assignee
```text
Hey Ahsan Amin,

CF7-421 (Payment Gateway Testing) has not been updated for 28 hours.
Please share the current status, update the ticket, or let us know if there are any blockers.
```

### Discord Missing User Mapping Warning (Amber Embed)
```text
⚠️ Mattermost Mapping Required
No verified Mattermost account mapping exists for this resource.

Jira User:       New Contractor (`jira-user-unmapped-999`)
Intended Action: Send Stale Task Mattermost Direct Message
Status:          ❌ Direct Message was NOT sent.
```

---

## 11. Security Model & Credential Redaction

* **Zero Hard-Coded Credentials**: All secrets live in `.env` and environment variables.
* **Automatic Redaction**: `app/utils/logger.py` and `app/services/audit_service.py` intercept and scrub API tokens, Bearer tokens, Discord webhook URLs, passwords, and authorization headers from logs and database audit entries.
* **Capability Security Levels**: Connector capabilities are strictly partitioned into `READ`, `WRITE`, and `DESTRUCTIVE`. Destructive operations (e.g., deleting issues) are rejected unconditionally.
* **No Arbitrary HTTP Requests**: The Action Engine only executes registered typed actions against configured connectors.

---

## 12. Known Limitations (V0.1)

1. In-process event bus and scheduler run within the local FastAPI process (designed for single-instance local deployments).
2. Discord interactive slash commands (`/status`, `/assign`) are stubbed in `DiscordBotConnector` for V0.2+; V0.1 handles all outbound alerts via `DiscordWebhookConnector`.
3. Jira JQL search runs on-demand without local caching of complete issue backlogs.

---

## 13. Roadmap to V0.2+ (AI & Autonomous Operations)

Because V0.1 strictly implements the decoupled Action Engine and domain event architecture, an LLM or autonomous AI agent layer can be integrated seamlessly in future versions:

```
                 AI / LLM AGENT (V0.2+)
                           │
             ┌─────────────┴─────────────┐
             │                           │
          Queries                     Actions
             │                           │
             ▼                           ▼
       Core REST APIs              Action Engine
             │                           │
      (Task & Event state)         Approval Layer
                                         │
                                     Connectors
```

* **No direct API access**: The AI layer will **never** make direct HTTP calls to Jira or Mattermost.
* **Tool-based execution**: The AI agent will invoke domain tools (`TransitionTask`, `SendMessage`, `AddComment`) that pass through the Action Engine, Approval Engine, Idempotency checks, and Audit logging automatically.
