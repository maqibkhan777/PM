# Milestone 3 Completion Report: Historical Effort Benchmark Integration into AI Planning

**Date:** October 1, 2026  
**Branch:** `AI`  
**Status:** Completed & Validated  
**Safety Status:** Advisory-Only, 0 Jira Mutations, 0 Production DB Writes  

---

## 1. Executive Summary
Milestone 3 successfully integrates empirical historical effort benchmarks (computed in Milestone 2) into the PM Operations Agent AI Planning architecture. AI planning proposals now ground task duration estimates in project-specific historical distributions (quantiles P50 and P75/P90), while enforcing strict project isolation, within-project fallback disclosure, deterministic safety gating, and concise Discord summary formatting.

---

## 2. Files Changed & Architecture

### A. New Modules Created
1. [`app/core/intelligence/effort_recommendation_models.py`](file:///d:/PM/app/core/intelligence/effort_recommendation_models.py):
   - Defines `BenchmarkRecommendationStatus` (`USABLE`, `LOW_CONFIDENCE`, `INSUFFICIENT_DATA`, `NOT_FOUND`).
   - Defines `TaskEffortBenchmarkRecommendation` data model with quantile values, tier grouping information, fallback disclosure, sample count, and lifecycle duration distinction notes.
2. [`app/core/intelligence/effort_retrieval_service.py`](file:///d:/PM/app/core/intelligence/effort_retrieval_service.py):
   - Implements `HistoricalEffortBenchmarkRetrievalService`.
   - Queries `historical_effort_benchmarks` table in the local/dev database.
   - Enforces strict project isolation (e.g. `SMTPSUPORT` never reads or falls back to `GF`).
   - Implements hierarchical within-project fallback: Compound (`issue_type:priority`) -> Issue Type (`issue_type`) -> Overall (`all`).
   - Provides batch context enrichment via `recommend_effort_for_context`.
3. [`tests/unit/intelligence/test_effort_retrieval_service.py`](file:///d:/PM/tests/unit/intelligence/test_effort_retrieval_service.py):
   - 7 unit tests verifying project isolation, fallback tiers, sparse data handling, and lifecycle notes.
4. [`tests/unit/planning/test_effort_planning_integration.py`](file:///d:/PM/tests/unit/planning/test_effort_planning_integration.py):
   - 2 integration tests verifying end-to-end planning proposal enrichment and Discord router integration.

### B. Modified Services & Integration Points
1. [`app/core/intelligence/__init__.py`](file:///d:/PM/app/core/intelligence/__init__.py):
   - Exported `BenchmarkRecommendationStatus`, `TaskEffortBenchmarkRecommendation`, and `HistoricalEffortBenchmarkRetrievalService`.
2. [`app/services/ai/planning.py`](file:///d:/PM/app/services/ai/planning.py):
   - Initialized `HistoricalEffortBenchmarkRetrievalService`.
   - Post-processed generated proposals to attach `EvidenceType.RESOURCE_HISTORY` and `PlanningEstimate` derived from empirical quantiles.
3. [`app/services/ai/planning_prompt.py`](file:///d:/PM/app/services/ai/planning_prompt.py):
   - Updated `PlanningPromptBuilder.format_compact_context()` to pass empirical benchmark facts (`p50_hours`, `p90_hours`, sample count, reliability) directly to LLM context.
4. [`app/connectors/discord/ai_discord_router.py`](file:///d:/PM/app/connectors/discord/ai_discord_router.py):
   - Formatted concise user-facing evidence section displaying recommended P50/P90 effort, sample count ($n$), reliability status, and fallback/limitation notes without dumping raw tables.

---

## 3. Benchmark Retrieval & Selection Logic

### Retrieval & Isolation Rules
- **Strict Project Filtering:** Segment keys must start with `PROJECT_KEY:`. Cross-project queries return `NOT_FOUND` / zero records.
- **Deterministic Reliability:**
  - **`USABLE` ($n \ge 10$):** Recommended effort is P50 (median); P75/P90 quantile provided as upper bound.
  - **`LOW_CONFIDENCE` ($5 \le n \le 9$):** Labeled tentative with explicit data quality warning.
  - **`INSUFFICIENT_DATA` ($n < 5$):** Estimate value is `None`. Advisory estimate is withheld to prevent ungrounded predictions.
- **Lifecycle Distinction:** Logged developer effort is explicitly distinguished from elapsed wall-clock calendar lead time.

---

## 4. Example Proposal Outputs

### A. Post SMTP Support (`SMTPSUPORT`)
```markdown
📋 **AI Planning Proposal (Advisory Only — Scope: Post SMTP Support (SMTPSUPORT))**
**Anchor Date:** 2026-03-02 | **Horizon:** 10 working days
**Confidence:** 85%

**Summary:** Plan generated with empirical effort benchmarks grounded in historical support issue distributions.

**Proposed Tasks (2):**
• **SMTPSUPORT-101** (~2.5hours) [Due: 2026-03-06]
• **SMTPSUPORT-102** (~4.0hours) [Due: 2026-03-06]

📊 **Historical Effort Reference (Empirical Benchmarks):**
• **SMTPSUPORT-101** (Support/Medium): P50: 2.5h, P75/P90: 5.0h (n=28, Usable)
  ↳ *Note:* Compound segment 'Support:Medium' not observed. Used issue-type benchmark 'Support' (n=28) within project 'SMTPSUPORT'.

⚠️ *This proposal is for review only. It has not been approved or executed.*
```

### B. Gutena Forms (`GF`)
```markdown
📋 **AI Planning Proposal (Advisory Only — Scope: Gutena Forms (GF))**
**Anchor Date:** 2026-03-02 | **Horizon:** 10 working days
**Confidence:** 85%

**Summary:** Backlog schedule proposal grounded in Gutena Forms historical distributions.

**Proposed Tasks (1):**
• **GF-204** (~5.5hours) [Due: 2026-03-06]

📊 **Historical Effort Reference (Empirical Benchmarks):**
• **GF-204** (Feature/High): P50: 5.5h, P75/P90: 9.0h (n=22, Usable)
  ↳ *Note:* Specific groupings for 'Feature' have insufficient data in project 'GF'. Fell back to overall project benchmark (n=22) within project 'GF'.

⚠️ *This proposal is for review only. It has not been approved or executed.*
```

---

## 5. How Sparse or Missing Data is Handled
1. **Sample Size $< 5$:** Status is set to `INSUFFICIENT_DATA`. `recommended_effort_hours`, `p50_effort_hours`, and `p90_effort_hours` remain `None`. Zero manufactured estimates.
2. **Missing Grouping:** An explicit fallback to broader tiers within the same project is performed only when the broader tier has $\ge 5$ samples, accompanied by an explicit `fallback_disclosure` string.
3. **Missing Project Data:** If no benchmarks exist for the project, status is `NOT_FOUND` with message `"No historical effort benchmarks found for project '<KEY>'."`

---

## 6. Test Execution & Verification

### Test Runs Completed
- `pytest tests/unit/intelligence/ tests/unit/planning/`
  - **171 passed** (including all benchmark engine, retrieval service, and planning integration tests)
- `pytest tests/unit/ai/ tests/test_deepseek_provider.py`
  - **169 passed** (including planning approval, proposal validator, and safety gate tests)
- **Total Passing Tests:** 340 tests across the intelligence, planning, and AI suites.

---

## 7. Safety, Database, and Deployment Verification
- **Advisory-Only:** No Jira mutations occurred (no issue edits, comments, assignment updates, or estimate writes).
- **Database Safety:** Zero production database writes or schema modifications occurred. All tests used isolated SQLite test databases.
- **Branch Integrity:** Strict operation on branch `AI`. No commits, merges, pushes, or deployments were executed.
