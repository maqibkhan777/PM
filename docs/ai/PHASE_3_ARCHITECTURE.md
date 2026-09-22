# PHASE 3 — DETERMINISTIC RESOURCE INTELLIGENCE & PLANNING
## Architectural Design & Repository Integration Plan

**Document Version:** 1.0.0  
**Status:** DESIGN ONLY — APPROVED FOR SPECIFICATION  
**Target Branch:** `AI`  
**Base Checkpoint:** `638885b`  
**Execution Mode:** Read-Only Analysis & Design (Zero Commits, Zero Migrations, Zero Jira Mutations)  

---

## 1. Executive Summary

Phase 2E demonstrated that DeepSeek V3 can reliably act as a bounded, fail-closed reasoning layer when evaluated against strict schema, grounding, and safety contracts (8/8 live scenarios passed, 595 regression tests passing, zero live retries).

However, a fundamental rule of this platform is:
> **The LLM must NEVER become the source of PM facts.**  
> The deterministic system calculates facts from Jira ground truth. DeepSeek reasons over those bounded facts.

**Phase 3** designs the deterministic intelligence and planning layer that provides verified project management facts to the AI planning layer. Phase 3 equips the system to deterministically understand:
1. Historical pace, delivery duration, and estimation variance per resource.
2. Active queue depth, priority distribution, age, and blocker status.
3. Realistic working capacity (derived from business days, empirical workload, and configured schedules).
4. Task complexity and duration distributions derived statistically from historical evidence.
5. Work artifacts (deliverables produced and consumed across tasks).
6. Cross-task and cross-resource dependency graphs (blocking, artifact, and sequence relationships).
7. Unified team-level constraints, bottlenecks, and handoff sequencing.
8. Bounded planning contexts tailored specifically for AI reasoning.

Crucially, **Phase 3 does not reinvent what already exists**. In-depth repository inspection revealed that the codebase already possesses sophisticated Phase A (`app/core/performance`) and Phase B (`app/core/intelligence`) deterministic engines, models, and database tables. Phase 3 composes and extends this existing infrastructure, adding the missing pieces: **Artifacts, Dependency DAGs, Team-Wide Sequence Projections, and AI Planning Contracts**.

---

## 2. Current Repository Capabilities Discovered

A comprehensive inspection of the `PM` repository revealed that substantial analytical foundations are already operational in SQLite and Python:

### 2.1 Database Schema & Projections (`app/database/schema.py`)
- **`jira_issue_state`**: Canonical local projection cache of Jira issues with columns: `jira_issue_key`, `summary`, `status`, `assignee`, `assignee_account_id`, `priority`, `due_date`, `created_at`, `updated_at`, `resolved_at`, `last_activity_at`, `project_key`, `issue_type`, `original_estimate_seconds`, `time_spent_seconds`, `subtask_count`, `labels` (JSON), `components` (JSON), `team_group`, and `raw_reference` (JSON payload from Jira).
- **`jira_worklogs`**: Detailed worklog records containing `worklog_id`, `jira_issue_key`, `author_account_id`, `author_display_name`, `time_spent_seconds`, `started_at`, and `comment`.
- **`employee_role_assignments`**: Authoritative role mapping storing `account_id`, `display_name`, `designation`, `role_category` (e.g., `WordPress Development`, `Frontend Development`, `QA`, `Design`, `SEO`), and `jira_queue_filter_id`.
- **Phase A Tables**: `resource_performance_profiles`, `resource_effort_statistics`, `resource_task_classifications`, `task_delivery_forecasts`, `performance_signals`, `performance_evidence`.
- **Phase B Tables**: `historical_intelligence_profiles`, `historical_task_mix`, `historical_effort_benchmarks`, `historical_trends`, `historical_workload_snapshots`, `historical_delivery_context`, `historical_evidence`.

### 2.2 Phase A Engine (`app/core/performance/`)
- **`HistoricalPaceAnalyzer`**: Computes historical velocity, daily hours, task duration statistics (P25, median, mean, P75), on-time delivery rates, and rolling window trends (7d, 14d, 30d).
- **`CapacityCalculator`**: Computes working days (Monday–Friday excluding configured holidays) with nominal daily capacity (default 6.75 hours/day). Projects completion dates sequentially from cumulative queue hours.
- **`CurrentQueueAnalyzer`**: Inspects open tasks for a resource, evaluates logged vs remaining hours, detects missing estimates, and assigns fallback duration estimates.
- **`DueDateForecaster`**: Sequentially walks a resource's active queue against working day capacity, computing projected completion dates, slack hours against due dates, and risk levels (`GREEN`, `YELLOW`, `ORANGE`, `RED`).
- **`TaskComplexityCalculator`**: Evaluates task complexity on a 1–5 scale based on issue type, description length, subtask count, component count, and keyword indicators.
- **`BlockerAnalyzer`**: Extracts blocker flags and flags tasks blocked by status or transitions.

