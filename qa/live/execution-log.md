# Live QA Execution Log

Chronological audit log of all live QA runs.

## Run `QA-20260911-234853` — 2026-09-11T23:48:58.396178+00:00
- **Tier**: `DRY_RUN_E2E`
- **Target Issue**: `TREN-378`
- **Total Tests**: `34`
- **Passed**: `31` | **Failed**: `3` | **Pass Rate**: `91.18%`

| Test ID | Category | Title | Status | Duration | Evidence |
|:---|:---|:---|:---:|:---:|:---|
| `RT-ASSIGN-001` | Assignment | Assignment of TREN-378 to PM (Personal Notification) | ❌ FAIL | 0.13ms | `qa/evidence/RT-ASSIGN-001_QA-20260911-234853.json` |
| `RT-ASSIGN-002` | Assignment | Assignment of TREN-378 to Mubashir (Team Awareness, No PM DM) | ❌ FAIL | 0.27ms | `qa/evidence/RT-ASSIGN-002_QA-20260911-234853.json` |
| `RT-ASSIGN-003` | Assignment | Assignment Switch Deduplication Non-Suppression | ✅ PASS | 0.01ms | `qa/evidence/RT-ASSIGN-003_QA-20260911-234853.json` |
| `RT-STAT-001` | Status Testing | Transition: To Do -> In Progress | ✅ PASS | 0.16ms | `qa/evidence/RT-STAT-001_QA-20260911-234853.json` |
| `RT-STAT-002` | Status Testing | Transition: In Progress -> Blocked | ✅ PASS | 0.13ms | `qa/evidence/RT-STAT-002_QA-20260911-234853.json` |
| `RT-STAT-003` | Status Testing | Transition: Blocked -> In Progress | ✅ PASS | 0.11ms | `qa/evidence/RT-STAT-003_QA-20260911-234853.json` |
| `RT-STAT-004` | Status Testing | Transition: In Progress -> Done | ❌ FAIL | 0.16ms | `qa/evidence/RT-STAT-004_QA-20260911-234853.json` |
| `RT-STAT-005` | Status Testing | Transition: Done -> In Progress | ✅ PASS | 0.11ms | `qa/evidence/RT-STAT-005_QA-20260911-234853.json` |
| `RT-STAT-006` | Status Testing | Transition: Done -> To Do | ✅ PASS | 0.15ms | `qa/evidence/RT-STAT-006_QA-20260911-234853.json` |
| `RT-VIOL-001` | Workflow Violation | Activity in 'To Do' Triggers ActiveWorkRule Violation Alert | ✅ PASS | 0.45ms | `qa/evidence/RT-VIOL-001_QA-20260911-234853.json` |
| `RT-VIOL-002` | Workflow Violation | Activity in 'In Progress' Does NOT Trigger Violation | ✅ PASS | 0.13ms | `qa/evidence/RT-VIOL-002_QA-20260911-234853.json` |
| `RT-COMM-001` | Comment / Mention | Comment Mentioning PM Generates Personal Discord Notification | ✅ PASS | 0.12ms | `qa/evidence/RT-COMM-001_QA-20260911-234853.json` |
| `RT-COMM-002` | Comment / Mention | Comment Without PM Mention Ingested Silently (COMMENT_NOTIFY_ALL=False) | ✅ PASS | 0.12ms | `qa/evidence/RT-COMM-002_QA-20260911-234853.json` |
| `RT-STALE-001` | Stale Task Testing | Stale Task Detection Under Safe QA Threshold (1h) | ✅ PASS | 0.01ms | `qa/evidence/RT-STALE-001_QA-20260911-234853.json` |
| `RT-STALE-002` | Stale Task Testing | Stale Task Mattermost DM Routing Strictly to Current Assignee | ✅ PASS | 0.0ms | `qa/evidence/RT-STALE-002_QA-20260911-234853.json` |
| `RT-MM-001` | Mattermost Identity | Authoritative Exact Identity Resolution | ✅ PASS | 0.0ms | `qa/evidence/RT-MM-001_QA-20260911-234853.json` |
| `RT-MM-002` | Mattermost Identity | Unresolved User Mapping Rejection & Zero-Guessing Guarantee | ✅ PASS | 0.0ms | `qa/evidence/RT-MM-002_QA-20260911-234853.json` |
| `RT-BLCK-001` | Blocked Task | Blocked Task Detection and Discord Alert Generation | ✅ PASS | 0.08ms | `qa/evidence/RT-BLCK-001_QA-20260911-234853.json` |
| `RT-REOP-001` | Reopened Task | Reopened Task Detection (Done -> In Progress) | ✅ PASS | 0.11ms | `qa/evidence/RT-REOP-001_QA-20260911-234853.json` |
| `RT-WORK-001` | Worklog Testing | Worklog Ingestion & Duration Extraction (2h on TREN-378) | ✅ PASS | 0.07ms | `qa/evidence/RT-WORK-001_QA-20260911-234853.json` |
| `RT-OVRD-001` | Due Date / Overdue | Overdue Task Evaluation & Daily Digest Inclusion | ✅ PASS | 0.0ms | `qa/evidence/RT-OVRD-001_QA-20260911-234853.json` |
| `RT-ACT-001` | Action Engine & Idempotency | Action Execution & Idempotency: ADD_COMMENT | ✅ PASS | 0.03ms | `qa/evidence/RT-ACT-001_QA-20260911-234853.json` |
| `RT-ACT-002` | Action Engine & Idempotency | Action Execution & Idempotency: CHANGE_PRIORITY | ✅ PASS | 0.03ms | `qa/evidence/RT-ACT-002_QA-20260911-234853.json` |
| `RT-ACT-003` | Action Engine & Idempotency | Action Execution & Idempotency: ASSIGN_TASK | ✅ PASS | 0.03ms | `qa/evidence/RT-ACT-003_QA-20260911-234853.json` |
| `RT-ACT-004` | Action Engine & Idempotency | Action Execution & Idempotency: TRANSITION_TASK | ✅ PASS | 0.03ms | `qa/evidence/RT-ACT-004_QA-20260911-234853.json` |
| `RT-ACT-005` | Action Engine & Idempotency | Action Execution & Idempotency: SEND_NOTIFICATION | ✅ PASS | 0.03ms | `qa/evidence/RT-ACT-005_QA-20260911-234853.json` |
| `RT-ACT-006` | Action Engine & Idempotency | Action Execution & Idempotency: SEND_MESSAGE | ✅ PASS | 0.03ms | `qa/evidence/RT-ACT-006_QA-20260911-234853.json` |
| `RT-DRY-001` | DRY_RUN Verification | DRY_RUN=True Prevents External Mutations & Records Audit Trail | ✅ PASS | 0.01ms | `qa/evidence/RT-DRY-001_QA-20260911-234853.json` |
| `RT-DEDUP-001` | Polling vs Webhook | Unified Deduplication Across Jira Webhooks & Poller | ✅ PASS | 0.0ms | `qa/evidence/RT-DEDUP-001_QA-20260911-234853.json` |
| `RT-FAIL-001` | Failure & Recovery | HTTP 429 Rate-Limit Handling with Backoff & Checkpoint Preservation | ✅ PASS | 0.0ms | `qa/evidence/RT-FAIL-001_QA-20260911-234853.json` |
| `RT-SCHED-001` | Scheduler Testing | Continuous Scheduler Loop Non-Blocking & Exception Resilience | ✅ PASS | 0.01ms | `qa/evidence/RT-SCHED-001_QA-20260911-234853.json` |
| `RT-NOTIF-001` | Notification Verification | Human-Visible Notification Payload Structure & Embed Links | ✅ PASS | 0.05ms | `qa/evidence/RT-NOTIF-001_QA-20260911-234853.json` |
| `RT-SEC-001` | Security | Zero Exposure of Jira Tokens, Discord Webhooks, and Mattermost Tokens | ✅ PASS | 0.04ms | `qa/evidence/RT-SEC-001_QA-20260911-234853.json` |
| `RT-PERF-001` | Performance Foundation | Performance Foundation Data Integrity & Zero Score / Zero Ranking Audit | ✅ PASS | 4593.41ms | `qa/evidence/RT-PERF-001_QA-20260911-234853.json` |

