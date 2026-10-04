# Planning Data & Cross-Project Capacity Validation Report

- **Report ID:** `val-d3a602eb`
- **Generated At:** `2026-10-01T15:18:03.675065+00:00`
- **Anchor Date:** `2026-10-01` | **Horizon:** 10 working days
- **Overall Validation Outcome:** **`VALID`**

## 1. Executive Summary
Validated planning data across 2 project(s) (SMTPSUPORT, GF): 19 active tasks, 9 assigned resources, 0 over-allocated, 1 cross-project allocations. Status: VALID.

## 2. Project Scope & Resolution
- **Configured Projects:** SMTPSUPORT, GF
- **Successfully Resolved (2):** SMTPSUPORT, GF

## 3. Workload & Estimate Breakdown
| Metric | Count |
| :--- | :---: |
| **Total Active Tasks Evaluated** | 19 |
| **Tasks with Explicit Jira Remaining Estimates** | 4 |
| **Tasks with Empirical Benchmark Proxies** | 15 |
| **Tasks with Missing/Unestimated Effort** | 0 |
| **Tasks Missing Assignee** | 0 |
| **Blocked Tasks (Unresolved Predecessors)** | 2 |

## 4. Resource Cross-Project Capacity Audit
| Resource | Role | Projects | Active Tasks | Remaining Effort | Available (10d) | Allocation % | Contention |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| **Aqib Khan** | Unknown | GF, SMTPSUPORT | 8 | 18.0h | 65.0h | 27.7% | ⚠️ Cross-Project |
| **Mubashir Butt** | Customer Support Engineer | SMTPSUPORT | 2 | 2.0h | 65.0h | 3.1% | Single Project |
| **Nauman Sadiq** | Senior BA | SMTPSUPORT | 2 | 5.6h | 65.0h | 8.6% | Single Project |
| **Ahsan Iftikhar** | Senior BA | SMTPSUPORT | 1 | 4.6h | 65.0h | 7.0% | Single Project |
| **Hamza Hanif** | SEO | SMTPSUPORT | 1 | 0.0h | 65.0h | 0.0% | Single Project |
| **Syed ali** | Senior WordPress Developer | GF | 1 | 4.5h | 65.0h | 6.9% | Single Project |
| **Muhammad Bilal Khan** | Mid-level QA | GF | 2 | 48.3h | 65.0h | 74.4% | Single Project |
| **Muhammad Sufiyan** | Senior QA Engineer | GF | 1 | 4.5h | 65.0h | 6.9% | Single Project |
| **Azain Hassan** | Designer | GF | 1 | 3.0h | 65.0h | 4.6% | Single Project |

## 5. Capacity Calculation & Data Quality Disclosures
- **Formula Used:** `Available Capacity = forecast_daily_hours (6.5h) * horizon_working_days (10d) = 65.0h`
- **Working Hours:** Nominal default (6.5h - 6.75h) per standard working day.
- **Absence & Calendar Status:**
  - Working Days/Holidays: `UNKNOWN` (No authoritative local holiday calendar repository).
  - Vacation/Sick Leave: `UNKNOWN` (No authoritative leave tracking system integration).
  - Non-Project Focus Factor: `UNKNOWN` (Assumed 100% project allocation).

## 6. Dependency & Blocker Analysis
| Source Issue | Target Issue | Relationship | Target Status | Cross-Project? | Notes |
| :--- | :--- | :--- | :--- | :---: | :--- |
| AFM-576 | AFM-905 | Blocks | UNRESOLVED | No | Intra-project dependency |
| AFM-576 | AFM-961 | Blocks | UNRESOLVED | No | Intra-project dependency |
| AFM-576 | AFM-969 | Blocks | UNRESOLVED | No | Intra-project dependency |
| AFM-999 | AFM-976 | Blocks | UNRESOLVED | No | Intra-project dependency |
| GF-148 | GF-406 | Blocks | UNRESOLVED | No | Intra-project dependency |
| GF-405 | GF-406 | Blocks | Resolved | No | Intra-project dependency |
| GF-306 | GF-382 | Blocks | UNRESOLVED | No | Intra-project dependency |
| GF-514 | GF-382 | Blocks | UNRESOLVED | No | Intra-project dependency |
| GF-523 | GF-382 | Blocks | UNRESOLVED | No | Intra-project dependency |
| GF-382 | GF-413 | Blocks | UNRESOLVED | No | Intra-project dependency |
| GF-393 | GF-474 | Blocks | UNRESOLVED | No | Intra-project dependency |
| GF-447 | GF-474 | Blocks | Resolved | No | Intra-project dependency |
| GF-488 | GF-474 | Blocks | UNRESOLVED | No | Intra-project dependency |
| GF-507 | GF-474 | Blocks | UNRESOLVED | No | Intra-project dependency |
| GF-517 | GF-474 | Blocks | UNRESOLVED | No | Intra-project dependency |

## 7. Limitations & Recommendations
- ⚠️ Authoritative employee leave, holiday calendars, and part-time schedules are UNKNOWN; nominal 6.5h/day baseline applied.

---
*Advisory Report only: Zero Jira mutations or schedule modifications executed.*