### 2.3 Phase B Engine (`app/core/intelligence/`)
- **`HistoricalIntelligenceEngine`**: Aggregates multi-dimensional evidence across personal baselines, effort benchmarks, task nature classification, workload pressure, and review/rework rates.
- **`PersonalBaselineEngine`**: Compares current metrics to an individual's personal 30/60/90-day baseline (categorizing as `ABOVE_PERSONAL_BASELINE`, `NEAR_PERSONAL_BASELINE`, `BELOW_PERSONAL_BASELINE`, or `INSUFFICIENT_HISTORY`).
- **`HistoricalEffortBenchmarkEngine`**: Computes segmented effort distributions across issue types, role categories, and complexity bands.
- **`TaskNatureClassifier`**: Classifies tasks deterministically into functional domains (`DEVELOPMENT`, `BUG_FIX`, `QA_TESTING`, `DESIGN`, `DOCUMENTATION`, `DEPLOYMENT`, etc.).
- **`WorkloadPressureAnalyzer`**: Categorizes workload pressure (`LOW`, `NORMAL`, `ELEVATED`, `HIGH`) without using subjective or punitive scoring.
- **`ReviewReworkAnalyzer`**: Tracks rework cycles, QA kickbacks, and requirement churn.

### 2.4 Existing AI Subsystem (`app/services/ai/`)
- **`ContextBuilder`**: Compiles bounded attention contexts, sanitizing tokens and stripping customer/confidential data.
- **`AISafetyGate`**: Fail-closed gate checking token budgets, prompt injection patterns, and schema validation.
- **`DeepSeekProvider` / `AIProvider`**: Production-tested HTTP connector executing structured JSON completion.

---

## 3. Existing Infrastructure That Will Be Reused

Phase 3 will strictly reuse existing components by composition rather than duplication:

| Phase 3 Requirement | Existing Component to Reuse | Reusability Strategy |
| :--- | :--- | :--- |
| **Issue Ingestion & State** | `JiraIssueStateRepository`, `JiraPoller` | **Reuse Directly**: Already updates `jira_issue_state` and `jira_worklogs` every 60s via Jira REST API. |
| **Resource Identity & Roles** | `EmployeeRoleAssignmentRepository`, `resolve_resource_role()` | **Reuse Directly**: Maps Jira `accountId` to canonical name, designation, and `RoleCategory`. |
| **Pace & Velocity Stats** | `HistoricalPaceAnalyzer` | **Reuse Directly**: Provides sample-sized P25, median, P75, and rolling window metrics. |
| **Active Queue Facts** | `CurrentQueueAnalyzer` | **Reuse & Extend**: Reuses queue extraction; extends to surface dependency links and artifact tags. |
| **Nominal Working Capacity** | `CapacityCalculator` | **Reuse Directly**: Provides business day arithmetic and 6.75h/day realistic scheduling constraints. |
| **Effort Benchmarks** | `HistoricalEffortBenchmarkEngine` | **Reuse Directly**: Provides fallback duration distributions when specific task estimates are missing. |
| **Task Categorization** | `TaskNatureClassifier` | **Reuse Directly**: Maps tasks to functional types (`DESIGN`, `DEVELOPMENT`, `QA_TESTING`, etc.). |
| **AI Boundary & Safety** | `ContextBuilder`, `AISafetyGate`, `AIProvider` | **Extend**: Implement new `PlanningContextBuilder` adhering to the existing `AIProvider` contract. |

---

## 4. Gaps Identified (What Must Be Built)

While individual resource performance and historical intelligence are robust, the repository currently lacks:

1. **Explicit Artifact Model**: Tasks have descriptions and attachments in Jira, but there is no structured concept of an "Artifact" (e.g., API Spec, Figma Design, Database Migration, Build Artifact, Test Sign-off) or producer/consumer relationships.
2. **Deterministic Dependency Graph (DAG)**: Existing queue forecasting assumes tasks are executed in a flat linear list per resource. It cannot model:
   - Issue links in Jira (`blocks`, `is blocked by`, `relates to`).
   - Cross-resource sequence dependencies (e.g., QA task on Alice's queue waiting for Dev task on Bob's queue).
   - Artifact dependencies (e.g., Backend implementation waiting for Frontend API contract).
3. **Team-Wide Schedule & Bottleneck Analysis**: The current `DueDateForecaster` runs on individual queues in isolation. It does not project cross-resource critical paths, bottleneck handoffs, or team-level idle/overload balance.
4. **Planning Context Contract (`PlanningContext`)**: Existing AI context (`PMAttentionContext`) is tuned for reactive alerts. A specialized, compact schema representing queues, dependencies, capacities, and artifacts is required for multi-step AI planning.

---

## 5. Phase 3 Architecture & Data Flow

Phase 3 introduces a pure deterministic layer between Jira persistence and the future AI Planning Engine:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        Jira Cloud REST API                             │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │ (Periodic polling via JiraPoller)
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│                     Local SQLite Persistence Cache                     │
│  - jira_issue_state (with issue_links, components, labels, worklogs)   │
│  - employee_role_assignments                                           │
│  - resource_performance_profiles & historical_intelligence_profiles     │
│  - NEW: project_artifacts & artifact_dependencies (metadata layer)     │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │ (Deterministic Extraction & Computation)
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│                PHASE 3: DETERMINISTIC PLANNING ENGINE                  │
│                                                                        │
│  ┌─────────────────────────┐         ┌──────────────────────────────┐  │
│  │ ResourcePerformance     │         │ DependencyGraphEngine        │  │
│  │ & Queue Engine          │         │ - Jira issue links           │  │
│  │ - Pace / Capacity       │         │ - Cross-resource blockers    │  │
│  │ - Queue depth / Age     │         │ - Topological sort / Cycles  │  │
│  └────────────┬────────────┘         └──────────────┬───────────────┘  │
│               │                                     │                  │
│               └──────────────────┬──────────────────┘                  │
│                                  ▼                                     │
│               ┌─────────────────────────────────────┐                  │
│               │ TeamSchedule & Bottleneck Forecaster│                  │
│               │ - Unified multi-resource timeline   │                  │
│               │ - Critical path detection           │                  │
│               │ - Artifact availability milestone   │                  │
│               └──────────────────┬──────────────────┘                  │
│                                  ▼                                     │
│               ┌─────────────────────────────────────┐                  │
│               │ PlanningContextBuilder              │                  │
│               │ - Bounded, token-capped projection  │                  │
│               │ - Honest uncertainty & data-quality │                  │
│               └──────────────────┬──────────────────┘                  │
└──────────────────────────────────┼─────────────────────────────────────┘
                                   │ (Bounded PlanningContext)
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│                     FUTURE PHASE 4: AI REASONING                       │
│  - DeepSeek V3 evaluates alternatives & trade-offs                     │
│  - Generates structured Proposal: {reassignments, date_adjustments}    │
│  - Deterministic Safety Gate validates proposal constraints            │
│  - Human PM approval via Discord/CLI before any Jira mutation          │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 6. Resource Performance Model

