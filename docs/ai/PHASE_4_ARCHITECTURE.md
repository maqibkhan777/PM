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

