# Event 1: Recipient Exclusion Review

## 1. Executive Summary

This document reviews the active Jira account exclusions configured in the PM Operations Agent, identifies their source, evaluates how they affect **Event 1: Daily Mursaleen Cluster Worklog Reminder**, and documents the decisions required before production activation.

---

## 2. Configured Exclusions and Account IDs

The single source of truth for Jira account exclusions is defined in `app/config/settings.py` via `DAILY_WORKLOG_EXCLUDED_ACCOUNT_IDS` and accessed programmatically via `settings.get_canonical_excluded_account_ids()` / `settings.CANONICAL_EXCLUDED_ACCOUNT_IDS`.

| Jira Display Name | Jira Stable Account ID | Config Source | Global vs. Event-Specific |
|---|---|---|---|
| **Aqib Khan** (Primary) | `712020:566cad70-4a54-42bc-bf36-0c6132fe3cf0` | `settings.DAILY_WORKLOG_EXCLUDED_ACCOUNT_IDS` | Global Canonical Exclusion |
| **Aqib Khan / Alias** | `712020:1b564792-a3ab-447c-951d-17aa5507b946` | `settings.DAILY_WORKLOG_EXCLUDED_ACCOUNT_IDS` | Global Canonical Exclusion |
| **Mohammad Mursaleen** | `557058:8b3f9c31-7d88-473a-9351-abacc5b84933` | `settings.DAILY_WORKLOG_EXCLUDED_ACCOUNT_IDS` | Global Canonical Exclusion |
| **Syed Muhammad Usman** | `5f83e3937d9637006ffd0436` | `settings.DAILY_WORKLOG_EXCLUDED_ACCOUNT_IDS` | Global Canonical Exclusion |

---

## 3. Exclusion Source & Scope Analysis

1. **Global Canonical Scope:**
   - In `app/config/settings.py` (lines 331–356), `get_canonical_excluded_account_ids()` parses the comma-separated environment/default string.
   - These exclusions are applied across:
     - Daily Team Worklog Report (`app/core/reports/worklog_report.py`)
     - Performance scoring and role metrics (`app/core/performance/roles.py`)
     - Historical evaluation v1 and v2

2. **Analysis of Syed Muhammad Usman (`5f83e3937d9637006ffd0436`):**
   - **Current Status:** Syed Muhammad Usman is currently part of the Jira group `"Mursaleen Cluster"` (active: True) but is excluded globally by `DAILY_WORKLOG_EXCLUDED_ACCOUNT_IDS`.
   - **Reason in Existing Codebase:** In the daily team worklog report and executive summaries, leadership/senior accounts and external managers are excluded from individual worklog threshold monitoring.
   - **Event 1 Impact:** Because Usman's account ID is in `CANONICAL_EXCLUDED_ACCOUNT_IDS`, Event 1 evaluates 18 cluster members instead of 19.

---

## 4. Unresolved Decisions Needed from User

Before finalizing the Event 1 production roster, the following decisions must be confirmed:

1. **Syed Muhammad Usman Exclusion Policy:**
   - *Option A (Recommended):* Keep Syed Muhammad Usman excluded from Event 1 daily reminder DMs (aligning with team lead / supervisor exclusion policy).
   - *Option B:* Remove Usman from `DAILY_WORKLOG_EXCLUDED_ACCOUNT_IDS` or create an Event-1-specific recipient inclusion filter if he should receive daily worklog reminders.

2. **Event-Specific vs. Global Exclusion Boundary:**
   - Confirm whether Event 1 should continue to inherit `settings.get_canonical_excluded_account_ids()` directly, or if an explicit `EVENT1_EXCLUDED_ACCOUNT_IDS` setting should be introduced.