### 6.1 Principles
- Reuse `app.core.performance.pace.HistoricalPaceAnalyzer` and `app.core.intelligence.baselines.PersonalBaselineEngine`.
- Use robust statistical aggregations (Median, Interquartile Range P25–P75) instead of naive arithmetic means.
- Explicitly tag every metric with a `ConfidenceLevel` based on sample count and history duration.

### 6.2 Metrics Specification

| Metric | Source Data | Calculation / Aggregation | Window | Min Sample | Missing-Data Behavior | Use in Planning? |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Median Completion Duration** | `jira_issue_state` + `jira_worklogs` | `median([time_spent_seconds or (resolved_at - created_at)])` | 90 days | $\ge 5$ tasks | Fall back to Role Benchmark | **Yes (Primary)** |
| **IQR Variance (P25 - P75)** | `jira_issue_state` | Percentile 75 - Percentile 25 hours | 90 days | $\ge 5$ tasks | Low confidence flag | **Yes (Uncertainty)** |
| **Recent Pace Factor** | `jira_worklogs` | Ratio of 14d pace vs 90d baseline pace | 14 days vs 90 days | $\ge 3$ tasks | Clamp to 1.0 (Neutral) | **Yes (Adjustment)** |
| **On-Time Completion Rate** | `jira_issue_state` | `count(resolved_at <= due_date) / count(due_date present)` | 90 days | $\ge 5$ due dates | `None` (Unknown) | **Contextual only** |
| **Reopen / Rework Rate** | `jira_issue_state.reopen_count` | `count(reopen_count > 0) / total_tasks` | 90 days | $\ge 5$ tasks | 0.0 (Assume clean) | **Risk buffer** |
| **Blocker Susceptibility** | `jira_issue_state.blocker_hours`| Sum of blocked hours / active hours | 90 days | $\ge 5$ tasks | 0.0 | **Risk buffer** |

---

## 7. Current Queue Intelligence

The current queue representation separates **hard facts** from **derived metrics**:

```
[ HARD JIRA FACTS ]
├── Issue Key, Summary, Issue Type
├── Assignee Account ID
├── Status & Status Category ('To Do', 'In Progress', 'Done')
├── Jira Due Date (if present)
├── Original Estimate & Time Spent
└── Direct Blocker Flags / Inward Issue Links

        │ (Deterministic Processing)
        ▼
[ DERIVED METRICS ]
├── Inferred Remaining Effort (Estimate - Time Spent, or Benchmark Median)
├── Task Age in Current Status (working days)
├── Sla/Stale Status (no activity in > 48h)
├── Sequential Queue Position
└── Projected Start & Finish Timestamps (Capacity-driven)

        │ (Bounded Planning Contract)
        ▼
[ AI PLANNING INTERPRETATION ]
└── DeepSeek reasons about sequencing, trade-offs, and reallocation options.
```

---

## 8. Capacity Model

### 8.1 Reality-Based Constraints
- Standard nominal capacity is set to **6.75 productive hours per working day** (Mon–Fri, accounting for lunch, standups, and overhead).
- **Available Capacity** $= \text{working\_days}(\text{start\_date}, \text{target\_date}) \times 6.75 \text{ hours}$.
- **Committed Capacity** $= \sum \text{remaining\_hours}(\text{active tasks in queue})$.
- **Remaining / Buffer Capacity** $= \text{Available Capacity} - \text{Committed Capacity}$.
- **Capacity State**:
  - `UNDER_UTILIZED`: Committed $< 0.6 \times$ Available
  - `BALANCED`: $0.6 \times \text{Available} \le \text{Committed} \le 1.0 \times \text{Available}$
  - `OVERLOADED`: Committed $> 1.0 \times$ Available
  - `SATURATED`: Committed $> 1.4 \times$ Available (Immediate delivery risk)

---

## 9. Task Complexity & Duration Estimation

When a task in the queue lacks an explicit Jira estimate, the system deterministically derives a proxy estimate without guessing:

1. **Task Complexity Evaluation** (`TaskComplexityCalculator`):
   - Categorizes task into Score 1 (Trivial: 2h), Score 2 (Small: 4h), Score 3 (Medium: 8h), Score 4 (Large: 16h), Score 5 (Very Large: 32h).
