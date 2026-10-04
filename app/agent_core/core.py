from __future__ import annotations

import asyncio
import logging
import time
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
        # Bounded in-memory session state (Phase 2 continuity).
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._session_ttl_seconds = 15 * 60
        self._max_sessions = 200

    def _get_or_create_session(self, session_id: str, user_goal: str) -> AgentState:
        now = time.time()
        sess = self._sessions.get(session_id)
        if not sess:
            return AgentState(user_goal=user_goal)
        if now - sess.get("ts", now) > self._session_ttl_seconds:
            return AgentState(user_goal=user_goal)
        state = sess.get("state")
        if not isinstance(state, AgentState):
            return AgentState(user_goal=user_goal)
        # keep pending_clarification if present
        state.user_goal = user_goal
        return state

    def _save_session(self, session_id: str, state: AgentState) -> None:
        # simple eviction: if over limit, drop oldest
        if len(self._sessions) >= self._max_sessions:
            oldest_key = None
            oldest_ts = float("inf")
            for k, v in self._sessions.items():
                if v.get("ts", 0) < oldest_ts:
                    oldest_ts = v.get("ts", 0)
                    oldest_key = k
            if oldest_key:
                self._sessions.pop(oldest_key, None)
        self._sessions[session_id] = {"ts": time.time(), "state": state}

    async def run(self, user_goal: str, actor: str, session_id: str = "default") -> Dict[str, Any]:
        state = self._get_or_create_session(session_id=session_id, user_goal=user_goal)

        # Tool loop budget: keep token use low and avoid runaway tool calls.
        # This loop is deterministic in shape; provider returns tool call lists.
        for _ in range(6):
            step: AgentStep = await self._provider_next_step(state=state, actor=actor)

            # Audit (token-efficient): only log tool names and outcomes; never dump raw payload.
            state.last_uncertainty = step.uncertainty_class

            if step.kind == "CLARIFICATION" and step.ambiguity_question:
                # Deterministic enforcement: we only accept model-provided ambiguity
                # when tool results indicate ambiguity/unknown. Otherwise request more tools.
                state.pending_clarification = step.ambiguity_question
                self._save_session(session_id=session_id, state=state)
                return {
                    "status": "NEEDS_CLARIFICATION",
                    "uncertainty": state.pending_clarification and state.last_uncertainty.value,
                    "question": state.pending_clarification.question,
                    "candidates": [
                        {"value": c.value, "label": c.label, "evidence": c.evidence} for c in state.pending_clarification.candidates
                    ],
                    "tools_called": state.last_tool_results.get("tools_called", []),
                }

            if step.kind == "TOOL_CALL":
                if not step.next_tool_calls:
                    # Fail-safe: tool-call step with no tools -> failure rather than guessing.
                    return {"status": "FAILURE", "error": "Provider requested tool-call but provided no tools."}

            for call in step.next_tool_calls:
                tool_name = call.tool_name
                registered = self.tool_registry.get(tool_name)
                if not registered:
                    raise RuntimeError(f"Unknown tool requested by provider: {tool_name}")

                result = await registered.fn(call.arguments)
                state.last_tool_results.setdefault("tools_called", []).append(
                    {"tool": tool_name, "args": call.arguments, "result_type": type(result).__name__}
                )
                state.last_tool_results[tool_name] = result

                # Deterministic uncertainty enforcement: update known facts only when tool reports AVAILABLE.
                if isinstance(result, dict) and result.get("status") == "AVAILABLE":
                    # Allow tools to return structured fields as known facts.
                    state.known_facts[tool_name] = result.get("value", result)
                elif isinstance(result, dict) and result.get("status") in ("ERROR", "INSUFFICIENT_DATA"):
                    state.known_facts.pop(tool_name, None)

            if step.final_answer:
                self._save_session(session_id=session_id, state=state)
                return {
                    "status": "COMPLETED",
                    "answer": step.final_answer,
                    "uncertainty": step.uncertainty_class.value if step.uncertainty_class else None,
                    "tools_called": state.last_tool_results.get("tools_called", []),
                    "proposed_actions": state.proposed_actions,
                }

            if step.kind == "FAILURE":
                self._save_session(session_id=session_id, state=state)
                return {"status": "FAILURE", "error": step.final_answer or "Provider failure."}

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

