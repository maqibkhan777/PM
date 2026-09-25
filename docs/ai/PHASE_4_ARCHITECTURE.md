# PHASE 4 — AI PLANNING REASONING & PROPOSAL ENGINE
## Architectural Design & DeepSeek Reasoning Specification

**Document Version:** 1.0.0  
**Status:** PHASE 4A & 4B COMPLETE — READ-ONLY  
**Target Branch:** `AI`  
**Base Checkpoint:** `cb25796`  
**Execution Mode:** Read-Only Analysis, Prompt Engineering & Safety Enforcement (Zero Jira Mutations, Zero Action Engine Calls)

---

## 1. Executive Summary & Architecture Pipeline

Phase 4 establishes the AI Planning Reasoning Layer of the PM Operations system. The engine consumes a deterministic, bounded `PlanningContext` produced by Phase 3E, compiles a structured reasoning prompt, invokes the configured `AIProvider` (DeepSeek / Mock / Null), and validates the resulting `PlanningProposal` against Phase 4A contracts and grounding safety constraints.

```
┌────────────────────────────────────────────────────────────────────────┐
│                   DETERMINISTIC PLANNING FOUNDATION                    │
│                                                                        │
│  - ResourceQueueSnapshots (Phase 3C)                                   │
│  - Dependency DAG & Cycle Detection (Phase 3A)                         │
│  - Artifact Engine & Deliverable Handoffs (Phase 3B)                   │
│  - TeamScheduleForecaster & Bottlenecks (Phase 3D)                     │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│                  PHASE 3E: PLANNING CONTEXT BUILDER                    │
│  - Bounded, token-capped PlanningContext                               │
│  - Deterministic ordering & secret redaction                           │
│  - Explicit truncation metadata                                        │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │ (PlanningContext)
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│               PHASE 4B: DEEPSEEK AI PLANNING REASONING                 │
│                                                                        │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │ PlanningPromptBuilder (Version: planning-v1)                     │  │
│  │ - Strictly enforces PlanningContext as ground-truth facts        │  │
│  │ - Prohibits inventing issue keys, resources, or dependencies     │  │
│  │ - Prohibits overriding HARD_BLOCK facts                          │  │
│  │ - Requires JSON adhering to PlanningProposal schema              │  │
│  └───────────────────────────────┬──────────────────────────────────┘  │
│                                  │ (Sanitized Chat Messages)           │
│                                  ▼                                     │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │ AIProvider Protocol (DeepSeekAIProvider / MockAIProvider)        │  │
│  │ - OpenAI-compatible Chat Completion API                          │  │
│  │ - JSON object mode with bounded token budgets & timeouts         │  │
│  └───────────────────────────────┬──────────────────────────────────┘  │
│                                  │ (Raw JSON String)                   │
│                                  ▼                                     │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │ PlanningProposal Parsing & Grounding Safety Gate                 │  │
│  │ - Phase 4A Pydantic validation (extra='forbid', bounds check)    │  │
│  │ - AISafetyGate.validate_planning_proposal(proposal, context)     │  │
│  │ - Enforces requires_human_review = True                          │  │
│  │ - Verifies issue_keys & resource_ids are strictly grounded       │  │
│  └───────────────────────────────┬──────────────────────────────────┘  │
│                                  │ (Validated Proposal)                │
│                                  ▼                                     │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │ Safe Audit Logging (AuditService)                                │  │
│  │ - Latency, token counts, proposal summary (Zero secrets)         │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │ (PlanningProposal)
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│            FUTURE PHASE 4C: DETERMINISTIC FEASIBILITY VALIDATION       │
│  - Capacity & working-day verification                                 │
│  - Dependency order validation                                         │
│  - Optimal schedule feasibility checking                               │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│            FUTURE PHASE 4E: PM HUMAN APPROVAL & JIRA DISPATCH          │
│  - Discord / CLI human review interface                                │
│  - Explicit PM approval before any Action Engine dispatch              │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Phase 4A: Typed AI Planning Contracts (Checkpoint `cb25796`)

Phase 4A defined the typed Pydantic contracts in `app/core/models/planning.py`:
- `PlanningProposal`: Root contract with metadata, summary, overall confidence, task proposals, sequencing proposals, risk signals, assumptions, and evidence references.
- `TaskPlanningProposal`: Advisory planning for existing Jira tasks (estimates in hours, start/due dates, sequencing position, proposed predecessors/successors, risk level).
- `SequencingProposal`: 1-indexed advisory queue sequencing.
- `PlanningRiskSignal`: Bounded operational risk categories (`CAPACITY_RISK`, `DEPENDENCY_RISK`, `DEADLINE_RISK`, `ESTIMATION_UNCERTAINTY`, `DATA_QUALITY_RISK`, `ARTIFACT_HANDOFF_RISK`, `SCHEDULE_DRIFT_RISK`, `OTHER`).
- `EvidenceReference`: Traceable links to deterministic facts (`RESOURCE_HISTORY`, `CURRENT_QUEUE`, `CAPACITY`, `TASK_ESTIMATE`, `TASK_COMPLEXITY`, `DEPENDENCY`, `ARTIFACT`, `TEAM_SCHEDULE`, `BOTTLENECK`, `DATA_QUALITY`, `OTHER`).
- `PlanningAssumption`: Explicitly modeled assumptions made during reasoning.

**Key Safety Invariants**:
- All models forbid extra fields (`extra = "forbid"`), preventing arbitrary mutation payloads.
- `requires_human_review` strictly defaults to `True` across all proposal models.
- Numeric confidences are bounded in `[0.0, 1.0]`.
- Date strings are validated strictly as `YYYY-MM-DD`.
- Units are restricted exclusively to `EstimateUnit.HOURS`.

---

## 3. Phase 4B: DeepSeek Reasoning Engine

### 3.1 `AIPlanningService` (`app/services/ai/planning.py`)
- Provider-agnostic service orchestrating planning requests.
- Checks `settings.AI_ENABLED`; if `False`, safely fails closed by returning a deterministic advisory proposal via `NullAIProvider` without network calls.
- Executes `self.provider.analyze_planning(planning_context)`.
- Validates proposal against `AISafetyGate.validate_planning_proposal(proposal, planning_context)`.
- Enforces `requires_human_review = True`.
- Logs structured audit records via `AuditService` containing latency, token counts, proposal metrics, and data quality flags without leaking tokens or raw sensitive payloads.

### 3.2 `PlanningPromptBuilder` (`app/services/ai/planning_prompt.py`)
- Introduces stable prompt version `PLANNING_PROMPT_VERSION = "planning-v1"`.
- Compiles `SYSTEM_PROMPT_PLANNING` establishing strict authority rules:
  - `PlanningContext` is the exclusive source of factual truth.
  - The AI must never invent issue keys, resources, capacities, dependencies, or artifacts.
  - Advisory artifact relationships must never be converted into `HARD_BLOCK` dependencies.
  - `HARD_BLOCK` dependencies must never be ignored or overridden.
  - Explicitly states truncation warnings when `context.truncation.is_truncated == True`.
  - Enforces output format strictly as structured JSON adhering to `PlanningProposal`.

### 3.3 Provider Implementations (`app/services/ai/provider.py`, `deepseek.py`)
- `AIProvider` protocol updated with `async def analyze_planning(self, context: PlanningContext) -> PlanningProposal`.
- `NullAIProvider`: Deterministic fail-closed no-op when AI is disabled.
- `MockAIProvider`: Fully deterministic offline proposal generation from `PlanningContext` tasks, dependencies, and resources.
- `DeepSeekAIProvider`: Uses OpenAI-compatible HTTP POST `/chat/completions` in `json_object` mode, parsing and validating `PlanningProposal` schemas.

### 3.4 Structural & Grounding Safety Gate (`app/services/ai/safety.py`)
`AISafetyGate.validate_planning_proposal(proposal, planning_context)` verifies:
1. `proposal` is a valid `PlanningProposal` instance.
2. `requires_human_review == True` (AI is strictly advisory).
3. `overall_confidence` and individual item confidences are in `[0.0, 1.0]`.
4. Summary is non-empty.
5. All `task_proposals`, `sequencing_proposals`, and `risk_signals` reference valid, existing `issue_key`s present in `PlanningContext`.
6. Estimates are positive numbers with unit `hours`.

---

## 4. Operational Invariants & Ground Rules

1. **READ-ONLY**: Zero Jira writes, zero due date updates, zero comment additions, zero Action Engine executions, zero reassignments.
2. **Fail-Closed**: Any schema mismatch, grounding violation, network timeout, client error, or missing context fails safely without inventing hallucinated proposals.
3. **No Direct Production Provider Coupling**: Services depend strictly on the `AIProvider` protocol.
4. **Zero Discord Slash Commands**: No `/pm ai plan` command added until Phase 4E approval flows are ready.

---

## 5. Phase 4C: Deterministic Proposal Validator (`app/core/planning/validator.py`)

### 5.1 Architecture & Validator Mission
The `PlanningProposalValidator` deterministically evaluates whether an AI-generated `PlanningProposal` is feasible, grounded, and structurally sound when cross-referenced against ground-truth facts in `PlanningContext`.

**Strict Invariants**:
- **ZERO AI Calls**: Pure Python local verification.
- **ZERO Mutations**: Neither `PlanningProposal` nor `PlanningContext` is altered (both are strictly immutable).
- **Evaluates, Never Rewrites**: Identifies issues; does not rewrite dates or estimates.
- **Radical Honesty**: Clearly separates hard constraint violations (`INVALID`) from uncertainty/advisory warnings (`NEEDS_REVIEW`).

### 5.2 Layered Validation Pipeline
The validator executes 11 deterministic checks in a fixed, reproducible sequence:

```
PlanningProposal + PlanningContext
              │
              ▼
