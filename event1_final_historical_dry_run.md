# Event 1: Final Historical Dry-Run and Recipient Validation Report

## 1. Executive Summary

A complete, end-to-end non-delivering historical simulation of **Event 1 (Daily Mursaleen Cluster Worklog Reminder)** was conducted across the 30-day evaluation window (**2026-09-09 to 2026-10-08**, `Asia/Karachi`), reading all **18 active Jira-to-Mattermost mappings directly from the database repository** and evaluating the exact reminder message and duplicate-prevention mechanisms.

### Key Results
- **Evaluated Period:** 30 calendar days (22 working weekdays, 8 weekend days).
- **Total Member-Date Evaluations:** **540** ($30 \times 18$).
- **Mappings Persisted & Validated:** **18/18** unique, verified 1-to-1 mappings.
- **Reminder Candidates Identified & Prepared:** **8** (100% reproducible with Evaluation v1 & v2).
- **Exact Message Verification:** **100% compliant** across all 8 prepared candidates.
- **Duplicate-Prevention / Idempotency:** **Verified** (each candidate generated a distinct deterministic key; repeat evaluations prevent duplicate dispatches).
- **Live Dispatches:** **0** (no live Mattermost DMs, channel posts, or Discord messages were sent).

---

## 2. Mapping Validation Totals

All mappings were resolved directly from the SQLite `user_mappings` database table using `UserMappingService`:

| Metric | Result | Status |
|---|---|---|
| Total Eligible Jira Accounts in Cluster | **18** | Confirmed |
| Persisted Explicit Mappings in Database | **18** | Confirmed (`EXPLICIT_MAPPING`) |
| Unique Mattermost User IDs | **18** | Confirmed (Zero collisions/duplicates) |
| Excluded Accounts in Mapping Set | **0** | Confirmed (Aqib, Mursaleen, Usman omitted) |
| Muhammad Ali Siddiqui Resolution | `alisiddiqui` (`ceujrj84zjyfuce8kx8b7bnn4a`) | Confirmed (Distinct from Korany) |
| Ambiguous or Unresolved Accounts | **0** | Confirmed (Fail-closed baseline intact) |

---

## 3. Historical Category Reconciliation (540 Evaluations)

| Category | Replay Count | Description |
|---|---|---|
| 🟢 **`COMPLIANT`** | **346** | Member logged $\ge 60$ minutes on a working weekday |
| 🟡 **`REMINDER_CANDIDATE`** | **8** | Underlogged ($1\text{--}59\text{m}$) or $0\text{m}$ with verified Jira activity |
| ⚪ **`SKIPPED_NO_ACTIVITY_LEAVE_UNVERIFIED`** | **42** | $0\text{m}$ logged and zero Jira activity detected (presumed out of office) |
| 🌴 **`SKIPPED_APPROVED_LEAVE`** | **0** | No external leave records exist in repo |
| 📅 **`SKIPPED_WEEKEND`** | **144** | 8 weekend dates $\times$ 18 members |
| 🏖️ **`SKIPPED_HOLIDAY`** | **0** | No official gazetted holidays fell in this date range |
| ❓ **`UNDETERMINED`** | **0** | All combinations cleanly categorized |
| **Total Evaluated Combinations** | **540** | **100% Reconciled** |

---

## 4. Candidate-by-Candidate Dry-Run Results

For each of the 8 reminder candidates, the exact message was prepared, verified, and tagged with a durable idempotency key (`SHA256("EVENT1:{date}:{mm_user_id}")[:16]`):

