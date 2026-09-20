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
    from app.config.settings import settings

    # 1. Gate check: RUN_LIVE_AI_EVAL opt-in flag
    if os.getenv("RUN_LIVE_AI_EVAL", "").strip().lower() != "true":
        print(
            "ABORTED: Live evaluation is opt-in only. "
            "To execute, set environment variable RUN_LIVE_AI_EVAL=true."
        )
        sys.exit(0)

    # 2. Gate check: AI_ENABLED (from environment or .env via settings)
    ai_enabled = getattr(settings, "AI_ENABLED", False) or os.getenv("AI_ENABLED", "").strip().lower() == "true"
    if not ai_enabled:
        print(
            "ABORTED: AI_ENABLED is false. "
            "To execute live evaluation, ensure AI_ENABLED=true in .env or environment."
        )
        sys.exit(0)

    # 3. Gate check: AI_PROVIDER
    provider_name = (os.getenv("AI_PROVIDER") or getattr(settings, "AI_PROVIDER", "deepseek")).strip().lower()
    if provider_name != "deepseek":
        print(
            f"ABORTED: AI_PROVIDER must be 'deepseek', got '{provider_name}'."
        )
        sys.exit(0)

    # 4. Gate check: AI_API_KEY
    api_key = os.getenv("AI_API_KEY") or getattr(settings, "AI_API_KEY", None)
    if not api_key or not str(api_key).strip():
        print(
            "ABORTED: AI_API_KEY is required for live DeepSeek evaluation (configured via .env or environment)."
        )
        sys.exit(0)

    model_name = os.getenv("AI_MODEL") or getattr(settings, "AI_MODEL", None) or "deepseek-chat"

    print("=================================================================")
    print("🚀 Starting Controlled DeepSeek Real-Provider Evaluation (Phase 2E)")
    print("=================================================================")
    print(f"Model: {model_name}")
    print(f"Scenarios: {len(EVALUATION_SCENARIOS)}")
    print("API Key: [REDACTED — Validated Present in Local Configuration]")
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
    print(f"Tokens (Prompt / Comp / Tot): {summary.total_prompt_tokens} / {summary.total_completion_tokens} / {summary.total_tokens}")
    print("-----------------------------------------------------------------")

    for res in summary.scenario_results:
        status_icon = "✅" if res.success and (res.grounding and res.grounding.is_grounded) else "⚠️"
        tokens_info = f"tokens: {res.total_tokens} (p:{res.prompt_tokens}, c:{res.completion_tokens})" if res.total_tokens else "tokens: N/A"
        print(f"{status_icon} [{res.scenario_id}] {res.scenario_name} (latency: {res.latency_seconds}s | {tokens_info})")
        if res.grounding and res.grounding.violations:
            for v in res.grounding.violations:
                print(f"   - Violation: {v}")
        if res.error_message:
            print(f"   - Error: {res.error_message}")

    print("=================================================================\n")


if __name__ == "__main__":
    main()
