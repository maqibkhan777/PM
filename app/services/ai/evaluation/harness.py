"""Evaluation harness executing scenarios, collecting performance metrics, and checking safety."""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.services.ai.evaluation.grounding import GroundingResult, GroundingValidator
from app.services.ai.evaluation.scenarios import EVALUATION_SCENARIOS, ScenarioFixture
from app.services.ai.models import PMAttentionAnalysis
from app.services.ai.providers.deepseek import (
    AI_PROMPT_VERSION,
    DeepSeekAIProvider,
    DeepSeekProviderError,
)


@dataclass
class ScenarioEvaluationResult:
    scenario_id: str
    scenario_name: str
    success: bool
    latency_seconds: float
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    schema_valid: bool = False
    grounding: Optional[GroundingResult] = None
    error_message: Optional[str] = None
    analysis: Optional[PMAttentionAnalysis] = None


@dataclass
class EvaluationSummary:
    total_scenarios: int
    successful_calls: int
    failed_calls: int
    schema_valid_count: int
    grounded_count: int
    min_latency: float
    max_latency: float
    median_latency: float
    avg_latency: float
    total_prompt_tokens: int
    total_completion_tokens: int
    total_tokens: int
    prompt_version: str
    model: str
    scenario_results: List[ScenarioEvaluationResult] = field(default_factory=list)


class EvaluationHarness:
    """Executes a suite of scenario fixtures against a configured DeepSeekAIProvider."""

    def __init__(self, provider: DeepSeekAIProvider):
        self.provider = provider

    async def evaluate_scenario(self, scenario: ScenarioFixture) -> ScenarioEvaluationResult:
        t0 = time.monotonic()
        try:
            analysis = await self.provider.analyze_attention(scenario.context)
            latency = round(time.monotonic() - t0, 3)

            # Validate grounding
            grounding_result = GroundingValidator.validate(analysis, scenario)

            # Extract token usage if available
            usage = getattr(self.provider, "last_usage", {}) or {}
            pt = usage.get("prompt_tokens")
            ct = usage.get("completion_tokens")
            tt = usage.get("total_tokens") or ((pt or 0) + (ct or 0) if pt or ct else None)

            return ScenarioEvaluationResult(
                scenario_id=scenario.scenario_id,
                scenario_name=scenario.name,
                success=True,
                latency_seconds=latency,
                prompt_tokens=pt,
                completion_tokens=ct,
                total_tokens=tt,
                schema_valid=True,
                grounding=grounding_result,
                analysis=analysis,
            )
        except Exception as e:
            latency = round(time.monotonic() - t0, 3)
            return ScenarioEvaluationResult(
                scenario_id=scenario.scenario_id,
                scenario_name=scenario.name,
                success=False,
                latency_seconds=latency,
                schema_valid=False,
                grounding=None,
                error_message=str(e),
            )

    async def run_evaluation(
        self,
        scenarios: Optional[List[ScenarioFixture]] = None,
    ) -> EvaluationSummary:
        scenario_list = scenarios or EVALUATION_SCENARIOS
        results: List[ScenarioEvaluationResult] = []

        for scen in scenario_list:
            res = await self.evaluate_scenario(scen)
            results.append(res)

        successful = [r for r in results if r.success]
        latencies = [r.latency_seconds for r in results]
        sorted_lat = sorted(latencies) if latencies else [0.0]

        n = len(sorted_lat)
        if n % 2 == 1:
            median_lat = sorted_lat[n // 2]
        else:
            median_lat = round((sorted_lat[n // 2 - 1] + sorted_lat[n // 2]) / 2.0, 3)

        avg_lat = round(sum(latencies) / max(len(latencies), 1), 3)

        return EvaluationSummary(
            total_scenarios=len(scenario_list),
            successful_calls=len(successful),
            failed_calls=len(results) - len(successful),
            schema_valid_count=sum(1 for r in results if r.schema_valid),
            grounded_count=sum(1 for r in results if r.grounding and r.grounding.is_grounded),
            min_latency=min(latencies) if latencies else 0.0,
            max_latency=max(latencies) if latencies else 0.0,
            median_latency=median_lat,
            avg_latency=avg_lat,
            total_prompt_tokens=sum(r.prompt_tokens or 0 for r in results),
            total_completion_tokens=sum(r.completion_tokens or 0 for r in results),
            total_tokens=sum(r.total_tokens or 0 for r in results),
            prompt_version=AI_PROMPT_VERSION,
            model=self.provider.model,
            scenario_results=results,
        )