| # | Date | Day | Jira Display Name | Jira Account ID | Mattermost User ID | Logged Time | Activity Detected | Prepared Message | Idempotency Key | Simulated Status |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | **2026-09-09** | Wednesday | **Azain Hassan** | `712020:1ddac8e3-e006-48e7-b4c9-ee941efc8e6e` | `zhth9yx8yprpbds1nyc4h7tich` | 40m | Yes | `You have not yet logged your work. Kindly log your work for today in the respective tickets.` | `1445d3b39344326b` | `SIMULATED_PREPARED` |
| 2 | **2026-09-09** | Wednesday | **Mubashir Butt** | `712020:e268bcd8-d981-4b4d-992d-d5694745df8b` | `e956k4q3tpbfteu4t7sanb914r` | 0m | Yes | `You have not yet logged your work. Kindly log your work for today in the respective tickets.` | `ece7dfb11558c78b` | `SIMULATED_PREPARED` |
| 3 | **2026-09-09** | Wednesday | **Muhammad Ali Siddiqui** | `712020:2783ea21-c611-402d-9adb-0529f5b7066d` | `ceujrj84zjyfuce8kx8b7bnn4a` | 0m | Yes | `You have not yet logged your work. Kindly log your work for today in the respective tickets.` | `c43110987efe7f14` | `SIMULATED_PREPARED` |
| 4 | **2026-09-09** | Wednesday | **Tahir Ali** | `638855b85fce844d606bb422` | `k3n3krao33gy9dwubgwr1udrbe` | 50m | Yes | `You have not yet logged your work. Kindly log your work for today in the respective tickets.` | `3517bbb7f7bf14c9` | `SIMULATED_PREPARED` |
| 5 | **2026-09-10** | Thursday | **Muhammad Usama Azad** | `712020:fb8608cb-6393-48a7-a3ab-1ad744a2b7f6` | `7j347ruqitnotr8mrgibjkrdbw` | 35m | Yes | `You have not yet logged your work. Kindly log your work for today in the respective tickets.` | `84db7455ba0b5344` | `SIMULATED_PREPARED` |
| 6 | **2026-09-14** | Monday | **Muhammad Hamza** | `5fb3d908facfd6007697c25a` | `rx11z31933n59bnakwaadbcf5y` | 0m | Yes | `You have not yet logged your work. Kindly log your work for today in the respective tickets.` | `60f48d796b518836` | `SIMULATED_PREPARED` |
| 7 | **2026-09-25** | Friday | **Muhammad Usama Azad** | `712020:fb8608cb-6393-48a7-a3ab-1ad744a2b7f6` | `7j347ruqitnotr8mrgibjkrdbw` | 0m | Yes | `You have not yet logged your work. Kindly log your work for today in the respective tickets.` | `35ade1d363c6c40d` | `SIMULATED_PREPARED` |
| 8 | **2026-09-29** | Tuesday | **Ahsan Amin** | `712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de` | `kf4nkziu7t8w9de1pdog9cemfo` | 25m | Yes | `You have not yet logged your work. Kindly log your work for today in the respective tickets.` | `505e46f964865ff5` | `SIMULATED_PREPARED` |

---

## 5. Exact Message Validation

Every simulated reminder candidate produces the identical, unpersonalized message payload:

```text
You have not yet logged your work. Kindly log your work for today in the respective tickets.
```

- **Greeting check:** `PASS` (zero greetings like `Hi`, `Hello`, `Dear`).
- **Personalization check:** `PASS` (zero recipient names or `@mentions`).
- **Tag / Formatting check:** `PASS` (no markdown headers, emojis, tags, or signatures).
- **Punctuation check:** `PASS` (exact sentence punctuation).

---

## 6. Exclusion Validation

Confirmed active exclusions in `app/config/settings.py` (`DAILY_WORKLOG_EXCLUDED_ACCOUNT_IDS`):
1. `712020:566cad70-4a54-42bc-bf36-0c6132fe3cf0` (Aqib Khan - Primary) $\rightarrow$ `EXCLUDED`
2. `712020:1b564792-a3ab-447c-951d-17aa5507b946` (Aqib Khan - Alias) $\rightarrow$ `EXCLUDED`
3. `557058:8b3f9c31-7d88-473a-9351-abacc5b84933` (Mohammad Mursaleen) $\rightarrow$ `EXCLUDED`
4. `5f83e3937d9637006ffd0436` (Syed Muhammad Usman) $\rightarrow$ `EXCLUDED`

Zero excluded accounts were included in the 18 eligible member roster or among the 8 reminder candidates.

---

## 7. Duplicate-Prevention & Idempotency Validation

- **Idempotency Strategy:** `EVENT1:{target_date}:{mattermost_user_id}` hashed into a persistent unique delivery key.
- **Repeat Simulation Test:** Re-evaluating the historical batch confirmed that all 8 keys match existing records, preventing duplicate delivery requests.

---

## 8. Safety & Operational Confirmation

- **Live Dispatches:** **Zero** messages were sent.
- **Safety Flags:**
  - `DRY_RUN`: `True`
  - `JIRA_POLLING_ENABLED`: `False`
  - `SCHEDULER_ENABLED`: `False`
  - `MATTERMOST_SELF_ONLY_USER_ID`: `yp7quu37tf8p5enxr9rr8gsqay` (Active and enforced)
- **Event 1 Readiness:** Fully verified and dry-run validated. Event 1 remains **inactive and un-scheduled** pending explicit authorization.