## Run `QA-20260911-234924` — 2026-09-11T23:49:29.511081+00:00
- **Tier**: `DRY_RUN_E2E`
- **Target Issue**: `TREN-378`
- **Total Tests**: `34`
- **Passed**: `33` | **Failed**: `1` | **Pass Rate**: `97.06%`

| Test ID | Category | Title | Status | Duration | Evidence |
|:---|:---|:---|:---:|:---:|:---|
| `RT-ASSIGN-001` | Assignment | Assignment of TREN-378 to PM (Personal Notification) | ✅ PASS | 0.12ms | `qa/evidence/RT-ASSIGN-001_QA-20260911-234924.json` |
| `RT-ASSIGN-002` | Assignment | Assignment of TREN-378 to Mubashir (Team Awareness, No PM DM) | ✅ PASS | 0.15ms | `qa/evidence/RT-ASSIGN-002_QA-20260911-234924.json` |
| `RT-ASSIGN-003` | Assignment | Assignment Switch Deduplication Non-Suppression | ✅ PASS | 0.01ms | `qa/evidence/RT-ASSIGN-003_QA-20260911-234924.json` |
| `RT-STAT-001` | Status Testing | Transition: To Do -> In Progress | ✅ PASS | 0.1ms | `qa/evidence/RT-STAT-001_QA-20260911-234924.json` |
| `RT-STAT-002` | Status Testing | Transition: In Progress -> Blocked | ✅ PASS | 0.14ms | `qa/evidence/RT-STAT-002_QA-20260911-234924.json` |
| `RT-STAT-003` | Status Testing | Transition: Blocked -> In Progress | ✅ PASS | 0.09ms | `qa/evidence/RT-STAT-003_QA-20260911-234924.json` |
| `RT-STAT-004` | Status Testing | Transition: In Progress -> Done | ❌ FAIL | 0.1ms | `qa/evidence/RT-STAT-004_QA-20260911-234924.json` |
| `RT-STAT-005` | Status Testing | Transition: Done -> In Progress | ✅ PASS | 0.09ms | `qa/evidence/RT-STAT-005_QA-20260911-234924.json` |
| `RT-STAT-006` | Status Testing | Transition: Done -> To Do | ✅ PASS | 0.09ms | `qa/evidence/RT-STAT-006_QA-20260911-234924.json` |
| `RT-VIOL-001` | Workflow Violation | Activity in 'To Do' Triggers ActiveWorkRule Violation Alert | ✅ PASS | 0.28ms | `qa/evidence/RT-VIOL-001_QA-20260911-234924.json` |
| `RT-VIOL-002` | Workflow Violation | Activity in 'In Progress' Does NOT Trigger Violation | ✅ PASS | 0.09ms | `qa/evidence/RT-VIOL-002_QA-20260911-234924.json` |
| `RT-COMM-001` | Comment / Mention | Comment Mentioning PM Generates Personal Discord Notification | ✅ PASS | 0.11ms | `qa/evidence/RT-COMM-001_QA-20260911-234924.json` |
| `RT-COMM-002` | Comment / Mention | Comment Without PM Mention Ingested Silently (COMMENT_NOTIFY_ALL=False) | ✅ PASS | 0.1ms | `qa/evidence/RT-COMM-002_QA-20260911-234924.json` |
| `RT-STALE-001` | Stale Task Testing | Stale Task Detection Under Safe QA Threshold (1h) | ✅ PASS | 0.0ms | `qa/evidence/RT-STALE-001_QA-20260911-234924.json` |
| `RT-STALE-002` | Stale Task Testing | Stale Task Mattermost DM Routing Strictly to Current Assignee | ✅ PASS | 0.0ms | `qa/evidence/RT-STALE-002_QA-20260911-234924.json` |
| `RT-MM-001` | Mattermost Identity | Authoritative Exact Identity Resolution | ✅ PASS | 0.0ms | `qa/evidence/RT-MM-001_QA-20260911-234924.json` |
| `RT-MM-002` | Mattermost Identity | Unresolved User Mapping Rejection & Zero-Guessing Guarantee | ✅ PASS | 0.0ms | `qa/evidence/RT-MM-002_QA-20260911-234924.json` |
| `RT-BLCK-001` | Blocked Task | Blocked Task Detection and Discord Alert Generation | ✅ PASS | 0.11ms | `qa/evidence/RT-BLCK-001_QA-20260911-234924.json` |
| `RT-REOP-001` | Reopened Task | Reopened Task Detection (Done -> In Progress) | ✅ PASS | 0.09ms | `qa/evidence/RT-REOP-001_QA-20260911-234924.json` |
| `RT-WORK-001` | Worklog Testing | Worklog Ingestion & Duration Extraction (2h on TREN-378) | ✅ PASS | 0.2ms | `qa/evidence/RT-WORK-001_QA-20260911-234924.json` |
| `RT-OVRD-001` | Due Date / Overdue | Overdue Task Evaluation & Daily Digest Inclusion | ✅ PASS | 0.0ms | `qa/evidence/RT-OVRD-001_QA-20260911-234924.json` |
| `RT-ACT-001` | Action Engine & Idempotency | Action Execution & Idempotency: ADD_COMMENT | ✅ PASS | 0.02ms | `qa/evidence/RT-ACT-001_QA-20260911-234924.json` |
| `RT-ACT-002` | Action Engine & Idempotency | Action Execution & Idempotency: CHANGE_PRIORITY | ✅ PASS | 0.02ms | `qa/evidence/RT-ACT-002_QA-20260911-234924.json` |
| `RT-ACT-003` | Action Engine & Idempotency | Action Execution & Idempotency: ASSIGN_TASK | ✅ PASS | 0.02ms | `qa/evidence/RT-ACT-003_QA-20260911-234924.json` |
| `RT-ACT-004` | Action Engine & Idempotency | Action Execution & Idempotency: TRANSITION_TASK | ✅ PASS | 0.02ms | `qa/evidence/RT-ACT-004_QA-20260911-234924.json` |
| `RT-ACT-005` | Action Engine & Idempotency | Action Execution & Idempotency: SEND_NOTIFICATION | ✅ PASS | 0.02ms | `qa/evidence/RT-ACT-005_QA-20260911-234924.json` |
| `RT-ACT-006` | Action Engine & Idempotency | Action Execution & Idempotency: SEND_MESSAGE | ✅ PASS | 0.01ms | `qa/evidence/RT-ACT-006_QA-20260911-234924.json` |
| `RT-DRY-001` | DRY_RUN Verification | DRY_RUN=True Prevents External Mutations & Records Audit Trail | ✅ PASS | 0.0ms | `qa/evidence/RT-DRY-001_QA-20260911-234924.json` |
| `RT-DEDUP-001` | Polling vs Webhook | Unified Deduplication Across Jira Webhooks & Poller | ✅ PASS | 0.0ms | `qa/evidence/RT-DEDUP-001_QA-20260911-234924.json` |
| `RT-FAIL-001` | Failure & Recovery | HTTP 429 Rate-Limit Handling with Backoff & Checkpoint Preservation | ✅ PASS | 0.01ms | `qa/evidence/RT-FAIL-001_QA-20260911-234924.json` |
| `RT-SCHED-001` | Scheduler Testing | Continuous Scheduler Loop Non-Blocking & Exception Resilience | ✅ PASS | 0.0ms | `qa/evidence/RT-SCHED-001_QA-20260911-234924.json` |
| `RT-NOTIF-001` | Notification Verification | Human-Visible Notification Payload Structure & Embed Links | ✅ PASS | 0.06ms | `qa/evidence/RT-NOTIF-001_QA-20260911-234924.json` |
| `RT-SEC-001` | Security | Zero Exposure of Jira Tokens, Discord Webhooks, and Mattermost Tokens | ✅ PASS | 0.03ms | `qa/evidence/RT-SEC-001_QA-20260911-234924.json` |
| `RT-PERF-001` | Performance Foundation | Performance Foundation Data Integrity & Zero Score / Zero Ranking Audit | ✅ PASS | 4853.41ms | `qa/evidence/RT-PERF-001_QA-20260911-234924.json` |