2. **Empirical Benchmark Matching** (`HistoricalEffortBenchmarkEngine`):
   - **Tier 1 (Exact Match)**: Resource's own historical median for `(issue_type, complexity_score)` (Requires $\ge 3$ completed tasks).
   - **Tier 2 (Role Benchmark)**: Team role historical median for `(role_category, issue_type, complexity_score)` (Requires $\ge 5$ tasks).
   - **Tier 3 (Global Benchmark)**: Project-wide median for `(issue_type)` (Requires $\ge 5$ tasks).
   - **Tier 4 (Nominal Fallback)**: Deterministic defaults based on Complexity Score with confidence = `LOW`.

---

## 10. Artifact Model

### 10.1 Concept & Purpose
In real-world project delivery, tasks are frequently blocked not by direct Jira links, but because an underlying **work product (artifact)** is not yet ready. Phase 3 establishes an explicit Artifact abstraction:

```
[Task A: Backend API] ─────────► [PRODUCES] ───► [Artifact: OpenAPI Spec v2]
                                                        │
                                                        ▼
[Task B: Frontend Integration] ──► [CONSUMES] ◄─────────┘
```

### 10.2 Model Representation
- **`ArtifactType`**: `SPECIFICATION`, `DESIGN_ASSET`, `API_CONTRACT`, `DATABASE_MIGRATION`, `BUILD_PACKAGE`, `TEST_SUITE`, `DEPLOYMENT_ENV`, `DOCUMENTATION`.
- **`ArtifactStatus`**: `PLANNED`, `IN_PROGRESS`, `READY_FOR_REVIEW`, `ACCEPTED`, `SUPERSEDED`.
- **`ArtifactProvenance`**: Tracks creating task key, producing resource, version/link, and timestamp.

### 10.3 Discovery & Ingestion Strategy
1. **Jira Issue Fields & Attachments**: Parse structured fields, Jira components (e.g. `Component: API`), and labels (`artifact:spec-v1`).
2. **Explicit Metadata Repository**: A lightweight local table `project_artifacts` and `artifact_dependencies` mapped to Jira issues.
3. **No Speculative NLP**: If an artifact is not explicitly declared via label/component or linked task, the system flags it as `UNKNOWN`, not hallucinated.

---

## 11. Dependency Model & DAG Engine

### 11.1 Dependency Types
1. **Explicit Jira Link**: Parsed from Jira issue links (`is blocked by`, `depends on`, `clones`).
2. **Subtask / Parent Relationship**: Subtasks must complete before parent resolution.
3. **Artifact Dependency**: Task B consumes an artifact produced by Task A.
4. **Sequence Constraint**: Resource-level sequential queue constraint (Task A scheduled at index 0 precedes Task B at index 1).

### 11.2 DAG Validation & Scheduling
- **Cycle Detection**: Kahn's algorithm / Depth-First Search cycle check prevents infinite planning loops.
- **Topological Sort**: Determines earliest possible start date for downstream tasks:
  $$\text{EarliestStart}(T_B) = \max \left( \text{ProjectedFinish}(T_A) \right) \quad \forall T_A \in \text{Dependencies}(T_B)$$
- **Cross-Resource Delay Hand-offs**: Accounts for working hours hand-off buffer (e.g. 0.5 working day) when dependencies cross different resources.

---

## 12. Team-Level Model & Bottleneck Detection

The `TeamWorkloadSnapshot` aggregates individual resource contexts into a coherent team-wide operational view:

1. **Resource Availability Matrix**: Who has spare capacity within the 5/10/15-day horizon vs. who is overloaded.
2. **Cross-Resource Critical Path**: The chain of dependent tasks spanning multiple team members that dictates the earliest final delivery date.
3. **Bottleneck Identification**: Identifies resources who are on the critical path of multiple high-priority tasks and have insufficient capacity.
4. **Sequencing Conflict Alerts**: Flags where Task B is scheduled to start on Monday, but Task A (assigned to another resource) will not finish until Wednesday.

---

## 13. Data Freshness & Caching Strategy

To support efficient operation on a resource-constrained VM (Oracle Cloud 1–2 OCPU, SQLite):

| Artifact / Computation | Trigger | Invalidation / TTL | Storage |
| :--- | :--- | :--- | :--- |
| **Jira Issue State Cache** | Jira Poller (every 60s) | Instant upon webhook/poll | SQLite `jira_issue_state` |
| **Resource Historical Baselines** | Daily cron or on-demand | 24 Hours | SQLite `resource_performance_profiles` |
| **Active Queue Snapshot** | On demand when planning | 5 Minutes | In-memory cache |
| **Dependency DAG & Timeline** | On demand when planning | Computed on fresh queue | Transient Pydantic Model |
| **PlanningContext for AI** | Generated per planning request | Single-use ephemeral | In-memory |

---

## 14. Data Quality & Uncertainty Strategy

The system adheres to radical honesty regarding data limitations:

```python
if completed_sample_count < 3:
    confidence = ConfidenceLevel.INSUFFICIENT
    guidance = "Insufficient historical samples for empirical forecast. Relying on default role baseline."
elif completed_sample_count < 10:
    confidence = ConfidenceLevel.LOW
    guidance = "Small sample size. Variance may be elevated."
else:
    confidence = ConfidenceLevel.HIGH
    guidance = "Robust empirical statistical baseline."
```

- When Jira due dates or estimates are absent, they are explicitly output as `None` / `UNESTIMATED` rather than assumed.
- The AI context includes explicit `data_quality_notes` summarizing unestimated tasks, missing dependencies, and confidence scores.

