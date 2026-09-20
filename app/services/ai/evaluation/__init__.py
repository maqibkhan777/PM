"""Controlled evaluation infrastructure for DeepSeek AI Provider (Phase 2E).

Exposes:
- Synthetic evaluation scenarios covering PM attention cases
- Deterministic factual grounding checks
- Evaluation runner tracking latency, token usage, schema validity, and grounding
"""

from app.services.ai.evaluation.scenarios import EVALUATION_SCENARIOS, ScenarioFixture
from app.services.ai.evaluation.grounding import GroundingValidator, GroundingResult
from app.services.ai.evaluation.harness import EvaluationHarness, ScenarioEvaluationResult

__all__ = [
    "EVALUATION_SCENARIOS",
    "ScenarioFixture",
    "GroundingValidator",
    "GroundingResult",
    "EvaluationHarness",
    "ScenarioEvaluationResult",
]