## Run `QA-20260911-235109` — 2026-09-11T23:51:14.805760+00:00
- **Tier**: `DRY_RUN_E2E`
- **Target Issue**: `TREN-378`
- **Total Tests**: `34`
- **Passed**: `34` | **Failed**: `0` | **Pass Rate**: `100.0%`

| Test ID | Category | Title | Status | Duration | Evidence |
|:---|:---|:---|:---:|:---:|:---|
| `RT-ASSIGN-001` | Assignment | Assignment of TREN-378 to PM (Personal Notification) | ✅ PASS | 0.19ms | `qa/evidence/RT-ASSIGN-001_QA-20260911-235109.json` |
| `RT-ASSIGN-002` | Assignment | Assignment of TREN-378 to Mubashir (Team Awareness, No PM DM) | ✅ PASS | 0.18ms | `qa/evidence/RT-ASSIGN-002_QA-20260911-235109.json` |
| `RT-ASSIGN-003` | Assignment | Assignment Switch Deduplication Non-Suppression | ✅ PASS | 0.01ms | `qa/evidence/RT-ASSIGN-003_QA-20260911-235109.json` |
| `RT-STAT-001` | Status Testing | Transition: To Do -> In Progress | ✅ PASS | 0.15ms | `qa/evidence/RT-STAT-001_QA-20260911-235109.json` |
| `RT-STAT-002` | Status Testing | Transition: In Progress -> Blocked | ✅ PASS | 0.16ms | `qa/evidence/RT-STAT-002_QA-20260911-235109.json` |
| `RT-STAT-003` | Status Testing | Transition: Blocked -> In Progress | ✅ PASS | 0.13ms | `qa/evidence/RT-STAT-003_QA-20260911-235109.json` |
| `RT-STAT-004` | Status Testing | Transition: In Progress -> Done | ✅ PASS | 0.14ms | `qa/evidence/RT-STAT-004_QA-20260911-235109.json` |
| `RT-STAT-005` | Status Testing | Transition: Done -> In Progress | ✅ PASS | 0.13ms | `qa/evidence/RT-STAT-005_QA-20260911-235109.json` |
| `RT-STAT-006` | Status Testing | Transition: Done -> To Do | ✅ PASS | 0.1ms | `qa/evidence/RT-STAT-006_QA-20260911-235109.json` |
| `RT-VIOL-001` | Workflow Violation | Activity in 'To Do' Triggers ActiveWorkRule Violation Alert | ✅ PASS | 0.45ms | `qa/evidence/RT-VIOL-001_QA-20260911-235109.json` |
| `RT-VIOL-002` | Workflow Violation | Activity in 'In Progress' Does NOT Trigger Violation | ✅ PASS | 0.26ms | `qa/evidence/RT-VIOL-002_QA-20260911-235109.json` |
| `RT-COMM-001` | Comment / Mention | Comment Mentioning PM Generates Personal Discord Notification | ✅ PASS | 0.3ms | `qa/evidence/RT-COMM-001_QA-20260911-235109.json` |
| `RT-COMM-002` | Comment / Mention | Comment Without PM Mention Ingested Silently (COMMENT_NOTIFY_ALL=False) | ✅ PASS | 0.13ms | `qa/evidence/RT-COMM-002_QA-20260911-235109.json` |
| `RT-STALE-001` | Stale Task Testing | Stale Task Detection Under Safe QA Threshold (1h) | ✅ PASS | 0.0ms | `qa/evidence/RT-STALE-001_QA-20260911-235109.json` |
| `RT-STALE-002` | Stale Task Testing | Stale Task Mattermost DM Routing Strictly to Current Assignee | ✅ PASS | 0.01ms | `qa/evidence/RT-STALE-002_QA-20260911-235109.json` |
| `RT-MM-001` | Mattermost Identity | Authoritative Exact Identity Resolution | ✅ PASS | 0.0ms | `qa/evidence/RT-MM-001_QA-20260911-235109.json` |
| `RT-MM-002` | Mattermost Identity | Unresolved User Mapping Rejection & Zero-Guessing Guarantee | ✅ PASS | 0.01ms | `qa/evidence/RT-MM-002_QA-20260911-235109.json` |
| `RT-BLCK-001` | Blocked Task | Blocked Task Detection and Discord Alert Generation | ✅ PASS | 0.13ms | `qa/evidence/RT-BLCK-001_QA-20260911-235109.json` |
| `RT-REOP-001` | Reopened Task | Reopened Task Detection (Done -> In Progress) | ✅ PASS | 0.14ms | `qa/evidence/RT-REOP-001_QA-20260911-235109.json` |
| `RT-WORK-001` | Worklog Testing | Worklog Ingestion & Duration Extraction (2h on TREN-378) | ✅ PASS | 0.11ms | `qa/evidence/RT-WORK-001_QA-20260911-235109.json` |
| `RT-OVRD-001` | Due Date / Overdue | Overdue Task Evaluation & Daily Digest Inclusion | ✅ PASS | 0.0ms | `qa/evidence/RT-OVRD-001_QA-20260911-235109.json` |
| `RT-ACT-001` | Action Engine & Idempotency | Action Execution & Idempotency: ADD_COMMENT | ✅ PASS | 0.03ms | `qa/evidence/RT-ACT-001_QA-20260911-235109.json` |
| `RT-ACT-002` | Action Engine & Idempotency | Action Execution & Idempotency: CHANGE_PRIORITY | ✅ PASS | 0.04ms | `qa/evidence/RT-ACT-002_QA-20260911-235109.json` |
| `RT-ACT-003` | Action Engine & Idempotency | Action Execution & Idempotency: ASSIGN_TASK | ✅ PASS | 0.03ms | `qa/evidence/RT-ACT-003_QA-20260911-235109.json` |
| `RT-ACT-004` | Action Engine & Idempotency | Action Execution & Idempotency: TRANSITION_TASK | ✅ PASS | 0.03ms | `qa/evidence/RT-ACT-004_QA-20260911-235109.json` |
| `RT-ACT-005` | Action Engine & Idempotency | Action Execution & Idempotency: SEND_NOTIFICATION | ✅ PASS | 0.02ms | `qa/evidence/RT-ACT-005_QA-20260911-235109.json` |
| `RT-ACT-006` | Action Engine & Idempotency | Action Execution & Idempotency: SEND_MESSAGE | ✅ PASS | 0.03ms | `qa/evidence/RT-ACT-006_QA-20260911-235109.json` |
| `RT-DRY-001` | DRY_RUN Verification | DRY_RUN=True Prevents External Mutations & Records Audit Trail | ✅ PASS | 0.0ms | `qa/evidence/RT-DRY-001_QA-20260911-235109.json` |
| `RT-DEDUP-001` | Polling vs Webhook | Unified Deduplication Across Jira Webhooks & Poller | ✅ PASS | 0.0ms | `qa/evidence/RT-DEDUP-001_QA-20260911-235109.json` |
| `RT-FAIL-001` | Failure & Recovery | HTTP 429 Rate-Limit Handling with Backoff & Checkpoint Preservation | ✅ PASS | 0.01ms | `qa/evidence/RT-FAIL-001_QA-20260911-235109.json` |
| `RT-SCHED-001` | Scheduler Testing | Continuous Scheduler Loop Non-Blocking & Exception Resilience | ✅ PASS | 0.01ms | `qa/evidence/RT-SCHED-001_QA-20260911-235109.json` |
| `RT-NOTIF-001` | Notification Verification | Human-Visible Notification Payload Structure & Embed Links | ✅ PASS | 0.05ms | `qa/evidence/RT-NOTIF-001_QA-20260911-235109.json` |
| `RT-SEC-001` | Security | Zero Exposure of Jira Tokens, Discord Webhooks, and Mattermost Tokens | ✅ PASS | 0.04ms | `qa/evidence/RT-SEC-001_QA-20260911-235109.json` |
| `RT-PERF-001` | Performance Foundation | Performance Foundation Data Integrity & Zero Score / Zero Ranking Audit | ✅ PASS | 5003.97ms | `qa/evidence/RT-PERF-001_QA-20260911-235109.json` |

## Run `QA-20260911-235454` — 2026-09-11T23:55:00.856449+00:00
- **Tier**: `LIVE_E2E`
- **Target Issue**: `TREN-378`
- **Total Tests**: `34`
- **Passed**: `34` | **Failed**: `0` | **Pass Rate**: `100.0%`