---

## 15. Proposed Typed Domain Models

All models will be implemented as strict Pydantic v2 classes extending `app/core/models/` and `app/services/ai/contracts.py`:

```
app/core/models/planning.py
├── ResourcePlanningProfile
├── QueueTaskDetail
├── ResourceQueueSnapshot
├── ResourceCapacitySnapshot
├── ArtifactRecord
├── ArtifactDependencyRecord
├── TaskDependencyLink
├── DependencyDAG
├── TeamWorkloadSnapshot
└── PlanningContext (Contract with AI layer)
```

### 15.1 Core Model Signatures

```python
class TaskDependencyLink(BaseModel):
    source_key: str
    target_key: str
    dependency_type: str  # "JIRA_BLOCKS", "SUBTASK", "ARTIFACT", "SEQUENCE"
    confidence: ConfidenceLevel = ConfidenceLevel.HIGH
    is_inferred: bool = False
    evidence: str

class ArtifactRecord(BaseModel):
    artifact_id: str
    name: str
    artifact_type: str  # "SPEC", "DESIGN", "API", "CODE", "QA_APPROVAL"
    status: str        # "PLANNED", "IN_PROGRESS", "AVAILABLE"
    producer_task_key: str
    producer_account_id: Optional[str] = None
    expected_availability_date: Optional[str] = None
    actual_availability_date: Optional[str] = None

class ResourceQueueSnapshot(BaseModel):
    account_id: str
    display_name: str
    role_category: str
    total_active_tasks: int
    total_remaining_hours: float
    capacity_5d_hours: float
    net_capacity_hours: float
    workload_state: str  # "UNDER_UTILIZED", "BALANCED", "OVERLOADED"
    tasks: List[QueueTaskDetail]
    overdue_task_keys: List[str]
    blocked_task_keys: List[str]

class PlanningContext(BaseModel):
    context_id: str
    created_at: str
    planning_horizon_working_days: int
    team_snapshots: List[ResourceQueueSnapshot]
    dependency_links: List[TaskDependencyLink]
    artifacts: List[ArtifactRecord]
    bottlenecks: List[str]
    data_quality_flags: List[str]
```

---

## 16. Persistence Design

Phase 3 introduces minimal additional tables, maximizing reuse of existing schema:

1. **`project_artifacts`**:
   - Stores defined deliverables and their status.
   - Schema: `artifact_id (PK)`, `project_key`, `name`, `artifact_type`, `status`, `producer_task_key`, `created_at`, `updated_at`.
2. **`artifact_dependencies`**:
   - Maps consumer tasks to artifacts.
   - Schema: `consumer_task_key`, `artifact_id`, `is_blocking (bool)`.
3. **`jira_issue_links`**:
   - Normalized local cache of Jira issue-to-issue links parsed during Jira polling.
   - Schema: `link_id (PK)`, `inward_key`, `outward_key`, `link_type`, `synced_at`.

*Note: All queue projections, DAGs, and timelines remain ephemeral derived calculations to eliminate stale database state.*

---

## 17. Performance Considerations (Low-Resource VM)

- **Execution Bounds**: The DAG calculation and topological sort for 200 tasks executes in $< 15$ ms in pure Python without graph database overhead.
- **Memory Footprint**: Transient planning models for 20 team members and 300 tasks consume $< 3$ MB of RAM.
- **Jira API Protection**: Polling queries fetch only modified issues (`updated >= -10m`) every 60 seconds; planning computations hit the local SQLite cache with zero live HTTP calls to Jira.

---

## 18. Test Strategy

1. **Unit Tests (`tests/unit/planning/`)**:
   - `test_dependency_dag_cycles.py`: Verifies cycle detection and deterministic topological ordering.
   - `test_queue_capacity.py`: Validates working day calculations, weekend skips, and load state categorization.
   - `test_artifact_handoffs.py`: Verifies that consumer tasks cannot be scheduled prior to producer task completion.
   - `test_uncertainty_handling.py`: Confirms fallback to role benchmarks when sample count $< 3$.
2. **Integration Tests (`tests/integration/planning/`)**:
   - Verifies end-to-end extraction from SQLite `jira_issue_state` into `PlanningContext`.
   - Token budget verification: Ensure `PlanningContext` fits within the configured `AI_MAX_INPUT_TOKENS` limit (3,000 tokens).

---

## 19. Phase 3 Sub-Phases (Implementation Sequence)

```
Phase 3A: Issue Links & Dependency Foundation
  └── Parse Jira issue links into local projection; build DependencyDAG & Cycle Detector.

Phase 3B: Artifact Model & Handoff Abstraction
  └── Implement project_artifacts & artifact_dependencies schema; define Producer/Consumer contracts.

Phase 3C: Resource Queue & Capacity Engine Composition
  └── Unify HistoricalPaceAnalyzer and CurrentQueueAnalyzer into ResourceQueueSnapshot.

Phase 3D: Cross-Resource Schedule & Bottleneck Forecaster
  └── Multi-resource topological timeline; critical path analysis and sequence validation.

Phase 3E: PlanningContextBuilder & AI Token Bounds
  └── Construct bounded, sanitized PlanningContext matching existing AISafetyGate contracts.

Phase 3F: Comprehensive Regression & Deterministic Verification
  └── Full test suite execution across edge cases, cycle checks, and missing data scenarios.
```

---

## 20. Phase 4 Boundary (Strict Limits of Phase 3)

