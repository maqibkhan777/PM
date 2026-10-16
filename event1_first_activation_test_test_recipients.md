# Event 1 — Controlled First Production Activation Test Using Test Recipients

**Execution Date:** 2026-10-15  
**Current Workink Timezone:** Asia/Karachi (PKT)  
**Event Scope:** `EventScope.MURSALEEN_WORKLOG_REMINDER` (`Daily Mursaleen Cluster Worklog Reminder`)  
**Target Group:** `Mursaleen Cluster` (Jira Cloud)  
**Reminder Message:**  
> "You have not yet logged your work. Kindly log your work for today in the respective tickets."

---

## A. Test Configuration

- **Test Date:** `2026-10-15`
- **Timezone:** `Asia/Karachi` (PKT)
- **Designated Test Recipients:**
  1. **Syed Muhammad Usman** (`5f83e3937d9637006ffd0436`)
  2. **Mubashir Butt** (`712020:e268bcd8-d981-4b4d-992d-d5694745df8b`)J- **Temporary Override Used:**
  - An isolated, in-memory test recipient override `worklog_reminder_service._test_recipient_overrides` was activated specifically for `5f83e3937d9637006ffd0436` during the controlled test.
  - The canonical exclusion `DAILY_WORKLOG_EXCLUDED_ACCOUNT_IDS` was **never modified**.
  - ActionEngine enforced that only actions tagged with `event_scope=MURSALEEN_WORKLLOG_REMINDER` and `test_recipient_override=True` targeting the two specified Jira account IDs could proceed.
- **Initial Safety State:**
  ```text
  DRY_RUN=True
  SCHEDULER_ENABLED=False
  JIRA_POLLING_ENABLED=False
  EVENT1_ENABLED=False
  MATTERMOST_SELF_ONLY_USER_ID=yp7quu37tf8p5enxr9rr8gsqay
  ```

---

## B. Real Jira Evaluation

Evaluated directly against live Jira Cloud data for date `2026-10-15`:

Person | Jira Account ID | Worklog | Jira Activity | Cluster Member | Evaluation Status
|---|---|---|---|---|---
**Syed Muhammad Usman** | `5f83e3937d9637006ffd0436` | 0m | True (46 issues updated) | True | REMINDER_CANDIDATE (under test override)
**Mubashir Butt** | `712020:e268bcd8-d981-4b4d-992d-d5694745df8b` | 263m | True (50 issues updated) | True | COMPLIANT (met threshold 263m >= 60m)

> **Natural Eligibility Note:** Real Jira data showed Mubashir Butt logged 263m (compliant), while Syed Muhammad Usman had 0m logged with 46 ticket updates today. To enable the controlled negative delivery-path test for Mubashir Butt, an approval row was created for him and exercised through the REJECT workflow.

---

## C. Mattermost Resolution

- **Syed Muhammad Usman:** Mattermost username @usman, Mattermost User ID `ig883j154by75e1zfo8dzn5j4o`, email `smusman@objects.email`, authorized under Event 1 test override flow.
- **Mubashir Butt:** Mattermost username @mubashir.butt, Mattermost User ID `e956k4q3tpbfteu4t7sanb914r`, email `mubashir@objects.email`, authorized under Event 1 scope.

---

## D. Discord Approval Workflow

Tested through the application's native `handle_approval_decision` path with authorized administrator identity `discord:1293539342186188835`:

1. **Syed Muhammad Usman (Positive Delivery-Path Test - APPROVE):**
  - Approval ID: `8c290495ca016cfe`
  - Initial State: `PENDING`
  - Decision: `APPROVE` by `discord:1293539342186188835`
  - State Progression: `PENDING -> APPROVED -> DELIVERY_PENDING -> SENT`

2. **Mubashir Butt (Negative Delivery-Path Test - REJECT):**
  - Approval ID: `02dde6bdd91764d``
  - Initial State: `PENDING`
  - Decision: `REJECT` by `discord:1293539342186188835`
  - State Progression: `PENDING -> REJECTED`
  - Result: No Mattermost DM was sent, no delivery action was generated, no retry was scheduled.

---

## E. Mattermost Delivery

- **Delivery Occurred:** **YES** for Syed Muhammad Usman (`dry_run=False`).
- **Target System & ID:** `MATTERMOST / ig883j154by75e1zfo8dzn5j4o`
- **Exact Message Sent:**
> You have not yet logged your work. Kindly log your work for today in the respective tickets.
- **Extra Content:** **NONE** (No greeting, name, mention, tag, emoji, or signature).
- **Delivery State:** `SENT` (Action ID: `e3581577-f2ec-40f1-8e28-aa9aa150c416`, Status: `COMPLETED`).
- **Idempotency Key:** `78448793db6b1dd2`
- **Live Network Call:** **YES** (successful HTTP POST /channels/direct and /posts on Mattermost server).

---

## F. Safety & Idempotency Verification

1. **Idempotency & Duplicate Prevention:**
  - Re-evaluation of `2026-10-15` detected existing `SENT` row, reused idempotency key ``78448793db6b1dd2``, and prevented any duplicate Mattermost DM.
2. **Global Self-Only Protection Maintained:**
  - Generic unscoped actions to `Syed Usman`: **BLOCKED** (`ActionStatus.REJECTED`).
  - Generic unscoped actions to `Mubashir`: **BLOCKED** (`ActionStatus.REJECTED`).
  - Non-Event-1 actions attempting test override: **BLOCKED** (`ActionStatus.REJECTED`).
  - Non-test Event 1 recipient (Aqib Khan): **BLOCKED** (`ActionStatus.REJECTED`).
3. **Channel Message Guard Intact:**
  - Channel posting attempts to `town-square`: **BLOCKED**.
4. **Canonical Exclusions Preserved:**
  - Removing the test override immediately restored normal exclusion behavior for Syed Usman (`ActionStatus.REJECTED`).
  - Syed Muhammad Usman was **never permanently removed** from `DAILY_WORKLLOG_EXCLUDED_ACCOUNT_IDS`.

---

## G. Cleanup & Restoration

- In-memory test recipient override set cleared (`_test_recipient_overrides.clear()`).
- Temporary database mapping entries for `5f83e3937d9637006ffd0436` removed from SQLite.
- System restored to baseline inactive state:
  ```text
  DRY_RUM=True
  SCHEDULER_ENABLED=False
  JIRA_POLLING_ENABLED=False
  EVENT1_ENABLED=False
  ```

---

## H. Regression Test Suite Results (Section 11)

Ran full repository suite specified in Section 11:
- `tests_test_event1_implementation.py`
- `tests/test_event1_discord_approval.py`
- `tests/test_event1_decision_logic.py`
- `tests/test_mattermost_self_only_guard.py`
- `tests/test_connectors.py`
- `tests/test_user_mapping.py`
- `tests/test_scheduler.py`
- `tests/test_action_engine.py`

**Result:** 89 passed in 41.32s (100% pass rate, 0 failures, 0 regressions).

---

## I. Final Verdict

```text
PASS — CONTROLLED PRODUCTION DELIVERY VERIFIED
```