| Test ID | Category | Title | Status | Duration | Evidence |
|:---|:---|:---|:---:|:---:|:---|
| `RT-ASSIGN-001` | Assignment | Assignment of TREN-378 to PM (Personal Notification) | ✅ PASS | 0.2ms | `qa/evidence/RT-ASSIGN-001_QA-20260911-235454.json` |
| `RT-ASSIGN-002` | Assignment | Assignment of TREN-378 to Mubashir (Team Awareness, No PM DM) | ✅ PASS | 0.16ms | `qa/evidence/RT-ASSIGN-002_QA-20260911-235454.json` |
| `RT-ASSIGN-003` | Assignment | Assignment Switch Deduplication Non-Suppression | ✅ PASS | 0.01ms | `qa/evidence/RT-ASSIGN-003_QA-20260911-235454.json` |
| `RT-STAT-001` | Status Testing | Transition: To Do -> In Progress | ✅ PASS | 0.21ms | `qa/evidence/RT-STAT-001_QA-20260911-235454.json` |
| `RT-STAT-002` | Status Testing | Transition: In Progress -> Blocked | ✅ PASS | 1.12ms | `qa/evidence/RT-STAT-002_QA-20260911-235454.json` |
| `RT-STAT-003` | Status Testing | Transition: Blocked -> In Progress | ✅ PASS | 0.18ms | `qa/evidence/RT-STAT-003_QA-20260911-235454.json` |
| `RT-STAT-004` | Status Testing | Transition: In Progress -> Done | ✅ PASS | 0.21ms | `qa/evidence/RT-STAT-004_QA-20260911-235454.json` |
| `RT-STAT-005` | Status Testing | Transition: Done -> In Progress | ✅ PASS | 0.12ms | `qa/evidence/RT-STAT-005_QA-20260911-235454.json` |
| `RT-STAT-006` | Status Testing | Transition: Done -> To Do | ✅ PASS | 0.13ms | `qa/evidence/RT-STAT-006_QA-20260911-235454.json` |
| `RT-VIOL-001` | Workflow Violation | Activity in 'To Do' Triggers ActiveWorkRule Violation Alert | ✅ PASS | 0.48ms | `qa/evidence/RT-VIOL-001_QA-20260911-235454.json` |
| `RT-VIOL-002` | Workflow Violation | Activity in 'In Progress' Does NOT Trigger Violation | ✅ PASS | 0.16ms | `qa/evidence/RT-VIOL-002_QA-20260911-235454.json` |
| `RT-COMM-001` | Comment / Mention | Comment Mentioning PM Generates Personal Discord Notification | ✅ PASS | 0.15ms | `qa/evidence/RT-COMM-001_QA-20260911-235454.json` |
| `RT-COMM-002` | Comment / Mention | Comment Without PM Mention Ingested Silently (COMMENT_NOTIFY_ALL=False) | ✅ PASS | 0.13ms | `qa/evidence/RT-COMM-002_QA-20260911-235454.json` |
| `RT-STALE-001` | Stale Task Testing | Stale Task Detection Under Safe QA Threshold (1h) | ✅ PASS | 0.01ms | `qa/evidence/RT-STALE-001_QA-20260911-235454.json` |
| `RT-STALE-002` | Stale Task Testing | Stale Task Mattermost DM Routing Strictly to Current Assignee | ✅ PASS | 0.01ms | `qa/evidence/RT-STALE-002_QA-20260911-235454.json` |
| `RT-MM-001` | Mattermost Identity | Authoritative Exact Identity Resolution | ✅ PASS | 0.01ms | `qa/evidence/RT-MM-001_QA-20260911-235454.json` |
| `RT-MM-002` | Mattermost Identity | Unresolved User Mapping Rejection & Zero-Guessing Guarantee | ✅ PASS | 0.01ms | `qa/evidence/RT-MM-002_QA-20260911-235454.json` |
| `RT-BLCK-001` | Blocked Task | Blocked Task Detection and Discord Alert Generation | ✅ PASS | 0.11ms | `qa/evidence/RT-BLCK-001_QA-20260911-235454.json` |
| `RT-REOP-001` | Reopened Task | Reopened Task Detection (Done -> In Progress) | ✅ PASS | 0.16ms | `qa/evidence/RT-REOP-001_QA-20260911-235454.json` |
| `RT-WORK-001` | Worklog Testing | Worklog Ingestion & Duration Extraction (2h on TREN-378) | ✅ PASS | 0.13ms | `qa/evidence/RT-WORK-001_QA-20260911-235454.json` |
| `RT-OVRD-001` | Due Date / Overdue | Overdue Task Evaluation & Daily Digest Inclusion | ✅ PASS | 0.01ms | `qa/evidence/RT-OVRD-001_QA-20260911-235454.json` |
| `RT-ACT-001` | Action Engine & Idempotency | Action Execution & Idempotency: ADD_COMMENT | ✅ PASS | 0.03ms | `qa/evidence/RT-ACT-001_QA-20260911-235454.json` |
| `RT-ACT-002` | Action Engine & Idempotency | Action Execution & Idempotency: CHANGE_PRIORITY | ✅ PASS | 0.04ms | `qa/evidence/RT-ACT-002_QA-20260911-235454.json` |
| `RT-ACT-003` | Action Engine & Idempotency | Action Execution & Idempotency: ASSIGN_TASK | ✅ PASS | 0.03ms | `qa/evidence/RT-ACT-003_QA-20260911-235454.json` |
| `RT-ACT-004` | Action Engine & Idempotency | Action Execution & Idempotency: TRANSITION_TASK | ✅ PASS | 0.03ms | `qa/evidence/RT-ACT-004_QA-20260911-235454.json` |
| `RT-ACT-005` | Action Engine & Idempotency | Action Execution & Idempotency: SEND_NOTIFICATION | ✅ PASS | 0.03ms | `qa/evidence/RT-ACT-005_QA-20260911-235454.json` |
| `RT-ACT-006` | Action Engine & Idempotency | Action Execution & Idempotency: SEND_MESSAGE | ✅ PASS | 0.05ms | `qa/evidence/RT-ACT-006_QA-20260911-235454.json` |
| `RT-DRY-001` | DRY_RUN Verification | DRY_RUN=True Prevents External Mutations & Records Audit Trail | ✅ PASS | 0.01ms | `qa/evidence/RT-DRY-001_QA-20260911-235454.json` |
| `RT-DEDUP-001` | Polling vs Webhook | Unified Deduplication Across Jira Webhooks & Poller | ✅ PASS | 0.01ms | `qa/evidence/RT-DEDUP-001_QA-20260911-235454.json` |
| `RT-FAIL-001` | Failure & Recovery | HTTP 429 Rate-Limit Handling with Backoff & Checkpoint Preservation | ✅ PASS | 0.01ms | `qa/evidence/RT-FAIL-001_QA-20260911-235454.json` |
| `RT-SCHED-001` | Scheduler Testing | Continuous Scheduler Loop Non-Blocking & Exception Resilience | ✅ PASS | 0.01ms | `qa/evidence/RT-SCHED-001_QA-20260911-235454.json` |
| `RT-NOTIF-001` | Notification Verification | Human-Visible Notification Payload Structure & Embed Links | ✅ PASS | 0.08ms | `qa/evidence/RT-NOTIF-001_QA-20260911-235454.json` |
| `RT-SEC-001` | Security | Zero Exposure of Jira Tokens, Discord Webhooks, and Mattermost Tokens | ✅ PASS | 0.05ms | `qa/evidence/RT-SEC-001_QA-20260911-235454.json` |
| `RT-PERF-001` | Performance Foundation | Performance Foundation Data Integrity & Zero Score / Zero Ranking Audit | ✅ PASS | 6461.86ms | `qa/evidence/RT-PERF-001_QA-20260911-235454.json` |

## Run `QA-20260912-001140` — 2026-09-12T00:11:55.962683+00:00
- **Tier**: `DRY_RUN_E2E`
- **Target Issue**: `TREN-378`
- **Operations**: `Jira: NOT_USED` | `Discord: NOT_USED` | `Mattermost: NOT_USED` | `Database: REAL`
- **Total Tests**: `13` | **Passed**: `13` | **Failed**: `0` | **Not Executed**: `0`
- **Live Integrity**: `13 Valid` | `0 Invalid`
- **Pass Rate (Active)**: `100.0%`

