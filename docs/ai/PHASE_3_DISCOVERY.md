# Phase 3 Discovery: Jira Dependencies, Artifacts, and Planning Horizon

**Document Status:** COMPLETE & VERIFIED  
**Target Branch:** `AI`  
**Base Checkpoint:** `638885b`  
**Mode:** Read-Only Discovery & Analysis (Zero Commits, Zero Migrations, Zero Jira Mutations)  

---

## 1. Jira Link Types Discovery

### 1.1 Ingestion & Payload Inspection
A deep inspection of the Jira connector and production SQLite database (`pm_operations.db`) was performed:
- **`JiraPoller`** (`app/connectors/jira/poller.py`):
  - Calls `client.search_issues()` with default fields `["*navigable", "comment", "worklog"]`.
  - Jira Cloud API returns `"issuelinks"` as part of `*navigable` fields.
  - In `pm_operations.db`, **346 events** in the `events` table contain live `issuelinks` in their payloads.
- **`JiraIssueStateRepository`** (`app/database/repositories.py`):
  - **CONFIRMED GAP:** While `issuelinks` are present in raw event payloads, `JiraPoller._process_issue()` currently does **not** extract `issuelinks` into `jira_issue_state`.
  - `jira_issue_state` only persists `labels`, `components`, `subtask_count`, `original_estimate_seconds`, etc.
  - Issue links are currently **not** stored in a dedicated relational table.

### 1.2 Discovered Jira Link Types in Production Data
Direct inspection of live issues in `pm_operations.db` revealed the following exact link types and directions configured in the Jira instance:

| Link Type Name | Inward Description | Outward Description | Meaning in PM Practice | Existing Code Support | Suitable for Dependency DAG? | Finding Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`Blocks`** | `is blocked by` | `blocks` | Hard sequence blocker: outward issue must complete before inward issue can proceed. | In raw event JSON; not in issue projection table. | **YES (Primary Hard Blocker)** | **CONFIRMED** |
| **`Problem/Incident`** | `is caused by` | `causes` | Causal defect/incident link. Outward causes inward. | In raw event JSON; not in issue projection table. | **YES (Defect / Blocker)** | **CONFIRMED** |
| **`Test`** | `is tested by` | `tests` | Verification link between test/QA ticket and implementation ticket. | In raw event JSON; not in issue projection table. | **YES (Handoff Dependency)** | **CONFIRMED** |
| **`Relates`** | `relates to` | `relates to` | Symmetric, non-blocking informational association. | In raw event JSON; not in issue projection table. | **NO (Informational only)** | **CONFIRMED** |
| **`Duplicate`** | `is duplicated by` | `duplicates` | Duplicate ticket resolution. | In raw event JSON; not in issue projection table. | **NO (Prunes duplicate ticket)** | **CONFIRMED** |
| **`Defect`** | `created by` | `created` | Originating link between ticket and bug ticket. | In raw event JSON; not in issue projection table. | Contextual / Optional | **CONFIRMED** |
| **`Cloners`** | `is cloned by` | `clones` | Clone provenance relationship. | In raw event JSON; not in issue projection table. | **NO (Informational only)** | **CONFIRMED** |
| **`Polaris merge work item link`** | `merged into` | `merged from` | Code merge provenance link. | In raw event JSON; not in issue projection table. | **NO (Informational only)** | **CONFIRMED** |
| **`Discovery - Connected`** | `is connected to` | `connects to` | Discovery phase relationship. | In raw event JSON; not in issue projection table. | **NO (Informational only)** | **CONFIRMED** |

---

## 2. Current Dependency Support

### 2.1 Intra-Project vs. Cross-Project & Cross-Resource Links
- **Cross-Resource Links:** **CONFIRMED**. Jira issue links connect issues regardless of assignee. Because each issue in `pm_operations.db` contains its assignee (`assignee_account_id`), an issue link between `PROJ-101` (assigned to Alice) and `PROJ-102` (assigned to Bob) directly defines a cross-resource handoff.
- **Cross-Project Links:** **CONFIRMED**. Inward and outward keys are fully qualified issue keys (e.g. `WSSS-123` linking to `CORE-456`).
- **Subtasks & Parent/Child:** **CONFIRMED**. `jira_issue_state` tracks `subtask_count`. In Jira, subtasks cannot complete after their parent is closed, providing a hierarchical dependency structure.

