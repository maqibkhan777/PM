# Completion Report: Multi-Project Historical Effort Benchmarking (Milestone 2)

- **Date**: 2026-10-01
- **Branch**: `AI`
- **Projects Evaluated**: Post SMTP Support (`SMTPSUPORT`), Gutena Forms (`GF`)
- **Status**: **COMPLETE & READY FOR REVIEW**

---

## 1. Project Validation & Live Probe Diagnostic Findings

Both `SMTPSUPORT` and `GF` were validated against live Jira data via read-only diagnostic probes (`reports/smtpsuport_quality_probe.md` and `reports/gutena_forms_quality_probe.md`).

### Project Summaries (90-Day Lookback Window, Cap = 200)

| Metric / Dimension | Post SMTP Support (`SMTPSUPORT`) | Gutena Forms (`GF`) |
| :--- | :--- | :--- |
| **Probe Run ID** | `probe-9ceeed9e` | `probe-ef97f598` |
| **Total Completed Issues** | 73 (Complete set in window) | 123 (Complete set in window) |
| **Issues with Logged Effort** | 60 (82.2%) | 96 (78.0%) |
| **Issues Missing Effort** | 13 (17.8%) | 27 (22.0%) |
| **Explicit Zero Effort vs Missing** | Handled explicitly | Handled explicitly |
| **Total Logged Hours** | 181.48h | 375.15h |
| **Avg / Median Logged Effort** | Avg: 3.0h \| Median: 1.2h | Avg: 3.9h \| Median: 2.7h |
| **Avg / Median Elapsed Lifecycle** | Avg: 4385.5h \| Median: 3379.3h | Avg: 1272.3h \| Median: 449.6h |
| **Original Estimate Coverage (>0)**| 0 / 73 (0.0%) | 3 / 123 (2.4%) |
| **Assignee Populated** | 61 / 73 (83.6%) | 66 / 123 (53.7%) |
| **Issue Type Populated** | 73 / 73 (100.0%) | 123 / 123 (100.0%) |
| **Priority Populated** | 73 / 73 (100.0%) | 123 / 123 (100.0%) |
| **Labels Populated** | 44 / 73 (60.3%) | 5 / 123 (4.1%) |
| **Components Populated** | 0 / 73 (0.0%) | 0 / 123 (0.0%) |
| **Parent / Epic Linked** | 0 / 73 (0.0%) | 0 / 123 (0.0%) |
| **Changelog Available** | 73 / 73 (100.0%) | 123 / 123 (100.0%) |
| **Changelog Estimate Changes** | 60 / 73 (82.2%) | 96 / 123 (78.0%) |
| **Changelog Reopens** | 9 / 73 (12.3%) | 2 / 123 (1.6%) |
| **API Errors / Failures** | None (0 errors) | None (0 errors) |
| **Feasibility Recommendation** | **READY** | **READY** |

### Key Diagnostic Takeaways
1. **Separation of Lifecycle Lead Time vs. Logged Effort**: Wall-clock calendar duration (averaging 4,385.5h for SMTPSUPORT and 1,272.3h for GF) includes backlog and queue wait time and is strictly decoupled from developer effort.
2. **Original Estimates Unusable**: Original estimates in Jira are nearly absent in both projects (0.0% and 2.4%), confirming that planning and estimation must rely on empirical historical actuals.
3. **High Worklog Quality**: 82.2% of SMTPSUPORT issues and 78.0% of GF issues contain granular worklog records.

---

## 2. Benchmark Implementation Status (Milestone 2)