| Status | Test ID | Tier | Category | Title | Execution Mode | Duration | Evidence |
|:---:|:---|:---:|:---|:---|:---|:---:|:---|
| ✅ PASS | `RT-DRY-001` | `DRY_RUN_E2E` | DRY_RUN Verification | DRY_RUN=True Prevents External Mutations & Produces Audit Trail | `database:REAL, action_engine:REAL` | 168.35ms | `qa/evidence/RT-DRY-001_QA-20260912-001140.json` |
| ✅ PASS | `RT-INT-VIOL-001` | `INTEGRATION` | Workflow Violation (Integration) | Comment on 'To Do' Triggers ActiveWorkRule Violation Alert | `jira:SIMULATED, discord:SIMULATED, database:REAL, action_engine:REAL` | 0.82ms | `qa/evidence/RT-INT-VIOL-001_QA-20260912-001140.json` |
| ✅ PASS | `RT-INT-DEDUP-001` | `INTEGRATION` | Deduplication (Integration) | Unified Deduplication Across Webhook & Poller Ingestion | `jira:SIMULATED, database:REAL, webhook:SIMULATED, polling:SIMULATED` | 0.02ms | `qa/evidence/RT-INT-DEDUP-001_QA-20260912-001140.json` |
| ✅ PASS | `RT-INT-STALE-001` | `INTEGRATION` | Stale Task (Integration) | Stale Task Detection Under Safe QA Threshold (1h) | `jira:SIMULATED, discord:SIMULATED, database:REAL, scheduler:SIMULATED` | 0.01ms | `qa/evidence/RT-INT-STALE-001_QA-20260912-001140.json` |
| ✅ PASS | `RT-INT-MM-001` | `INTEGRATION` | Mattermost Identity (Integration) | Unmapped User Mapping Rejection & Zero Guessing Guarantee | `mattermost:SIMULATED, database:REAL, action_engine:REAL` | 0.01ms | `qa/evidence/RT-INT-MM-001_QA-20260912-001140.json` |
| ✅ PASS | `RT-INT-BLCK-001` | `INTEGRATION` | Blocked Task (Integration) | Blocked Task Detection and Discord Alert Generation | `jira:SIMULATED, discord:SIMULATED, database:REAL, action_engine:REAL` | 0.23ms | `qa/evidence/RT-INT-BLCK-001_QA-20260912-001140.json` |
| ✅ PASS | `RT-INT-REOP-001` | `INTEGRATION` | Reopened Task (Integration) | Reopened Task Detection (Done -> In Progress) | `jira:SIMULATED, discord:SIMULATED, database:REAL, action_engine:REAL` | 0.20ms | `qa/evidence/RT-INT-REOP-001_QA-20260912-001140.json` |
| ✅ PASS | `RT-INT-FAIL-001` | `INTEGRATION` | Failure & Recovery (Integration) | HTTP 429 Rate-Limit Handling with Backoff & Checkpoint Preservation | `jira:SIMULATED, database:REAL, polling:SIMULATED` | 0.01ms | `qa/evidence/RT-INT-FAIL-001_QA-20260912-001140.json` |
| ✅ PASS | `RT-INT-SCHED-001` | `INTEGRATION` | Scheduler (Integration) | Continuous Scheduler Loop Non-Blocking & Exception Resilience | `database:REAL, scheduler:REAL` | 0.01ms | `qa/evidence/RT-INT-SCHED-001_QA-20260912-001140.json` |
| ✅ PASS | `RT-INT-PERF-001` | `INTEGRATION` | Performance Foundation (Integration) | Performance Foundation Data Integrity & Zero Score / Zero Ranking Audit | `database:REAL` | 15710.02ms | `qa/evidence/RT-INT-PERF-001_QA-20260912-001140.json` |
| ✅ PASS | `RT-UNIT-SEC-001` | `UNIT` | Security (Unit) | Zero Exposure of Jira Tokens, Discord Webhooks, and Mattermost Tokens | `in_memory` | 0.04ms | `qa/evidence/RT-UNIT-SEC-001_QA-20260912-001140.json` |
| ✅ PASS | `RT-UNIT-NOTIF-001` | `UNIT` | Notification Verification (Unit) | Human-Visible Notification Payload Structure & Embed Links | `in_memory` | 0.06ms | `qa/evidence/RT-UNIT-NOTIF-001_QA-20260912-001140.json` |
| ✅ PASS | `RT-UNIT-SAFE-001` | `UNIT` | Safeguards (Unit) | Live QA Safeguard Blocks Mutations on Non-QA Jira Projects | `in_memory` | 0.01ms | `qa/evidence/RT-UNIT-SAFE-001_QA-20260912-001140.json` |

## Run `QA-20260912-001201` — 2026-09-12T00:12:17.909878+00:00
- **Tier**: `LIVE_E2E`
- **Target Issue**: `TREN-378`
- **Operations**: `Jira: REAL` | `Discord: REAL` | `Mattermost: NOT_USED` | `Database: REAL`
- **Total Tests**: `18` | **Passed**: `18` | **Failed**: `0` | **Not Executed**: `0`
- **Live Integrity**: `6 Valid` | `0 Invalid`
- **Pass Rate (Active)**: `100.0%`

