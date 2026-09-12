# Live QA Scenario Guide

This directory contains executable scenario descriptions, verification procedures, notification presentation standards, and the living chronological execution log for the PM Operations Agent.

---

## 1. Live Execution Prerequisites

1. **Explicit Safeguard Configuration**:
   Before running live mutations, ensure `.env` has valid credentials and safeguard settings:
   ```bash
   LIVE_QA_ENABLED=true
   LIVE_QA_JIRA_ISSUE=TREN-378
   DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
   ```

2. **Primary Disposable Target**:
   All live tests execute exclusively against `TREN-378`. Any non-approved issue (e.g. `--issue WRONG-999`) is rejected immediately by the safeguard gate.

3. **Execution Script**:
   Run the CLI orchestrator to execute the live scenario suite:
   ```bash
   python scripts/run_live_qa.py --tier live --issue TREN-378
   ```

---

## 2. Test Suite Breakdown in Live Runner

When running `python scripts/run_live_qa.py --tier live --issue TREN-378`, 18 scenarios are executed with honest tier separation:

- **6 `LIVE_E2E` Scenarios**: Real Jira REST API mutations (`TREN-378`), event bus ingestion, rules engine, Discord webhook dispatch, audit persistence, and remote Jira read-back confirmation.
- **9 `INTEGRATION` Scenarios**: Local multi-component pipelines testing deduplication, stale task rules, rate limits, blocked/reopened states, scheduler resilience, and performance foundation validation.
- **3 `UNIT` Scenarios**: In-memory security redaction, notification embed layout, and safeguard gate assertion logic.

---

## 3. Environment Dependencies & Non-Live Scenarios

- **Mattermost**: Currently `NOT_CONFIGURED` in this environment. Tests are marked `NOT_EXECUTED` / `ENVIRONMENT_REQUIRED`. Zero-guessing is verified via integration testing (`RT-INT-MM-001`).
- **Jira Webhooks**: Real unsolicited webhook delivery requires a publicly accessible HTTPS endpoint. Polling ingestion is verified live (`JiraPoller`); deduplication is verified via integration tests (`RT-INT-DEDUP-001`).
- **Discord Read-Back**: Discord incoming webhooks are write-only. Delivery is real (HTTP POST), and verification is marked `HUMAN_REQUIRED`.

---

## 4. File Overview

- `jira-scenarios.md`: Step-by-step procedures for live Jira mutations on `TREN-378`.
- `notification-scenarios.md`: Visual inspection criteria for Discord embeds and Mattermost direct messages.
- `execution-log.md`: Chronological log of real test runs with test IDs, durations, statuses, execution modes, and evidence links.
