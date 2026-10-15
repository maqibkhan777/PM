# Event 1: Production Readiness & Activation Checklist

## 1. Readiness Summary

| Component | Status | Required Action Before Production |
|---|---|---|
| **Safety Baseline (Self-Only Guard)** | 🟢 Verified & Active | Maintain self-only guard until all mappings & event scopes are approved |
| **Jira Cloud Connectivity & Group API** | 🟢 Verified | Operational; fetches 22 active members via `JiraClient.get_group_members()` |
| **Historical Worklog & Activity Evaluation** | 🟢 Verified (v2) | 540 evaluations reconciled; 8 reminder candidates reproduced |
| **Mattermost Identity Mapping** | 🔴 Incomplete (18 Unmapped) | Populate `user_mappings` SQLite table using verified Mattermost IDs |
| **Holiday Calendar & Persistence** | 🟡 Model Needed | Implement `holiday_calendar` schema and seed gazetted public holidays |
| **Discord Holiday Approval Interface** | 🟡 Design Approved | Implement slash command `/pm holiday approve <id>` / interactive buttons |
| **Scheduler & Periodic Worker** | 🟡 Ready for Integration | Register 18:20 PKT cron trigger in `PeriodicScheduler` |

---

## 2. Step-by-Step Action Plan Before Activation

### Phase 1: Identity & Authorization Setup (Prerequisite)
- [ ] Review `event1_mattermost_mapping_audit.csv` and `event1_mapping_input_template.csv`.
- [ ] Obtain verified Mattermost user IDs for the 18 eligible Mursaleen Cluster members.
- [ ] Seed the `user_mappings` database table with verified `(jira_account_id, mattermost_user_id)` pairs.
- [ ] Confirm the decision regarding Syed Muhammad Usman's exclusion status.

### Phase 2: Holiday Calendar Implementation
- [ ] Create `holiday_calendar` table migration (`holiday_date`, `name`, `source_type`, `source_reference`, `status`, `approved_by`).
- [ ] Seed annual Pakistani gazetted holidays with `status="VERIFIED"`.
- [ ] Add `/pm holiday approve` slash command handling restricted to `DISCORD_ADMIN_USER_ID`.

### Phase 3: Event 1 Service Implementation
- [ ] Create `app/services/worklog_reminder_service.py` implementing the validated decision matrix.
- [ ] Create `scheduled_event_deliveries` repository for durable idempotency (`EVENT1:{work_date}:{user_id}`).
- [ ] Integrate 18:20 PKT trigger into `app/services/scheduler.py`.

### Phase 4: Controlled Dry-Run & Production Cutover
- [ ] Execute automated dry-run with `DRY_RUN=true` to verify log output and idempotency tracking.
- [ ] Conduct operator review of dry-run execution logs.
- [ ] Enable `SCHEDULER_ENABLED=true` under monitored production conditions.