| Status | Test ID | Tier | Category | Title | Execution Mode | Duration | Evidence |
|:---:|:---|:---:|:---|:---|:---|:---:|:---|
| ✅ PASS | `RT-LIVE-ASSIGN-001` | `LIVE_E2E` | Assignment (Live) | Live Assignment of TREN-378 to Aqib Khan (PM) | `jira:REAL, discord:REAL, database:REAL, webhook:SIMULATED, polling:REAL, action_engine:REAL` | 2269.38ms | `qa/evidence/RT-LIVE-ASSIGN-001_QA-20260912-001201.json` |
| ✅ PASS | `RT-LIVE-ASSIGN-002` | `LIVE_E2E` | Assignment (Live) | Live Assignment of TREN-378 to Mubashir Butt | `jira:REAL, discord:REAL, database:REAL, webhook:SIMULATED, polling:REAL, action_engine:REAL` | 2033.97ms | `qa/evidence/RT-LIVE-ASSIGN-002_QA-20260912-001201.json` |
| ✅ PASS | `RT-LIVE-TRANS-001` | `LIVE_E2E` | Status Testing (Live) | Live Transition of TREN-378 to 'In Progress' | `jira:REAL, discord:REAL, database:REAL, webhook:SIMULATED, polling:REAL, action_engine:REAL` | 1831.95ms | `qa/evidence/RT-LIVE-TRANS-001_QA-20260912-001201.json` |
| ✅ PASS | `RT-LIVE-COMM-001` | `LIVE_E2E` | Comment / Mention (Live) | Live Comment on TREN-378 (With PM Mention) | `jira:REAL, discord:REAL, database:REAL, webhook:SIMULATED, polling:REAL, action_engine:REAL` | 494.62ms | `qa/evidence/RT-LIVE-COMM-001_QA-20260912-001201.json` |
| ✅ PASS | `RT-LIVE-COMM-002` | `LIVE_E2E` | Comment / Mention (Live) | Live Comment on TREN-378 (No Mention) | `jira:REAL, discord:REAL, database:REAL, webhook:SIMULATED, polling:REAL, action_engine:REAL` | 499.51ms | `qa/evidence/RT-LIVE-COMM-002_QA-20260912-001201.json` |
| ✅ PASS | `RT-LIVE-ACT-001` | `LIVE_E2E` | Action Engine (Live) | Live Action Engine Execution & Idempotency on TREN-378 | `jira:REAL, database:REAL, action_engine:REAL` | 1000.08ms | `qa/evidence/RT-LIVE-ACT-001_QA-20260912-001201.json` |
| ✅ PASS | `RT-INT-VIOL-001` | `INTEGRATION` | Workflow Violation (Integration) | Comment on 'To Do' Triggers ActiveWorkRule Violation Alert | `jira:SIMULATED, discord:SIMULATED, database:REAL, action_engine:REAL` | 1.95ms | `qa/evidence/RT-INT-VIOL-001_QA-20260912-001201.json` |
| ✅ PASS | `RT-INT-DEDUP-001` | `INTEGRATION` | Deduplication (Integration) | Unified Deduplication Across Webhook & Poller Ingestion | `jira:SIMULATED, database:REAL, webhook:SIMULATED, polling:SIMULATED` | 0.03ms | `qa/evidence/RT-INT-DEDUP-001_QA-20260912-001201.json` |
| ✅ PASS | `RT-INT-STALE-001` | `INTEGRATION` | Stale Task (Integration) | Stale Task Detection Under Safe QA Threshold (1h) | `jira:SIMULATED, discord:SIMULATED, database:REAL, scheduler:SIMULATED` | 0.02ms | `qa/evidence/RT-INT-STALE-001_QA-20260912-001201.json` |
| ✅ PASS | `RT-INT-MM-001` | `INTEGRATION` | Mattermost Identity (Integration) | Unmapped User Mapping Rejection & Zero Guessing Guarantee | `mattermost:SIMULATED, database:REAL, action_engine:REAL` | 0.02ms | `qa/evidence/RT-INT-MM-001_QA-20260912-001201.json` |
| ✅ PASS | `RT-INT-BLCK-001` | `INTEGRATION` | Blocked Task (Integration) | Blocked Task Detection and Discord Alert Generation | `jira:SIMULATED, discord:SIMULATED, database:REAL, action_engine:REAL` | 0.46ms | `qa/evidence/RT-INT-BLCK-001_QA-20260912-001201.json` |
| ✅ PASS | `RT-INT-REOP-001` | `INTEGRATION` | Reopened Task (Integration) | Reopened Task Detection (Done -> In Progress) | `jira:SIMULATED, discord:SIMULATED, database:REAL, action_engine:REAL` | 0.48ms | `qa/evidence/RT-INT-REOP-001_QA-20260912-001201.json` |
| ✅ PASS | `RT-INT-FAIL-001` | `INTEGRATION` | Failure & Recovery (Integration) | HTTP 429 Rate-Limit Handling with Backoff & Checkpoint Preservation | `jira:SIMULATED, database:REAL, polling:SIMULATED` | 0.02ms | `qa/evidence/RT-INT-FAIL-001_QA-20260912-001201.json` |
| ✅ PASS | `RT-INT-SCHED-001` | `INTEGRATION` | Scheduler (Integration) | Continuous Scheduler Loop Non-Blocking & Exception Resilience | `database:REAL, scheduler:REAL` | 0.02ms | `qa/evidence/RT-INT-SCHED-001_QA-20260912-001201.json` |
| ✅ PASS | `RT-INT-PERF-001` | `INTEGRATION` | Performance Foundation (Integration) | Performance Foundation Data Integrity & Zero Score / Zero Ranking Audit | `database:REAL` | 8199.02ms | `qa/evidence/RT-INT-PERF-001_QA-20260912-001201.json` |
| ✅ PASS | `RT-UNIT-SEC-001` | `UNIT` | Security (Unit) | Zero Exposure of Jira Tokens, Discord Webhooks, and Mattermost Tokens | `in_memory` | 0.08ms | `qa/evidence/RT-UNIT-SEC-001_QA-20260912-001201.json` |
| ✅ PASS | `RT-UNIT-NOTIF-001` | `UNIT` | Notification Verification (Unit) | Human-Visible Notification Payload Structure & Embed Links | `in_memory` | 0.08ms | `qa/evidence/RT-UNIT-NOTIF-001_QA-20260912-001201.json` |
| ✅ PASS | `RT-UNIT-SAFE-001` | `UNIT` | Safeguards (Unit) | Live QA Safeguard Blocks Mutations on Non-QA Jira Projects | `in_memory` | 0.01ms | `qa/evidence/RT-UNIT-SAFE-001_QA-20260912-001201.json` |

## Run `QA-20260912-001244` — 2026-09-12T00:13:02.969712+00:00
- **Tier**: `LIVE_E2E`
- **Target Issue**: `WRONG-999`
- **Operations**: `Jira: REAL` | `Discord: REAL` | `Mattermost: NOT_USED` | `Database: REAL`
- **Total Tests**: `18` | **Passed**: `12` | **Failed**: `0` | **Not Executed**: `0`
- **Live Integrity**: `0 Valid` | `6 Invalid`
- **Pass Rate (Active)**: `66.67%`

| Status | Test ID | Tier | Category | Title | Execution Mode | Duration | Evidence |
|:---:|:---|:---:|:---|:---|:---|:---:|:---|
| 🛑 INVALID_LIVE | `RT-LIVE-ASSIGN-001` | `LIVE_E2E` | Assignment (Live) | Live Assignment of WRONG-999 to Aqib Khan (PM) | `database:REAL` | 0.00ms | `qa/evidence/RT-LIVE-ASSIGN-001_QA-20260912-001244.json` |
| 🛑 INVALID_LIVE | `RT-LIVE-ASSIGN-002` | `LIVE_E2E` | Assignment (Live) | Live Assignment of WRONG-999 to Mubashir Butt | `database:REAL` | 0.00ms | `qa/evidence/RT-LIVE-ASSIGN-002_QA-20260912-001244.json` |
| 🛑 INVALID_LIVE | `RT-LIVE-TRANS-001` | `LIVE_E2E` | Status Testing (Live) | Live Transition of WRONG-999 to 'In Progress' | `database:REAL` | 0.00ms | `qa/evidence/RT-LIVE-TRANS-001_QA-20260912-001244.json` |
| 🛑 INVALID_LIVE | `RT-LIVE-COMM-001` | `LIVE_E2E` | Comment / Mention (Live) | Live Comment on WRONG-999 (With PM Mention) | `database:REAL` | 0.00ms | `qa/evidence/RT-LIVE-COMM-001_QA-20260912-001244.json` |
| 🛑 INVALID_LIVE | `RT-LIVE-COMM-002` | `LIVE_E2E` | Comment / Mention (Live) | Live Comment on WRONG-999 (No Mention) | `database:REAL` | 0.00ms | `qa/evidence/RT-LIVE-COMM-002_QA-20260912-001244.json` |
| 🛑 INVALID_LIVE | `RT-LIVE-ACT-001` | `LIVE_E2E` | Action Engine (Live) | Live Action Engine Execution & Idempotency on WRONG-999 | `database:REAL, action_engine:REAL` | 0.00ms | `qa/evidence/RT-LIVE-ACT-001_QA-20260912-001244.json` |
| ✅ PASS | `RT-INT-VIOL-001` | `INTEGRATION` | Workflow Violation (Integration) | Comment on 'To Do' Triggers ActiveWorkRule Violation Alert | `jira:SIMULATED, discord:SIMULATED, database:REAL, action_engine:REAL` | 1.01ms | `qa/evidence/RT-INT-VIOL-001_QA-20260912-001244.json` |
| ✅ PASS | `RT-INT-DEDUP-001` | `INTEGRATION` | Deduplication (Integration) | Unified Deduplication Across Webhook & Poller Ingestion | `jira:SIMULATED, database:REAL, webhook:SIMULATED, polling:SIMULATED` | 0.01ms | `qa/evidence/RT-INT-DEDUP-001_QA-20260912-001244.json` |
| ✅ PASS | `RT-INT-STALE-001` | `INTEGRATION` | Stale Task (Integration) | Stale Task Detection Under Safe QA Threshold (1h) | `jira:SIMULATED, discord:SIMULATED, database:REAL, scheduler:SIMULATED` | 0.01ms | `qa/evidence/RT-INT-STALE-001_QA-20260912-001244.json` |
| ✅ PASS | `RT-INT-MM-001` | `INTEGRATION` | Mattermost Identity (Integration) | Unmapped User Mapping Rejection & Zero Guessing Guarantee | `mattermost:SIMULATED, database:REAL, action_engine:REAL` | 0.01ms | `qa/evidence/RT-INT-MM-001_QA-20260912-001244.json` |
| ✅ PASS | `RT-INT-BLCK-001` | `INTEGRATION` | Blocked Task (Integration) | Blocked Task Detection and Discord Alert Generation | `jira:SIMULATED, discord:SIMULATED, database:REAL, action_engine:REAL` | 0.27ms | `qa/evidence/RT-INT-BLCK-001_QA-20260912-001244.json` |
| ✅ PASS | `RT-INT-REOP-001` | `INTEGRATION` | Reopened Task (Integration) | Reopened Task Detection (Done -> In Progress) | `jira:SIMULATED, discord:SIMULATED, database:REAL, action_engine:REAL` | 0.16ms | `qa/evidence/RT-INT-REOP-001_QA-20260912-001244.json` |
| ✅ PASS | `RT-INT-FAIL-001` | `INTEGRATION` | Failure & Recovery (Integration) | HTTP 429 Rate-Limit Handling with Backoff & Checkpoint Preservation | `jira:SIMULATED, database:REAL, polling:SIMULATED` | 0.01ms | `qa/evidence/RT-INT-FAIL-001_QA-20260912-001244.json` |
| ✅ PASS | `RT-INT-SCHED-001` | `INTEGRATION` | Scheduler (Integration) | Continuous Scheduler Loop Non-Blocking & Exception Resilience | `database:REAL, scheduler:REAL` | 0.01ms | `qa/evidence/RT-INT-SCHED-001_QA-20260912-001244.json` |
| ✅ PASS | `RT-INT-PERF-001` | `INTEGRATION` | Performance Foundation (Integration) | Performance Foundation Data Integrity & Zero Score / Zero Ranking Audit | `database:REAL` | 18027.23ms | `qa/evidence/RT-INT-PERF-001_QA-20260912-001244.json` |
| ✅ PASS | `RT-UNIT-SEC-001` | `UNIT` | Security (Unit) | Zero Exposure of Jira Tokens, Discord Webhooks, and Mattermost Tokens | `in_memory` | 0.05ms | `qa/evidence/RT-UNIT-SEC-001_QA-20260912-001244.json` |
| ✅ PASS | `RT-UNIT-NOTIF-001` | `UNIT` | Notification Verification (Unit) | Human-Visible Notification Payload Structure & Embed Links | `in_memory` | 0.05ms | `qa/evidence/RT-UNIT-NOTIF-001_QA-20260912-001244.json` |
| ✅ PASS | `RT-UNIT-SAFE-001` | `UNIT` | Safeguards (Unit) | Live QA Safeguard Blocks Mutations on Non-QA Jira Projects | `in_memory` | 0.01ms | `qa/evidence/RT-UNIT-SAFE-001_QA-20260912-001244.json` |

