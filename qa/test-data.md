# Canonical QA Test Data & Fixtures

This document contains the authoritative test fixtures, account IDs, channel configurations, transition identifiers, sample payloads, and evidence schema used for all QA runs against the PM Operations Agent.

---

## 1. Primary Jira Fixture Ticket

- **Ticket Key**: `TREN-378`
- **Jira Issue URL**: [https://objectsws.atlassian.net/browse/TREN-378](https://objectsws.atlassian.net/browse/TREN-378)
- **Project**: `TREN`
- **Issue Type**: `Task`
- **Supported Transitions**:
  - `To Do` -> `In Progress`
  - `In Progress` -> `Blocked`
  - `Blocked` -> `In Progress`
  - `In Progress` -> `Done`
  - `Done` -> `In Progress` (Reopened)
  - `Done` -> `To Do`

---

## 2. Authoritative Accounts & Identity Mappings

| Person | Jira Account ID | Canonical Mattermost Username | Role / Designation | Exclusion Status |
|:---|:---|:---|:---|:---:|
| **Mubashir Butt** | `712020:e268bcd8-d981-4b4d-992d-d5694745df8b` | `mubashir.butt` | Senior Backend Engineer | Active |
| **Aqib Khan (PM)** | `712020:566cad70-4a54-42bc-bf36-0c6132fe3cf0` | `aqib.khan` | Product Manager | Excluded (PM) |
| **Ahsan Amin** | `712020:1b564792-a3ab-447c-951d-17aa5507b946` | `ahsan.amin` | Principal Architect | Excluded |
| **Ali Asghar** | `557058:8b3f9c31-7d88-473a-9351-abacc5b84933` | `ali.asghar` | Lead QA Engineer | Excluded |
| **Adeel Munir** | `5f83e3937d9637006ffd0436` | `adeel.munir` | VP Engineering | Excluded |

---

## 3. Safe Action Engine Safelist

The live test suite restricts mutations strictly to non-destructive operations:

1. `ADD_COMMENT`: Appends a verification comment to `TREN-378`.
2. `CHANGE_PRIORITY`: Changes issue priority (e.g., `Medium` -> `High` -> `Medium`).
3. `ASSIGN_TASK`: Switches assignee between Mubashir Butt and Aqib Khan.
4. `TRANSITION_TASK`: Executes supported workflow transitions on `TREN-378`.
5. `SEND_NOTIFICATION`: Sends formatted embed alert to Discord test channel.
6. `SEND_MESSAGE`: Sends direct message to mapped Mattermost user (when configured).

> [!CAUTION]
> Destructive actions such as `DELETE_ISSUE` or bulk data mutations are strictly prohibited in the live QA framework.

---

## 4. Structured Evidence JSON Schema

Every executed scenario persists a sanitized evidence record under `qa/evidence/<test_id>_<run_id>.json` containing:

```json
{
  "test_id": "RT-LIVE-ASSIGN-001",
  "run_id": "QA-20260912-003005",
  "test_tier": "LIVE_E2E",
  "category": "Assignment (Live)",
  "title": "Live Assignment of TREN-378 to Aqib Khan (PM)",
  "status": "PASS",
  "priority": "P0",
  "jira_issue": "TREN-378",
  "jira_assignee": "Aqib Khan (PM)",
  "event_id": "3006c40f-8341-4a86-825d-20b7c10414a4",
  "rule_result": {
    "rule_evaluated": "AssignmentRule",
    "status": "EVALUATED"
  },
  "action_id": null,
  "action_result": null,
  "notification_result": {
    "delivery": "REAL",
    "verification": "HUMAN_REQUIRED",
    "channel": "1547090800318357505"
  },
  "audit_result": {
    "persisted": true,
    "event_id": "3006c40f-8341-4a86-825d-20b7c10414a4"
  },
  "idempotency_result": null,
  "execution_mode": {
    "jira": "REAL",
    "discord": "REAL",
    "mattermost": "NOT_USED",
    "database": "REAL",
    "webhook": "SIMULATED",
    "polling": "REAL",
    "scheduler": "NOT_USED",
    "action_engine": "REAL"
  },
  "external_operations": [
    "JIRA_GET_ISSUE_PRE",
    "JIRA_ASSIGN_ISSUE_MUTATION",
    "JIRA_GET_ISSUE_POST_CONFIRM",
    "PM_AGENT_INGEST_EVENT"
  ],
  "pre_state": {
    "assignee_id": "712020:e268bcd8-d981-4b4d-992d-d5694745df8b",
    "assignee_name": "Mubashir Butt"
  },
  "mutation": {
    "action": "ASSIGN_TASK",
    "target_account_id": "712020:566cad70-4a54-42bc-bf36-0c6132fe3cf0"
  },
  "observed_external_state": {
    "confirmed_assignee_id": "712020:566cad70-4a54-42bc-bf36-0c6132fe3cf0",
    "confirmed_name": "Aqib Khan"
  },
  "expected": "Jira reflects assignment to Aqib Khan (PM) (712020:566cad70-4a54-42bc-bf36-0c6132fe3cf0); verified via Jira API.",
  "actual": "Jira API verified assignment: observed accountId=712020:566cad70-4a54-42bc-bf36-0c6132fe3cf0.",
  "started_at": "2026-09-12T00:30:05.123456+00:00",
  "completed_at": "2026-09-12T00:30:21.395436+00:00",
  "duration_ms": 16271.98
}
```
