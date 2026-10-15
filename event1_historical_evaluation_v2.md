# Event 1: Historical Evaluation v2 & Reconciliation Report

## 1. Methodology & Evaluation Scope

- **Evaluation Window:** 30 completed calendar days ending yesterday (`2026-09-09` through `2026-10-08`, inclusive).
- **Timezone:** `Asia/Karachi` (Pakistan Standard Time, UTC+5).
- **Roster Container:** Jira group `"Mursaleen Cluster"` retrieved dynamically via `JiraClient.get_group_members()`.
- **Total Group Members:** 22 active accounts.
- **Canonical Excluded Accounts:**
  - Mohammad Mursaleen (`557058:8b3f9c31-7d88-473a-9351-abacc5b84933`)
  - Aqib Khan (`712020:566cad70-4a54-42bc-bf36-0c6132fe3cf0` and `712020:1b564792-a3ab-447c-951d-17aa5507b946`)
  - Syed Muhammad Usman (`5f83e3937d9637006ffd0436`)
- **Eligible Cluster Members Evaluated:** 18 active team members.
- **Total Member-Date Evaluations:** $30 \text{ dates} \times 18 \text{ members} = \mathbf{540} \text{ combinations}$.

---

## 2. Mutually Exclusive Decision Rules & Precedence Order

Every member-date combination is categorized into exactly one mutually exclusive outcome according to this strict precedence:

1. **Exclusion Check:** If member is in `CANONICAL_EXCLUDED_ACCOUNT_IDS` $\rightarrow$ `EXCLUDED` (omitted from eligible evaluations).
2. **Weekend Rule:** If date is Saturday or Sunday $\rightarrow$ `SKIPPED_WEEKEND`.
3. **Holiday Rule:** If date is an official gazetted holiday or approved local holiday $\rightarrow$ `SKIPPED_HOLIDAY`.
4. **Worklog Compliance ($\ge$ 60 min):** If logged time $\ge 60$ minutes on a working day $\rightarrow$ `COMPLIANT`.
5. **Partial Worklog ($1\text{--}59$ min):**
   - If confirmed approved leave exists $\rightarrow$ `SKIPPED_APPROVED_LEAVE`.
   - Otherwise $\rightarrow$ `REMINDER_CANDIDATE`.
6. **Zero Worklog ($0$ min):**
   - If confirmed approved leave exists $\rightarrow$ `SKIPPED_APPROVED_LEAVE`.
   - If meaningful Jira activity detected today $\rightarrow$ `REMINDER_CANDIDATE`.
   - If no Jira activity detected $\rightarrow$ `SKIPPED_NO_ACTIVITY_LEAVE_UNVERIFIED`.
7. **Missing / Unresolvable Data:** $\rightarrow$ `UNDETERMINED`.

---

## 3. Reconciliation Between Historical Evaluation v1 and v2

| Category | v1 Count | v2 Count | Status | Reconciliation Notes |
|---|---|---|---|---|
| **Total Expected Combinations** | 540 | 540 | Exact Match | 30 dates $\times$ 18 members |
| **Evaluated Combinations** | 540 | 540 | Exact Match | 0 dropped, 0 duplicate combinations |
| 🟢 **`COMPLIANT`** ($\ge 60$m) | 346 | 346 | Exact Match | Fully reproducible across all 18 members |
| 🟡 **`REMINDER_CANDIDATE`** | 8 | 8 | Exact Match | Exactly the same 8 member-dates reproduced |
| ⚪ **`SKIPPED_NO_ACTIVITY_LEAVE_UNVERIFIED`** | 42 | 42 | Exact Match | Clarified label: 0m + 0 activity (leave unverified) |
| 🌴 **`SKIPPED_APPROVED_LEAVE`** | 0 | 0 | Exact Match | Evaluated; 0 records because no leave system exists |
| 📅 **`SKIPPED_WEEKEND`** | 144 | 144 | Exact Match | 8 weekend dates $\times$ 18 members |
| 🏖️ **`SKIPPED_HOLIDAY`** | 0 | 0 | Exact Match | Evaluated; 0 official holidays in this date range |
| ❓ **`UNDETERMINED`** | 0 | 0 | Exact Match | All data points cleanly resolved |
| **Sum Total** | **540** | **540** | **100% Reconciled** | Sum of categories equals total evaluated combinations |

---

## 4. Reproducibility of the 8 Reminder Candidates

All **8 reminder candidates** from Evaluation v1 were reproduced identically in v2:

| # | Date | Day | Jira Display Name | Jira Account ID | Minutes Logged | Jira Activity Detected | Trigger Reason |
|---|---|---|---|---|---|---|---|
| 1 | 2026-09-09 | Wednesday | **Azain Hassan** | `712020:1ddac8e3-e006-48e7-b4c9-ee941efc8e6e` | 40m | Yes | Underlogged (40m < 60m) |
| 2 | 2026-09-09 | Wednesday | **Mubashir Butt** | `712020:e268bcd8-d981-4b4d-992d-d5694745df8b` | 0m | Yes | Zero worklog with Jira updates |
| 3 | 2026-09-09 | Wednesday | **Muhammad Ali Siddiqui** | `712020:2783ea21-c611-402d-9adb-0529f5b7066d` | 0m | Yes | Zero worklog with Jira updates |
| 4 | 2026-09-09 | Wednesday | **Tahir Ali** | `638855b85fce844d606bb422` | 50m | Yes | Underlogged (50m < 60m) |
| 5 | 2026-09-10 | Thursday | **Muhammad Usama Azad** | `712020:fb8608cb-6393-48a7-a3ab-1ad744a2b7f6` | 35m | Yes | Underlogged (35m < 60m) |
| 6 | 2026-09-14 | Monday | **Muhammad Hamza** | `5fb3d908facfd6007697c25a` | 0m | Yes | Zero worklog with Jira updates |
| 7 | 2026-09-25 | Friday | **Muhammad Usama Azad** | `712020:fb8608cb-6393-48a7-a3ab-1ad744a2b7f6` | 0m | Yes | Zero worklog with Jira updates |
| 8 | 2026-09-29 | Tuesday | **Ahsan Amin** | `712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de` | 25m | Yes | Underlogged (25m < 60m) |

---

## 5. Data Quality, Activity, and Leave Audit Findings

1. **Leave Tracking Source:**
   - There is no HR or leave management API/database connected to the agent.
   - Leave status for all 540 evaluations is strictly reported as `leave unverified`.
   - By design, the fail-safe policy skips 0-worklog days where no Jira activity is detected (42 instances), preventing false reminders when employees are out of office.

2. **Activity Correlation:**
   - 684 Jira issues were scanned across 14 paginated JQL search pages with changelog expansions.
   - Any comments, status transitions, or field updates authored on that calendar date by the member correctly marked the member as active.

3. **Mattermost Delivery Readiness:**
   - All 18 eligible members are currently `UNMAPPED` in the local database.
   - Because the system fails closed, zero unauthorized DMs would be sent in production until identities are confirmed and seeded into `user_mappings`.
