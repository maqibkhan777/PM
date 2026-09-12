# PM Operations Agent — Real-Time QA Framework

This directory contains the authoritative living Quality Assurance (QA) architecture, specifications, scenarios, evidence standards, and execution logs for the [PM Operations Agent](https://github.com/maqibkhan777/PM).

---

## 1. QA Architecture & Philosophy

The objective of this QA framework is **NOT** merely to add isolated mock unit tests. It validates the complete end-to-end operational pipeline across real components:

```text
Real Jira Mutation (TREN-378)
      ↓
Poller / Ingestion (TaskAssigned, TaskStatusChanged, TaskCommentAdded)
      ↓
Event Persistence (SQLite events table)
      ↓
EventBus (Publish & Dispatch)
      ↓
Rules Engine (AssignmentRule, StatusChangedRule, CommentNotificationRule)
      ↓
Action Engine (SendNotification, AddComment)
      ↓
Discord Webhook (Real dispatch / human-required verification)
      ↓
Audit Log Persistence (SQLite audit table)
      ↓
Remote Jira REST API State Confirmation
```

### Strict Tier Separation & Honest Classification

The QA suite strictly enforces four distinct execution tiers:

| Tier | Active Count | Identifier | Description | External Network / Mutation |
|:---|:---:|:---|:---|:---|
| **Live E2E** | **6** | `LIVE_E2E` | Real Jira REST API mutations against `TREN-378`, real-time PM Agent ingestion, rules evaluation, real Discord webhook dispatch, and remote read-back state confirmation. | Real external mutations & queries (Jira Cloud REST API, Discord Webhook) |
| **Integration** | **9** | `INTEGRATION` | Multi-component pipelines (Event Bus -> Rules Engine -> Action Engine -> SQLite DB -> DataQualityValidator). Tests deduplication, stale task rules, rate-limits, blocked/reopened workflows, and scheduler loop health. | Local SQLite DB, internal event pipeline, simulated connector responses |
| **Unit** | **3** | `UNIT` | Pure in-memory security token redaction, notification embed schemas, and safeguard assertions. | None (pure in-memory) |
| **Dry Run E2E** | **1** | `DRY_RUN_E2E` | End-to-end Action Engine execution with `dry_run=True`. Confirms action simulation previews, idempotency keys, and zero external mutation. | Local SQLite DB / ActionEngine (Network calls blocked) |

> [!IMPORTANT]
> **Anti-Faking Policy**: A test is never classified as `LIVE_E2E` if connectors or external state changes are simulated. Running a test through `scripts/run_live_qa.py` does not alter its tier; actual external behavior determines the tier.

---

## 2. Primary Live Test Fixture & Safety Controls

### Primary Protected Fixture: `TREN-378`
- **Issue URL**: [https://objectsws.atlassian.net/browse/TREN-378](https://objectsws.atlassian.net/browse/TREN-378)
- **Project**: `TREN`
- **Safeguard Rule**: To prevent unintended mutations on production tickets, the live test suite strictly rejects mutations on any ticket other than `TREN-378` (enforced by `settings.assert_live_qa_safe`).

### Safeguard Gates
1. **`LIVE_QA_ENABLED=false` Gate**: When disabled, all live scenarios immediately report `NOT_EXECUTED` (Never a false `PASS`).
2. **Issue Allowlist Gate**: Any ticket key other than `TREN-378` (e.g. `--issue WRONG-999`) is immediately rejected with `INVALID_LIVE_TEST` and exits with an error code before making any external API call.
3. **Secret Redaction**: All API tokens, webhooks, and bot tokens are automatically scrubbed and saved as `[REDACTED]` in evidence files and logs.

### Authoritative Assignee Switching
Live assignment tests toggle exclusively between:
1. **Aqib Khan (PM)**: `712020:566cad70-4a54-42bc-bf36-0c6132fe3cf0` (resolved via `settings.MY_JIRA_ACCOUNT_ID`)
2. **Mubashir Butt**: `712020:e268bcd8-d981-4b4d-992d-d5694745df8b`

---

## 3. Environment-Dependent Coverage & Limitations

To maintain strict truthfulness, capabilities that require external infrastructure not available in the current environment are explicitly documented:

- **Discord Notification Verification**:
  - Discord webhook dispatch is **REAL** (HTTP POST to `#pm-alerts`).
  - Because incoming webhooks are write-only without read-back capabilities, automated read-back is marked `HUMAN_REQUIRED`. Embed structure and clickable links are verified via unit tests (`RT-UNIT-NOTIF-001`).
- **Mattermost Verification**:
  - Mattermost is currently `NOT_CONFIGURED` in this environment.
  - Live Mattermost tests are marked `NOT_EXECUTED` / `ENVIRONMENT_REQUIRED`.
  - Zero-guessing and user mapping rejection are verified through integration testing (`RT-INT-MM-001`).
- **Jira Webhooks**:
  - Real Jira Cloud webhook delivery requires a publicly accessible HTTPS endpoint.
  - In local environments, Jira ingestion is exercised live via polling (`JiraPoller`) and verified via simulation in integration tests (`RT-INT-DEDUP-001`).
- **Scheduler Live Timing**:
  - Periodic scheduler loop resilience is verified through integration testing (`RT-INT-SCHED-001`).

---

## 4. Directory Layout

```text
qa/
├── README.md                      # This document (Architecture, Tiers, & Safety)
├── QA.md                          # Master Living QA Matrix & Final Acceptance Status
├── test-data.md                   # Canonical fixtures, account IDs, and sample payloads
├── live/
│   ├── README.md                  # Live scenario execution instructions
│   ├── jira-scenarios.md          # Jira state transitions, comments, and worklogs
│   ├── notification-scenarios.md  # Discord & Mattermost visual presentation standards
│   └── execution-log.md           # Chronological run history with run IDs & evidence links
└── evidence/
    └── *.json                     # Sanitized structured test evidence files
```

---

## 5. Execution Commands

### Running Live E2E QA Suite Against TREN-378
```bash
python scripts/run_live_qa.py --tier live --issue TREN-378
```

### Running Dry-Run QA Suite (Simulated Mutations)
```bash
python scripts/run_live_qa.py --tier dry_run
```

### Running Integration Suite Only
```bash
python scripts/run_live_qa.py --tier integration
```

### Running Full Automated Pytest Suite
```bash
pytest -v
```

---

## 6. Mandatory Living QA Rules For Future Development

Every new feature or modification to the PM Operations Agent **MUST** adhere to the living QA protocol:

1. Add unit and integration tests covering happy path, edge cases, and failure modes.
2. Add idempotency and deduplication coverage where applicable.
3. Update `qa/QA.md` with new/modified test cases.
4. If system behavior changes intentionally: **Never delete historical test cases.** Mark them `DEPRECATED` with an explicit reason and append the replacing test cases.
5. Never label a simulated test as `LIVE_E2E`.
