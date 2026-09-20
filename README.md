# PM Operations Agent — V1.2.1

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688.svg)](https://fastapi.tiangolo.com)
[![SQLite](https://img.shields.io/badge/SQLite-WAL_Mode-003B57.svg)](https://www.sqlite.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A local-first, connector-based automation and operational reporting platform for Project Managers. The system monitors Jira Cloud activity in near real-time, projects state into SQLite, evaluates workflow and policy rules, dispatches mobile-first Discord reports and alerts, and executes project management operations safely through a centralized Action Engine and strict production safety guards.

---

## 1. Core Architectural Principles

1. **Decoupled Domain Core**: The application core operates strictly on domain models (`Task`, `Project`, `User`, `Event`, `Action`, `Rule`, `Notification`, `Report`). The core logic is isolated from external communication protocols.
2. **Connector Layer**: Dedicated connectors translate incoming external data into normalized events (`TaskCreated`, `TaskStatusChanged`, `TaskCommentAdded`, `TaskAssigned`, etc.) and translate domain actions (`SendMessage`, `TransitionTask`, `AddComment`, `CreateTask`, `UpdateTask`) into external API calls.
3. **Idempotent Action Engine**: Every action carries a deterministic `idempotency_key` and is tracked in SQLite to prevent duplicate executions across network retries or overlapping schedules.
4. **Centralized Dry Run**: When `DRY_RUN=true` (the default), the Action Engine intercepts and simulates all external mutating calls, emitting human-readable preview logs and audit records without altering Jira, Discord, or Mattermost.
5. **Authoritative Employee & Role Resolution**: Team assignments, saved-filter lookups, and reporting boundaries resolve deterministically against local SQLite repositories (`employee_roles`, `plugin_boards`).
6. **In-Process Periodic Scheduler**: Periodically evaluates polling loops, time-based rules, and scheduled daily reports without external broker dependencies.
7. **Production Automation Safety Guards**: Automated Jira mutations (transitions, comments) are gated behind explicit environment feature flags and disabled by default.
8. **Role-Based Discord Authorization**: Interactive slash commands enforce strict user allowlist verification, defaulting to deny-all in production.

---

## 2. System Architecture & Workflows

### Main Event & Action Processing Flow

```
   ┌────────────────────────────────────────────────────────┐
   │                       Jira Cloud                       │
   └───────────────────────────┬────────────────────────────┘
                               │ (REST API v3 / Polling every 2m)
                               ▼
   ┌────────────────────────────────────────────────────────┐
   │                      Jira Poller                       │
   └───────────────────────────┬────────────────────────────┘
                               │ (Normalized Events)
                               ▼
   ┌────────────────────────────────────────────────────────┐
   │         Event Bus & SQLite Projection Layer            │
   │  - event_store / jira_issue_state (WAL Mode)           │
   │  - notification_dedup_service                          │
   └───────────────┬────────────────────────┬───────────────┘
                   │                        │
                   ▼                        ▼
   ┌─────────────────────────────┐  ┌───────────────────────┐
   │        Rules Engine         │  │    Report Engine      │
   │  - Stale / Overdue Tasks    │  │  - Daily Worklog      │
   │  - Blocked / Reopened       │  │  - Daily Overdue      │
   │  - Comment Mentions         │  │  - PM Attention       │
   │  - Ticket Creation Policy   │  │  - Active Queue       │
   │  - Mubashir Support Rules   │  │  - Daily Activity     │
   └───────────────┬─────────────┘  └───────┬───────────────┘
                   │                        │
                   └───────────┬────────────┘
                               ▼
   ┌────────────────────────────────────────────────────────┐
   │                    Action Engine                       │
   │  - Idempotency & Validation Gate                       │
   │  - Centralized Dry Run Handler                         │
   │  - Audit Service (Sensitive data redacted)             │
   └───────────────────────────┬────────────────────────────┘
                               │
                               ▼
   ┌────────────────────────────────────────────────────────┐
   │                   Discord Connector                    │
   │  - Mobile-First Multi-Embed Presentations              │
   │  - Webhook Dispatcher & Interactive Gateway Bot        │
   └────────────────────────────────────────────────────────┘
```

### Active Queue Architecture

```
   [Resource / User Query]
              │
              ▼
   [EmployeeRoleRepository / PluginBoardRepository] ─── Resolve Authoritative Filter ID
              │
              ▼
   [Live Jira REST API v3 Query] ────────────────────── `filter = {filter_id}` (Cursor Pagination)
              │
              ├─► Success: Warm/Update `jira_issue_state` SQLite Cache
              │
              └─► Failure/Offline: Fall back to local SQLite cache projection (`source = sqlite_cache`)
              │
              ▼
   [Discord Mobile-First Embed Presentation] ────────── Clickable links, priority, status & overflow guard
```

---

## 3. Discord Slash Commands Reference

The agent registers a top-level `/pm` command with **15 subcommands** via the Discord Gateway API. Interactive access requires authorization through `DISCORD_PM_ALLOWED_USERS`.

| Subcommand | Usage | Description | Parameters |
| :--- | :--- | :--- | :--- |
| **`help`** | `/pm help` | Displays the operational command list and syntax. | None |
| **`status`** | `/pm status <ticket>` | Fetches read-only status and details for a Jira ticket. | `ticket` (required) |
| **`worklog`** | `/pm worklog [user] [date]` | Generates the daily team worklog report or individual resource breakdown. | `user` (optional), `date` (optional: `YYYY-MM-DD`) |
| **`overdue`** | `/pm overdue [user] [date]` | Generates the overdue tasks digest for the team or a specific resource. | `user` (optional), `date` (optional: `YYYY-MM-DD`) |
| **`queue`** | `/pm queue <user> [date]` | Fetches the live active Jira queue for a resource using their authoritative filter. | `user` (required), `date` (optional: `YYYY-MM-DD`) |
| **`attention`** | `/pm attention [date]` | Generates the consolidated PM Attention Digest (inactive, reopened, unassigned). | `date` (optional: `YYYY-MM-DD`) |
| **`activity`** | `/pm activity [date]` | Generates the Daily Activity Report detailing member activity and status transitions. | `date` (optional: `YYYY-MM-DD`) |
| **`transition`** | `/pm transition <ticket> <status>` | Transitions a Jira ticket to a target workflow status. | `ticket` (required), `status` (required) |
| **`assign`** | `/pm assign <ticket> <user>` | Assigns a Jira ticket to a user (strict account ID or canonical display name). | `ticket` (required), `user` (required) |
| **`comment`** | `/pm comment <ticket> <comment>` | Adds an operational comment to a Jira ticket. | `ticket` (required), `comment` (required) |
| **`create`** | `/pm create <project> <summary> ...` | Creates a new Jira task with optional description, assignee, and initial comment. | `project` (req), `summary` (req), `description`, `assignee`, `comment` |
| **`update`** | `/pm update <ticket> <field> <value>`| Updates an allowlisted field (`summary`, `description`, `priority`, `labels`, `duedate`). | `ticket` (req), `field` (req), `value` (req) |
| **`notify`** | `/pm notify <user> <message>` | Dispatches a notification to a Discord channel or user. | `user` (required), `message` (required) |
| **`report`** | `/pm report <name> [user] [date]` | *[Deprecated]* Compatibility shim; routes to canonical `/pm <name>` handlers. | `name` (required: choices), `user` (opt), `date` (opt) |
| **`message`** | `/pm message <user> <message>` | *[Deprecated]* Compatibility shim; routes to `/pm notify`. | `user` (required), `message` (required) |

---

## 4. Automated Jobs & Background Schedulers

Background evaluations execute in an asynchronous event loop within `PeriodicScheduler`:

| Job Name | Schedule | Default State | Config Flag | Description |
| :--- | :--- | :--- | :--- | :--- |
| **Jira Poller** | Every 2 min | **Enabled** | `JIRA_POLLING_ENABLED=true` | Queries updated issues across monitored Jira projects/groups and publishes normalized events. |
| **Stale Task Evaluation** | Every 15 min | **Enabled** | `SCHEDULER_ENABLED=true` | Evaluates in-progress tasks inactive for $\ge 24\text{h}$. PM channel alert muted by default (`STALE_TASK_NOTIFY_PM=false`). |
| **Overdue Task Evaluation**| Every 15 min | **Enabled** | `SCHEDULER_ENABLED=true` | Evaluates tasks past due date. Individual PM channel alert muted by default (`OVERDUE_NOTIFY_PM=false`). |
| **Daily Worklog Report** | Daily at 23:59 PKT | **Enabled** | `DAILY_WORKLOG_REPORT_ENABLED=true` | Dispatches daily team worklog summary embed to `#pm-alerts`. Idempotent (once per day). |
| **Performance Analysis** | Every 60 min | **Enabled** | `PERFORMANCE_ANALYSIS_ENABLED=true` | Re-computes velocity, historical distributions, and confidence tiers in SQLite. |
| **Daily Overdue Digest** | Daily at 08:40 PKT | **Disabled** | `OVERDUE_DIGEST_ENABLED=false` | Aggregates all overdue cluster tasks into a single morning embed. |
| **PM Attention Digest** | Daily at 08:40 PKT | **Disabled** | `PM_ATTENTION_DIGEST_ENABLED=false` | Aggregates inactive, reopened, and unassigned issues into a morning embed. |
| **Daily Activity Report** | Daily at 08:40 PKT | **Disabled** | `DAILY_ACTIVITY_REPORT_ENABLED=false` | Scheduled Discord broadcast for activity metrics (available on-demand via `/pm activity`). |
| **Active Epic Review** | Every 15 min | **Disabled** | `EPIC_REVIEW_ENABLED=false` | **Mutating Job**: Synchronizes active Epic statuses based on child sprint ticket progress. |
| **Mubashir Stale Support** | Every 15 min | **Disabled** | `MUBASHIR_STALE_SUPPORT_ENABLED=false` | **Mutating Job**: Posts reminder comments on internal Support tickets inactive $\ge 3$ business days. |
| **Mubashir Support Rule** | Event-Driven | **Disabled** | `MUBASHIR_SUPPORT_RULE_ENABLED=false` | **Mutating Job**: Enforces sprint assignment and product label on created Support tickets. |

---

## 5. Production Safety & Access Controls

### Automated Jira Mutation Guards
All automated background operations capable of creating Jira comments or transitioning issues are guarded by explicit Boolean switches:
```ini
# Production Automation Safety Guards (Default: false)
EPIC_REVIEW_ENABLED=false
MUBASHIR_STALE_SUPPORT_ENABLED=false
MUBASHIR_SUPPORT_RULE_ENABLED=false
```
When set to `false`, the scheduler and rules engine completely bypass mutation logic.

### Discord Role-Based Authorization
Interactive `/pm` commands check the user's Discord ID against `DISCORD_PM_ALLOWED_USERS`:
* **Production (`APP_ENV=production`)**: An empty allowlist strictly **denies all users**.
* **Development (`APP_ENV!=production`)**: An empty allowlist permits local testing.
* **Configured Allowlist**: Comma-separated Discord numeric user IDs (e.g., `DISCORD_PM_ALLOWED_USERS=123456789012345678`).
* **Wildcard (`DISCORD_PM_ALLOWED_USERS=*`)**: Explicitly permits all authenticated guild members.

### Centralized Dry Run
```ini
DRY_RUN=true
```
When enabled, all mutating operations (`TRANSITION_TASK`, `ASSIGN_TASK`, `ADD_COMMENT`, `CREATE_TASK`, `UPDATE_TASK`, `SEND_MESSAGE`) produce simulated execution results (`DRY_RUN_SIMULATED`) and audit entries without making outbound network write calls.

---

## 6. Report Presentation Standards

All operational reports share a standardized mobile-first design system:
* **Rich Embed Layouts**: Clear visual hierarchy, category headers, and color branding.
* **Clickable Jira Links**: Issue keys format as Markdown links (`[WSSS-326](https://domain.atlassian.net/browse/WSSS-326)`).
* **Multi-Embed Chunking**: Handled via `_chunk_embed_lines()` with deterministic limits ($\le 10$ embeds, $\le 3,800$ chars per description, $\le 5,800$ cumulative text budget).
* **Explicit Overflow Handling**: Reports exceeding budget display atomic overflow summaries (`• ... and N more item(s)`) rather than arbitrary text slicing.
* **Standard Report Colors**:
  * 🟣 Purple (`0x9B59B6`): Daily Worklog & Daily Activity
  * 🔴 Red (`0xE74C3C`): Overdue Digest
  * 🔵 Blue (`0x3498DB`): Active Queue
  * 🟠 Amber (`0xF39C12`): PM Attention Digest
  * 🟢 Green (`0x2ECC71`): Empty States ("No overdue tasks", "No attention items")

---

## 7. Local Installation & Quick Start

### Prerequisites
* Python 3.10+
* Windows, macOS, or Linux

### Step 1: Clone and Create Virtual Environment
```powershell
python -m venv .venv

# On Windows (PowerShell):
.venv\Scripts\Activate.ps1
# On Linux/macOS:
source .venv/bin/activate
```

### Step 2: Install Dependencies
```powershell
pip install -r requirements.txt
```

### Step 3: Configure Environment
Copy the example configuration:
```powershell
copy .env.example .env
```
Configure your credentials in `.env`:
```ini
APP_ENV=development
DEBUG=true
DRY_RUN=true

# Jira Cloud
JIRA_BASE_URL=https://your-domain.atlassian.net
JIRA_EMAIL=pm-agent@your-domain.com
JIRA_API_TOKEN=your_jira_api_token
JIRA_TEAM_GROUP=Mursaleen Cluster

# Discord
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
DISCORD_BOT_TOKEN=your_bot_token
DISCORD_APPLICATION_ID=your_app_id
DISCORD_PM_ALLOWED_USERS=your_numeric_discord_user_id
```

### Step 4: Run the Application
```powershell
python run.py
```
FastAPI runs at `http://127.0.0.1:8000`. Interactive OpenAPI documentation is available at `http://127.0.0.1:8000/docs`.

---

## 8. Production Deployment Safety Checklist

Before deploying the PM Operations Agent to production:

1. **Verify Environment Mode**: Set `APP_ENV=production` and `DEBUG=false` in your production environment.
2. **Configure Authorized Discord Users**: Add your authorized numeric Discord user ID(s) to `DISCORD_PM_ALLOWED_USERS`. An empty value in production denies all commands.
3. **Keep Safety Guards Disabled Initially**: Ensure `EPIC_REVIEW_ENABLED=false`, `MUBASHIR_STALE_SUPPORT_ENABLED=false`, and `MUBASHIR_SUPPORT_RULE_ENABLED=false` until manual review is complete.
4. **Validate in Dry Run**: Run with `DRY_RUN=true` first to observe incoming events, state projections, and simulated actions in the audit log.
5. **Database Persistence**: Ensure the directory containing `DB_PATH` is persistent across container/process restarts. SQLite runs in `WAL` mode for high-concurrency read/write operations.
6. **Timezone Setting**: Verify `REPORT_TIMEZONE=Asia/Karachi` matches your operational expectations for daily scheduled reports.

---

## 9. Testing & Quality Assurance

The repository includes a comprehensive Pytest test suite covering event ingestion, rules, reports, multi-embed formatting, Discord slash commands, and production safety guards:

```powershell
pytest
```

**Verified Test Status**: **376 passed, 1 skipped** (100% operational test pass rate).

---

## 10. AI Foundation Architecture (Phase 1 — Decision Support)

The PM Operations Agent includes an isolated AI decision support layer (`app/services/ai/`) designed to provide structured insights without compromising operational safety:

* **Decision Support Only**: AI serves purely as an advisory intelligence layer. It produces structured data models (`AIDecision`), not executable code.
* **No Direct External Calls**: AI never calls Jira or Discord connectors directly. All external mutations remain strictly governed by the centralized Action Engine.
* **Strict Safety Gate & Approval Boundary**: All proposed actions from AI must pass through `AISafetyGate` and require human or supervisor approval (`requires_approval=True`). Destructive operations are strictly rejected.
* **Provider Abstraction**: Provider interaction is decoupled behind the `AIProvider` protocol, enabling seamless plugging of models (Gemini, Anthropic, OpenAI, local LLMs) or deterministic test mocks (`MockAIProvider`, `NullAIProvider`).
* **Bounded Context**: `ContextBuilder` aggregates strictly scoped task, resource, and metric domain summaries, systematically redacting secrets and credentials before AI evaluation.
* **Disabled by Default**: AI features are guarded by `AI_ENABLED=false` and remain completely inactive unless explicitly enabled.