## Run `QA-20260912-003005` — 2026-09-12T00:30:37.564527+00:00
- **Tier**: `LIVE_E2E`
- **Target Issue**: `TREN-378`
- **Operations**: `Jira: REAL` | `Discord: REAL` | `Mattermost: NOT_USED` | `Database: REAL`
- **Total Tests**: `18` | **Passed**: `18` | **Failed**: `0` | **Not Executed**: `0`
- **Live Integrity**: `6 Valid` | `0 Invalid`
- **Pass Rate (Active)**: `100.0%`

| Status | Test ID | Tier | Category | Title | Execution Mode | Duration | Evidence |
|:---:|:---|:---:|:---|:---|:---|:---:|:---|
| ✅ PASS | `RT-LIVE-ASSIGN-001` | `LIVE_E2E` | Assignment (Live) | Live Assignment of TREN-378 to Aqib Khan (PM) | `jira:REAL, discord:REAL, database:REAL, webhook:SIMULATED, polling:REAL, action_engine:REAL` | 16271.98ms | `qa/evidence/RT-LIVE-ASSIGN-001_QA-20260912-003005.json` |
| ✅ PASS | `RT-LIVE-ASSIGN-002` | `LIVE_E2E` | Assignment (Live) | Live Assignment of TREN-378 to Mubashir Butt | `jira:REAL, discord:REAL, database:REAL, webhook:SIMULATED, polling:REAL, action_engine:REAL` | 3055.93ms | `qa/evidence/RT-LIVE-ASSIGN-002_QA-20260912-003005.json` |
| ✅ PASS | `RT-LIVE-TRANS-001` | `LIVE_E2E` | Status Testing (Live) | Live Transition of TREN-378 to 'In Progress' | `jira:REAL, discord:REAL, database:REAL, webhook:SIMULATED, polling:REAL, action_engine:REAL` | 1877.60ms | `qa/evidence/RT-LIVE-TRANS-001_QA-20260912-003005.json` |
| ✅ PASS | `RT-LIVE-COMM-001` | `LIVE_E2E` | Comment / Mention (Live) | Live Comment on TREN-378 (With PM Mention) | `jira:REAL, discord:REAL, database:REAL, webhook:SIMULATED, polling:REAL, action_engine:REAL` | 866.37ms | `qa/evidence/RT-LIVE-COMM-001_QA-20260912-003005.json` |
| ✅ PASS | `RT-LIVE-COMM-002` | `LIVE_E2E` | Comment / Mention (Live) | Live Comment on TREN-378 (No Mention) | `jira:REAL, discord:REAL, database:REAL, webhook:SIMULATED, polling:REAL, action_engine:REAL` | 554.20ms | `qa/evidence/RT-LIVE-COMM-002_QA-20260912-003005.json` |
| ✅ PASS | `RT-LIVE-ACT-001` | `LIVE_E2E` | Action Engine (Live) | Live Action Engine Execution & Idempotency on TREN-378 | `jira:REAL, database:REAL, action_engine:REAL` | 893.55ms | `qa/evidence/RT-LIVE-ACT-001_QA-20260912-003005.json` |
| ✅ PASS | `RT-INT-VIOL-001` | `INTEGRATION` | Workflow Violation (Integration) | Comment on 'To Do' Triggers ActiveWorkRule Violation Alert | `jira:SIMULATED, discord:SIMULATED, database:REAL, action_engine:REAL` | 1.37ms | `qa/evidence/RT-INT-VIOL-001_QA-20260912-003005.json` |
| ✅ PASS | `RT-INT-DEDUP-001` | `INTEGRATION` | Deduplication (Integration) | Unified Deduplication Across Webhook & Poller Ingestion | `jira:SIMULATED, database:REAL, webhook:SIMULATED, polling:SIMULATED` | 0.03ms | `qa/evidence/RT-INT-DEDUP-001_QA-20260912-003005.json` |
| ✅ PASS | `RT-INT-STALE-001` | `INTEGRATION` | Stale Task (Integration) | Stale Task Detection Under Safe QA Threshold (1h) | `jira:SIMULATED, discord:SIMULATED, database:REAL, scheduler:SIMULATED` | 0.03ms | `qa/evidence/RT-INT-STALE-001_QA-20260912-003005.json` |
| ✅ PASS | `RT-INT-MM-001` | `INTEGRATION` | Mattermost Identity (Integration) | Unmapped User Mapping Rejection & Zero Guessing Guarantee | `mattermost:SIMULATED, database:REAL, action_engine:REAL` | 0.03ms | `qa/evidence/RT-INT-MM-001_QA-20260912-003005.json` |
| ✅ PASS | `RT-INT-BLCK-001` | `INTEGRATION` | Blocked Task (Integration) | Blocked Task Detection and Discord Alert Generation | `jira:SIMULATED, discord:SIMULATED, database:REAL, action_engine:REAL` | 0.43ms | `qa/evidence/RT-INT-BLCK-001_QA-20260912-003005.json` |
| ✅ PASS | `RT-INT-REOP-001` | `INTEGRATION` | Reopened Task (Integration) | Reopened Task Detection (Done -> In Progress) | `jira:SIMULATED, discord:SIMULATED, database:REAL, action_engine:REAL` | 0.42ms | `qa/evidence/RT-INT-REOP-001_QA-20260912-003005.json` |
| ✅ PASS | `RT-INT-FAIL-001` | `INTEGRATION` | Failure & Recovery (Integration) | HTTP 429 Rate-Limit Handling with Backoff & Checkpoint Preservation | `jira:SIMULATED, database:REAL, polling:SIMULATED` | 0.07ms | `qa/evidence/RT-INT-FAIL-001_QA-20260912-003005.json` |
| ✅ PASS | `RT-INT-SCHED-001` | `INTEGRATION` | Scheduler (Integration) | Continuous Scheduler Loop Non-Blocking & Exception Resilience | `database:REAL, scheduler:REAL` | 0.02ms | `qa/evidence/RT-INT-SCHED-001_QA-20260912-003005.json` |
| ✅ PASS | `RT-INT-PERF-001` | `INTEGRATION` | Performance Foundation (Integration) | Performance Foundation Data Integrity & Zero Score / Zero Ranking Audit | `database:REAL` | 8501.03ms | `qa/evidence/RT-INT-PERF-001_QA-20260912-003005.json` |
| ✅ PASS | `RT-UNIT-SEC-001` | `UNIT` | Security (Unit) | Zero Exposure of Jira Tokens, Discord Webhooks, and Mattermost Tokens | `in_memory` | 0.10ms | `qa/evidence/RT-UNIT-SEC-001_QA-20260912-003005.json` |
| ✅ PASS | `RT-UNIT-NOTIF-001` | `UNIT` | Notification Verification (Unit) | Human-Visible Notification Payload Structure & Embed Links | `in_memory` | 0.08ms | `qa/evidence/RT-UNIT-NOTIF-001_QA-20260912-003005.json` |
| ✅ PASS | `RT-UNIT-SAFE-001` | `UNIT` | Safeguards (Unit) | Live QA Safeguard Blocks Mutations on Non-QA Jira Projects | `in_memory` | 0.01ms | `qa/evidence/RT-UNIT-SAFE-001_QA-20260912-003005.json` |

