from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from app.agent_core.agent_models import AgentState, AgentStep, Candidate, ToolCall, ToolSpec
from app.agent_core.tooling import ToolRegistry
from app.agent_core.uncertainty import UncertaintyModel
from app.services.ai.provider import AIProvider
from app.services.ai.agent_provider import AgentProvider

logger = logging.getLogger(__name__)


class AgentCore:
    """
    Tool-driven, uncertainty-aware orchestrator.

    Contract:
    - Uses provider abstraction for *deciding next tools* and for *final responses*.
    - Tool registry executes deterministic reads only.
    - No secrets are logged or persisted.
    """

    def __init__(
        self,
        provider: AIProvider,
        tool_registry: ToolRegistry,
        agent_provider: Optional[AgentProvider] = None,
    ) -> None:
        self.provider = provider
        self.tool_registry = tool_registry
        self.agent_provider = agent_provider

    async def run(self, user_goal: str, actor: str) -> Dict[str, Any]:
        state = AgentState(user_goal=user_goal)

        # Tool loop budget: keep token use low and avoid runaway tool calls.
        # This loop is deterministic in shape; provider returns tool call lists.
        for _ in range(6):
            step: AgentStep = await self._provider_next_step(state=state, actor=actor)

            # Audit (token-efficient): only log tool names and outcomes; never dump raw payload.
            state.last_uncertainty = step.uncertainty_class

            if step.ambiguity_question:
                state.pending_clarification = step.ambiguity_question
                return {
                    "status": "NEEDS_CLARIFICATION",
                    "uncertainty": step.uncertainty_class.value if step.uncertainty_class else None,
                    "question": step.ambiguity_question.question,
                    "candidates": [
                        {"value": c.value, "label": c.label, "evidence": c.evidence} for c in step.ambiguity_question.candidates
                    ],
                    "tools_called": state.last_tool_results.get("tools_called", []),
                }

            for call in step.next_tool_calls:
                tool_name = call.tool_name
                registered = self.tool_registry.get(tool_name)
                if not registered:
                    raise RuntimeError(f"Unknown tool requested by provider: {tool_name}")

                result = await registered.fn(call.arguments)
                state.last_tool_results.setdefault("tools_called", []).append(
                    {"tool": tool_name, "args": call.arguments, "result_type": type(result).__name__}
                )
                # Store only minimal results to keep prompt small.
                state.last_tool_results[tool_name] = result
                state.known_facts.update(result if isinstance(result, dict) else {})

            if step.final_answer:
                return {
                    "status": "COMPLETED",
                    "answer": step.final_answer,
                    "uncertainty": step.uncertainty_class.value if step.uncertainty_class else None,
                    "tools_called": state.last_tool_results.get("tools_called", []),
                    "proposed_actions": state.proposed_actions,
                }

        return {
            "status": "FAILED",
            "error": "Agent core tool loop exceeded budget",
        }

    async def _provider_next_step(self, state: AgentState, actor: str) -> AgentStep:
        """
        Delegates the decision of next tool calls to a provider-compatible agent interface.

        Token-efficient strategy:
        - Provide only goal + summarized tool results + uncertainty status.
        """
        agent_provider = self.agent_provider
        if agent_provider is not None:
            return await agent_provider.next_step(user_goal=state.user_goal, actor=actor, state=state, tools=self.tool_registry.list_specs())

        # Fallback: if provider doesn't support tool calling, force completion error.
        raise RuntimeError("No AgentProvider configured for tool-calling")