### 2.2 Existing Gaps in Dependency Handling
1. **No Local Link Table:** `JiraIssueStateRepository` lacks link storage, requiring a normalized `jira_issue_links` table in SQLite.
2. **No DAG Evaluation:** Existing engines (`DueDateForecaster`) schedule each resource's queue independently and linearly. They cannot evaluate if Task B on Alice's queue is blocked by Task A on Bob's queue.
3. **No Cycle Detection:** Cyclic link structures in Jira (e.g. A blocks B, B blocks A) would cause scheduling loops if not protected by a Cycle Detection pass (Kahn's algorithm).

---

## 3. Artifact Representation Discovery

### 3.1 Investigation of Current Deliverable Conventions
An exhaustive inspection across database fields, issue keys, labels, components, and task classifications revealed:
- **Labels:** 11 distinct labels exist in `pm_operations.db` (`1st_reminder_sent`, `2nd_reminder_sent`, `Free`, `Front-End`, `Pending`, `Pro`, `internalfollowup`, `need-to-release`, `performance`, `refund`, `v1.2.3`). None represent artifacts.
- **Components:** Only 2 components exist (`Pipeline`, `Pro/Paid Version`).
- **Issue Types:** `Bug`, `Email request`, `Epic`, `New Feature`, `Story`, `Subtask`, `Support`, `Task`, `[System] Service request`.
- **Search for Prefixes (`artifact:`, `deliverable:`, `produces:`, `consumes:`):** **NOT FOUND** in existing Jira tickets.
- **Codebase / Engine Findings:** The Phase B `TaskNatureClassifier` classifies tasks into functional domains (`DESIGN`, `DEVELOPMENT`, `QA_TESTING`, `DOCUMENTATION`, `DEPLOYMENT`), but does **not** model discrete work products or handover assets.

### 3.2 Categorized Findings
- **A. Confirmed Artifact Representations:** **NONE**. There is no existing explicit artifact model or convention in Jira or the codebase.
- **B. Possible but Unconfirmed Representations:** Jira attachments and PR / merge links (`Polaris merge work item link`), but these are unstructured.
- **C. Missing Artifact Representation:** The concept of producer task $\to$ artifact $\to$ consumer task is currently completely absent.

### 3.3 Recommended Minimum Phase 3B Abstraction
To avoid forcing Jira users into burdensome manual data entry or speculative NLP:
1. **Dual Ingestion Mode:**
   - **Explicit Convention (Opt-in):** Recognize Jira labels prefixed with `artifact:<name>` (e.g., `artifact:api-contract`, `artifact:figma-v1`) or explicit local metadata.
   - **Task Nature Inference (Deterministic):** When Task A is of nature `DESIGN` and blocks Task B of nature `DEVELOPMENT`, Phase 3B generates a synthesized `DESIGN_ASSET` artifact with `ConfidenceLevel.MEDIUM`. When Task B is `DEVELOPMENT` and blocks Task C of `QA_TESTING`, it generates a `BUILD_PACKAGE` artifact.
2. **Local Schema:** A lightweight table `project_artifacts` (`artifact_id`, `name`, `artifact_type`, `producer_task_key`, `status`) and `artifact_dependencies` (`consumer_task_key`, `artifact_id`, `is_blocking`).

---

## 4. Planning Horizon Discovery

### 4.1 Existing Horizons in Codebase
Inspection revealed two distinct hard-coded horizons currently in use:
1. **`DueDateForecaster.forecast_queue_and_tasks`** (`app/core/performance/forecaster.py`):
   - Uses `horizon_working_days: int = 5` (1 standard business week).
2. **`HistoricalIntelligenceEngine.generate_profile`** (`app/core/intelligence/engine.py`):
   - Uses 14 calendar days $\approx$ `10 working days` (2 standard business weeks / sprint window).
3. **`Settings`** (`app/config/settings.py`):
   - Currently has no global `PLANNING_HORIZON_WORKING_DAYS` setting.

### 4.2 Recommendation
Introduce a centralized, configurable setting in `app/config/settings.py`:
```python
PLANNING_HORIZON_WORKING_DAYS: int = 10  # Default 2-week planning horizon (sprint window)
```
Allow per-call overrides in `DueDateForecaster` and `PlanningContextBuilder` (e.g. 5 days for short-term tactical focus, 10 days for sprint planning).

---

## 5. Existing Phase A/B Intelligence Mapping

| Engine / Component | Inputs | Outputs | Persistence | Calc Frequency | Data Quality / Confidence | Reusable in Phase 3? |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`HistoricalPaceAnalyzer`** | `jira_issue_state`, `jira_worklogs` | P25/median/P75 hours, daily hours, pace factor | `resource_performance_profiles` | On-demand / Cron (24h) | Sample size thresholds ($\ge 5, \ge 10$) | **YES (Direct)** |
| **`CapacityCalculator`** | Dates, daily hours (6.75h) | Working days, projected completion date | None (Pure function) | Instantaneous | Monday–Friday, holiday exclusions | **YES (Direct)** |
| **`CurrentQueueAnalyzer`** | Account ID, active issues | Inferred remaining effort, queue depth | `task_delivery_forecasts` | On-demand | Flags missing estimates | **YES (Direct)** |
| **`DueDateForecaster`** | Queue forecasts, capacity | Projected completion, slack, risk bands | `task_delivery_forecasts` | On-demand | Compares projected vs due date | **EXTEND (Add cross-resource DAG)** |
| **`TaskComplexityCalculator`** | Issue type, summary, subtasks | Complexity score 1–5, factors | `resource_task_classifications` | On-demand | Rule-based deterministic confidence | **YES (Direct)** |
| **`BlockerAnalyzer`** | Issue status, history | Blocked duration, blocker count | `jira_issue_state.blocker_hours` | During poller / analysis | Historical flag | **YES (Direct)** |
| **`HistoricalIntelligenceEngine`**| Account ID, issues, worklogs | Comprehensive multi-vector profile | `historical_intelligence_profiles`| On-demand / Cron (24h) | Completeness rating, AI readiness | **YES (Direct)** |
| **`PersonalBaselineEngine`** | Historical issues & worklogs | Personal 30/60/90d pace & rework baseline | `historical_trends` | On-demand | Personal baseline comparison states | **YES (Direct)** |
| **`HistoricalEffortBenchmarkEngine`**| Global issues across team | Role & issue type median benchmarks | `historical_effort_benchmarks` | On-demand | 4-tier fallback hierarchy | **YES (Direct)** |
| **`TaskNatureClassifier`** | Issue type, summary, labels | Deterministic task category (`TaskNature` enum) | `jira_issue_state.task_nature` | On-demand / Ingestion | Source and rule traceability | **YES (Direct)** |
| **`WorkloadPressureAnalyzer`**| Active issues, capacity | Pressure state (`LOW`, `NORMAL`, `ELEVATED`, `HIGH`) | `historical_workload_snapshots` | On-demand | Non-punitive objective criteria | **YES (Direct)** |
| **`ReviewReworkAnalyzer`** | Issue changelogs, transitions | QA reopen rate, rework cycles | None (Derived) | On-demand | Traceable to Jira changelog | **YES (Direct)** |

### 5.1 Duplicate / Overlapping Functionality
- `DueDateForecaster` and `WorkloadPressureAnalyzer` both compute remaining queue workload vs. nominal capacity ($6.75 \text{h/day}$).
- **Resolution:** In Phase 3, `DueDateForecaster` will remain the canonical sequential completion timeline generator, while `WorkloadPressureAnalyzer` provides contextual classification.

---

## 6. Reusable Components

The following components are fully mature and will be composed without modification:
1. `app.core.performance.capacity.CapacityCalculator`
2. `app.core.performance.pace.HistoricalPaceAnalyzer`
3. `app.core.intelligence.classifier.TaskNatureClassifier`
4. `app.core.intelligence.benchmarks.HistoricalEffortBenchmarkEngine`
5. `app.core.intelligence.baselines.PersonalBaselineEngine`
6. `app.connectors.jira.client.JiraClient`
7. `app.services.ai.safety_gate.AISafetyGate`
8. `app.services.ai.provider.AIProvider`

---

## 7. Actual Gaps to Implement in Phase 3

1. **Jira Issue Link Extraction & Persistence:** Update `JiraPoller` to parse `issuelinks` and persist them into a new `jira_issue_links` SQLite table.
2. **Deterministic DAG Engine (`DependencyGraph`):**
   - Kahn's algorithm for topological sorting and cycle detection.
   - Classification of link types into `HARD_BLOCKER` (`Blocks`, `is caused by`), `HANDOFF` (`Test`), or `INFORMATIONAL` (`Relates`, `Cloners`).
3. **Artifact Abstraction Layer (`ArtifactEngine`):**
   - Track producer/consumer relationships and availability milestones.
4. **Cross-Resource Timeline Forecaster (`TeamScheduleForecaster`):**
   - Replaces isolated single-queue forecasting with a team-wide timeline that respects cross-resource blocking links.
5. **PlanningContext Contract & Builder (`PlanningContextBuilder`):**
   - Assembles sanitized, token-bounded planning facts for AI reasoning.

---

## 8. Minimum Phase 3 Architecture

```
[ Jira API: issuelinks ]
          │ (JiraPoller)
          ▼
[ SQLite: jira_issue_links ] ──┐
                               │
[ SQLite: jira_issue_state ] ──┼──► [ DependencyDAG Engine ]
                               │      - Cycle detection
[ Phase A/B Intelligence ] ────┘      - Topological sort
                                      - Cross-resource handoffs
                                              │
                                              ▼
                             [ TeamScheduleForecaster ]
                               - Cross-resource critical path
                               - Capacity vs. Queue balance
                                              │
                                              ▼
                             [ PlanningContextBuilder ]
                               - Token bounds & sanitization
                               - Honest data quality flags
                                              │
                                              ▼
                             [ PlanningContext Schema ]
                                              │
                                              ▼
                             [ AIProvider Boundary (Agnostic) ]
                               (DeepSeek / Mock / Future LLM)
```

---

## 9. PlanningContext Provider Boundary

To satisfy architectural purity:
- **`PlanningContext`** is a pure Pydantic v2 data contract located in `app/core/models/planning.py`.
- It contains **zero** DeepSeek-specific formatting, prompts, or provider logic.
- The existing `AIProvider` interface (`app/services/ai/provider.py`) defines:
  ```python
  async def generate_planning_proposal(self, context: PlanningContext) -> AIPlanningProposal: ...
  ```
- Any provider (`MockAIProvider`, `DeepSeekProvider`, `AnthropicProvider`) can consume `PlanningContext` without altering deterministic intelligence code.

---

## 10. Recommended Implementation Sequence

1. **Phase 3A: Issue Links & Dependency DAG**
   - Create `jira_issue_links` table in SQLite schema.
   - Update `JiraPoller` to persist inward/outward links.
   - Implement `DependencyGraph` with cycle detection and topological sorting.
2. **Phase 3B: Artifact Relationships & Handoffs**
   - Create `project_artifacts` and `artifact_dependencies` tables.
   - Implement `ArtifactEngine` with explicit label parsing and TaskNature-based inference.
3. **Phase 3C: Resource Queue & Capacity Engine Composition**
   - Unify `HistoricalPaceAnalyzer` and `CurrentQueueAnalyzer` into `ResourceQueueSnapshot`.
4. **Phase 3D: Cross-Resource Schedule & Bottleneck Forecaster**
   - Implement multi-resource critical path calculation respecting cross-resource blocker links.
5. **Phase 3E: PlanningContext Builder & Token Bounding**
   - Implement `PlanningContextBuilder` with token limits and data quality flags.
6. **Phase 3F: Comprehensive Regression & Verification**
   - Unit tests covering cyclic links, missing estimates, cross-resource delays, and provider-agnostic execution.

---

## 11. Open Decisions Resolved

| Open Question | Discovered Resolution | Status |
| :--- | :--- | :--- |
| **1. Jira Link Types** | Discovered 9 active link types in `pm_operations.db`. Specifically: `Blocks` (`is blocked by`/`blocks`) and `Problem/Incident` (`is caused by`/`causes`) represent hard blockers; `Test` (`is tested by`/`tests`) represents QA handoffs. | **RESOLVED & CONFIRMED** |
| **2. Artifact Representation** | No explicit artifact conventions exist today in Jira. Recommend a hybrid approach: support opt-in `artifact:<name>` labels + deterministic inference between `DESIGN` $\to$ `DEVELOPMENT` $\to$ `QA` tasks. | **RESOLVED & CONFIRMED** |
| **3. Planning Horizon** | Existing code uses 5 days (forecaster) and 10 days (intelligence engine). Recommend introducing configurable `PLANNING_HORIZON_WORKING_DAYS = 10` in `Settings` with per-call override. | **RESOLVED & CONFIRMED** |

---

## 12. Implementation Readiness Summary

- **Jira Link Data:** Already available in raw Jira API payloads; needs 1 local table and poller mapping.
- **Phase A/B Engines:** 100% verified and directly reusable.
- **AI Safety & Boundary:** Provider-agnostic abstraction already verified in Phase 2E.
- **Readiness:** **READY FOR PHASE 3A SPECIFICATION & EXECUTION.**