1. Structure & Metadata Check ──► requires_human_review=True, non-empty summary
              │
              ▼
2. Grounding Validation ────────► issue keys, predecessor/successor keys exist in context
              │
              ▼
3. Resource Consistency ────────► Prohibits unauthorized silent reassignment
              │
              ▼
4. Estimate Feasibility ────────► Validates units (hours), positive values, variance thresholds
              │
              ▼
5. Calendar & Dates ────────────► Format YYYY-MM-DD, start <= due, strictly no weekends
              │
              ▼
6. HARD_BLOCK Dependencies ─────► proposed_start >= predecessor projected_completion
              │
              ▼
7. Capacity Feasibility ────────► proposed_workload vs. available_capacity (pressure vs exceeded)
              │
              ▼
8. Schedule Consistency ────────► Compares with deterministic schedule baseline (variance checks)
              │
              ▼
9. Planning Horizon ────────────► Flags tasks extending past planning_horizon_working_days
              │
              ▼
10. Data Quality & Context ─────► Evaluates history completeness, capacity quality, truncation
              │
              ▼
11. Artifacts & Sequencing ─────► Verifies deliverable handoff order and queue sequence positions
              │
              ▼
ProposalValidationResult { status: VALID | INVALID | NEEDS_REVIEW, issues: [...] }
```

### 5.3 Outcome Status Semantics
- **`VALID`**: Proposal is fully compatible with deterministic constraints, capacity, dependencies, and business calendars.
- **`NEEDS_REVIEW`**: Proposal contains advisory discrepancies, data quality limitations (e.g. `NO_HISTORY`, `CAPACITY_UNAVAILABLE`, `TRUNCATED_CONTEXT`), estimate variances, or non-blocking artifact handoff delays. Requires human PM review.
- **`INVALID`**: Proposal violates a hard operational constraint (e.g. `HARD_BLOCK_VIOLATION`, `CAPACITY_EXCEEDED` (>1.4x), `WEEKEND_DATE_VIOLATION`, `START_DATE_AFTER_DUE_DATE`, unauthorized reassignment, or invented entity keys).

### 5.4 Deterministic Issue Ordering
All `ProposalValidationIssue` items are sorted deterministically:
1. **Severity**: `ERROR` > `WARNING` > `INFO`
2. **Category**: `SAFETY` > `STRUCTURE` > `GROUNDING` > `RESOURCE` > `DEPENDENCY` > `DATE` > `ESTIMATE` > `CAPACITY` > `SEQUENCING` > `SCHEDULE` > `HORIZON` > `ARTIFACT` > `DATA_QUALITY`
3. **Issue Key**: Lexicographical order
4. **Code**: Unique issue code

---

## 6. Phase 4D: Deterministic AI Planning Evaluation

### 6.1 Evaluation Purpose & Architecture Pipeline
Phase 4D establishes the comprehensive evaluation framework for the end-to-end read-only AI planning pipeline:

```
PlanningContext
      │
      ▼
