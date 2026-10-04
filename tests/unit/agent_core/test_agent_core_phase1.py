import pytest

from app.agent_core.agent_models import AgentStep, AgentState, Candidate, ToolCall, ToolSpec, UncertaintyClass, AmbiguityQuestion, ToolResultStatus
from app.agent_core.core import AgentCore
from app.agent_core.tooling import ToolRegistry
from app.services.ai.agent_provider import AgentProvider
from app.services.ai.provider import AIProvider
from app.services.ai.pm_tools import build_tool_registry


class ScriptedProvider:
    """Deterministic provider for tool-loop tests."""

    def __init__(self, scripted_steps):
        self.scripted_steps = list(scripted_steps)
        self.calls = 0

    async def next_agent_step(self, user_goal: str, actor: str, state: AgentState, tools):
        if self.calls >= len(self.scripted_steps):
            raise AssertionError("Provider received more iterations than scripted.")
        step = self.scripted_steps[self.calls]
        self.calls += 1
        return step


@pytest.mark.asyncio
async def test_tool_selection_and_multi_tool_accumulation():
    async def t1(args):
        return {"x": 1}

    async def t2(args):
        return {"y": args.get("k", 2)}

    registry = ToolRegistry()
    registry.register("tool1", ToolSpec(name="tool1", description="t1"), t1)
    registry.register("tool2", ToolSpec(name="tool2", description="t2"), t2)

    steps = [
        AgentStep(
            kind="TOOL_CALL",
            next_tool_calls=[ToolCall(tool_name="tool1", arguments={}), ToolCall(tool_name="tool2", arguments={"k": 7})],
            uncertainty_class=UncertaintyClass.KNOWN,
            reasoning_trace=["scripted"],
            final_answer="done",
        )
    ]

    provider = ScriptedProvider(steps)
    agent_provider = AgentProvider(provider)  # type: ignore[arg-type]
    core = AgentCore(provider=provider, tool_registry=registry, agent_provider=agent_provider)  # type: ignore[arg-type]

    res = await core.run("goal", actor="u1")
    assert res["status"] == "COMPLETED"
    assert len(res["tools_called"]) == 2
    assert res["uncertainty"] == UncertaintyClass.KNOWN.value


@pytest.mark.asyncio
async def test_ambiguous_user_produces_clarification_question():
    async def dummy(args):
        return {
            "status": ToolResultStatus.AMBIGUOUS.value,
            "tool": "find_user",
            "candidates": [
                {"value": "A", "label": "Alice (Developer)", "evidence": ["role"]},
                {"value": "B", "label": "Bob (QA)", "evidence": ["role"]},
            ],
        }

    registry = ToolRegistry()
    registry.register("find_user", ToolSpec(name="find_user", description="users"), dummy)

    steps = [
        AgentStep(
            kind="TOOL_CALL",
            next_tool_calls=[ToolCall(tool_name="find_user", arguments={})],
            final_answer=None,
            uncertainty_class=UncertaintyClass.AMBIGUOUS,
            reasoning_trace=["scripted"],
        )
    ]
    provider = ScriptedProvider(steps)
    agent_provider = AgentProvider(provider)  # type: ignore[arg-type]
    core = AgentCore(provider=provider, tool_registry=registry, agent_provider=agent_provider)  # type: ignore[arg-type]

    res = await core.run("goal", actor="u1")
    assert res["status"] == "NEEDS_CLARIFICATION"
    assert "Which user?" in res["question"] or "Alice" in str(res["candidates"])
    assert len(res["candidates"]) == 2


@pytest.mark.asyncio
async def test_single_user_match_resolves_without_clarification():
    async def single(args):
        return {
            "status": ToolResultStatus.AVAILABLE.value,
            "tool": "find_user",
            "value": {"account_id": "acc-1", "display_name": "Ali Raza", "role": "Developer", "designation": "Developer", "role_category": "Engineering"},
        }

    registry = ToolRegistry()
    registry.register("find_user", ToolSpec(name="find_user", description="users"), single)
    provider = ScriptedProvider([AgentStep.tool_calls([ToolCall(tool_name="find_user", arguments={"query": "Ali"})]), AgentStep.final("Ali Raza")])
    core = AgentCore(provider=provider, tool_registry=registry, agent_provider=AgentProvider(provider))  # type: ignore[arg-type]
    res = await core.run("Who is Ali?", actor="u1")
    assert res["status"] == "COMPLETED"
    assert "Ali Raza" in res["answer"]


