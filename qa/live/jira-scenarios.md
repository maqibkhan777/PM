# Jira Live Test Scenarios (Target: TREN-378)

This guide details the step-by-step procedures executed for genuine `LIVE_E2E` tests on the primary fixture ticket `TREN-378`.

---

## 1. Genuine LIVE_E2E Scenarios (Executed on Jira Cloud)

### Scenario 1.1: Live Assignment to PM (`RT-LIVE-ASSIGN-001`)
1. **Pre-State Query**: Jira REST API is queried (`GET /rest/api/3/issue/TREN-378`). Pre-mutation assignee is recorded.
2. **Mutation**: Issue is assigned to **Aqib Khan (PM)** (`712020:566cad70-4a54-42bc-bf36-0c6132fe3cf0`) via `PUT /rest/api/3/issue/TREN-378/assignee`.
3. **PM Agent Pipeline Ingestion**: `TaskAssigned` event ingested into PM Agent orchestrator.
4. **Rules & Notification**: `AssignmentRule` executes; `SendNotification` action dispatched to Discord.
5. **State Confirmation**: Post-mutation assignee queried via Jira REST API and confirmed matching PM account ID.
6. **Persistence**: Event and audit records persisted in SQLite.

### Scenario 1.2: Live Assignment to Mubashir Butt (`RT-LIVE-ASSIGN-002`)
1. **Mutation**: Issue is assigned to **Mubashir Butt** (`712020:e268bcd8-d981-4b4d-992d-d5694745df8b`) via Jira REST API.
2. **PM Agent Pipeline Ingestion**: `TaskAssigned` event ingested.
3. **Rules & Notification**: `AssignmentRule` (Team Awareness) executes; notification dispatched; PM does not receive personal "assigned to you" alert.
4. **State Confirmation**: Post-mutation assignee queried and confirmed matching Mubashir's account ID.

### Scenario 1.3: Live Workflow Transition to 'In Progress' (`RT-LIVE-TRANS-001`)
1. **Query Available Transitions**: Jira REST API is queried (`GET /rest/api/3/issue/TREN-378/transitions`).
2. **Mutation**: Transition to `In Progress` executed via `POST /rest/api/3/issue/TREN-378/transitions`.
3. **PM Agent Pipeline Ingestion**: `TaskStatusChanged` event ingested; state projection updated.
4. **State Confirmation**: Status queried from Jira Cloud and confirmed as `In Progress`.

### Scenario 1.4: Live Comment with PM Mention (`RT-LIVE-COMM-001`)
1. **Mutation**: Comment created on `TREN-378` containing `[~accountid:<PM_ACCOUNT_ID>]` via `POST /rest/api/3/issue/TREN-378/comment`.
2. **PM Agent Pipeline Ingestion**: `TaskCommentAdded` event ingested.
3. **Rules & Notification**: `CommentNotificationRule` executes; PM mention notification generated.
4. **State Confirmation**: Returned comment ID confirmed.

### Scenario 1.5: Live Comment without PM Mention (`RT-LIVE-COMM-002`)
1. **Mutation**: Comment created on `TREN-378` without PM mention.
2. **PM Agent Pipeline Ingestion**: `TaskCommentAdded` event ingested.
3. **Rules & Notification**: Silent ingestion confirmed (`COMMENT_NOTIFY_ALL=false`); no PM alert dispatched.
4. **State Confirmation**: Returned comment ID confirmed.

### Scenario 1.6: Live Action Engine Execution & Idempotency (`RT-LIVE-ACT-001`)
1. **First Execution**: `ActionEngine.execute()` called with an `AddComment` action and a unique `idempotency_key`. Mutation executed on Jira Cloud; status returned `COMPLETED`.
2. **Second Execution (Repeat)**: `ActionEngine.execute()` called with identical action and idempotency key. Database idempotency barrier returns cached result; zero secondary network mutation on Jira Cloud; status returned `COMPLETED` with identical `action_id`.

---

## 2. Integration Scenarios (Local Pipeline & Rules Verification)

The following scenarios are verified through the integration suite (`tests/integration/`):
- `RT-INT-VIOL-001`: Comment on `To Do` triggers `ActiveWorkRule` violation alert.
- `RT-INT-DEDUP-001`: Unified deduplication across webhook and poller streams.
- `RT-INT-STALE-001`: Stale task detection under safe QA threshold (1h).
- `RT-INT-MM-001`: Unmapped user mapping rejection & zero guessing guarantee.
- `RT-INT-BLCK-001`: Blocked task detection and alert generation.
- `RT-INT-REOP-001`: Reopened task detection (`Done` -> `In Progress`).
- `RT-INT-FAIL-001`: Simulated HTTP 429 rate limit backoff and checkpoint preservation.
- `RT-INT-SCHED-001`: Continuous scheduler loop non-blocking error resilience.
- `RT-INT-PERF-001`: Performance foundation data quality and zero score / ranking audit.