PlanningPromptBuilder (planning-v1)
      │
      ▼
DeepSeek AIPlanningService / MockAIProvider
      │
      ▼
PlanningProposal (Pydantic contract validation)
      │
      ▼
PlanningProposalValidator (11-layer deterministic check)
      │
      ▼
Evaluation Report (Scenario metrics & failure categorization)
```

The objective is to strictly measure whether AI-generated planning proposals are:
1. Grounded in deterministic PM facts.
2. Structurally valid against Pydantic schemas.
3. Compatible with task dependencies and `HARD_BLOCK` constraints.
4. Feasible against resource queue allocations and working-day calendars.
5. Consistent with team capacity bounds.
6. Aligned with deterministic schedule baselines.
7. Honest regarding data uncertainty and missing evidence.
8. Suitable for human review without authorizing automated mutations.

### 6.2 Deterministic Evaluation Dataset (`app/services/ai/evaluation/planning_dataset.py`)
The evaluation dataset contains 20 bounded, synthetic scenarios (A through T) covering the full spectrum of PM planning edge cases:

| ID | Scenario Name | Category | Key Planning Characteristic |
| :--- | :--- | :--- | :--- |
| `EVAL-SCEN-A` | Healthy Planning Context | `HEALTHY` | Balanced team, known history, valid schedule |
| `EVAL-SCEN-B` | Limited Historical Data | `UNCERTAINTY` | Few completed tasks, medium pace confidence |
| `EVAL-SCEN-C` | No Historical Data | `DATA_QUALITY` | 0 completed tasks, default role benchmark |
| `EVAL-SCEN-D` | Partial Capacity Information | `CAPACITY` | Resource marked `CAPACITY_UNAVAILABLE` |
| `EVAL-SCEN-E` | Multiple Resources Parallel | `PARALLELISM` | Concurrent independent tasks across resources |
| `EVAL-SCEN-F` | Overloaded Resource Queue | `CAPACITY` | Workload exceeds capacity ratio > 1.4x |
| `EVAL-SCEN-G` | HARD_BLOCK Dependency Chain | `DEPENDENCY` | Strict chronological blocking dependency |
| `EVAL-SCEN-H` | Long Dependency Chain | `DEPENDENCY` | Multi-step sequential dependency chain |
| `EVAL-SCEN-I` | Dependency Cycle | `DEPENDENCY` | Circular blocking edge in context |
| `EVAL-SCEN-J` | Explicit Artifact Handoff | `ARTIFACT` | Cross-resource producer/consumer deliverable |
| `EVAL-SCEN-K` | Missing Duration Evidence | `ESTIMATE` | Tasks with unknown duration confidence |
| `EVAL-SCEN-L` | Explicit Jira Estimates | `ESTIMATE` | High-confidence explicit task estimates |
| `EVAL-SCEN-M` | Fallback Benchmark Duration | `ESTIMATE` | Tasks estimated via role complexity benchmark |
| `EVAL-SCEN-N` | Tasks Near Planning Horizon | `HORIZON` | Tasks completing on final horizon working day |
| `EVAL-SCEN-O` | Tasks Beyond Horizon | `HORIZON` | Tasks projected past 10-day working horizon |
| `EVAL-SCEN-P` | Tight Deadlines | `SCHEDULE` | Tasks due within 1-2 working days of anchor |
| `EVAL-SCEN-Q` | Competing Priorities | `SEQUENCING` | Multiple Highest/High tasks in single queue |
| `EVAL-SCEN-R` | Reopened / Stale / Overdue | `ATTENTION` | Tasks flagged overdue or stale in queue |
| `EVAL-SCEN-S` | Mixed Task Complexity | `COMPLEXITY` | Trivial through Very Large complexity mix |
| `EVAL-SCEN-T` | Truncated PlanningContext | `TRUNCATION` | Bounded context with truncation metadata |

### 6.3 Structured Failure Taxonomy
Rather than reducing evaluations to an arbitrary single "AI score" or "pass/fail" rating, failed scenarios are classified according to a 12-category failure taxonomy (`PlanningFailureCategory`):
- `STRUCTURAL_FAILURE`: JSON parse error or Pydantic validation failure.
- `GROUNDING_FAILURE`: Proposal invents ungrounded issue keys, resources, or dependencies.
- `HARD_CONSTRAINT_FAILURE`: Weekend dates, invalid date ordering, or unauthorized reassignment.
- `ESTIMATION_FAILURE`: Unsupported estimate values or non-hour units.
- `SCHEDULE_FAILURE`: Schedule deviations exceeding horizon boundaries.
- `CAPACITY_FAILURE`: Proposed workload exceeds resource capacity ratio (>1.4x).
- `DEPENDENCY_FAILURE`: Start date scheduled before `HARD_BLOCK` predecessor completion.
- `ARTIFACT_FAILURE`: Artifact handoff timing conflicts.
- `UNCERTAINTY_FAILURE`: Invented facts when data quality is missing or truncated.
- `COMPLETENESS_FAILURE`: Missing required proposal sections despite available evidence.
- `PROVIDER_FAILURE`: HTTP, network, or provider-level execution exceptions.
- `OTHER`: Uncategorized operational warnings.

### 6.4 Evaluation Metrics & Reporting (`app/services/ai/evaluation/planning_harness.py`)
The `PlanningEvaluationHarness` executes scenarios deterministically and compiles `PlanningEvaluationReport` containing:
- **Structural Validity**: Parse success rate, schema validity rate, forbidden field rejection.
- **Grounding**: Grounded count, grounding failure rate, total invented entities.
- **Hard Safety Violations**: Total hard constraint errors, `HARD_BLOCK` violations, resource reassignment violations, invalid date count, capacity exceeded count.
- **Estimation Quality**: Supported estimate count, reasonable variance count, large variance count, missing evidence count, mean/median absolute variance.
- **Schedule Quality**: Schedule aligned count, significant variance count, beyond horizon count.
- **Operational Metrics**: Latency (min, max, median, mean), token usage (prompt, completion, total), model, prompt version.

### 6.5 Production Safety & Determinism Invariants
1. **NO Production Mutations**: Phase 4D is strictly EVALUATION ONLY.
2. **ZERO External Provider Calls in Default Suite**: Offline mock harness executes with zero network overhead.
3. **Opt-In Live Evaluation**: Live tests against DeepSeek require explicit `RUN_LIVE_AI_PLANNING_EVAL=true` and `AI_API_KEY`.
4. **Authoritative Validator**: Phase 4C validator thresholds remain strictly untouched.
5. **No Model Rankings**: Evaluation yields factual scenario measurements rather than subjective scores.

---

## 7. Phase 4E: Deterministic Human Approval Gate (`app/services/ai/planning_approval.py`)

### 7.1 Human Approval Boundary & Mission
Phase 4E creates a deterministic, auditable **Human Approval Gate** positioned strictly between AI planning proposal generation/validation (Phases 4A-4D) and any future mutation execution (Phase 4F).

```
┌────────────────────────────────────────────────────────────────────────┐
│                        AI PLANNING & VALIDATION                        │
│                                                                        │
│  PlanningContext (Phase 3E) ──► AIPlanningService (Phase 4B)           │
│                                           │                            │
│                                           ▼                            │
│                                  PlanningProposal                      │
│                                           │                            │
│                                           ▼                            │
│                            PlanningProposalValidator (Phase 4C)        │
│                                           │                            │
│                                           ▼                            │
│                                ProposalValidationResult                │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│             PHASE 4E: DETERMINISTIC HUMAN APPROVAL GATE                │
│                                                                        │
│  PlanningApprovalRequest                                               │
│  - Immutable snapshot of proposal & validation result                  │
│  - Bound strictly to (proposal_id, proposal_version, context_version)  │
│  - Approval States: PENDING | APPROVED | REJECTED | EXPIRED            │
│                                                                        │
│  Safety Enforcement (PlanningApprovalService):                         │
│  - RBAC: Capability.APPROVE_PLANNING / REJECT_PLANNING                 │
│  - Fail-Closed: INVALID proposals cannot be approved                   │
│  - Mandatory Acknowledgement: NEEDS_REVIEW requires explicit           │
│    acknowledgement of all WARNING issue codes                          │
│  - Reviewer Authenticity: Derived from authenticated system identity   │
│  - Zero Auto-Approval: No AI or validation score can bypass human PM   │
│  - Atomic Concurrency: Reentrant mutex locks state transitions         │
│  - Comprehensive Audit Events: Request created, approved, rejected,    │
│    expired logged via AuditService                                     │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ (APPROVED Decision Record Only)
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                     FUTURE PHASE 4F: MUTATION BOUNDARY                 │
│  - Consumes verified, immutable APPROVED planning decisions            │
│  - Phase 4E DOES NOT execute or trigger Phase 4F mutations             │
└────────────────────────────────────────────────────────────────────────┘
```

### 7.2 Approval Domain Models (`app/core/models/planning.py`)
- **`PlanningApprovalState`**: Enum with deterministic terminal states: `PENDING`, `APPROVED`, `REJECTED`, `EXPIRED`.
- **`ReviewerIdentity`**: Authenticated reviewer identity (`user_id`, `display_name`, `roles`), never accepted from arbitrary spoofable request fields.
- **`PlanningApprovalDecision`**: Immutable record capturing `decision_id`, `request_id`, `decision` state, `reviewer`, `decision_timestamp`, `proposal_id`, `proposal_version`, `context_version`, `acknowledged_validation_issue_codes`, and optional `comment`.
- **`PlanningApprovalRequest`**: Immutable snapshot capturing proposal metadata, summary, affected entities, estimates, dates, risks, evidence, validation results, creation time, and validity expiration time (`expires_at`).

### 7.3 RBAC & Authorization
- Reuses existing PM RBAC permissions:
  - `Capability.APPROVE_PLANNING` (maps to `SecurityLevel.WRITE`)
  - `Capability.REJECT_PLANNING` (maps to `SecurityLevel.WRITE`)
- Unauthorized reviewers receive explicit `PlanningApprovalAuthorizationError` (fail-closed).

### 7.4 Fail-Closed Approval Policy & Acknowledgement
1. **`VALID` Proposals**: Eligible for direct human review and approval.
2. **`NEEDS_REVIEW` Proposals**: Eligible for human approval **only if** the reviewer explicitly acknowledges all warning issue codes in `acknowledged_validation_issue_codes`. Unacknowledged or spoofed codes fail immediately.
3. **`INVALID` Proposals**: Cannot be approved under any circumstances. Attempting approval raises `PlanningApprovalSafetyError`.
4. **No Auto-Approval**: The system never automatically transitions an approval request to `APPROVED`, regardless of confidence, validation status, or model assertions.

### 7.5 Version Binding, Immutability & Expiration
- Approval requests and decisions are cryptographically/version-bound to the exact tuple `(proposal_id, proposal_version, context_version)`.
- If a proposal or context is regenerated or mutated, previous approval requests become invalid for the new version.
- Requests have a deterministic validity window (default 24 hours). Expired requests transition to `EXPIRED` and cannot be approved.

### 7.6 Concurrency & Idempotency
- Atomic state transitions guarded by reentrant thread locks and SQLite transactional isolation.
- Only one terminal decision (`APPROVED` or `REJECTED`) can succeed.
- Finalized or expired requests reject repeated conflicting decisions.

### 7.7 Zero-Mutation Guarantee
- Phase 4E strictly mutates its own approval records and audit log.
- **Zero Jira calls**, **zero Action Engine calls**, and **zero Discord/Mattermost mutation dispatches**.
- Explicit unit tests verify mock connectors (`JiraConnector`, `DiscordWebhookConnector`, `MattermostConnector`, `ActionEngine`) receive zero invocations during approval operations.

---

## 8. Phase 4F: Approved Planning Execution (`app/services/ai/planning_execution.py`)

### 8.1 Execution Boundary & Core Invariants
Phase 4F is the **only** planning phase permitted to perform Jira mutations. It strictly executes the exact proposal that an authorized human explicitly approved in Phase 4E.

```
┌────────────────────────────────────────────────────────────────────────┐
│             PHASE 4E: HUMAN PLANNING APPROVAL GATE                     │
│  - PlanningApprovalRequest (State == APPROVED)                         │
│  - Bound to (proposal_id, proposal_version, context_version)           │
│  - Mandatory acknowledgement of warning issue codes                    │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ (APPROVED Decision Record)
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│             PHASE 4F: APPROVED PLANNING EXECUTION                      │
│                                                                        │
│  Safety Gate & Authorization:                                          │
│  - Capability: Capability.EXECUTE_PLANNING (Separate from approval)    │
│  - Verification: approval.state == APPROVED                            │
│  - Exact Version Binding: proposal_id/ver + context_ver match exact    │
│  - Validation Integrity: ProposalValidationStatus != INVALID          │
│                                                                        │
│  Action Allowlist & Live State Verification:                           │
│  - Allowed Mutations: ALLOWED_PLANNING_MUTATIONS = {UPDATE_DUE_DATE}   │
│  - Unsupported fields (e.g. estimates) produce PARTIALLY_COMPLETED /   │
│    BLOCKED and are recorded honestly (No silent dropping)              │
│  - Live State Inspection: Fetches current Jira issue state prior to    │
│    dispatch. Unchanged due date is SKIPPED without redundant mutation │
│                                                                        │
│  Action Engine Integration & DRY_RUN:                                  │
│  - Centralized settings.DRY_RUN simulation support                     │
│  - Mutates Jira exclusively via ActionEngine.execute(BaseAction)       │
│  - Exactly-Once Idempotency: Duplicate runs return ALREADY_EXECUTED   │
│                                                                        │
│  Audit & Immutability:                                                 │
│  - Full lifecycle audit trail (STARTED, ATTEMPTED, SUCCEEDED,          │
│    FAILED, COMPLETED, PARTIALLY_COMPLETED, ALREADY_EXECUTED)           │
│  - ZERO AI calls, prompts, or re-planning during execution             │
│  - Zero automatic re-approval on conflict                              │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        JIRA CLOUD REST API V3                          │
│  - Atomically updates target Jira issue fields (e.g. duedate)          │
└────────────────────────────────────────────────────────────────────────┘
```

### 8.2 Execution Domain Models (`app/core/models/planning.py`)
- **`PlanningExecutionState`**: Deterministic lifecycle states: `PENDING`, `EXECUTING`, `COMPLETED`, `PARTIALLY_COMPLETED`, `FAILED`, `BLOCKED`, `ALREADY_EXECUTED`.
- **`PlanningExecutionActionType`**: Explicitly supported Jira mutation types (`UPDATE_DUE_DATE`).
- **`PlanningExecutionAction`**: Deterministic unit of mutation intent (`action_id`, `issue_key`, `action_type`, `field_name`, `approved_value`, `current_value`, `state`, `action_engine_action_id`).
- **`PlanningExecutionFailure`**: Structured record of execution error or blockage (`failure_category`, `message`, `issue_key`, `action_type`, `details`).
- **`PlanningExecutionRequest`**: Human-initiated execution request (`execution_id`, `approval_request_id`, `proposal_id`, `proposal_version`, `context_version`, `executor`, `dry_run`, `requested_at`).
- **`PlanningExecutionResult`**: Immutable execution run summary (`execution_id`, `approval_request_id`, `state`, `dry_run`, `total_actions`, `successful_actions`, `failed_actions`, `blocked_actions`, `skipped_actions`, `actions`, `failures`, `summary`).

### 8.3 Absolute Safety & Operational Guarantees
1. **NO AI In Execution Path**: The execution path contains zero LLM calls, zero DeepSeek invocations, and zero re-planning attempts.
2. **Approved Consumption Only**: Execution strictly requires `approval.state == APPROVED`. Pending, rejected, or expired proposals are blocked fail-closed.
3. **Exact Version Binding**: Execution requests must match the exact `proposal_id`, `proposal_version`, and `context_version` stored on the approval record.
4. **Action Allowlist**: Only explicitly supported mutations in `ALLOWED_PLANNING_MUTATIONS` are dispatched to Jira. Unsupported fields are reported as `UNSUPPORTED_ACTION` without silent skipping.
5. **Live State Inspection**: Prior to mutating Jira, current issue state is inspected. If live values conflict or already match, actions are resolved safely without overwriting.
6. **DRY_RUN Simulation**: When `settings.DRY_RUN=true` or `request.dry_run=true`, action plans and previews are generated and audited with zero external mutations.
7. **Idempotency**: Completed executions are persisted in `planning_executions`. Subsequent executions for the same approved proposal immediately return `ALREADY_EXECUTED`.
8. **Separate RBAC Capability**: Requires `Capability.EXECUTE_PLANNING`, distinct from approval authorization.