@pytest.mark.asyncio
async def test_single_sprint_without_asking_clarification():
    # We model "single match" behavior purely at the uncertainty-question level:
    # if provider returns final_answer directly, AgentCore should complete.
    registry = ToolRegistry()
    async def get_active_sprints(_args):
        return {"sprints": ["S1"]}

    registry.register("get_active_sprints", ToolSpec(name="get_active_sprints", description="sprints"), get_active_sprints)

    steps = [
        AgentStep(
            kind="TOOL_CALL",
            next_tool_calls=[ToolCall(tool_name="get_active_sprints", arguments={})],
            final_answer="single sprint chosen",
            ambiguity_question=None,
            uncertainty_class=UncertaintyClass.KNOWN,
            reasoning_trace=["scripted"],
        )
    ]
    provider = ScriptedProvider(steps)
    agent_provider = AgentProvider(provider)  # type: ignore[arg-type]
    core = AgentCore(provider=provider, tool_registry=registry, agent_provider=agent_provider)  # type: ignore[arg-type]

    res = await core.run("goal", actor="u1")
    assert res["status"] == "COMPLETED"
    assert "question" not in res


@pytest.mark.asyncio
async def test_write_tools_only_emit_proposed_actions():
    # Tool results should not directly be "executed actions".
    async def propose_actions(args):
        return {"proposed_actions": [{"action_type": "TRANSITION_TASK", "target_id": args["issue"]}]}

    registry = ToolRegistry()
    registry.register("plan_write", ToolSpec(name="plan_write", description="propose only"), propose_actions)

    steps = [
        AgentStep(
            kind="TOOL_CALL",
            next_tool_calls=[ToolCall(tool_name="plan_write", arguments={"issue": "WSSS-1"})],
            final_answer="proposal ready",
            ambiguity_question=None,
            uncertainty_class=UncertaintyClass.INFERABLE,
            reasoning_trace=["scripted"],
        )
    ]
    provider = ScriptedProvider(steps)
    agent_provider = AgentProvider(provider)  # type: ignore[arg-type]
    core = AgentCore(provider=provider, tool_registry=registry, agent_provider=agent_provider)  # type: ignore[arg-type]

    res = await core.run("goal", actor="u1")
    assert res["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_ambiguous_active_sprints_become_clarification():
    async def sprints(_args):
        return {
            "status": ToolResultStatus.AMBIGUOUS.value,
            "tool": "get_active_sprints",
            "candidates": [
                {"value": "Sprint 42", "label": "Sprint 42", "evidence": ["active sprint"]},
                {"value": "Sprint 43", "label": "Sprint 43", "evidence": ["active sprint"]},
            ],
        }

    registry = ToolRegistry()
    registry.register("get_active_sprints", ToolSpec(name="get_active_sprints", description="sprints"), sprints)
    provider = ScriptedProvider([AgentStep.tool_calls([ToolCall(tool_name="get_active_sprints", arguments={})])])
    core = AgentCore(provider=provider, tool_registry=registry, agent_provider=AgentProvider(provider))  # type: ignore[arg-type]
    res = await core.run("Which sprint should I use?", actor="u1")
    assert res["status"] == "NEEDS_CLARIFICATION"
    assert "Sprint 42" in str(res["candidates"]) and "Sprint 43" in str(res["candidates"])


@pytest.mark.asyncio
async def test_zero_matches_returns_truthful_failure():
    async def missing(_args):
        return {"status": ToolResultStatus.EMPTY.value, "tool": "find_user", "reason": "No matching user found."}

    registry = ToolRegistry()
    registry.register("find_user", ToolSpec(name="find_user", description="users"), missing)
    provider = ScriptedProvider([AgentStep.tool_calls([ToolCall(tool_name="find_user", arguments={"query": "Nobody"})])])
    core = AgentCore(provider=provider, tool_registry=registry, agent_provider=AgentProvider(provider))  # type: ignore[arg-type]
    res = await core.run("Who is Nobody?", actor="u1")
    assert res["status"] == "FAILURE"
    assert "couldn't verify" in res["error"].lower()


@pytest.mark.asyncio
async def test_inferable_carries_derivation_in_state():
    async def infer_hours(_args):
        return {
            "status": ToolResultStatus.INSUFFICIENT_DATA.value,
            "tool": "capacity_workload_summary",
            "value": {"remaining_hours": 10},
            "derivation": ["capacity 40h - committed 30h = 10h remaining"],
            "reason": "Derived remaining capacity",
        }

    registry = ToolRegistry()
    registry.register("capacity_workload_summary", ToolSpec(name="capacity_workload_summary", description="capacity"), infer_hours)
    provider = ScriptedProvider([AgentStep.tool_calls([ToolCall(tool_name="capacity_workload_summary", arguments={})]), AgentStep.final("done")])
    core = AgentCore(provider=provider, tool_registry=registry, agent_provider=AgentProvider(provider))  # type: ignore[arg-type]
    res = await core.run("How much time is left?", actor="u1", session_id="cap1")
    assert res["status"] == "COMPLETED"
    session = core._sessions["cap1"]["state"]
    assert "capacity_workload_summary" in session.inferable_facts


@pytest.mark.asyncio
async def test_clarification_continuity_resumes_original_goal():
    class ContinuityProvider:
        async def next_agent_step(self, user_goal: str, actor: str, state: AgentState, tools):
            if state.selected_sprint:
                return AgentStep.final(f"Resumed goal for {state.selected_sprint}.")
            return AgentStep.tool_calls([ToolCall(tool_name="get_active_sprints", arguments={})], uncertainty=UncertaintyClass.AMBIGUOUS)

    async def sprints(_args):
        return {
            "status": ToolResultStatus.AMBIGUOUS.value,
            "tool": "get_active_sprints",
            "candidates": [
                {"value": "Sprint A", "label": "Sprint A", "evidence": ["active sprint"]},
                {"value": "Sprint B", "label": "Sprint B", "evidence": ["active sprint"]},
            ],
        }

    registry = ToolRegistry()
    registry.register("get_active_sprints", ToolSpec(name="get_active_sprints", description="sprints"), sprints)
    provider = ContinuityProvider()
    core = AgentCore(provider=provider, tool_registry=registry, agent_provider=AgentProvider(provider))  # type: ignore[arg-type]
    first = await core.run("Which sprint?", actor="u1", session_id="t1")
    assert first["status"] == "NEEDS_CLARIFICATION"
    second = await core.run("Sprint B", actor="u1", session_id="t1")
    assert second["status"] == "COMPLETED"
    assert "Sprint B" in second["answer"]


@pytest.mark.asyncio
async def test_unregistered_tool_rejected_and_loop_limit_safe():
    class BadProvider:
        async def next_agent_step(self, user_goal: str, actor: str, state: AgentState, tools):
            return AgentStep.tool_calls([ToolCall(tool_name="not_registered", arguments={})])

    registry = ToolRegistry()
    provider = BadProvider()
    core = AgentCore(provider=provider, tool_registry=registry, agent_provider=AgentProvider(provider))  # type: ignore[arg-type]
    res = await core.run("goal", actor="u1", session_id="bad1")
    assert res["status"] == "FAILURE"
    assert "Unknown tool" in res["error"]

    class LoopProvider:
        async def next_agent_step(self, user_goal: str, actor: str, state: AgentState, tools):
            return AgentStep.tool_calls([ToolCall(tool_name="noop", arguments={})])

    async def noop(_args):
        return {"status": ToolResultStatus.AVAILABLE.value, "tool": "noop", "value": {"ok": True}}

    registry = ToolRegistry()
    registry.register("noop", ToolSpec(name="noop", description="noop"), noop)
    loop_core = AgentCore(provider=LoopProvider(), tool_registry=registry, agent_provider=AgentProvider(LoopProvider()))  # type: ignore[arg-type]
    res2 = await loop_core.run("loop", actor="u1", session_id="loop1")
    assert res2["status"] == "FAILED"


@pytest.mark.asyncio
async def test_not_available_comments_path_returns_not_available():
    registry = build_tool_registry()
    tool = registry.get("get_comments")
    assert tool is not None
    result = await tool.fn({"issue_key": "WSSS-1"})
    assert result["status"] == "NOT_AVAILABLE"


