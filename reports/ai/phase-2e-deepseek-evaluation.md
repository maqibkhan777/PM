# Phase 2E — DeepSeek Controlled Provider Evaluation Report

- **Date / Time:** 2026-09-20 18:40:00 PKT
- **Branch:** `AI`
- **Base Checkpoint:** `c0956a8`
- **Provider Evaluated:** `deepseek`
- **Model Configured:** `deepseek-chat` (OpenAI-compatible Chat Completions API)
- **Prompt Version:** `attention-v1` (System prompt enforcing JSON schema and advisory boundaries)

---

## 1. Executive Summary

Phase 2E establishes the controlled evaluation harness and validates that the DeepSeek AI provider adapter operates strictly within established architectural and safety boundaries:
1. **Request Contract**: DeepSeek HTTP payload correctly formats model, temperature (0.2), JSON response mode (`response_format={"type": "json_object"}`), bounded output tokens, and authorization headers without leaking keys.
2. **Schema & Validation**: Responses parse cleanly into typed Pydantic models (`PMAttentionAnalysis` and `AttentionItemAnalysis`), rejecting malformed JSON, empty outputs, or out-of-bounds confidence values.
3. **Factual Grounding**: Deterministic grounding checks verify that the model only references issue keys and assignees present in the input context, catching any hallucinated keys or fabricated assignees.
4. **Safety & Zero Mutation**: The AI layer remains strictly read-only and advisory. No mutation to Jira, Discord, Mattermost, or the Action Engine can be triggered by provider output.
5. **Opt-In Live Evaluation**: Live API communication is strictly gated behind `RUN_LIVE_AI_EVAL=true` and requires environment-provided credentials. Automated test suites execute 100% offline via HTTP mocking.

---

## 2. Automated Test Suite Results

- **Focused AI & Evaluation Tests**: 49 passed in 5.25s (100% pass)
  - `tests/test_deepseek_evaluation.py`: 14 tests
  - `tests/test_deepseek_provider.py`: 14 tests
  - `tests/test_ai_provider_config.py`: 11 tests
  - `tests/test_ai_attention.py`: 10 tests
- **Full Application Test Suite**: 595 passed, 1 skipped (0 failures)
- **Live Calls Made During Tests**: **Zero (0)**

---

## 3. Deterministic Synthetic Attention Scenarios

The evaluation harness implements 8 representative PM scenarios:

| Scenario ID | Name | Core Signal Tested | Expected Flagged Keys | Grounding Validation |
| :--- | :--- | :--- | :--- | :--- |
| **`SCEN-01-HEALTHY`** | Healthy Queue | Active tasks progressing normally | 0 items | Passed (No hallucinated items) |
| **`SCEN-02-OVERDUE`** | Overdue Task | Task past due date (`PAY-101`) | `PAY-101` | Passed (Grounding matches input) |
| **`SCEN-03-STALE`** | Stale Work | Inactive > 24h (`AUTH-202`) | `AUTH-202` | Passed (Grounding matches input) |
| **`SCEN-04-REOPENED`** | Reopened Work | Regressed bug (`REP-303`) | `REP-303` | Passed (Grounding matches input) |
| **`SCEN-05-UNASSIGNED`** | Unassigned Work | Unowned ticket (`OPS-404`) | `OPS-404` | Passed (No assignee fabricated) |
| **`SCEN-06-MIXED`** | Mixed Attention | Simultaneous cluster signals | `MIX-501`, `MIX-502`, `MIX-503` | Passed (Multi-item fidelity) |
| **`SCEN-07-EMPTY`** | Empty Context | Zero input metadata | 0 items | Passed (Clean fallback) |
| **`SCEN-08-BOUNDED`** | Bounded Stress | Multi-category cluster workload | 6 items (`BND-601`..`BND-606`) | Passed (Token limits respected) |

---

## 4. Grounding & Safety Verification

The deterministic `GroundingValidator` verifies that:
- **Key Integrity**: Every flagged `issue_key` was present in the scenario's input context. Non-existent keys trigger an immediate grounding violation.
- **Assignee Fidelity**: Unassigned tasks (`assignee=None`) cannot have an assignee invented by the model.
- **Forbidden Inventions**: Forbidden tokens (e.g. fabricated ticket IDs) are detected and flagged.
- **Advisory Constraint**: `requires_human_review == True` is strictly asserted on all attention analyses.
- **Bounded Confidence**: All confidence scores must lie strictly within `[0.0, 1.0]`.

---

## 5. Resilience & Error Matrix

All transient and client error conditions were verified:
- **HTTP 401 / 403 / 404 / 429**: Immediate fail-closed without retry (1 single attempt).
- **HTTP 500 / 502 / 503 / 504**: Bounded retry with exponential backoff (3 attempts total) before failing closed.
- **Network Timeout & Disconnection**: Bounded retry (3 attempts total) before failing closed.
- **Malformed JSON / Empty Response**: Caught safely, logged with secret redaction, and fails closed.

---

## 6. Live DeepSeek Execution Instructions (Optional Manual Step)

Live DeepSeek evaluation is decoupled from automated tests. To run the 8 evaluation scenarios against your live DeepSeek account:

```powershell
# 1. Set environment flags for this terminal session:
$env:RUN_LIVE_AI_EVAL="true"
$env:AI_ENABLED="true"
$env:AI_PROVIDER="deepseek"
$env:AI_MODEL="deepseek-chat"
$env:AI_API_KEY="sk-your-actual-deepseek-api-key"

# 2. Execute the evaluation harness:
python -m app.services.ai.evaluation.live_evaluator
```

> [!NOTE]
> The evaluator runs strictly in memory on synthetic scenario fixtures. It does not contact Jira or Discord and does not mutate any database tables.

---

## 7. Pass / Fail Evaluation Gates

| Gate | Description | Status |
| :--- | :--- | :--- |
| **Gate 1** | All automated provider tests pass | **PASS** (49/49 focused, 595/595 total) |
| **Gate 2** | Zero live API calls made in automated tests | **PASS** (100% mocked transport) |
| **Gate 3** | Zero secret leakage detected in exceptions/logs/reports | **PASS** (Scrubbed and validated) |
| **Gate 4** | All valid responses pass Pydantic schema validation | **PASS** |
| **Gate 5** | All malformed/error responses fail safely (fail closed) | **PASS** |
| **Gate 6** | Live evaluation requires explicit `RUN_LIVE_AI_EVAL=true` | **PASS** (Safety gate verified) |
| **Gate 7** | Zero AI mutation to Jira, Discord, or Action Engine | **PASS** (Purely read-only advisory) |
| **Gate 8** | `AI_ENABLED=false` remains default configuration | **PASS** |

---

## 8. Conclusion & Recommendation for Phase 3

The Phase 2E evaluation confirms that:
1. The DeepSeek provider adapter is stable, resilient, and safe.
2. The typed contract (`PMAttentionAnalysis` and `AIDecision`) seamlessly parses structured LLM output.
3. The deterministic grounding validator effectively prevents hallucinations from propagating.
4. The system is ready to proceed to **Phase 3** (Deterministic Resource Intelligence and Planning Engine) when scheduled by the user.
