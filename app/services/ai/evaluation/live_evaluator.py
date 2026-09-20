"""Standalone opt-in live evaluation script for DeepSeek Real-Provider testing.

SAFETY ENFORCEMENT:
1. RUN_LIVE_AI_EVAL MUST be explicitly set to 'true'.
2. AI_ENABLED MUST be explicitly set to 'true'.
3. AI_PROVIDER MUST be 'deepseek'.
4. AI_API_KEY MUST be provided via environment.
5. All secrets are scrubbed; no API keys or raw prompts are printed or saved.
6. Does not call Jira, Discord, Action Engine, or make any external mutations.
"""

import asyncio
import os
import sys
from app.services.ai.config import resolve_ai_provider
from app.services.ai.evaluation.harness import EvaluationHarness
from app.services.ai.evaluation.scenarios import EVALUATION_SCENARIOS


def main():
    # 1. Gate check: RUN_LIVE_AI_EVAL
    if os.getenv("RUN_LIVE_AI_EVAL", "").strip().lower() != "true":
        print(
            "ABORTED: Live evaluation is opt-in only. "
            "To execute, set environment variable RUN_LIVE_AI_EVAL=true."
        )
        sys.exit(0)

    # 2. Gate check: AI_ENABLED
    if os.getenv("AI_ENABLED", "").strip().lower() != "true":
        print(
            "ABORTED: AI_ENABLED is false. "
            "To execute live evaluation, set AI_ENABLED=true."
        )
        sys.exit(0)

    # 3. Gate check: AI_PROVIDER
    provider_name = os.getenv("AI_PROVIDER", "").strip().lower()
    if provider_name != "deepseek":
        print(
            f"ABORTED: AI_PROVIDER must be 'deepseek', got '{provider_name}'."
        )
        sys.exit(0)

    # 4. Gate check: AI_API_KEY
    api_key = os.getenv("AI_API_KEY", "").strip()
    if not api_key:
        print(
            "ABORTED: AI_API_KEY environment variable is required for live DeepSeek evaluation."
        )
        sys.exit(0)

    print("=================================================================")
    print("🚀 Starting Controlled DeepSeek Real-Provider Evaluation (Phase 2E)")
    print("=================================================================")
    print(f"Model: {os.getenv('AI_MODEL', 'deepseek-chat')}")
    print(f"Scenarios: {len(EVALUATION_SCENARIOS)}")
    print("API Key: [REDACTED — Validated Present in Environment]")
    print("Safety: Read-only synthetic evaluation; Zero Jira/Discord mutations.")
    print("-----------------------------------------------------------------")

    provider = resolve_ai_provider()
    harness = EvaluationHarness(provider)

    summary = asyncio.run(harness.run_evaluation(EVALUATION_SCENARIOS))

    print("\n=================================================================")
    print("📊 Evaluation Summary Results")
    print("=================================================================")
    print(f"Total Scenarios Evaluated: {summary.total_scenarios}")
    print(f"Successful Calls:          {summary.successful_calls}")
    print(f"Failed Calls:              {summary.failed_calls}")
    print(f"Schema Valid:              {summary.schema_valid_count}/{summary.total_scenarios}")
    print(f"Grounded Responses:        {summary.grounded_count}/{summary.total_scenarios}")
    print(f"Latency (Min / Avg / Max): {summary.min_latency}s / {summary.avg_latency}s / {summary.max_latency}s")
    print(f"Median Latency:            {summary.median_latency}s")
    print("-----------------------------------------------------------------")

    for res in summary.scenario_results:
        status_icon = "✅" if res.success and (res.grounding and res.grounding.is_grounded) else "⚠️"
        print(f"{status_icon} [{res.scenario_id}] {res.scenario_name} (latency: {res.latency_seconds}s)")
        if res.grounding and res.grounding.violations:
            for v in res.grounding.violations:
                print(f"   - Violation: {v}")
        if res.error_message:
            print(f"   - Error: {res.error_message}")

    print("=================================================================\n")


if __name__ == "__main__":
    main()