## Run `QA-20260912-003053` — 2026-09-12T00:31:01.895688+00:00
- **Tier**: `LIVE_E2E`
- **Target Issue**: `WRONG-999`
- **Operations**: `Jira: REAL` | `Discord: REAL` | `Mattermost: NOT_USED` | `Database: REAL`
- **Total Tests**: `18` | **Passed**: `12` | **Failed**: `0` | **Not Executed**: `0`
- **Live Integrity**: `0 Valid` | `6 Invalid`
- **Pass Rate (Active)**: `66.67%`

| Status | Test ID | Tier | Category | Title | Execution Mode | Duration | Evidence |
|:---:|:---|:---:|:---|:---|:---|:---:|:---|
| 🛑 INVALID_LIVE | `RT-LIVE-ASSIGN-001` | `LIVE_E2E` | Assignment (Live) | Live Assignment of WRONG-999 to Aqib Khan (PM) | `database:REAL` | 0.00ms | `qa/evidence/RT-LIVE-ASSIGN-001_QA-20260912-003053.json` |
| 🛑 INVALID_LIVE | `RT-LIVE-ASSIGN-002` | `LIVE_E2E` | Assignment (Live) | Live Assignment of WRONG-999 to Mubashir Butt | `database:REAL` | 0.00ms | `qa/evidence/RT-LIVE-ASSIGN-002_QA-20260912-003053.json` |
| 🛑 INVALID_LIVE | `RT-LIVE-TRANS-001` | `LIVE_E2E` | Status Testing (Live) | Live Transition of WRONG-999 to 'In Progress' | `database:REAL` | 0.00ms | `qa/evidence/RT-LIVE-TRANS-001_QA-20260912-003053.json` |
| 🛑 INVALID_LIVE | `RT-LIVE-COMM-001` | `LIVE_E2E` | Comment / Mention (Live) | Live Comment on WRONG-999 (With PM Mention) | `database:REAL` | 0.00ms | `qa/evidence/RT-LIVE-COMM-001_QA-20260912-003053.json` |
| 🛑 INVALID_LIVE | `RT-LIVE-COMM-002` | `LIVE_E2E` | Comment / Mention (Live) | Live Comment on WRONG-999 (No Mention) | `database:REAL` | 0.00ms | `qa/evidence/RT-LIVE-COMM-002_QA-20260912-003053.json` |
| 🛑 INVALID_LIVE | `RT-LIVE-ACT-001` | `LIVE_E2E` | Action Engine (Live) | Live Action Engine Execution & Idempotency on WRONG-999 | `database:REAL, action_engine:REAL` | 0.00ms | `qa/evidence/RT-LIVE-ACT-001_QA-20260912-003053.json` |
| ✅ PASS | `RT-INT-VIOL-001` | `INTEGRATION` | Workflow Violation (Integration) | Comment on 'To Do' Triggers ActiveWorkRule Violation Alert | `jira:SIMULATED, discord:SIMULATED, database:REAL, action_engine:REAL` | 1.18ms | `qa/evidence/RT-INT-VIOL-001_QA-20260912-003053.json` |
| ✅ PASS | `RT-INT-DEDUP-001` | `INTEGRATION` | Deduplication (Integration) | Unified Deduplication Across Webhook & Poller Ingestion | `jira:SIMULATED, database:REAL, webhook:SIMULATED, polling:SIMULATED` | 0.01ms | `qa/evidence/RT-INT-DEDUP-001_QA-20260912-003053.json` |
| ✅ PASS | `RT-INT-STALE-001` | `INTEGRATION` | Stale Task (Integration) | Stale Task Detection Under Safe QA Threshold (1h) | `jira:SIMULATED, discord:SIMULATED, database:REAL, scheduler:SIMULATED` | 0.01ms | `qa/evidence/RT-INT-STALE-001_QA-20260912-003053.json` |
| ✅ PASS | `RT-INT-MM-001` | `INTEGRATION` | Mattermost Identity (Integration) | Unmapped User Mapping Rejection & Zero Guessing Guarantee | `mattermost:SIMULATED, database:REAL, action_engine:REAL` | 0.01ms | `qa/evidence/RT-INT-MM-001_QA-20260912-003053.json` |
| ✅ PASS | `RT-INT-BLCK-001` | `INTEGRATION` | Blocked Task (Integration) | Blocked Task Detection and Discord Alert Generation | `jira:SIMULATED, discord:SIMULATED, database:REAL, action_engine:REAL` | 0.26ms | `qa/evidence/RT-INT-BLCK-001_QA-20260912-003053.json` |
| ✅ PASS | `RT-INT-REOP-001` | `INTEGRATION` | Reopened Task (Integration) | Reopened Task Detection (Done -> In Progress) | `jira:SIMULATED, discord:SIMULATED, database:REAL, action_engine:REAL` | 0.21ms | `qa/evidence/RT-INT-REOP-001_QA-20260912-003053.json` |
| ✅ PASS | `RT-INT-FAIL-001` | `INTEGRATION` | Failure & Recovery (Integration) | HTTP 429 Rate-Limit Handling with Backoff & Checkpoint Preservation | `jira:SIMULATED, database:REAL, polling:SIMULATED` | 0.01ms | `qa/evidence/RT-INT-FAIL-001_QA-20260912-003053.json` |
| ✅ PASS | `RT-INT-SCHED-001` | `INTEGRATION` | Scheduler (Integration) | Continuous Scheduler Loop Non-Blocking & Exception Resilience | `database:REAL, scheduler:REAL` | 0.01ms | `qa/evidence/RT-INT-SCHED-001_QA-20260912-003053.json` |
| ✅ PASS | `RT-INT-PERF-001` | `INTEGRATION` | Performance Foundation (Integration) | Performance Foundation Data Integrity & Zero Score / Zero Ranking Audit | `database:REAL` | 8117.31ms | `qa/evidence/RT-INT-PERF-001_QA-20260912-003053.json` |
| ✅ PASS | `RT-UNIT-SEC-001` | `UNIT` | Security (Unit) | Zero Exposure of Jira Tokens, Discord Webhooks, and Mattermost Tokens | `in_memory` | 0.04ms | `qa/evidence/RT-UNIT-SEC-001_QA-20260912-003053.json` |
| ✅ PASS | `RT-UNIT-NOTIF-001` | `UNIT` | Notification Verification (Unit) | Human-Visible Notification Payload Structure & Embed Links | `in_memory` | 0.04ms | `qa/evidence/RT-UNIT-NOTIF-001_QA-20260912-003053.json` |
| ✅ PASS | `RT-UNIT-SAFE-001` | `UNIT` | Safeguards (Unit) | Live QA Safeguard Blocks Mutations on Non-QA Jira Projects | `in_memory` | 0.01ms | `qa/evidence/RT-UNIT-SAFE-001_QA-20260912-003053.json` |
