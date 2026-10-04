from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from app.agent_core.agent_models import AgentState, AgentStep, Candidate, ToolCall, ToolSpec, ToolResultStatus, AmbiguityQuestion
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
        session_store: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> None:
        self.provider = provider
        self.tool_registry = tool_registry
        self.agent_provider = agent_provider
        # Bounded in-memory session state (Phase 2 continuity).
        self._sessions = session_store if session_store is not None else {}
        self._session_ttl_seconds = 15 * 60
        self._max_sessions = 200

    def _get_or_create_session(self, session_id: str, current_input: str) -> AgentState:
        now = time.time()
        sess = self._sessions.get(session_id)
        if not sess:
            return AgentState(user_goal=current_input, current_input=current_input)
        if now - sess.get("ts", now) > self._session_ttl_seconds:
            return AgentState(user_goal=current_input, current_input=current_input)
        state = sess.get("state")
        if not isinstance(state, AgentState):
            return AgentState(user_goal=current_input, current_input=current_input)
        # keep original goal, only update current input
        state.current_input = current_input
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
        state = self._get_or_create_session(session_id=session_id, current_input=user_goal)
        state.current_input = user_goal

        # Tool loop budget: keep token use low and avoid runaway tool calls.
        # This loop is deterministic in shape; provider returns tool call lists.
        for _ in range(6):
            # If the previous turn ended in a clarification and the user replied with a narrow answer,
            # capture that reply and resume the original goal.
            if state.pending_clarification:
                self._apply_clarification_response(state)

            try:
                step: AgentStep = await self._provider_next_step(state=state, actor=actor)
            except Exception as e:
                self._save_session(session_id=session_id, state=state)
                return {"status": "FAILURE", "error": f"Provider failure: {e}"}
            if not isinstance(step, AgentStep):
                self._save_session(session_id=session_id, state=state)
                return {"status": "FAILURE", "error": "Provider returned malformed agent step."}

            # Audit (token-efficient): only log tool names and outcomes; never dump raw payload.
            state.last_uncertainty = step.uncertainty_class

            if step.kind == "CLARIFICATION" and step.ambiguity_question:
                if self._clarification_is_supported(state):
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
                state.context["clarification_rejected"] = {
                    "question": step.ambiguity_question.question,
                    "reason": "clarification_not_supported_by_tool_history",
                }
                continue

            if step.kind == "TOOL_CALL":
                if not step.next_tool_calls:
                    # Fail-safe: tool-call step with no tools -> failure rather than guessing.
                    return {"status": "FAILURE", "error": "Provider requested tool-call but provided no tools."}

            for call in step.next_tool_calls:
                tool_name = call.tool_name
                registered = self.tool_registry.get(tool_name)
                if not registered:
                    self._save_session(session_id=session_id, state=state)
                    return {"status": "FAILURE", "error": f"Unknown tool requested by provider: {tool_name}"}
                if not registered.spec.read_only or registered.spec.mutates_external_system or registered.spec.requires_approval:
                    self._save_session(session_id=session_id, state=state)
                    return {"status": "FAILURE", "error": f"Tool '{tool_name}' is not permitted in read-only phase."}

                try:
                    result = await registered.fn(call.arguments)
                except Exception as e:
                    self._save_session(session_id=session_id, state=state)
                    return {"status": "FAILURE", "error": f"Tool '{tool_name}' failed: {e}"}
                state.last_tool_results.setdefault("tools_called", []).append(
                    {"tool": tool_name, "args": call.arguments, "result_type": type(result).__name__}
                )
                state.last_tool_results[tool_name] = result

                verdict = self._normalize_tool_result(tool_name, result)
                if verdict["status"] == ToolResultStatus.AMBIGUOUS:
                    q = verdict["clarification_question"]
                    state.pending_clarification = q
                    self._save_session(session_id=session_id, state=state)
                    return {
                        "status": "NEEDS_CLARIFICATION",
                        "uncertainty": ToolResultStatus.AMBIGUOUS.value,
                        "question": q.question,
                        "candidates": [
                            {"value": c.value, "label": c.label, "evidence": c.evidence} for c in q.candidates
                        ],
                        "tools_called": state.last_tool_results.get("tools_called", []),
                    }
                if verdict["status"] == ToolResultStatus.EMPTY:
                    self._save_session(session_id=session_id, state=state)
                    return {"status": "FAILURE", "error": verdict["message"]}
                if verdict["status"] == ToolResultStatus.ERROR:
                    self._save_session(session_id=session_id, state=state)
                    return {"status": "FAILURE", "error": verdict["message"]}
                if verdict["status"] == ToolResultStatus.NOT_AVAILABLE:
                    state.context.setdefault("unavailable_tools", {})[tool_name] = verdict["message"]
                if verdict["status"] == ToolResultStatus.AVAILABLE:
                    state.known_facts[tool_name] = verdict["value"]
                if verdict["status"] == ToolResultStatus.INFERABLE:
                    state.inferable_facts[tool_name] = {
                        "value": verdict["value"],
                        "derivation": verdict.get("derivation", []),
                    }

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

    def _normalize_tool_result(self, tool_name: str, result: Any) -> Dict[str, Any]:
        if not isinstance(result, dict):
            return {"status": ToolResultStatus.ERROR, "message": f"Tool '{tool_name}' returned non-dict result."}
        status_raw = str(result.get("status") or "").upper().strip()
        try:
            status = ToolResultStatus(status_raw)
        except Exception:
            return {"status": ToolResultStatus.ERROR, "message": f"Tool '{tool_name}' returned invalid status '{status_raw}'."}

        if status == ToolResultStatus.AMBIGUOUS:
            cands = result.get("candidates") or result.get("value") or []
            candidates: List[Candidate] = []
            for c in cands:
                if isinstance(c, dict):
                    candidates.append(
                        Candidate(
                            value=str(c.get("value") or c.get("account_id") or c.get("key") or c.get("id") or ""),
                            label=str(c.get("label") or c.get("display_name") or c.get("summary") or c.get("name") or ""),
                            evidence=list(c.get("evidence") or []),
                        )
                    )
            question = result.get("question") or f"I found multiple matches for {tool_name}. Which one do you mean?"
            return {"status": ToolResultStatus.AMBIGUOUS, "clarification_question": AmbiguityQuestion(question=question, candidates=candidates)}

        if status == ToolResultStatus.EMPTY:
            return {"status": ToolResultStatus.EMPTY, "message": f"I couldn't verify that from the available Jira data."}
        if status == ToolResultStatus.NOT_AVAILABLE:
            reason = str(result.get("reason") or "Resource not available from current tool.")
            return {"status": ToolResultStatus.NOT_AVAILABLE, "message": reason}
        if status == ToolResultStatus.INSUFFICIENT_DATA:
            if result.get("derivation"):
                return {
                    "status": ToolResultStatus.INFERABLE,
                    "value": result.get("value"),
                    "derivation": result.get("derivation"),
                    "message": str(result.get("reason") or ""),
                }
            return {
                "status": ToolResultStatus.INSUFFICIENT_DATA,
                "message": str(result.get("reason") or "Insufficient data to conclude."),
            }
        if status == ToolResultStatus.INFERABLE:
            derivation = result.get("derivation")
            if not derivation or not isinstance(derivation, list) or not any(str(item).strip() for item in derivation):
                return {
                    "status": ToolResultStatus.ERROR,
                    "message": f"Tool '{tool_name}' marked inferable without supporting derivation.",
                }
            return {
                "status": ToolResultStatus.INFERABLE,
                "value": result.get("value"),
                "derivation": derivation,
                "message": str(result.get("reason") or ""),
            }
        if status == ToolResultStatus.AVAILABLE:
            return {"status": ToolResultStatus.AVAILABLE, "value": result.get("value")}
        return {"status": ToolResultStatus.ERROR, "message": str(result.get("reason") or "Unknown tool result error.")}

    def _apply_clarification_response(self, state: AgentState) -> None:
        response = (state.current_input or "").strip()
        if not response or not state.pending_clarification:
            return
        candidates = state.pending_clarification.candidates
        if not candidates:
            return
        low = response.lower()
        matched = [c for c in candidates if low == c.value.lower() or low in c.label.lower() or c.label.lower() in low]
        if len(matched) == 1:
            chosen = matched[0]
            qtxt = state.pending_clarification.question.lower()
            if "sprint" in qtxt:
                state.selected_sprint = chosen.value
            elif "user" in qtxt or "person" in qtxt or "assignee" in qtxt:
                state.selected_user = chosen.value
            else:
                state.selected_issue = chosen.value
            state.context["clarification_response"] = response
            state.context["clarification_resolved"] = chosen.value
            state.pending_clarification = None

    def _clarification_is_supported(self, state: AgentState) -> bool:
        tools_called = state.last_tool_results.get("tools_called", [])
        if not tools_called:
            return True
        unresolved_statuses = {
            ToolResultStatus.AMBIGUOUS.value,
            ToolResultStatus.EMPTY.value,
            ToolResultStatus.NOT_AVAILABLE.value,
            ToolResultStatus.INSUFFICIENT_DATA.value,
        }
        for call in tools_called:
            tool_name = call.get("tool")
            raw_result = state.last_tool_results.get(tool_name)
            if not isinstance(raw_result, dict):
                continue
            status = str(raw_result.get("status") or "").upper().strip()
            if status in unresolved_statuses:
                return True
        return False

