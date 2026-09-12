# Notification Verification Criteria (Discord & Mattermost)

A test is **NOT** considered passed merely because an HTTP connector returns 200/204.
The notification payload must be verified against human-visible presentation standards, and delivery limitations must be explicitly declared.

---

## 1. Discord Notification Standards & Verification

### 1.1 Live Webhook Delivery Protocol
- **Delivery Mode**: **REAL** (HTTP POST via `DiscordWebhookConnector`).
- **Read-Back Mode**: **HUMAN_REQUIRED**. Incoming Discord webhooks are write-only by design and do not permit bot message querying without Discord Bot OAuth scopes.
- **Automated Verification**: Embed schema, field names, and clickable browse URLs (`https://objectsws.atlassian.net/browse/TREN-378`) are automatically verified via `RT-UNIT-NOTIF-001`.

### 1.2 Embed Presentation Criteria
- **Task Assigned Embed**:
  - Title: `Task Assigned: TREN-378`
  - Color: Blue (`0x3498DB`)
  - Fields: Issue link (`[TREN-378](https://objectsws.atlassian.net/browse/TREN-378)`), Assignee Name (`Aqib Khan` / `Mubashir Butt`), Status.
- **Task Blocked Embed**:
  - Title: `🚨 Task Blocked: TREN-378`
  - Color: Red (`0xE74C3C`)
  - Fields: Issue link, Current Assignee, Actor, Severity: `High`.
- **Workflow Violation Embed**:
  - Title: `⚠️ Workflow Violation: TREN-378`
  - Color: Orange (`0xE67E22`)
  - Description: Warns of activity on non-active issue (`To Do`).

---

## 2. Mattermost Notification Standards & Safety

### 2.1 Environmental Status
- **Current State**: `NOT_CONFIGURED` in local test environment.
- **Classification**: Live Mattermost delivery scenarios are marked `NOT_EXECUTED` / `ENVIRONMENT_REQUIRED`.

### 2.2 Safety & No-Guessing Guarantee
- **Unmapped User Handling**: If a Jira account ID has no verified mapping in `user_mappings`, the system **REFUSES** to guess and records `USER_MAPPING_REQUIRED`.
- **Integration Proof**: Verified via `RT-INT-MM-001` (zero random direct messages dispatched).

---

## 3. Evidence Capture & Redaction

For every scenario executed by `QARunner`, a sanitized JSON file is persisted in `qa/evidence/<test_id>_<run_id>.json`.
All secrets, Jira API tokens, and webhook URLs are scrubbed and replaced with `[REDACTED]`.