Milestone 2 is **fully implemented** in [app/core/intelligence/effort_benchmarking.py](file:///d:/PM/app/core/intelligence/effort_benchmarking.py).

### Core Features Implemented
- **Quantile & Statistical Metrics**: Calculates P25, P50 (median), P75, P90, Mean, Min, Max, and Standard Deviation for all segments.
- **Reliability Thresholds**:
  - `USABLE` ($n \ge 10$): Full quantiles (P25, P50, P75, P90) computed and enabled for planning.
  - `LOW_CONFIDENCE` ($5 \le n \le 9$): Median and mean reported with caution warning; P90 suppressed.
  - `INSUFFICIENT_DATA` ($n < 5$): Flagged; advanced quartiles withheld to prevent manufactured precision.
- **Groupings Supported**:
  1. `overall`: Overall project distribution.
  2. `issue_type`: Segmentation by issue type (e.g. `Support`, `Task`, `Subtask`, `Bug`).
  3. `priority`: Segmentation by priority (e.g. `Medium`, `Highest`).
  4. `issue_type_priority`: Compound segment (e.g. `Support:Medium`, `Task:Medium`).
  5. `label`: Label-level empirical distributions.
- **Strict Cross-Project Isolation**: Data ingestion and statistical analysis run strictly per project. No pooling or cross-contamination across projects.
- **Duplicate-Ingestion Protection & Cursor Pagination**: JQL pagination handles Jira token cursors and prevents duplicate issues or over-fetching beyond caps.
- **Persistence Schema**: Benchmark records persist into the local SQLite table `historical_effort_benchmarks` keyed by `analysis_run_id` and `segment_key`.

---

## 3. Actual Empirical Benchmark Results

Extracted from live run artifact `reports/historical_effort_benchmarks.md` (Run ID: `bench_run_9de8124a17ed`, Generated: `2026-09-30T07:27:03.577201+00:00`):

### Post SMTP Support (`SMTPSUPORT`)
- **Total Completed Issues**: 73 | **With Logged Effort**: 60 (82.2%) | **Missing Effort**: 13 (17.8%) | **Total Logged Hours**: 181.49h
- **Overall Project Distribution**:
  - Sample Size: $n = 60$ (`USABLE`)
  - **P50 (Median)**: `1.25h` | **Mean**: `3.02h` | **P25**: `0.50h` | **P75**: `3.14h` | **P90**: `6.37h`
  - Dispersion: Min = `0.17h` | Max = `29.00h` | StdDev = `5.32h`
- **Key Segment Benchmarks**:
  - `issue_type:Support` ($n=40$, `USABLE`): P50 = 1.00h, Mean = 1.43h, P25 = 0.50h, P75 = 1.73h, P90 = 3.11h
  - `issue_type:Task` ($n=13$, `USABLE`): P50 = 4.17h, Mean = 8.38h, P25 = 2.00h, P75 = 7.98h, P90 = 21.94h
  - `issue_type:Subtask` ($n=6$, `LOW_CONFIDENCE`): P50 = 1.16h, Mean = 2.51h
  - `priority:Medium` ($n=58$, `USABLE`): P50 = 1.24h, Mean = 3.03h, P25 = 0.50h, P75 = 3.06h, P90 = 6.46h

### Gutena Forms (`GF`)
- **Total Completed Issues**: 123 | **With Logged Effort**: 96 (78.0%) | **Missing Effort**: 27 (22.0%) | **Total Logged Hours**: 375.18h
- **Overall Project Distribution**:
  - Sample Size: $n = 96$ (`USABLE`)
  - **P50 (Median)**: `2.67h` | **Mean**: `3.91h` | **P25**: `0.68h` | **P75**: `5.07h` | **P90**: `9.34h`
  - Dispersion: Min = `0.02h` | Max = `21.58h` | StdDev = `4.59h`
- **Key Segment Benchmarks**:
  - `issue_type:Subtask` ($n=60$, `USABLE`): P50 = 1.81h, Mean = 2.67h, P25 = 0.54h, P75 = 3.46h, P90 = 6.44h
  - `issue_type:Task` ($n=30$, `USABLE`): P50 = 4.46h, Mean = 5.38h, P25 = 1.90h, P75 = 7.33h, P90 = 10.30h
  - `issue_type:Story` ($n=3$, `INSUFFICIENT_DATA`): Sample below threshold ($n=3$)
  - `priority:Medium` ($n=94$, `USABLE`): P50 = 2.67h, Mean = 3.94h, P25 = 0.67h, P75 = 5.16h, P90 = 9.47h

---

## 4. Tests and Verified Outcomes

All intelligence unit tests were executed and passed cleanly:

- **Command Run**: `pytest tests/unit/intelligence/`
- **Results**: `11 passed in 0.75s` (100% pass rate)

### Test Coverage Summary
- [test_effort_benchmarking.py](file:///d:/PM/tests/unit/intelligence/test_effort_benchmarking.py):
  1. `test_calculate_quantiles_and_stats_thresholds`: Verifies quantile calculations and threshold suppression ($n<5$, $5\le n\le 9$, $n\ge 10$).
  2. `test_multi_project_effort_benchmarking`: Verifies project isolation between SMTPSUPORT and GF, missing effort calculations, and database persistence.
  3. `test_effort_benchmark_formatter`: Verifies console and Markdown report rendering.
- [test_jira_historical_probe.py](file:///d:/PM/tests/unit/intelligence/test_jira_historical_probe.py):
  1. `test_probe_single_project_success`
  2. `test_probe_multi_project_separation`
  3. `test_probe_pagination_and_cap_handling`
  4. `test_probe_missing_and_zero_fields_distinction`
  5. `test_probe_changelog_transitions_and_reopens`
  6. `test_probe_lifecycle_vs_effort_separation`
  7. `test_probe_partial_failure_handling`
  8. `test_probe_formatter_output`

---

## 5. Files Changed and Database Architecture

### Intelligence & Benchmark Modules
- [app/core/intelligence/effort_benchmarking.py](file:///d:/PM/app/core/intelligence/effort_benchmarking.py): Empirical historical effort benchmarking engine.
- [app/core/intelligence/effort_benchmark_models.py](file:///d:/PM/app/core/intelligence/effort_benchmark_models.py): Pydantic data schemas and enum definitions (`BenchmarkReliability`, quantiles).
- [app/core/intelligence/effort_benchmark_formatter.py](file:///d:/PM/app/core/intelligence/effort_benchmark_formatter.py): Markdown and terminal formatters.
- [app/core/intelligence/probe.py](file:///d:/PM/app/core/intelligence/probe.py): Read-only diagnostic probe engine.
- [app/core/intelligence/probe_models.py](file:///d:/PM/app/core/intelligence/probe_models.py): Diagnostic probe metrics models.
- [app/core/intelligence/probe_formatter.py](file:///d:/PM/app/core/intelligence/probe_formatter.py): Diagnostic probe report formatters.

### CLI Entrypoints
- [scripts/generate_effort_benchmarks.py](file:///d:/PM/scripts/generate_effort_benchmarks.py): CLI tool for running multi-project benchmark calculation and export.
- [scripts/probe_jira_historical_data.py](file:///d:/PM/scripts/probe_jira_historical_data.py): CLI tool for running read-only diagnostic probes.

### Database Tables & Schema
- Target table: `historical_effort_benchmarks` in local SQLite (`app/database/schema.py`).
- Fields: `id`, `analysis_run_id`, `account_id`, `segmentation_tier`, `segment_type`, `segment_key`, `sample_count`, `mean_hours`, `median_hours`, `p25_hours`, `p75_hours`, `min_hours`, `max_hours`, `stddev_hours`, `confidence`, `is_fallback`, `created_at`.
- No migration required: Table already exists in schema.

---

## 6. Execution Commands

### Diagnostic Probe
```bash
# Single project
python scripts/probe_jira_historical_data.py --projects SMTPSUPORT --days 90 --cap 200 --output-file reports/smtpsuport_quality_probe.md

# Gutena Forms
python scripts/probe_jira_historical_data.py --projects GF --days 90 --cap 200 --output-file reports/gutena_forms_quality_probe.md

# Multi-project
python scripts/probe_jira_historical_data.py --projects SMTPSUPORT,GF --days 90 --cap 200
```

### Empirical Historical Effort Benchmarks
```bash
# Multi-project benchmark run with Markdown report export
python scripts/generate_effort_benchmarks.py --projects SMTPSUPORT,GF --days 90 --cap 200 --output-file reports/historical_effort_benchmarks.md
```

### Unit Tests
```bash
pytest tests/unit/intelligence/
```

---

## 7. Git & Repository Status

- **Current Branch**: `AI`
- **Head Commit**: `d2e7caa feat: add Jira poller, DeepSeek AI provider, planning components, database repositories, and tests`
- **Working Tree**: Clean with respect to core functionality; untracked intelligence modules and reports present. No commits or mutations performed.

---

## 8. Missing Evidence or Limitations

- **Sparse Sub-Segments**: Epics and Stories in `GF` and `SMTPSUPORT` have sample counts below 5 ($n < 5$), correctly classified as `INSUFFICIENT_DATA`.
- **Labels Sparsity**: Labels are present in 60.3% of SMTPSUPORT issues but only 4.1% of GF issues.
- **Original Estimates**: 0.0% coverage in SMTPSUPORT and 2.4% in GF.

---

## 9. Recommendation

**Milestone 2 is complete, verified by test suite and live project artifacts, and READY FOR REVIEW.** No further probe or rerun is required.