| Phase 3 Scope (IN) | Phase 4 Scope (OUT - Forbidden in Phase 3) |
| :--- | :--- |
| Extracting & verifying facts from Jira | Generating automated reassignments |
| Calculating capacity, pace, and slack | Mutating Jira due dates or priorities |
| Building topological dependency DAGs | Posting automated comments to Jira |
| Identifying bottlenecks and idle capacity | Dispatching Discord/Mattermost notifications |
| Packaging facts into bounded `PlanningContext` | Executing Action Engine mutations |

---

## 21. Risks and Mitigations

| Risk | Impact | Mitigation |
| :--- | :--- | :--- |
| **Circular Jira Links** | Infinite loops in scheduling | DFS cycle detection flags cycles and marks edges as non-blocking sequence advice. |
| **Sparse Jira Estimates** | Distorted capacity projections | Multi-tiered fallback hierarchy (Personal $\to$ Role $\to$ Global Benchmark) with explicit `LOW` confidence tag. |
| **Missing Due Dates** | Inability to calculate slack | Due date risk marked as `NO_DUE_DATE` without false alarms. |
| **Token Overflow in Planning Context** | AI call rejection / cost spike | Compact summarization of completed tasks; only include active queue items on critical path. |

---

## 22. Open Questions for User Approval

1. **Jira Issue Link Types**: What specific link names are used in your Jira project for blocking dependencies (e.g., `Blocks`, `is blocked by`, `Dependency`)?
2. **Artifact Definition Source**: Should artifacts initially be declared via Jira labels (e.g. `artifact:openapi-spec`), Jira Components, or a dedicated configuration file?
3. **Planning Horizon**: Is 10 working days (2 weeks) the preferred standard horizon for the automated planning context?

---

## 23. Phase 3 Implementation Readiness

- **Already Available in Repository**: Full Jira poller, local SQLite issue cache, Phase A performance analytics (P25/Median/P75 pace, capacity, complexity, risk bands), Phase B historical intelligence profiles, and the Phase 2E tested DeepSeek AI provider.
- **What Must Be Built**: Local Jira issue link sync, Dependency DAG engine, Artifact tracking tables, Multi-resource timeline forecaster, and the `PlanningContext` schema.
- **Architectural Status**: Ready for phased implementation upon user review and approval of the design specification.

---

## 24. Phase 3A Implementation: Jira Issue Link Normalization & Dependency DAG

### 24.1 Normalized Link Model (`jira_issue_links`)
- Normalized table added in `app/database/schema.py`:
  - Columns: `id (PK)`, `source_issue_key`, `target_issue_key`, `link_type_name`, `inward_description`, `outward_description`, `classification`, `source_issue_id`, `target_issue_id`, `first_seen_at`, `last_seen_at`, `is_active`.
  - Indexes: `idx_jira_issue_links_source`, `idx_jira_issue_links_target`, `idx_jira_issue_links_type`, `idx_jira_issue_links_class`.
  - Idempotent upsert via `JiraIssueLinkRepository` using deterministic composite key `source:target:link_type`.

### 24.2 Dependency Classification
Deterministic enum `DependencyClassification` mapped in `app/core/models/planning.py`:
- `HARD_BLOCK`: `Blocks` (`blocks` / `is blocked by`)
- `CAUSAL_DEPENDENCY`: `Problem/Incident` (`causes` / `is caused by`)
- `VERIFICATION_DEPENDENCY`: `Test` (`tests` / `is tested by`)
- `INFORMATIONAL`: `Relates` (`relates to`)
- `NON_DEPENDENCY`: `Duplicate`, `Cloners`, `Polaris merge work item link`, `Discovery - Connected`
- `CONTEXTUAL`: `Defect` (`created` / `created by`)
- `UNKNOWN`: Default fallback for unrecognized link types (safe, non-blocking).

### 24.3 Graph Semantics, Cycle Detection & Topological Ordering
- Implemented in `app/core/planning/dag.py` via `DependencyGraph`.
- Directed edge $A \to B$ represents: $A$ is a predecessor of $B$ ($A$ blocks $B$). $A$ must complete before $B$ can proceed.
- By default, `include_only_hard_blocks=True` filters non-blocking relationships (`Relates`, `Duplicate`, etc.) out of scheduling DAGs.
- **Cycle Detection**: 3-color DFS iterative search detecting cycles with full path reporting without recursing or crashing.
- **Topological Sorting**: Kahn's algorithm with deterministic tie-breaking on sorted issue keys.
- **Cross-Resource Support**: Graph operations operate purely on issue keys ($A \to B$), naturally accommodating cross-resource handoffs without coupling to individual employee identities.

---

## 25. Phase 3B Implementation: Artifact Model & Work Product Handoff Foundation

### 25.1 Canonical Artifact Models & Persistence
- Normalized tables added in `app/database/schema.py`:
  - **`project_artifacts`**: `id (PK: project_key:name)`, `name`, `project_key`, `artifact_type`, `status`, `producer_issue_key`, `provenance`, `confidence`, `first_seen_at`, `last_seen_at`, `is_active`.
  - **`artifact_dependencies`**: `id (PK: artifact_id:issue_key:relationship_type)`, `artifact_id`, `issue_key`, `relationship_type` (`PRODUCES` / `CONSUMES`), `provenance`, `confidence`, `is_inferred`, `first_seen_at`, `last_seen_at`, `is_active`.
  - Supported `ArtifactType`: `SPECIFICATION`, `DESIGN_ASSET`, `API_CONTRACT`, `DATABASE_MIGRATION`, `BUILD_PACKAGE`, `TEST_SUITE`, `DOCUMENTATION`, `GENERIC`.
  - Supported `ArtifactProvenance`: `EXPLICIT_JIRA_LABEL`, `EXPLICIT_JIRA_COMPONENT`, `EXPLICIT_CONFIGURATION`, `TASK_NATURE_INFERENCE`, `MANUAL`.
  - Supported `ArtifactStatus`: `PLANNED`, `IN_PROGRESS`, `AVAILABLE`, `SUPERSEDED`, `UNKNOWN`.
  - Idempotent upsert via `ArtifactRepository` in `app/database/repositories.py`.

