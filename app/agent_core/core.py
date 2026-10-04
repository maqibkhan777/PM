from __future__ import annotations

import asyncio
import logging
import time
import re
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
        if not state.pending_clarification:
            return AgentState(user_goal=current_input, current_input=current_input)
        # keep original goal, only update current input while clarification is pending
        state.current_input = current_input
        return state

    def _discard_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

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
                if self._clarification_cancelled(state.current_input):
                    self._discard_session(session_id=session_id)
                    return {"status": "CANCELLED", "answer": "OK, cancelled."}
                clarification_resolution = self._apply_clarification_response(state)
                if clarification_resolution == "NO_MATCH":
                    if self._clarification_looks_like_new_goal(state.current_input) or self._clarification_retry_count(state) >= 1:
                        state = AgentState(user_goal=user_goal, current_input=user_goal)
                        self._save_session(session_id=session_id, state=state)
                        continue
                    self._set_clarification_retry_count(state)
                    note = "I didn't recognise that."
                    question = state.pending_clarification.question
                    if not question.lower().startswith(note.lower()):
                        question = f"{note} {question}"
                    self._save_session(session_id=session_id, state=state)
                    return {
                        "status": "NEEDS_CLARIFICATION",
                        "uncertainty": state.last_uncertainty.value if state.last_uncertainty else None,
                        "question": question,
                        "clarification_kind": state.pending_clarification.kind,
                        "candidates": [
                            {
                                "value": c.value,
                                "label": c.label,
                                "evidence": c.evidence,
                                **(c.metadata or {}),
                            }
                            for c in state.pending_clarification.candidates
                        ],
                        "tools_called": state.last_tool_results.get("tools_called", []),
                    }
                if clarification_resolution == "MATCHED_PROJECT":
                    state.context.pop("clarification_retry_count", None)
                    project_key = state.selected_project
                    if project_key:
                        try:
                            if await self._process_tool_call(state, ToolCall(tool_name="get_active_sprints", arguments={"project_key": project_key}), session_id=session_id):
                                return await self._handle_tool_outcome(state, session_id)
                        except RuntimeError as exc:
                            return {"status": "FAILURE", "error": str(exc)}

            try:
                step: AgentStep = await self._provider_next_step(state=state, actor=actor)
            except Exception as e:
                self._discard_session(session_id=session_id)
                return {"status": "FAILURE", "error": f"Provider failure: {e}"}
            if not isinstance(step, AgentStep):
                self._discard_session(session_id=session_id)
                return {"status": "FAILURE", "error": "Provider returned malformed agent step."}

            # Audit (token-efficient): only log tool names and outcomes; never dump raw payload.
            state.last_uncertainty = step.uncertainty_class

            if step.kind == "CLARIFICATION" and step.ambiguity_question:
                if self._clarification_is_supported(state):
                    state.context.pop("clarification_retry_count", None)
                    state.pending_clarification = step.ambiguity_question
                    self._save_session(session_id=session_id, state=state)
                    return {
                        "status": "NEEDS_CLARIFICATION",
                        "uncertainty": state.pending_clarification and state.last_uncertainty.value,
                        "question": state.pending_clarification.question,
                        "clarification_kind": state.pending_clarification.kind,
                        "candidates": [
                            {"value": c.value, "label": c.label, "evidence": c.evidence, **(c.metadata or {})} for c in state.pending_clarification.candidates
                        ],
                        "tools_called": state.last_tool_results.get("tools_called", []),
                    }
                state.context["clarification_rejected"] = {
                    "question": step.ambiguity_question.question,
                    "reason": "clarification_not_supported_by_tool_history",
                }
                state.context.setdefault("clarification_rejections", []).append(
                    {
                        "question": step.ambiguity_question.question,
                        "reason": "clarification_not_supported_by_tool_history",
                        "tools_called": state.last_tool_results.get("tools_called", []),
                    }
                )
                continue

            if step.kind == "TOOL_CALL":
                if not step.next_tool_calls:
                    # Fail-safe: tool-call step with no tools -> failure rather than guessing.
                    return {"status": "FAILURE", "error": "Provider requested tool-call but provided no tools."}

            for call in step.next_tool_calls:
                try:
                    outcome = await self._process_tool_call(state, call, session_id=session_id)
                except RuntimeError as exc:
                    return {"status": "FAILURE", "error": str(exc)}
                if outcome:
                    return await self._handle_tool_outcome(state, session_id)

            if step.final_answer:
                if self._goal_requires_sprint_resolution(state) and not self._sprint_is_resolved(state):
                    try:
                        force_args = {"project_key": state.selected_project} if state.selected_project else {}
                        if await self._process_tool_call(state, ToolCall(tool_name="get_active_sprints", arguments=force_args), session_id=session_id):
                            return await self._handle_tool_outcome(state, session_id)
                    except RuntimeError as exc:
                        return {"status": "FAILURE", "error": str(exc)}
                    continue
                self._discard_session(session_id=session_id)
                return {
                    "status": "COMPLETED",
                    "answer": step.final_answer,
                    "uncertainty": step.uncertainty_class.value if step.uncertainty_class else None,
                    "tools_called": state.last_tool_results.get("tools_called", []),
                    "proposed_actions": state.proposed_actions,
                }

            if step.kind == "FAILURE":
                self._discard_session(session_id=session_id)
                return {"status": "FAILURE", "error": step.final_answer or "Provider failure."}

            if step.kind == "PROPOSE_ACTION":
                self._discard_session(session_id=session_id)
                return {
                    "status": "PROPOSED_ACTION",
                    "proposed_actions": step.proposed_actions,
                    "uncertainty": step.uncertainty_class.value if step.uncertainty_class else None,
                    "tools_called": state.last_tool_results.get("tools_called", []),
                }

        self._discard_session(session_id=session_id)
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

    async def _process_tool_call(self, state: AgentState, call: ToolCall, session_id: str) -> bool:
        tool_name = call.tool_name
        registered = self.tool_registry.get(tool_name)
        if not registered:
            self._discard_session(session_id=session_id)
            raise RuntimeError(f"Unknown tool requested by provider: {tool_name}")
        if not registered.spec.read_only or registered.spec.mutates_external_system or registered.spec.requires_approval:
            self._discard_session(session_id=session_id)
            raise RuntimeError(f"Tool '{tool_name}' is not permitted in read-only phase.")

        try:
            result = await registered.fn(call.arguments)
        except Exception as e:
            self._discard_session(session_id=session_id)
            raise RuntimeError(f"Tool '{tool_name}' failed: {e}")
        state.last_tool_results.setdefault("tools_called", []).append(
            {"tool": tool_name, "args": call.arguments, "result_type": type(result).__name__}
        )
        state.last_tool_results[tool_name] = result

        verdict = self._normalize_tool_result(tool_name, result)
        state.context["last_tool_verdict"] = {"tool": tool_name, **verdict}
        if verdict["status"] == ToolResultStatus.AMBIGUOUS:
            q = verdict["clarification_question"]
            state.context.pop("clarification_retry_count", None)
            state.pending_clarification = q
            self._save_session(session_id=session_id, state=state)
            return True
        if verdict["status"] == ToolResultStatus.EMPTY:
            self._discard_session(session_id=session_id)
            raise RuntimeError(verdict["message"])
        if verdict["status"] == ToolResultStatus.ERROR:
            self._discard_session(session_id=session_id)
            raise RuntimeError(verdict["message"])
        if verdict["status"] == ToolResultStatus.NOT_AVAILABLE:
            state.context.setdefault("unavailable_tools", {})[tool_name] = verdict["message"]
        if verdict["status"] == ToolResultStatus.AVAILABLE:
            state.known_facts[tool_name] = verdict["value"]
            if tool_name == "get_active_sprints" and isinstance(verdict.get("value"), dict):
                value = verdict.get("value") or {}
                if value.get("project_key"):
                    state.selected_project = str(value.get("project_key"))
                candidates = value.get("candidates") if isinstance(value.get("candidates"), list) else []
                if len(candidates) == 1:
                    sprint_candidate = candidates[0] if isinstance(candidates[0], dict) else {}
                    state.selected_sprint = str(sprint_candidate.get("value") or value.get("id") or "")
                    state.selected_sprint_name = str(value.get("name") or sprint_candidate.get("label") or "")
        if verdict["status"] == ToolResultStatus.INFERABLE:
            state.inferable_facts[tool_name] = {
                "value": verdict["value"],
                "derivation": verdict.get("derivation", []),
            }
        return False

    async def _handle_tool_outcome(self, state: AgentState, session_id: str) -> Dict[str, Any]:
        if state.pending_clarification:
            q = state.pending_clarification
            return {
                "status": "NEEDS_CLARIFICATION",
                "uncertainty": ToolResultStatus.AMBIGUOUS.value,
                "question": q.question,
                "clarification_kind": q.kind,
                "candidates": [
                    {"value": c.value, "label": c.label, "evidence": c.evidence, **(c.metadata or {})}
                    for c in q.candidates
                ],
                "tools_called": state.last_tool_results.get("tools_called", []),
            }
        return {"status": "FAILURE", "error": "Tool outcome did not produce a clarification."}

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
                    metadata = {k: v for k, v in c.items() if k not in {"value", "account_id", "key", "id", "label", "display_name", "summary", "name", "evidence"}}
                    candidates.append(
                        Candidate(
                            value=str(c.get("value") or c.get("account_id") or c.get("key") or c.get("id") or ""),
                            label=str(c.get("label") or c.get("display_name") or c.get("summary") or c.get("name") or ""),
                            evidence=list(c.get("evidence") or []),
                            metadata=metadata,
                        )
                    )
            question = result.get("question") or f"I found multiple matches for {tool_name}. Which one do you mean?"
            kind = str(result.get("clarification_kind") or result.get("kind") or self._infer_clarification_kind(tool_name)).lower()
            if kind not in {"project", "sprint", "issue", "user"}:
                kind = "issue"
            return {"status": ToolResultStatus.AMBIGUOUS, "clarification_question": AmbiguityQuestion(question=question, candidates=candidates, kind=kind)}

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

    def _apply_clarification_response(self, state: AgentState) -> str:
        response = (state.current_input or "").strip()
        if not response or not state.pending_clarification:
            return "NONE"
        candidates = state.pending_clarification.candidates
        if not candidates:
            return "NONE"
        low = response.lower()
        clarification_kind = state.pending_clarification.kind

        def _candidate_names(candidate: Candidate) -> List[str]:
            metadata = candidate.metadata or {}
            if clarification_kind == "project":
                return [
                    candidate.value,
                    str(metadata.get("project_key") or ""),
                    str(metadata.get("project_name") or candidate.label or ""),
                ]
            if clarification_kind == "sprint":
                return [
                    candidate.value,
                    str(metadata.get("sprint_name") or candidate.label or ""),
                ]
            if clarification_kind == "user":
                return [
                    candidate.value,
                    str(metadata.get("user_name") or metadata.get("name") or candidate.label or ""),
                ]
            return [candidate.value, candidate.label]

        def _candidate_matches(candidate: Candidate) -> bool:
            names = [str(name).strip() for name in _candidate_names(candidate) if str(name).strip()]
            name_norms = {name.lower(): name for name in names}
            if low in name_norms:
                return True
            if names:
                canonical_name = names[-1].lower()
                if len(low) >= 3 and (canonical_name.startswith(low) or canonical_name.endswith(low) or low in canonical_name):
                    # unique prefix/contains on the real entity name only
                    matching = 0
                    for other in candidates:
                        other_names = [str(name).strip().lower() for name in _candidate_names(other) if str(name).strip()]
                        if any(len(low) >= 3 and (oname.startswith(low) or oname.endswith(low) or low in oname) for oname in other_names[-1:]):
                            matching += 1
                    if matching == 1:
                        return True
            return False

        matched = [c for c in candidates if _candidate_matches(c)]
        if len(matched) == 1:
            chosen = matched[0]
            qkind = state.pending_clarification.kind
            if qkind == "project":
                state.selected_project = chosen.value
            elif qkind == "sprint":
                state.selected_sprint = chosen.value
                state.selected_sprint_name = chosen.label
            elif qkind == "user":
                state.selected_user = chosen.value
            else:
                state.selected_issue = chosen.value
            state.context["clarification_response"] = response
            state.context["clarification_resolved"] = chosen.value
            state.context["clarification_kind"] = qkind
            state.context.pop("clarification_retry_count", None)
            state.pending_clarification = None
            return f"MATCHED_{qkind.upper()}"
        return "NO_MATCH"

    def _clarification_retry_count(self, state: AgentState) -> int:
        try:
            return int(state.context.get("clarification_retry_count", 0))
        except Exception:
            return 0

    def _set_clarification_retry_count(self, state: AgentState) -> None:
        state.context["clarification_retry_count"] = self._clarification_retry_count(state) + 1

    def _clarification_looks_like_new_goal(self, text: str) -> bool:
        stripped = (text or "").strip()
        lowered = stripped.lower()
        if not stripped:
            return False
        if lowered in {"cancel", "cancel.", "nevermind", "never mind", "stop"}:
            return True
        if "?" in stripped:
            return True
        if self._looks_like_issue_key(stripped):
            return True
        return len(stripped.split()) >= 4

    def _clarification_cancelled(self, text: str) -> bool:
        lowered = (text or "").strip().lower()
        return lowered in {"cancel", "cancel.", "nevermind", "never mind", "stop"}

    def _goal_requires_sprint_resolution(self, state: AgentState) -> bool:
        raw_text = f"{state.user_goal} {state.current_input}"
        text = raw_text.lower()
        if self._looks_like_issue_key(raw_text):
            return False
        if state.selected_issue:
            return False
        for call in state.last_tool_results.get("tools_called", []):
            if isinstance(call, dict) and call.get("tool") in {"get_issue", "search_issues"}:
                return False
        return bool(
            re.search(r"\bsprint\b", text)
            or "on track" in text
            or re.search(r"\bvelocity\b", text)
            or re.search(r"\bburndown\b", text)
            or re.search(r"\bburn[- ]?down\b", text)
        )

    def _sprint_is_resolved(self, state: AgentState) -> bool:
        if state.selected_sprint:
            return True
        if any(k in state.last_tool_results for k in ("get_sprint", "get_sprint_issues")):
            return True
        for call in state.last_tool_results.get("tools_called", []):
            if isinstance(call, dict) and call.get("tool") in {"get_sprint", "get_sprint_issues"}:
                return True
        return False

    def _looks_like_issue_key(self, text: str) -> bool:
        return bool(re.search(r"\b[A-Z][A-Z0-9]+-\d+\b", text))

    def _infer_clarification_kind(self, tool_name: str) -> str:
        if tool_name == "get_active_sprints":
            return "project"
        if tool_name in {"find_user", "get_users"}:
            return "user"
        if tool_name in {"get_issue", "search_issues", "get_linked_issues"}:
            return "issue"
        return "issue"

    def _clarification_is_supported(self, state: AgentState) -> bool:
        tools_called = state.last_tool_results.get("tools_called", [])
        unresolved_statuses = {
            ToolResultStatus.AMBIGUOUS.value,
            ToolResultStatus.EMPTY.value,
            ToolResultStatus.NOT_AVAILABLE.value,
            ToolResultStatus.INSUFFICIENT_DATA.value,
        }
        if not tools_called:
            return False
        for call in tools_called:
            tool_name = call.get("tool")
            raw_result = state.last_tool_results.get(tool_name)
            if not isinstance(raw_result, dict):
                continue
            status = str(raw_result.get("status") or "").upper().strip()
            if status in unresolved_statuses:
                return True
        return False