### 25.2 Explicit Opt-In Label Convention
- The system recognizes opt-in labels on Jira issues:
  - `artifact:<name>` (e.g. `artifact:api-spec`): Declares interest in an artifact without fixed producer/consumer role.
  - `produces:<name>` or `produces:artifact:<name>`: Explicitly records the task as the authoritative `PRODUCES` entity.
  - `consumes:<name>` or `consumes:artifact:<name>`: Explicitly records the task as a `CONSUMES` entity.
- Non-artifact labels (e.g., `bug`, `frontend`, `v1.2.3`) and malformed labels are strictly ignored without logging noise or exceptions.
- Integrated directly into `JiraPoller` without making additional Jira API calls.

### 25.3 Task-Nature Advisory Inference
- Implemented in `ArtifactEngine.infer_handoff_artifact()`:
  - Evaluates functional transitions between dependent tasks (e.g., `DESIGN` $\to$ `DEVELOPMENT` generates `design-asset`, `DEVELOPMENT` $\to$ `QA_TESTING` generates `build-package`).
  - Marked with `provenance=TASK_NATURE_INFERENCE`, `confidence=MEDIUM`, and `is_inferred=True`.

### 25.4 Strict Separation from HARD_BLOCK Scheduling
- **Critical Architectural Invariant**: Inferred artifacts are advisory evidence.
- They are NOT authoritative Jira links and MUST NOT be converted into `HARD_BLOCK` edges in `DependencyGraph`.
- `DependencyGraph` remains strictly reserved for confirmed, authoritative Jira dependency semantics.

---

## 26. Phase 3C Implementation: Resource Queue & Capacity Intelligence Composition

### 26.1 Principles & Separation of Concerns
- **Core Mission**: Composes existing Phase A/B intelligence engines and Phase 3A/3B dependency models into a coherent, deterministic, and explainable `ResourceQueueSnapshot`.
- **Strict Non-Goals**:
  - Does NOT generate Jira due dates or estimates.
  - Does NOT reschedule or mutate Jira tasks.
  - Does NOT rank, score, or evaluate employees hierarchically.
  - Does NOT build the AI `PlanningContext` (reserved for Phase 3E).
  - Does NOT call DeepSeek or Action Engine.
- Answers: *"What is this resource currently carrying, what has their historical throughput looked like, and what capacity and dependency information is available?"*

### 26.2 Reused Intelligence Engines
Phase 3C cleanly orchestrates existing tested components without redundant calculations:
1. **`CurrentQueueAnalyzer`** (`app/core/performance/queue.py`): Active queue analysis using the 7-tier expected-effort inference hierarchy with explicit Jira raw remaining separation.
2. **`CapacityCalculator`** (`app/core/performance/capacity.py`): Nominal (6.75h/day), observed logged capacity, and bounded forecast capacity calculation over configurable planning horizons (default: 10 working days).
3. **`HistoricalPaceAnalyzer`** (`app/core/performance/pace.py`): Percentile-based effort statistics (mean, median, P25, P75) and sample-size-driven confidence tracking.
4. **`WorkloadPressureAnalyzer`** (`app/core/intelligence/workload.py`): Contextual workload vs. capacity ratio and deadline pressure (`LOW`, `NORMAL`, `ELEVATED`, `HIGH`).
5. **`TaskComplexityCalculator`** (`app/core/performance/complexity.py`): Intrinsic characteristic complexity scoring (1 to 5).
6. **`TaskNatureClassifier`** (`app/core/intelligence/classifier.py`): 17 deterministic functional categories.
7. **`BlockerAnalyzer`** (`app/core/performance/blockers.py`): Conservative evidence-based blocker detection.
8. **`JiraIssueLinkRepository`** (`app/database/repositories.py`): Direct Phase 3A hard blocker and predecessor/successor link queries.
9. **`ArtifactEngine`** (`app/core/planning/artifacts.py`): Phase 3B produced and consumed deliverable reference lookup.

### 26.3 Domain Models
Implemented in `app/core/models/planning.py`:
- **`QueueTaskDetail`**: Bounded facts for active assigned tasks (issue key, summary, status, priority, issue type, task nature, project, due date, complexity, estimates, spent, remaining, overdue, stale, blocked, reopened flags, hard blockers, and artifact references).
- **`ResourcePaceSummary`**: Completed task count, P25, median, mean, P75, pace factor, confidence, and fallback indicator.
- **`ResourceCapacitySummary`**: Nominal, observed, and forecast daily capacity, planning horizon (working days), available capacity, committed workload, remaining capacity, and `CapacityState` (`UNDER_UTILIZED`, `BALANCED`, `OVERLOADED`, `SATURATED`).
- **`ResourceDependencyContext`**: Total dependencies, hard blocker count, blocked issue keys, and downstream dependent keys.
- **`ResourceArtifactContext`**: Total artifacts, produced artifact IDs, and consumed artifact IDs.
- **`ResourceQueueSnapshot`**: Canonical resource ID, display name, designation, role category, team group, timestamp, active task details and counts, priority/type/nature summaries, workload pressure, dependency and artifact summaries, and explicit data quality ratings.
- **`TeamWorkloadSnapshot`**: Deterministic aggregation of multiple `ResourceQueueSnapshot` instances without employee ranking.

### 26.4 Data Quality & Freshness Semantics
- **History Completeness**: `SUFFICIENT_HISTORY` ($\ge 15$ completed tasks, $\ge 30$ active days), `LIMITED_HISTORY`, `NO_HISTORY`.
- **Capacity Quality**: `CAPACITY_KNOWN` ($\ge 15$ active days), `CAPACITY_PARTIAL`, `CAPACITY_UNAVAILABLE`.
- **Queue Completeness**: `QUEUE_COMPLETE` (all tasks estimated), `QUEUE_PARTIAL` (relying on complexity fallbacks), `QUEUE_EMPTY`.
- **Freshness**: On-demand calculation querying local SQLite projections with zero background workers or persistent snapshot table bloat.

---

## 27. Phase 3D Implementation: Deterministic Team Timeline & Bottleneck Forecaster

### 27.1 Purpose & Architectural Separation
- **Core Mission**: Build a deterministic `TeamScheduleForecaster` that composes `ResourceQueueSnapshot`s, `DependencyGraph` DAGs, and `ArtifactEngine` handoff relationships to produce a feasible, explainable timeline projection and bottleneck analysis.
- **Analytical Evidence Only**: Projected dates are analytical evidence intended exclusively for Phase 3E (`PlanningContext`) and downstream AI reasoning.
- **Strict Invariants & Non-Goals**:
  - ZERO AI calls (no DeepSeek, no OpenAI).
  - ZERO Jira mutations (no writing Jira due dates, comments, fields, or priorities).
  - ZERO Action Engine executions.
  - ZERO automatic reassignments or task rescheduling.
  - ZERO employee scoring, rankings, or productivity leaderboards.

### 27.2 Multi-Resource Concurrency & Single-Resource Serialization
- **Parallel Work**: Different resources execute their respective queues concurrently in parallel.
- **Single-Resource Serialization**: Each resource works on assigned tasks sequentially based on established queue priority (priority rank, due date, issue key).
- **DAG Gating**: For any task $T$, its projected start date is:
  $$\text{projected\_start}(T) = \max(\text{resource\_available}(\text{assignee}), \max_{P \in \text{HARD\_PREDECESSORS}}(\text{projected\_completion}(P)))$$
- **Non-Hard Dependencies**: Causal, verification, informational, and unknown link types do not block scheduling serially. They are preserved as structured `ScheduleConstraint` records.

### 27.3 Artifact Handoffs
- Explicit and inferred artifact relationships from Phase 3B are surfaced as `ScheduleConstraint` records and advisory `Bottleneck` signals (`type=ARTIFACT_HANDOFF`).
- Inferred artifacts remain advisory and are never promoted into `HARD_BLOCK` scheduling constraints.

### 27.4 Cycle Detection & Graceful Fallback
- `DependencyGraph.detect_cycles()` is evaluated prior to simulation.
- If a cycle is detected:
  - `schedule_valid` is set to `False`.
  - A `DEPENDENCY_CYCLE` bottleneck (`severity=HIGH`) is generated detailing the cycle path.
  - A structured, non-failing placeholder projection is returned without crashing the PM Agent.

### 27.5 Working Days, Calendar & Horizon
- Uses `CapacityCalculator.project_completion_date` to advance work across standard working days (Mon-Fri) while strictly skipping weekends.
- Planning horizon defaults to `settings.PLANNING_HORIZON_WORKING_DAYS` (10 working days / 2 weeks), with explicit per-call override support.
- Tasks completing past the horizon boundary are flagged with `is_beyond_horizon=True` and accounted for in `tasks_beyond_horizon_count` without discarding them.

### 27.6 Bottleneck Detection Rules
Deterministic, rule-based operational bottleneck detection:
1. `OVERLOADED_RESOURCE`: Committed workload exceeds available capacity over the planning horizon (`HIGH` severity if ratio $\ge 1.4$ or `SATURATED`).
2. `INSUFFICIENT_CAPACITY`: Zero daily capacity or `CAPACITY_UNAVAILABLE`.
3. `DEPENDENCY_CHAIN`: Sequential `HARD_BLOCK` chains with $\ge 3$ tasks (`HIGH` if $\ge 5$).
4. `BLOCKED_TASK`: Tasks waiting on hard predecessors.
5. `LONG_DURATION_TASK`: Tasks with estimated effort $\ge 20$ hours ($\ge 3$ working days).
6. `MISSING_DURATION_EVIDENCE`: Unestimated tasks relying on fallback heuristics.
7. `DEPENDENCY_CYCLE`: Circular dependencies blocking valid topological scheduling.
8. `ARTIFACT_HANDOFF`: Contextual deliverable handoff between tasks.

### 27.7 Input-Order Independence & Determinism
All inputs, resource queues, link records, and ready queues are sorted by canonical keys (`resource_id`, `priority_rank`, `duedate`, `issue_key`) ensuring identical outputs regardless of input collection ordering.
